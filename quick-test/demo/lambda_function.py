"""Public demo front door for the quick-test agent (Lambda function URL).

GET  /         -> the demo page (index.html)
POST /analyze  -> {"error": "<traceback>", "passcode": "..."} -> agent JSON

Only this function holds AWS credentials. It always uses its own MODEL_ID,
so visitors can't pick the model, and it caps the input size.
"""
import base64
import hmac
import json
import os
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
MODEL_ID = os.environ["MODEL_ID"]
DEMO_PASSCODE = os.environ.get("DEMO_PASSCODE", "")  # empty = no passcode
MAX_ERROR_CHARS = 8000

PAGE = (Path(__file__).parent / "index.html").read_text()
# Agent runs can exceed boto3's 60s default; no retries, since a retry is a second paid run
agentcore = boto3.client(
    "bedrock-agentcore",
    config=Config(read_timeout=110, retries={"total_max_attempts": 1}),
)


def respond(status, body, content_type="application/json"):
    if content_type == "application/json":
        body = json.dumps(body)
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


def lambda_handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")

    if method == "GET" and path == "/":
        page = PAGE.replace("__NEEDS_PASSCODE__", "true" if DEMO_PASSCODE else "false")
        return respond(200, page, "text/html; charset=utf-8")
    if method != "POST" or path != "/analyze":
        return respond(404, {"error": "not found"})

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    try:
        request = json.loads(body)
    except ValueError:
        return respond(400, {"error": "body must be JSON"})

    if DEMO_PASSCODE and not hmac.compare_digest(str(request.get("passcode", "")), DEMO_PASSCODE):
        return respond(401, {"error": "wrong passcode"})
    error_text = str(request.get("error", "")).strip()
    if not error_text:
        return respond(400, {"error": "paste an error first"})
    if len(error_text) > MAX_ERROR_CHARS:
        return respond(400, {"error": f"error text is longer than {MAX_ERROR_CHARS} characters"})

    try:
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            qualifier="DEFAULT",
            runtimeSessionId=str(uuid.uuid4()),  # one fresh sandbox per request
            payload=json.dumps({"model_id": MODEL_ID, "error": error_text}).encode(),
        )
        result = json.loads(response["response"].read())
    except Exception as e:  # surface the reason on the page and in CloudWatch
        print(f"invoke failed: {e!r}")
        return respond(502, {"error": f"agent call failed: {type(e).__name__}"})
    return respond(200, result)
