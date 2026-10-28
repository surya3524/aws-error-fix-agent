# Setup guide: AI error-fix suggestion agent on AWS

This guide takes you from an empty AWS account to this flow:

> your app logs an error in CloudWatch Logs → you get an email with the likely root cause,
> a 2–3 sentence fix suggestion, the file(s)/line(s) involved, and a confidence level.

Nothing is pushed to Azure DevOps and no pull requests are opened. The agent can only
**read** a snapshot of your code.

Run every command on your own computer, in a terminal, from the project root
(the folder that contains `agent/`, `orchestrator/`, `infra/`).

---

## 0. How it works (2-minute read)

```
App log group (CloudWatch Logs)
   │  subscription filter (only lines matching e.g. ERROR / Exception / Traceback)
   ▼
Orchestrator Lambda  ── TRUSTED: the only component with the Azure DevOps PAT
   1. decode + fingerprint the error, dedupe in DynamoDB (TTL cooldown, default 6 h)
   2. redact obvious secrets / emails / card numbers
   3. download the repo at the deployed commit SHA from Azure DevOps (zip, REST API)
   4. put the zip in S3, make a 15-minute pre-signed GET URL
   5. call the AgentCore Runtime (InvokeAgentRuntime), one new session per incident
   │
   ▼
AgentCore Runtime session ── UNTRUSTED sandbox: dedicated microVM per session
   • Claude Agent SDK → Claude on Amazon Bedrock
   • tools: Read, Grep, Glob only (plus a path guard: nothing outside the snapshot)
   • IAM role: invoke the one configured model (+ logs/ECR basics). No S3, no secrets,
     no Azure DevOps credentials, no production data
   • returns JSON: root_cause, fix_suggestion, files[{path,line}], confidence,
     insufficient_evidence
   │
   ▼
Orchestrator: saves JSON to S3 (results/), updates DynamoDB, publishes to SNS → email
```

Why the pre-signed URL: the sandbox gets read access to exactly one object for 15 minutes,
without having any S3 permission in its own IAM role. The URL is created by the orchestrator.

---

## 1. Prerequisites (install once)

| Tool | Version | Check |
|---|---|---|
| AWS CLI v2 | recent (it must know the `bedrock` commands) | `aws --version` |
| Python | 3.10 or newer (3.12 recommended) | `python3 --version` |
| Node.js | 20 or newer (for the AWS CDK CLI) | `node --version` |
| AWS CDK CLI | latest v2 | `npm install -g aws-cdk` then `cdk --version` |
| Docker | Docker Desktop, or Docker Engine with buildx | `docker buildx version` |

**ARM64 images.** AgentCore Runtime runs ARM64 (linux/arm64) containers. CDK builds the
image for you, but your Docker must be able to build ARM64:

* Apple Silicon Mac: works out of the box.
* Docker Desktop on Windows / Intel Mac: works out of the box (QEMU is built in).
* Linux on x86_64: install the emulators once:
  `docker run --privileged --rm tonistiigi/binfmt --install arm64`

You also need an IAM identity in the AWS account that can deploy CloudFormation, IAM roles,
Lambda, S3, DynamoDB, SNS, Secrets Manager, ECR and Bedrock AgentCore resources
(an administrator role is simplest for a first install), and an Azure DevOps account that
can read the repository.

Configure the CLI and confirm who you are:

```bash
aws configure            # or: aws configure sso / aws login
aws sts get-caller-identity
```

---

## 2. Choose a region

Pick **one** region and use it everywhere. It must have both:

1. **Amazon Bedrock AgentCore Runtime.** Check the AgentCore "Supported AWS Regions" page in
   the AgentCore Developer Guide. (The CloudFormation resource type
   `AWS::BedrockAgentCore::Runtime` is published in, among others, us-east-1, us-east-2,
   us-west-2, eu-central-1, eu-west-1, ap-southeast-2 and ap-northeast-1.)
2. **An Anthropic Claude model or inference profile** you can invoke (step 3).

```bash
export AWS_REGION=us-east-1          # example; use your region
export CDK_DEFAULT_REGION=$AWS_REGION
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
```

---

## 3. Enable Claude on Amazon Bedrock and pick the model ID

1. Open the Amazon Bedrock console in your region → **Model catalog** → choose an
   Anthropic Claude model → submit the **use case details** form (once per account;
   access is granted right after submission). In AWS Organizations you can do this once
   from the management account.
2. Find the ID you will configure. Prefer a **cross-region inference profile** ID, because
   newer Claude models are often invoke-able only through one:

   ```bash
   aws bedrock list-inference-profiles --type-equals SYSTEM_DEFINED --region $AWS_REGION \
     --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'anthropic')].[inferenceProfileId,status]" \
     --output table
   ```

   The IDs look like `<geo>.anthropic.<model-name>` (geo is for example `us`, `eu`, `apac`,
   `global`). For a code-analysis agent, a mid-tier (not the largest) Claude model is usually a good cost/quality default.
   You can also look the ID up in the console (Bedrock → Cross-region inference).

3. Save it (no default exists anywhere in this project, on purpose):

   ```bash
   export MODEL_ID='<paste the inference profile ID here>'
   ```

If invoking a model ID directly returns "on-demand throughput isn't supported", use the
inference profile ID instead.

---

## 4. Create an Azure DevOps Personal Access Token (MVP)

1. Azure DevOps → **User settings** → **Personal access tokens** → **New Token**.
2. Organization: the one that holds the repo. Scope: **Code → Read** only. Short expiry.
3. Copy the token. You will store it in AWS Secrets Manager in step 7; never put it in a file
   in this project.

Also note: your **organization**, **project** and **repository** names
(from `https://dev.azure.com/<org>/<project>/_git/<repo>`).

> Production: replace the PAT with a Microsoft Entra ID service principal / managed identity
> that has read access to this one repo (see Hardening).

---

## 5. Try the agent locally first (recommended, ~5 minutes)

This runs the exact agent code on your machine against the bundled sample app
(`samples/buggy-app`) and sample error (`samples/sample-error.json`). It uses your own AWS
credentials to call Bedrock.

```bash
python3 -m venv .venv
source .venv/bin/activate                    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export CLAUDE_CODE_USE_BEDROCK=1
export ANTHROPIC_MODEL="$MODEL_ID"           # AWS_REGION is already set (step 2)

python scripts/run_local.py --repo samples/buggy-app --error samples/sample-error.json
```

Expected output (wording varies):

```json
{
  "incident_id": "inc-…",
  "status": "ok",
  "analysis": {
    "root_cause": "average_unit_price() divides by the total quantity, which is 0 when every line has quantity 0 …",
    "fix_suggestion": "Guard against total_units == 0 … Two to three sentences.",
    "files": [{"path": "orders/pricing.py", "line": 14, "exists_in_repo": true}],
    "confidence": "high",
    "insufficient_evidence": false
  },
  "meta": {"subtype": "success", "num_turns": 5, "duration_ms": 41234, "estimated_cost_usd": 0.07}
}
```

Point `--repo` at a local clone of your own repo and `--error` at a file containing one real
log line to see how it does on your code.

If `meta.error` says the model is not available or access is denied, fix step 3 first.

### Optional: test the container exactly as AgentCore runs it

```bash
docker buildx build --platform linux/arm64 -t error-fix-agent:local --load agent/
python scripts/make_payload.py --repo samples/buggy-app --error samples/sample-error.json > payload.json

# Pass your credentials as env vars (temporary ones are fine). Example with a profile:
eval "$(aws configure export-credentials --format env)"
docker run --rm -p 8080:8080 \
  -e AWS_REGION -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e ANTHROPIC_MODEL="$MODEL_ID" -e CLAUDE_CODE_USE_BEDROCK=1 \
  error-fix-agent:local

# in a second terminal:
curl -s localhost:8080/ping
curl -s -X POST localhost:8080/invocations -H 'Content-Type: application/json' -d @payload.json
```

(On an x86 machine the ARM64 container runs under emulation, so it is slower.)

---

## 6. Deploy everything with CDK

The stack (`infra/`) creates:

| Resource | Purpose |
|---|---|
| ECR image (built from `agent/`) + `AWS::BedrockAgentCore::Runtime` | the sandboxed agent |
| Runtime execution role | Bedrock InvokeModel on **your** model ID only, plus logs/X-Ray/ECR-pull basics |
| Lambda `Orchestrator` (Python 3.13, 15 min timeout, async retries 0) | trusted orchestrator |
| S3 bucket | `snapshots/` (7-day expiry) and `results/` (90-day expiry) |
| DynamoDB table (TTL `expires_at`) | dedupe + incident status |
| SNS topic (+ optional email subscription) | delivery |
| Secrets Manager secret | holds the Azure DevOps PAT (you set the value) |
| CloudWatch Logs subscription filter | connects your app's log group to the Lambda |

1. Your application's log group must already exist. Note its name, for example
   `/aws/lambda/orders-api` or `/ecs/orders-api`. A log group can have at most two
   subscription filters.

2. Install the CDK dependencies and bootstrap (once per account+region):

   ```bash
   source .venv/bin/activate
   pip install -r infra/requirements.txt
   cd infra
   cdk bootstrap aws://$CDK_DEFAULT_ACCOUNT/$AWS_REGION
   ```

3. Deploy. All `-c` values marked required have no defaults:

   ```bash
   cdk deploy \
     -c modelId="$MODEL_ID" \
     -c logGroupName="/aws/lambda/orders-api" \
     -c azdoOrg="<org>" -c azdoProject="<project>" -c azdoRepo="<repo>" \
     -c notifyEmail="you@example.com"
   ```

   Optional context values:

   | Key | Default | Meaning |
   |---|---|---|
   | `filterPattern` | `?ERROR ?Exception ?Traceback` | CloudWatch Logs filter pattern. For JSON logs, e.g. `{ $.level = "ERROR" }` |
   | `fallbackBranch` | `main` | branch to analyze when a log line has no commit SHA |
   | `commitShaField` | `commit_sha` | JSON field in your log lines that holds the deployed 40-char commit SHA |
   | `maxConcurrentAnalyses` | `2` | Lambda reserved concurrency (cost cap). Set `0` to not reserve (see Troubleshooting) |
   | `agentMaxTurns` | `30` | max agent turns per incident |
   | `agentMaxBudgetUsd` | `2` | client-side cost estimate limit per incident |
   | `sandboxVpc` | `false` | `true` = run the sandbox in an isolated VPC (see Hardening) |

   Tip: put these in `infra/cdk.json` under `"context"` so you don't retype them.

4. Note the outputs (also visible later with
   `aws cloudformation describe-stacks --stack-name ErrorFixAgentStack --query "Stacks[0].Outputs"`):
   `AgentRuntimeArn`, `OrchestratorFunctionName`, `ArtifactsBucketName`,
   `IncidentsTableName`, `SuggestionsTopicArn`, `AzureDevOpsPatSecretArn`.

5. **Confirm the email subscription**: SNS sends a "Subscription Confirmation" email; click
   the link, or no suggestions will arrive.

### IAM in one place (what the stack creates)

Runtime (sandbox) role, the statement this project adds (CDK resolves the ARNs from
`modelId`; shown for an inference-profile ID):

```json
{
  "Sid": "InvokeConfiguredModelOnly",
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
  "Resource": [
    "arn:aws:bedrock:<region>:<account>:inference-profile/<MODEL_ID>",
    "arn:aws:bedrock:*::foundation-model/<MODEL_ID without the geo prefix>"
  ]
}
```

The CDK AgentCore construct adds: CloudWatch Logs write to `/aws/bedrock-agentcore/runtimes/*`,
X-Ray put, `cloudwatch:PutMetricData` (namespace-restricted), ECR pull of the CDK asset
repository, and AgentCore workload-identity token actions. Trust policy:
`bedrock-agentcore.amazonaws.com` with `aws:SourceAccount` / `aws:SourceArn` conditions.

Orchestrator role:

```json
[
  {"Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem"], "Resource": "<table ARN>"},
  {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "<bucket ARN>/snapshots/*"},
  {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "<bucket ARN>",
   "Condition": {"StringLike": {"s3:prefix": ["snapshots/*"]}}},
  {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "<bucket ARN>/results/*"},
  {"Effect": "Allow", "Action": "sns:Publish", "Resource": "<topic ARN>"},
  {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], "Resource": "<secret ARN>"},
  {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime", "Resource": ["<runtime ARN>", "<runtime ARN>/*"]},
  {"Effect": "Allow", "Action": "bedrock-agentcore:StopRuntimeSession", "Resource": ["<runtime ARN>", "<runtime ARN>/*"]}
]
```

plus the AWS managed `AWSLambdaBasicExecutionRole`. No IAM action uses a wildcard.

---

## 7. Store the Azure DevOps PAT

```bash
SECRET_ARN=$(aws cloudformation describe-stacks --stack-name ErrorFixAgentStack \
  --query "Stacks[0].Outputs[?OutputKey=='AzureDevOpsPatSecretArn'].OutputValue" --output text)

read -rs AZDO_PAT     # paste the PAT, press Enter (keeps it out of shell history)
aws secretsmanager put-secret-value --secret-id "$SECRET_ARN" --secret-string "$AZDO_PAT"
unset AZDO_PAT
```

The Lambda caches the PAT per container; after rotating it, wait ~15 minutes or redeploy.

---

## 8. Make your application log the commit SHA (strongly recommended)

The agent should read the code that actually ran. Emit structured JSON error logs that include
the full 40-character commit SHA your build deployed, for example:

```json
{"level":"ERROR","message":"Unhandled exception in POST /orders/A101/checkout",
 "exception":"ZeroDivisionError: division by zero",
 "stack_trace":"Traceback (most recent call last):\n  File \"/var/task/orders/pricing.py\", line 14, …",
 "commit_sha":"<40-char SHA>"}
```

Set it at build time (for example an environment variable from your Azure Pipelines
`$(Build.SourceVersion)`). Without it, the orchestrator analyzes `fallbackBranch`, which may
not match what is deployed. Also log the whole stack trace as **one** log event (multi-line
plain-text logs often arrive as one event per line, which loses the trace).

---

## 9. Test end to end

1. Send a synthetic error into your log group. Use a real commit SHA from your repo, or
   `--no-sha` to use the fallback branch:

   ```bash
   cd ..   # back to the project root
   python scripts/send_test_error.py --log-group "/aws/lambda/orders-api" \
     --file samples/sample-error.json --no-sha
   ```

   Equivalent with only the AWS CLI:

   ```bash
   aws logs create-log-stream --log-group-name /aws/lambda/orders-api --log-stream-name manual-test-1
   python3 -c 'import json,time;print(json.dumps([{"timestamp":int(time.time()*1000),"message":open("samples/sample-error.json").read().strip()}]))' > events.json
   aws logs put-log-events --log-group-name /aws/lambda/orders-api \
     --log-stream-name manual-test-1 --log-events file://events.json
   ```

   The sample error points at `orders/pricing.py`, which is not in your repo, so expect
   `insufficient_evidence: true` unless you push `samples/buggy-app` to a test repo. That is
   still a good wiring test. For a meaningful test, paste a real stack trace from your app
   into a copy of the sample file.

2. Watch the orchestrator (wait ~1–5 minutes):

   ```bash
   FN=$(aws cloudformation describe-stacks --stack-name ErrorFixAgentStack \
     --query "Stacks[0].Outputs[?OutputKey=='OrchestratorFunctionName'].OutputValue" --output text)
   aws logs tail /aws/lambda/$FN --follow
   ```

   AgentCore runtime logs are in log groups under `/aws/bedrock-agentcore/runtimes/`.

3. Check the email, or read the stored results:

   ```bash
   BUCKET=$(aws cloudformation describe-stacks --stack-name ErrorFixAgentStack \
     --query "Stacks[0].Outputs[?OutputKey=='ArtifactsBucketName'].OutputValue" --output text)
   aws s3 ls s3://$BUCKET/results/
   aws s3 cp s3://$BUCKET/results/<incident-id>.json -
   ```

4. Sending the same error again within 6 hours is deduplicated (by design). To re-test, change
   the exception text, or delete the item from the DynamoDB table.

### Test the runtime alone (skip CloudWatch and Azure DevOps)

```bash
ARN=$(aws cloudformation describe-stacks --stack-name ErrorFixAgentStack \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)
python scripts/make_payload.py --repo samples/buggy-app --error samples/sample-error.json > payload.json
python scripts/invoke_runtime.py --arn "$ARN" --payload payload.json
```

This normally returns an answer pointing at `orders/pricing.py` line 14.

---

## 10. Costs (what drives them)

* **Bedrock model tokens dominate.** Each incident is a multi-turn agent run (reading files).
  `agentMaxTurns` and `agentMaxBudgetUsd` cap a single run; dedupe and
  `maxConcurrentAnalyses` cap the rate. Look at `meta.estimated_cost_usd` in results (an
  estimate, not a bill) and at Bedrock usage in Cost Explorer.
* **AgentCore Runtime** bills for the resources a session uses while it runs (see the
  AgentCore pricing page). The orchestrator stops each session as soon as it finishes, and the
  idle timeout is 5 minutes.
* Lambda, DynamoDB on-demand, S3, SNS and Secrets Manager (one secret) are small at this scale.
* Set an AWS Budget with an alert before turning this on for a noisy log group.

---

## 11. Teardown

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name ErrorFixAgentStack \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactsBucketName'].OutputValue" --output text)
aws s3 rm s3://$BUCKET --recursive        # the bucket must be empty before deletion
cd infra && cdk destroy
```

`cdk destroy` removes the subscription filter, Lambda, runtime, table, topic and secret
(Secrets Manager keeps a deleted secret recoverable for a waiting period). Container images
stay in the CDK bootstrap ECR repository (`cdk-*-container-assets-*`); delete them there if
you want. The CDK bootstrap stack (`CDKToolkit`) can stay; it is shared by all CDK apps.
Revoke the Azure DevOps PAT in Azure DevOps.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `cdk deploy`: "Missing required CDK context 'modelId'" | pass all required `-c` values (step 6.3) |
| Docker error building `linux/arm64` | step 1, ARM64 note (install binfmt / use Docker Desktop) |
| Deploy fails: "ReservedConcurrentExecutions … decreases account's UnreservedConcurrentExecution below its minimum" | new accounts have a low Lambda concurrency quota: redeploy with `-c maxConcurrentAnalyses=0`, or request a quota increase |
| Deploy fails on the subscription filter: log group not found | create/choose the right `logGroupName` in the same region |
| Deploy fails: "Resource limit exceeded" on the subscription filter | the log group already has 2 subscription filters |
| No Lambda invocations after a test error | the line must match `filterPattern`; check the filter in the log group's "Subscription filters" tab |
| Lambda log: "Azure DevOps did not return a zip" | PAT wrong/expired/missing Code (Read), wrong org/project/repo, or SHA not in the repo |
| Result has `meta.error` with "AccessDenied" / "not authorized" from Bedrock | model access not enabled (step 3) or `modelId` differs from the ID you enabled; redeploy with the right ID |
| `meta.error` mentions on-demand throughput | use an inference profile ID, not a foundation model ID |
| Lambda timeout / `ReadTimeoutError` | big repo or hard bug: lower `agentMaxTurns`, or analyze a sub-folder; Lambda's limit is 15 min |
| Always `insufficient_evidence: true` | stack trace doesn't reach your code, or the snapshot is the wrong version (log `commit_sha`) |
| No email | confirm the SNS subscription; check spam |
| Same error never re-analyzed | dedupe cooldown (6 h): delete the fingerprint item in DynamoDB |

---

## 13. Hardening for production

The MVP keeps the security core (read-only tools, sandbox without credentials, trusted
orchestrator, redaction, dedupe, limits). Before wider use:

1. **Network egress deny-all.** The MVP runtime uses AgentCore's **public** network mode,
   i.e. the sandbox can reach the internet. A prompt-injected agent still has no credentials
   or write tools, but it could try to leak code it read through tool output into the model
   answer. Deploy with `-c sandboxVpc=true` to place the sandbox in private **isolated**
   subnets with no NAT / internet gateway, reaching only Bedrock Runtime and CloudWatch Logs
   through interface endpoints and S3 through a gateway endpoint (for the pre-signed URL).
   Then **test it**: invoke the runtime with a payload whose repo contains a file telling
   the agent to fetch a URL; confirm nothing leaves (VPC Flow Logs). If you later need
   internet access from the sandbox (e.g. package mirrors for a "run tests" phase), add a NAT
   path through **AWS Network Firewall** with a strict-order domain **allowlist** and default
   drop, and test it (allowlists are easy to get wrong).
2. **Replace the PAT** with a Microsoft Entra ID service principal or managed identity scoped
   to read this one repository; rotate automatically; keep it only in the orchestrator.
3. **Queue + DLQ.** Put SQS (with a dead-letter queue) between an intake Lambda and the
   orchestrator so bursts are smoothed and failures are visible and replayable.
4. **Tighten limits.** Lower `agentMaxTurns` / `agentMaxBudgetUsd` from observed data; cap
   snapshot size (`MAX_SNAPSHOT_BYTES` env) or snapshot only the service's sub-folder; keep
   reserved concurrency; add an AWS Budget alarm.
5. **Observability.** Alarm on Lambda errors and on DynamoDB items with status `FAILED`;
   enable AgentCore tracing / logging delivery; track acceptance (did engineers agree with
   the suggestion?) to build an evaluation set before moving to automated PRs.
6. **Data handling.** Extend the redaction rules for your log formats; review that code and
   logs may be sent to Bedrock under your data-handling policy; encrypt the bucket with a
   customer-managed KMS key if required; consider Bedrock Guardrails.
7. **Pin and scan.** Pin the container base image by digest, scan it in ECR, and pin the
   model ID per environment.
8. **Multiple services/repos.** Map log groups to repos (one subscription per log group and
   a small lookup table) instead of the single-repo environment variables.

---

## 14. Open items to verify (could not be confirmed from official docs while writing this)

Items marked `VERIFY` in the code:

* `orchestrator/azure_devops.py`: the code sends `api-version=7.1-preview.1` with
  `Accept: application/zip`, mirroring the official Microsoft Python SDK (`get_item_zip`).
  Microsoft Learn could not be reached while writing this; if Azure DevOps rejects the
  preview version, try `api-version=7.1`.
* `infra/model_arns.py`: the IAM resource shape for cross-region inference profiles (profile
  ARN + foundation model in every destination region) and for application inference profiles.
  If you get AccessDenied with a correct model ID, compare with the Bedrock "Inference profile
  prerequisites" page.
* `sandboxVpc=true`: which VPC endpoints an AgentCore VPC-mode runtime needs in your region
  (the stack adds Bedrock Runtime, CloudWatch Logs and S3).

Not marked in code but worth checking in the AgentCore docs for your region:

* AgentCore Runtime regional availability and quotas (concurrent sessions, maximum
  synchronous request duration vs. the 14-minute read timeout the orchestrator uses, image size).
* That the runtime's execution-role credentials reach the Claude Code subprocess through
  the default AWS credential chain (local and container tests use explicit credentials).
