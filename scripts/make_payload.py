#!/usr/bin/env python3
"""Build an AgentCore invocation payload with the repo snapshot inlined (base64 tar.gz).

Used for local container tests and direct runtime tests, where there is no orchestrator
to upload the snapshot to S3.

  python scripts/make_payload.py --repo samples/buggy-app --error samples/sample-error.json > payload.json
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tarfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "orchestrator"))
import logparse  # noqa: E402  (reuse the orchestrator's parsing + redaction)

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "bin", "obj", "dist", "build"}


def tar_repo(path: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                tf.add(full, arcname=os.path.relpath(full, path))
    return buf.getvalue()


def build(repo: str, error_file: str, service: str) -> dict:
    with open(error_file, encoding="utf-8") as fh:
        line = fh.read().strip()
    parsed = logparse.parse_message(line)
    return {
        "incident_id": f"inc-{uuid.uuid4().hex}",
        "error": {"message": logparse.redact(parsed["_text"]), "service": service},
        "repo": {
            "archive_b64": base64.b64encode(tar_repo(repo)).decode(),
            "commit_sha": logparse.commit_sha(parsed, "commit_sha"),
            "name": os.path.basename(os.path.abspath(repo)),
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--error", required=True, help="file with one log line (JSON or text)")
    ap.add_argument("--service", default="local-test")
    args = ap.parse_args()
    json.dump(build(args.repo, args.error, args.service), sys.stdout)
