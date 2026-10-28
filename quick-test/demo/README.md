# Public demo page (Lambda function URL)

```
Browser ──► Lambda function URL (public HTTPS) ──► InvokeAgentRuntime (IAM) ──► AgentCore agent
```

- `GET /` serves `index.html`, a page with a text box and an **Analyze** button.
- `POST /analyze` calls the agent and returns its JSON.
- Only the Lambda has AWS credentials. It always sends its own `MODEL_ID`, so visitors can't pick
  the model, and it rejects error text over 8,000 characters.
- Optional `DEMO_PASSCODE`: if you set it, the page asks for it before calling the agent.

Why a function URL and not API Gateway: an agent run can take longer than 30 seconds, and API
Gateway HTTP APIs time out at 30 seconds. A function URL waits as long as the Lambda timeout.

## Cost guardrails

| Guardrail | Where |
|---|---|
| Model fixed by the Lambda | `MODEL_ID` env var |
| Input capped at 8,000 characters | Lambda and `main.py` |
| At most 10 model calls per request | `MAX_TURNS` in `main.py` |
| At most 2 requests at once | Lambda reserved concurrency (step 4) |
| Optional passcode | `DEMO_PASSCODE` env var |
| Email alert | The $10 AWS Budget |

## Setup (console, about 15 minutes, region us-east-2)

### 1. Create the function

1. Go to **Lambda → Create function → Author from scratch**.
2. Name it `errorFixDemo`. For **Runtime**, choose **Python 3.13**. For **Architecture**, choose
   **arm64**.
3. Choose **Create function**.
4. On the **Code** tab, choose **Upload from → .zip file** and upload `demo-lambda.zip`. It contains
   `lambda_function.py` and `index.html`.

### 2. Configuration

1. Go to **Configuration → General configuration → Edit**. Set **Timeout** to **2 min 0 sec** and
   **Memory** to **256 MB**.
2. Go to **Configuration → Environment variables → Edit** and add these variables:
   - `AGENT_RUNTIME_ARN`:
     `arn:aws:bedrock-agentcore:us-east-2:043309323494:runtime/runtime_aws_error-4Vh607EJvc`
   - `MODEL_ID`: the `us.anthropic.…` ID that worked in your console test.
   - `DEMO_PASSCODE` (optional): any phrase you'll tell your audience.

### 3. Permission to call the agent

1. Go to **Configuration → Permissions** and click the role name. IAM opens.
2. Choose **Add permissions → Create inline policy → JSON**, paste the policy below, and name it
   `InvokeErrorFixAgent`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": [
      "arn:aws:bedrock-agentcore:us-east-2:043309323494:runtime/runtime_aws_error-4Vh607EJvc",
      "arn:aws:bedrock-agentcore:us-east-2:043309323494:runtime/runtime_aws_error-4Vh607EJvc/*"
    ]
  }]
}
```

This allows calling this one agent only, and nothing else in your account.

### 4. Limit concurrency

Go to **Configuration → Concurrency → Edit**, choose **Reserve concurrency**, and enter `2`. A third
simultaneous request gets a "too many requests" error instead of starting another agent run.

If the console refuses because the account's unreserved concurrency is too low, which happens on
new accounts, skip this step and rely on the passcode and the budget.

### 5. Make it public

1. Go to **Configuration → Function URL → Create function URL**.
2. For **Auth type**, choose **NONE**. The console adds the resource policy that allows public
   calls.
3. Choose **Save**, and copy the **Function URL**.

### 6. Demo

1. Open the function URL in a browser.
2. Click **Sample: real bug**, then **Analyze**. The expected result is `pricing.py:5` with high
   confidence.
3. Click **Sample: unknown file**, then **Analyze**. The expected result is low confidence.

## Troubleshooting

All logs are in **CloudWatch → Log groups → /aws/lambda/errorFixDemo**.

| Symptom | Cause | Fix |
|---|---|---|
| `UnknownServiceError: bedrock-agentcore` in the logs | The boto3 version bundled with Lambda is too old | Add a layer with a current boto3, or bundle boto3 into the zip |
| `agent call failed: AccessDeniedException` | Step 3 policy is missing or has the wrong ARN | Re-check step 3 |
| `agent call failed: ReadTimeoutError` | The agent took more than 110 seconds | Retry, or raise `read_timeout` and the Lambda timeout |
| Page loads but Analyze returns 502 | Check the Lambda log line `invoke failed: ...` | |
| `wrong passcode` | `DEMO_PASSCODE` is set | Enter it on the page |

## Turn it off after the demo

Use either of these:
- **Configuration → Function URL → Delete**. The page goes offline immediately.
- **Configuration → Concurrency → Reserve concurrency `0`**. This pauses the function and keeps
  the setup.
