#!/usr/bin/env python3
"""Write a synthetic error line into a CloudWatch log group to trigger the pipeline end to end.

  python scripts/send_test_error.py --log-group /aws/lambda/orders-api --file samples/sample-error.json
  python scripts/send_test_error.py --log-group ... --file ... --commit-sha <40-char SHA in your repo>
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

import boto3

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-group", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--commit-sha", help="override commit_sha in the sample (use a real commit)")
    ap.add_argument("--no-sha", action="store_true", help="drop commit_sha so the fallback branch is used")
    ap.add_argument("--region")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        line = fh.read().strip()
    if args.commit_sha or args.no_sha:
        obj = json.loads(line)
        obj.pop("commit_sha", None)
        if args.commit_sha:
            obj["commit_sha"] = args.commit_sha
        line = json.dumps(obj)

    logs = boto3.client("logs", region_name=args.region)
    stream = f"error-fix-test-{uuid.uuid4().hex[:8]}"
    logs.create_log_stream(logGroupName=args.log_group, logStreamName=stream)
    logs.put_log_events(
        logGroupName=args.log_group,
        logStreamName=stream,
        logEvents=[{"timestamp": int(time.time() * 1000), "message": line}],
    )
    print(f"Wrote 1 event to {args.log_group} / {stream}")
