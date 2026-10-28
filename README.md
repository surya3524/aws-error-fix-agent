# AI error-fix suggestion agent (AWS MVP)

When your application logs an error in **CloudWatch Logs**, this system reads the error and a
snapshot of your **Azure DevOps** repository at the deployed commit, and emails you:

* the likely **root cause**,
* a **fix suggestion** in 2–3 sentences,
* the **file(s) and line(s)** involved,
* a **confidence** level (`low` / `medium` / `high`), or an explicit "insufficient evidence".

It does **not** open pull requests or change code.

**Just want to try the core pieces first?** Follow [quick-test/README.md](quick-test/README.md): AWS console only, about 30 minutes, a few dollars.

**Full pipeline: [docs/SETUP.md](docs/SETUP.md)**: prerequisites, deployment, end-to-end test,
costs, teardown, troubleshooting and production hardening.

## Layout

| Path | What |
|---|---|
| `agent/` | The sandboxed agent: Claude Agent SDK on Amazon Bedrock, served by the AgentCore Python SDK (`BedrockAgentCoreApp`). Read/Grep/Glob tools only, plus a path guard. ARM64 container. |
| `orchestrator/` | Trusted Lambda: CloudWatch Logs subscription → dedupe → redact → Azure DevOps zip → S3 pre-signed URL → `InvokeAgentRuntime` → S3 + DynamoDB + SNS email. |
| `infra/` | AWS CDK (Python) app: AgentCore Runtime (L2 `aws_bedrockagentcore.Runtime`), Lambda, S3, DynamoDB, SNS, Secrets Manager, subscription filter; optional isolated VPC. |
| `scripts/` | `run_local.py` (run the agent on your machine), `make_payload.py`, `invoke_runtime.py`, `send_test_error.py`. |
| `samples/` | A tiny buggy app and the error log line it produces. |
| `quick-test/` | Minimal console-only test: one-file agent, tiny buggy app, GitHub Actions builds `agent.zip`. |
| `docs/design.md` | Architecture and security design. |
| `tests/` | Unit tests (AWS, Azure DevOps and the model are mocked). |

## Security model (short)

* The agent sandbox (one AgentCore microVM per incident) has **no** Azure DevOps credentials,
  no S3/Secrets Manager permissions and no production data. Its IAM role can invoke the one
  configured model.
* The agent can only read files inside the snapshot (tool allowlist, `dontAsk` permission
  mode, and a `PreToolUse` hook that denies paths outside the repo). Log text and repo
  content are treated as untrusted input.
* The orchestrator is the only component that holds the Azure DevOps PAT (Secrets Manager).
* The MVP sandbox uses public networking; `-c sandboxVpc=true` moves it to an isolated VPC
  with no internet route. See SETUP.md §13.

## Configuration

No model ID is hard-coded anywhere. You pass the Bedrock inference profile ID at deploy time
(`cdk deploy -c modelId=...`); SETUP.md §3 shows how to find it.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
