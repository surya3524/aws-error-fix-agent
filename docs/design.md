# AI Error-Fix Agent on AWS — Build Spec

Goal: when the application logs an error, an agent investigates the codebase
(hosted in **Azure DevOps**), writes a failing test + fix, verifies it, and
opens a **draft PR** for human review. The agent never merges or deploys.

Legend: **[web]** = verified against a web source (see Sources) ·
**[inferred]** = design judgement, not from a single source ·
**[unverified]** = not checked; confirm before relying on it.

---

## 1. Core principle

The agent runs in a throwaway, network-locked sandbox that holds **no
credentials that can change anything**. It outputs a patch file (data only).
A separate trusted service verifies the patch and opens the PR. A hijacked
agent (e.g. via prompt injection hidden in a log message) can at most produce
a bad patch, which a human reviews. **[inferred]**

---

## 2. Architecture

```
CloudWatch Logs ─(subscription filter)─► Lambda "intake" ─► SQS (+ DLQ)
                                                              │
                     Orchestrator (Lambda / Step Functions)  TRUSTED
                     • clones Azure DevOps repo at the deployed commit SHA
                     • holds the Azure DevOps credential (Secrets Manager)
                                                              │ repo snapshot + redacted error
               ┌──────────────────────────────────────────────▼──────────────┐
               │ UNTRUSTED SANDBOX: AgentCore Runtime session OR Fargate task│
               │ • Claude Code → Amazon Bedrock                              │
               │ • IAM role: bedrock:InvokeModel only                        │
               │ • private subnet → AWS Network Firewall domain allowlist    │
               │ • no Azure DevOps creds, no prod secrets, no prod data      │
               │ • CPU/memory/time limits; destroyed after the run           │
               └──────────────────────────────────────────────┬──────────────┘
                                                              │ patch.diff + report.json
                     Verifier (separate clean Fargate task)   TRUSTED
                     • apply patch → build → full tests
                     • new test fails on old code, passes on fix
                     • diff policy (no pipeline/infra/secret files, size cap)
                                                              │ pass only
                     Orchestrator → Azure DevOps REST API → draft PR
                                                              │
                     Metrics: PR merged / edited / closed → eval set
```

---

## 3. Component mapping

| Role | AWS service | Notes |
|---|---|---|
| Error source | CloudWatch Logs | **[web]** Subscription filter streams matching events to Lambda (base64 + gzip). Or metric filter → alarm → SNS for count-based alerting. |
| Intake | Lambda | Fingerprint (exception type + top stack frame), dedupe, rate-limit, redact secrets/PII, look up deployed SHA. **[inferred]** |
| Queue | SQS + dead-letter queue | **[inferred]** |
| Orchestrator | Lambda or Step Functions | Only component holding the Azure DevOps credential. **[inferred]** |
| Sandbox (VM-isolated, preferred) | Bedrock AgentCore Runtime | **[web]** Each session gets a dedicated microVM with isolated CPU, memory, filesystem. AWS blog covers hosting coding agents on it. |
| Sandbox (simpler) | ECS on Fargate, one task per incident | **[web, third-party only]** Fargate tasks run on Firecracker microVMs. Confirm in AWS docs. |
| Egress control | AWS Network Firewall, stateful domain list (ALLOWLIST) | **[web]** Matches on TLS SNI / HTTP Host. |
| Model | Claude on Amazon Bedrock | **[web]** Claude Code: `CLAUDE_CODE_USE_BEDROCK=1` + `AWS_REGION`. Pin model IDs. |
| Package deps | CodeArtifact / internal mirror, or pre-baked image | Shrinks the egress allowlist. **[inferred]** |
| PR creation | Azure DevOps REST API `POST .../_apis/git/repositories/{repositoryId}/pullrequests?api-version=7.1` | **[web]** |
| Buy option | AWS DevOps Agent | **[web]** Incident root-cause investigation; built-in GitHub, GitLab, Azure DevOps integrations. Evaluate before building. |

---

## 4. Build steps

1. **Logging**: emit structured JSON errors with exception type, stack trace,
   operation/trace ID, and the **deployed commit SHA** (tag at build time).
2. **Intake Lambda**: CloudWatch subscription filter on error level → Lambda
   → fingerprint, dedupe (e.g. DynamoDB table keyed by fingerprint with TTL
   cooldown **[inferred]**), redact, enqueue to SQS.
3. **Sandbox image**: pinned, scanned container with language toolchain,
   dependency caches, Claude Code CLI. No credentials baked in.
4. **Network**: sandbox in private subnet; default route → Network Firewall;
   default action drop; allowlist only: Bedrock endpoint, package mirror,
   container registry. Use STRICT_ORDER rule evaluation. **Test egress from
   inside the sandbox** (see caveat 7.2).
5. **IAM**: sandbox role = invoke one model only (example in §5).
6. **Agent run** inside sandbox: Claude Code in non-interactive mode
   (`claude -p "<task>" --allowedTools "..."`) restricted to
   Read/Grep/Glob/Edit + exact build/test commands. Task: reproduce with a
   failing test, fix, write `report.json` (root cause, evidence, confidence).
   "No fix / low confidence" is a valid output → post RCA on the work item
   instead of a PR. **[inferred]**
7. **Verifier**: fresh Fargate task applies patch to a clean checkout; build,
   full tests, fail-before/pass-after check, diff policy.
8. **Publisher**: orchestrator pushes branch `ai-fix/<fingerprint>` and opens a
   **draft** PR with RCA + evidence + test results; links work item.
9. **Azure DevOps side**: branch policies on `main` require human approval;
   the bot identity can create branches/PRs on that one repo only.
10. **Observability**: store transcript, tool calls, patch, verifier output per
    incident (S3). Track PR acceptance rate, reviewer edits, cost per
    incident, time to PR. Replay past incidents with known fixes as an eval.

---

## 5. Sandbox IAM policy (example)

Replace region/account/model ID with your values. **[inferred — standard IAM
syntax; check the exact Bedrock resource ARN format for your model /
inference profile]**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:<region>::foundation-model/<claude-model-id>"
    }
  ]
}
```

No S3, Secrets Manager, SQS, or other permissions on the sandbox role. The
orchestrator passes inputs in and collects outputs. **[inferred]**

## 6. Sandbox environment (example)

```bash
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_REGION=<region>
# Pin the model; aliases may point to a model not enabled in your account
export ANTHROPIC_MODEL=<pinned-bedrock-model-id>   # [unverified var name — check Claude Code Bedrock docs]
```

---

## 7. Production checklist

1. **Log text is untrusted input** (prompt injection). Mitigated by: no write
   creds in sandbox, deny-all egress, human PR review. **[inferred]**
2. **Firewall allowlist must be tested.** A re:Post user reported arbitrary
   80/443 traffic passing an allowlist; AWS advises not mixing allow and
   deny/alert domain-list groups under default ordering; domain filtering
   applies by default only to traffic from `HOME_NET` (the firewall's VPC
   CIDR). **[web]**
3. **Use the deployed SHA**, not `main`. **[inferred]**
4. **Dedupe before calling the model**; one bug can throw thousands of
   errors. **[inferred]**
5. **No prod data in tests**: mocks, in-memory fakes, throwaway DBs. **[inferred]**
6. **Limits**: wall-clock timeout, CPU/memory caps, max agent turns/tokens,
   concurrency cap, per-fingerprint cooldown. **[inferred]**
7. **Cross-cloud credential**: Azure DevOps identity only in the orchestrator
   (Secrets Manager), scoped to one repo. **[inferred]**
8. **Never auto-merge.** Human approval stays permanently. **[inferred]**

## 8. Rollout

- **Phase 0** – read-only: post RCA to the work item, no code changes.
- **Phase 1** – draft PRs for a narrow set of error types in one service.
- **Phase 2** – widen scope based on measured acceptance rate.
**[inferred]**

## 9. Open items to verify before building **[unverified]**

- AgentCore Runtime regional availability and limits (max session length,
  image size) vs. your build/test duration.
- Bedrock PrivateLink (interface VPC endpoint) support in your region, which
  would remove Bedrock from the internet allowlist.
- Fargate per-task isolation in AWS's own documentation.
- EKS + Kata/Fargate as a Kubernetes alternative.
- CodeBuild as an alternative runner.
- Exact Claude Code env var for model pinning on Bedrock.

---

## Sources

- CloudWatch alarming on logs: https://docs.aws.eu/AmazonCloudWatch/latest/monitoring/Alarm-On-Logs.html
- CloudWatch subscription filters → Lambda: https://oneuptime.com/blog/post/2026-02-12-stream-cloudwatch-logs-lambda-processing/markdown
- re:Post Lambda CloudWatch filter: https://repost.aws/knowledge-center/lambda-cloudwatch-filter
- AgentCore Runtime sessions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.md
- AgentCore Code Interpreter sessions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-session-characteristics.html
- AgentCore security best practices: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.md
- Hosting coding agents on AgentCore: https://aws.amazon.com/blogs/machine-learning/its-safe-to-close-your-laptop-now-hosting-coding-agents-on-amazon-bedrock-agentcore/
- Firecracker announcement: https://aws.amazon.com/blogs/opensource/firecracker-open-source-secure-fast-microvm-serverless/
- Firecracker / Fargate isolation (third-party): https://www.aquasec.com/blog/amazon-firecracker-serverless-container-security/
- Network Firewall domain lists: https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html
- Network Firewall RulesSourceList API: https://docs.aws.amazon.com/network-firewall/latest/APIReference/API_RulesSourceList.html
- re:Post domain rules: https://repost.aws/knowledge-center/network-firewall-configure-domain-rules
- re:Post allowlist bypass question: https://repost.aws/questions/QU1rEZzGniSdCxMJCaKweWnQ/statefull-domain-list
- AWS DevOps Agent FAQs: https://aws.amazon.com/devops-agent/faqs
- AWS DevOps Agent integrations: https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge.html
- AWS DevOps Agent GA: https://aws.amazon.com/blogs/mt/announcing-general-availability-of-aws-devops-agent
- Claude Code on Bedrock: https://code.claude.com/docs/en/amazon-bedrock
- Claude Code non-interactive mode: https://code.claude.com/docs/en/headless
- Claude Code sandboxing: https://code.claude.com/docs/en/sandboxing
- Azure DevOps PR create API: https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create
