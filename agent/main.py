"""AgentCore Runtime entrypoint (HTTP protocol: POST /invocations, GET /ping on port 8080).

Payload (sent by the orchestrator):
{
  "incident_id": "inc-...",
  "error":  {"message": "<redacted log text + stack trace>", "log_group": "...", ...},
  "repo":   {"archive_url": "<pre-signed S3 GET URL>"  OR  "archive_b64": "<base64 zip/tar.gz>",
             "commit_sha": "abc123", "name": "my-repo"}
}
Response: {"incident_id", "status": "ok"|"error", "analysis": {...}, "meta": {...}}
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile

from bedrock_agentcore import BedrockAgentCoreApp

import repo_fetch
from analyzer import analyze
from schema import insufficient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("error-fix-agent")

app = BedrockAgentCoreApp()

WORK_ROOT = os.environ.get("WORK_ROOT", "/tmp/work")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@app.entrypoint
async def invoke(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"status": "error", "error": "payload must be a JSON object"}
    incident_id = str(payload.get("incident_id", "local"))
    if not _ID_RE.match(incident_id):
        return {"status": "error", "error": "invalid incident_id"}
    error = payload.get("error") or {}
    repo = payload.get("repo") or {}
    if not isinstance(error, dict) or not error.get("message"):
        return {"incident_id": incident_id, "status": "error", "error": "error.message is required"}

    os.makedirs(WORK_ROOT, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix=f"{incident_id}-", dir=WORK_ROOT)
    try:
        try:
            data = repo_fetch.load_archive(repo)
            repo_root = repo_fetch.extract(data, os.path.join(workdir, "repo"))
        except (repo_fetch.ArchiveError, OSError, ValueError) as exc:
            log.warning("could not load repository snapshot: %s", exc)
            return {
                "incident_id": incident_id,
                "status": "ok",
                "analysis": insufficient("repository snapshot could not be loaded"),
                "meta": {"snapshot_error": str(exc)[:300]},
            }
        out = await analyze(error, repo_root, repo.get("commit_sha"))
        return {"incident_id": incident_id, "status": "ok", **out}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    app.run()
