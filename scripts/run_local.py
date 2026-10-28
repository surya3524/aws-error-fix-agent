#!/usr/bin/env python3
"""Run the agent on your machine (no Docker, no AgentCore) against a repo + error log.

Needs: pip install -r agent/requirements.txt, AWS credentials that can invoke the model,
and these env vars:
  export CLAUDE_CODE_USE_BEDROCK=1
  export AWS_REGION=<your region>
  export ANTHROPIC_MODEL=<inference profile ID from `aws bedrock list-inference-profiles`>

  python scripts/run_local.py --repo samples/buggy-app --error samples/sample-error.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
sys.path.insert(0, HERE)

import main as agent_main  # noqa: E402
from make_payload import build  # noqa: E402


async def run(repo: str, error_file: str) -> dict:
    payload = build(repo, error_file, "local-test")
    return await agent_main.invoke(payload)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--error", required=True)
    args = ap.parse_args()
    for var in ("ANTHROPIC_MODEL", "AWS_REGION"):
        if not os.environ.get(var):
            sys.exit(f"Set {var} first (see docs/SETUP.md, step 5).")
    os.environ.setdefault("WORK_ROOT", os.path.join(HERE, "..", ".work"))
    agent_main.WORK_ROOT = os.environ["WORK_ROOT"]
    print(json.dumps(asyncio.run(run(args.repo, args.error)), indent=2))
