# Quick test: error-fix agent on AgentCore (console walkthrough)

A minimal, throwaway setup to prove the main pieces work before building the
full pipeline:

| Piece | What this test proves |
|---|---|
| Claude on Amazon Bedrock | The model can read code + an error and explain the fix |
| AgentCore Runtime (sandbox) | Agent code runs in an isolated microVM session |
| Read-only code tools | The agent explores code itself (`list_files`, `read_file`) instead of being spoon-fed |
| Structured answer | Root cause + 2–3 sentence fix + file/line + confidence, as JSON |

Not included yet (comes in the full build): CloudWatch trigger, Azure DevOps
fetch, dedupe, notifications, network lockdown.

## What's in this folder

```
quick-test/
├── main.py            # the agent (AgentCore entrypoint + Bedrock Converse tool loop)
├── sample_app/        # tiny "random" app with a planted bug
│   ├── app.py         # checkout_handler(request)
│   ├── orders.py      # order_total(items, coupon)
│   └── pricing.py     # apply_discount(amount, coupon)  <- bug: coupon=None crashes
├── test_payload.json  # matching "random" error: KeyError: None
└── build.sh           # builds agent.zip (Linux arm64, Python 3.13)
```

The bug: checking out **without a coupon** passes `coupon=None`, and
`DISCOUNTS[None]` raises `KeyError: None`.

---

## Step 1 — Pick a region (2 min)

In the AWS console's top-right region menu, pick a region where **both**
"Amazon Bedrock" and "Amazon Bedrock AgentCore" open without an
"unsupported region" message (search for them in the top search bar). Use this
same region for every step below.

## Step 2 — Test the model alone in the Bedrock playground (5 min)

This checks model access before involving the sandbox.

1. Console search → **Amazon Bedrock** → **Playground** (chat).
2. Choose **Anthropic** as provider and a **Claude** model. If asked to submit
   Anthropic use-case details the first time, complete that form.
3. Paste the contents of the three `sample_app/*.py` files and the `error`
   text from `test_payload.json`, then ask: *"What is the root cause and fix,
   in 2–3 sentences?"* → **Run**.
4. **Copy the model ID** you'll use in Step 6. In the Bedrock console, find the
   model's ID in the model catalog, or the **cross-region inference profile**
   ID (often prefixed with a geography like `us.`). If a plain model ID later
   fails with an "on-demand throughput isn't supported" error, use the
   inference profile ID instead.

## Step 3 — Get the agent package (`agent.zip`) from GitHub, UI only (5 min)

AgentCore needs your code **plus its Python libraries built for Linux arm64**
in one zip. A GitHub Actions workflow in this repo
(`.github/workflows/build-quick-test.yml`) builds it for you.

1. On GitHub, open this repo → **Actions** tab → **Build quick-test agent.zip**.
2. Open the latest green run (or click **Run workflow** to start a new one and
   wait ~1 min).
3. At the bottom of the run page, under **Artifacts**, download **agent-zip**.
4. GitHub wraps artifacts in a zip, so the download is `agent-zip.zip`. Open it
   on your computer and take out **`agent.zip`**. That inner file is what you
   upload; don't unzip `agent.zip` itself.

**Upload it to S3 (console):**

1. Console search → **S3** → **Create bucket** → name e.g.
   `error-fix-quicktest-<your-account-id>` in the region from Step 1 → keep
   the defaults (Block all public access stays **on**) → **Create bucket**.
2. Open the bucket → **Upload** → **Add files** → `agent.zip` → **Upload**.
3. Click the uploaded `agent.zip` → copy its **S3 URI**.

(If the runtime form in Step 4 offers a direct `.zip` upload, you can skip S3.)

<details>
<summary>Alternative: build in CloudShell instead of GitHub</summary>

Download the repo ZIP (**Code → Download ZIP**), open **CloudShell** (`>_` in
the console top bar) → **Actions → Upload file**, then:

```bash
unzip -q aws-error-fix-agent-main.zip
cd aws-error-fix-agent-main/quick-test
./build.sh
BUCKET=error-fix-quicktest-$(aws sts get-caller-identity --query Account --output text)-$AWS_REGION
aws s3 mb s3://$BUCKET && aws s3 cp agent.zip s3://$BUCKET/agent.zip
```
</details>

## Step 4 — Create the AgentCore runtime in the console (5 min)

1. Console search → **Amazon Bedrock AgentCore** → **Runtime** →
   **Create runtime**. (Older console layouts: **Agents → Host agent**.)
2. **Name:** `errorFixQuickTest`
3. **Agent/tool source:** **S3** → browse to `s3://<your bucket>/agent.zip`
   (or upload `agent.zip` directly if the form offers it).
4. **Language runtime:** Python 3.13 · **Entry point:** `main.py`
5. **Permissions:** **Create default role**. Note the role name it creates.
6. If the form has **environment variables**, add `MODEL_ID` = the ID from
   Step 2. (If not, you'll pass it in the test payload instead.)
7. **Create runtime** and wait until its status is ready.

## Step 5 — Let the runtime call Claude (3 min)

The default role may not include Bedrock model invocation. Add it:

1. Console → **IAM → Roles** → open the role from Step 4.5.
2. **Add permissions → Create inline policy → JSON**, paste the policy below,
   replace `<ACCOUNT_ID>`, and save it as `invoke-claude-quicktest`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*",
        "arn:aws:bedrock:*:<ACCOUNT_ID>:inference-profile/*"
      ]
    }
  ]
}
```

This is deliberately loose for a quick test (any region, any Anthropic model).
Tighten it to the single model/profile ARN for anything long-lived.

## Step 6 — Run it from the console test panel (2 min)

1. Open the runtime → **Test** (opens the runtime playground).
2. In **Input**, paste (replace `<MODEL_ID>`; omit `model_id` if you set the
   env var in Step 4.6):

```json
{
  "model_id": "<MODEL_ID>",
  "error": "Traceback (most recent call last):\n  File \"app.py\", line 7, in checkout_handler\n    return {\"total\": order_total(items, coupon)}\n  File \"orders.py\", line 6, in order_total\n    return apply_discount(subtotal, coupon)\n  File \"pricing.py\", line 5, in apply_discount\n    rate = DISCOUNTS[coupon]\nKeyError: None\nRequest: POST /checkout {\"items\":[{\"price\":19.99,\"qty\":2}]}"
}
```

3. **Run.** Expected shape of the answer (wording will vary):

```json
{
  "root_cause": "apply_discount looks up DISCOUNTS[coupon] but coupon is None when the request has no coupon.",
  "fix_suggestion": "In pricing.apply_discount, return the amount unchanged when coupon is falsy, and use DISCOUNTS.get(coupon, 0) so unknown codes don't crash. Optionally validate coupon codes in checkout_handler and return a 400 for invalid ones.",
  "files": [{"path": "pricing.py", "line": 5}],
  "confidence": "high"
}
```

## Step 7 — Try your own "random" errors

Change only the `error` text. Good checks:

- **Unknown coupon:** `KeyError: 'BOGUS'` from `pricing.py` line 5. It should
  find the same function, with a slightly different fix.
- **Error not in this code:** `ZeroDivisionError` in `tax.py` line 12. It
  should say the code doesn't explain it, with `"confidence": "low"`. This is
  the "I don't know" behaviour you want before trusting it.
- **Your own code:** replace the files in `sample_app/` with a few files from
  your real app, rebuild (Step 3), and update the runtime's S3 source (create a
  new version).

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| `AccessDeniedException` calling Bedrock | Step 5 policy missing or the wrong role |
| "on-demand throughput isn't supported" | Use the cross-region inference profile ID as `model_id` |
| Model access / use-case error | Finish Step 2 in the playground first |
| `{"error": "Set MODEL_ID ..."}` | Add `model_id` to the payload |
| Runtime fails to start | Check the entry point is `main.py`, Python 3.13, and the zip was built with `build.sh` (arm64 wheels) |
| Need logs | CloudWatch → **Log groups** → search `bedrock-agentcore` |

## Clean up (console)

- **AgentCore** → Runtime → select `errorFixQuickTest` → **Delete**.
- **S3** → your bucket → select `agent.zip` → **Delete**; then select the
  bucket → **Delete**.
- **IAM** → Roles → the role from Step 4 → **Delete**.
- **CloudWatch** → Log groups → delete the `bedrock-agentcore` log group(s) for
  this runtime (optional; tiny cost).

## Estimated cost of this test

These are rough estimates for **about 50 test runs**. Prices come from
secondary sources or Anthropic's API list; check the official AWS pricing pages
before relying on them.

| Item | Estimate | Basis |
|---|---|---|
| Claude on Bedrock | **~$0.01–0.10 per run → ~$0.50–5 for 50 runs**, depending on model tier | ~8K input + ~3K output tokens per run (several tool turns). Per million tokens (in/out): Haiku-class ~$0.10/$0.50 to ~$1/$5, Sonnet-class ~$2/$10 to ~$3/$15, Opus-class ~$4/$20 to ~$5/$25. Bedrock in-region endpoints reportedly cost ~10% more |
| AgentCore Runtime | **< $0.01 per run → < $0.50 for 50 runs** | Billed for active CPU (~$0.09–0.13 per vCPU-hour) and memory (~$0.009–0.017 per GB-hour); time spent waiting on the model isn't billed as CPU |
| S3 (one 20 MB file) | < $0.01 | |
| CloudWatch Logs | < $0.10 | A few MB of logs |
| GitHub Actions build | $0 | ~1 min per build; private repos on GitHub Free include 2,000 min/month |
| CloudShell (optional) | $0 | No charge for CloudShell itself (not re-verified) |
| **Total** | **≈ $1–6** | Biggest lever: which Claude model you choose |

**Guardrail:** create a budget alert first: **Billing and Cost Management →
Budgets → Create budget → Zero spend or Monthly cost budget**, e.g. $10, with
an email alert.

**For later (not this test):** in the full pipeline, cost scales with
*distinct* errors analysed, not raw error volume, because dedupe stops
repeats. 100 distinct errors/month at ~$0.05–0.10 each is roughly
$5–10/month in model cost plus small Lambda, DynamoDB and S3 charges.

## Verification notes

- **Checked against AWS docs and blogs:**
  - Direct code (.zip) deployment, with arm64 wheels built by uv.
  - The console flow: Runtime → Create runtime, S3 source, language runtime +
    entry point, Create default role.
  - The Test → runtime playground with a JSON input.
  - Each session runs in its own microVM.
- **Tested locally:**
  - `build.sh` produced a working `agent.zip`.
  - The real `bedrock-agentcore` SDK imports `main.py`, and `/ping` returns
    healthy.
  - `/invocations` validates the payload.
  - The tool loop ran against a mocked Bedrock.
  - `read_file` refuses paths outside `sample_app/`.
- **Not verified (no AWS account access):**
  - A real Bedrock call.
  - Whether the console's default role already includes Bedrock invoke.
  - Whether the runtime form offers environment variables.
  - Exact console labels, which have changed over time.
  - The CloudWatch log group name.
