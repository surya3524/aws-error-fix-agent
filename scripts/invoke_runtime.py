#!/usr/bin/env python3
"""Invoke the deployed AgentCore runtime directly with a payload file (skips the orchestrator).

  python scripts/make_payload.py --repo samples/buggy-app --error samples/sample-error.json > payload.json
  python scripts/invoke_runtime.py --arn <AgentRuntimeArn output> --payload payload.json
"""

from __future__ import annotations

import argparse
import json
import uuid

import boto3
from botocore.config import Config

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arn", required=True)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--region")
    args = ap.parse_args()

    with open(args.payload, "rb") as fh:
        body = fh.read()
    client = boto3.client("bedrock-agentcore", region_name=args.region,
                          config=Config(read_timeout=900, retries={"max_attempts": 1}))
    session_id = f"manual-{uuid.uuid4().hex}"  # must be at least 33 characters
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=args.arn,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=body,
        )
        print(json.dumps(json.loads(resp["response"].read()), indent=2))
    finally:
        client.stop_runtime_session(agentRuntimeArn=args.arn, runtimeSessionId=session_id)
