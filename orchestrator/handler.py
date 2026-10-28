"""Trusted orchestrator Lambda.

CloudWatch Logs subscription -> decode -> fingerprint + dedupe -> redact -> fetch code
from Azure DevOps (only this function holds the PAT) -> upload snapshot to S3 ->
invoke the AgentCore runtime with a short-lived pre-signed URL -> store + notify.
No pull requests, no code changes.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import azure_devops
import logparse

log = logging.getLogger()
log.setLevel(logging.INFO)

ENV = os.environ
TABLE_NAME = ENV.get("TABLE_NAME", "")
BUCKET = ENV.get("BUCKET_NAME", "")
TOPIC_ARN = ENV.get("TOPIC_ARN", "")
RUNTIME_ARN = ENV.get("AGENT_RUNTIME_ARN", "")
SECRET_ID = ENV.get("AZDO_PAT_SECRET_ID", "")
AZDO_ORG = ENV.get("AZDO_ORG", "")
AZDO_PROJECT = ENV.get("AZDO_PROJECT", "")
AZDO_REPO = ENV.get("AZDO_REPO", "")
AZDO_FALLBACK_BRANCH = ENV.get("AZDO_FALLBACK_BRANCH", "main")
COMMIT_SHA_FIELD = ENV.get("COMMIT_SHA_FIELD", "commit_sha")
COOLDOWN_S = int(ENV.get("DEDUPE_COOLDOWN_SECONDS", "21600"))
MAX_INCIDENTS_PER_BATCH = int(ENV.get("MAX_INCIDENTS_PER_BATCH", "1"))
MAX_SNAPSHOT_BYTES = int(ENV.get("MAX_SNAPSHOT_BYTES", str(150 * 1024 * 1024)))
PRESIGN_TTL_S = int(ENV.get("PRESIGN_TTL_SECONDS", "900"))
AGENT_READ_TIMEOUT_S = int(ENV.get("AGENT_READ_TIMEOUT_SECONDS", "840"))

_clients: dict[str, Any] = {}
_pat_cache: dict[str, str] = {}


def client(name: str):
    if name not in _clients:
        cfg = None
        if name == "bedrock-agentcore":
            # Agent runs take minutes; boto3's default 60 s read timeout is far too short.
            cfg = Config(read_timeout=AGENT_READ_TIMEOUT_S, connect_timeout=10,
                         retries={"max_attempts": 1, "mode": "standard"})
        elif name == "s3":
            cfg = Config(signature_version="s3v4")
        _clients[name] = boto3.client(name, config=cfg) if cfg else boto3.client(name)
    return _clients[name]


def get_pat() -> str:
    if "pat" not in _pat_cache:
        _pat_cache["pat"] = client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
    return _pat_cache["pat"]


# ---------------------------------------------------------------- dedupe

def claim_fingerprint(fp: str, incident_id: str, sample: str) -> bool:
    """Atomically claim a fingerprint for COOLDOWN_S. False if already claimed (duplicate)."""
    now = int(time.time())
    try:
        client("dynamodb").put_item(
            TableName=TABLE_NAME,
            Item={
                "fingerprint": {"S": fp},
                "incident_id": {"S": incident_id},
                "status": {"S": "ANALYZING"},
                "first_seen": {"N": str(now)},
                "expires_at": {"N": str(now + COOLDOWN_S)},  # DynamoDB TTL attribute
                "sample": {"S": sample[:1000]},
            },
            # TTL deletion is lazy, so also treat an expired-but-present item as free.
            ConditionExpression="attribute_not_exists(fingerprint) OR expires_at < :now",
            ExpressionAttributeValues={":now": {"N": str(now)}},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def record_result(fp: str, status: str, analysis: dict[str, Any] | None, result_key: str | None) -> None:
    values = {":s": {"S": status}, ":k": {"S": result_key or ""}, ":c": {"S": (analysis or {}).get("confidence", "")}}
    client("dynamodb").update_item(
        TableName=TABLE_NAME,
        Key={"fingerprint": {"S": fp}},
        UpdateExpression="SET #st = :s, result_key = :k, confidence = :c",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues=values,
    )


# ---------------------------------------------------------------- code snapshot

def snapshot_url(sha: str | None) -> tuple[str, str]:
    """Return (presigned_url, version_label). Snapshots are cached per commit SHA."""
    version, vtype = (sha, "commit") if sha else (AZDO_FALLBACK_BRANCH, "branch")
    key = f"snapshots/{AZDO_REPO}/{vtype}-{version}.zip".replace("..", "_")
    s3 = client("s3")
    cached = False
    if vtype == "commit":  # a commit never changes, so reuse an earlier download
        listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
        cached = any(o.get("Key") == key for o in listing.get("Contents", []))
    if not cached:
        data = azure_devops.download_zip(AZDO_ORG, AZDO_PROJECT, AZDO_REPO, version, vtype,
                                         get_pat(), MAX_SNAPSHOT_BYTES)
        s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType="application/zip")
    url = s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key},
                                    ExpiresIn=PRESIGN_TTL_S)
    return url, f"{vtype}:{version}"


# ---------------------------------------------------------------- agent call

def invoke_agent(incident_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ac = client("bedrock-agentcore")
    session_id = incident_id  # one microVM per incident; >= 33 chars required
    try:
        resp = ac.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode(),
        )
        body = resp["response"]
        raw = body.read() if hasattr(body, "read") else body
        return json.loads(raw)
    finally:
        try:  # free the microVM now instead of waiting for the idle timeout
            ac.stop_runtime_session(agentRuntimeArn=RUNTIME_ARN, runtimeSessionId=session_id)
        except Exception as exc:  # noqa: BLE001 - best effort
            log.info("stop_runtime_session: %s", exc)


# ---------------------------------------------------------------- delivery

def format_email(incident: dict[str, Any], analysis: dict[str, Any]) -> tuple[str, str]:
    files = "\n".join(
        f"  - {f['path']}" + (f":{f['line']}" if f.get("line") else "")
        + ("" if f.get("exists_in_repo", True) else "  (not found in snapshot)")
        for f in analysis.get("files", [])
    ) or "  (none)"
    flag = " [insufficient evidence]" if analysis.get("insufficient_evidence") else ""
    subject = f"[error-fix] {incident['service']}: {analysis['confidence']} confidence{flag}"[:100]
    body = (
        f"Incident:   {incident['incident_id']}\n"
        f"Service:    {incident['service']}\n"
        f"Code:       {incident['version']}\n"
        f"Confidence: {analysis['confidence']}{flag}\n\n"
        f"ROOT CAUSE\n{analysis['root_cause']}\n\n"
        f"SUGGESTED FIX\n{analysis['fix_suggestion']}\n\n"
        f"FILES\n{files}\n\n"
        f"ERROR (redacted, truncated)\n{incident['error_text'][:1500]}\n\n"
        "AI-generated suggestion. Verify before acting. No code was changed.\n"
    )
    return subject, body


def deliver(incident: dict[str, Any], agent_out: dict[str, Any]) -> str:
    key = f"results/{incident['incident_id']}.json"
    record = {**incident, "agent": agent_out, "created_at": int(time.time())}
    client("s3").put_object(Bucket=BUCKET, Key=key, Body=json.dumps(record, indent=2).encode(),
                            ContentType="application/json")
    analysis = agent_out.get("analysis")
    if TOPIC_ARN and analysis:
        subject, body = format_email(incident, analysis)
        client("sns").publish(TopicArn=TOPIC_ARN, Subject=subject, Message=body)
    return key


# ---------------------------------------------------------------- entrypoint

def process_log_event(service: str, log_group: str, message: str) -> dict[str, Any] | None:
    parsed = logparse.parse_message(message)
    text = parsed["_text"]
    fp = logparse.fingerprint(service, text)
    incident_id = f"inc-{uuid.uuid4().hex}"
    redacted = logparse.redact(text)
    if not claim_fingerprint(fp, incident_id, redacted):
        log.info("duplicate fingerprint %s, skipped", fp)
        return None

    try:
        sha = logparse.commit_sha(parsed, COMMIT_SHA_FIELD)
        url, version = snapshot_url(sha)
        incident = {"incident_id": incident_id, "fingerprint": fp, "service": service,
                    "log_group": log_group, "version": version, "error_text": redacted}
        payload = {
            "incident_id": incident_id,
            "error": {"message": redacted, "service": service, "log_group": log_group},
            "repo": {"archive_url": url, "commit_sha": sha, "name": AZDO_REPO},
        }
        agent_out = invoke_agent(incident_id, payload)
        key = deliver(incident, agent_out)
        analysis = agent_out.get("analysis")
        record_result(fp, "DONE" if analysis else "AGENT_ERROR", analysis, key)
        return {"incident_id": incident_id, "result_key": key}
    except Exception:
        log.exception("incident %s failed", incident_id)
        record_result(fp, "FAILED", None, None)
        raise


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    data = logparse.decode_subscription_event(event)
    if data.get("messageType") != "DATA_MESSAGE":
        return {"skipped": data.get("messageType")}
    log_group = data.get("logGroup", "")
    service = ENV.get("SERVICE_NAME") or log_group.rsplit("/", 1)[-1]

    done, seen = [], set()
    for ev in data.get("logEvents", []):
        if len(done) >= MAX_INCIDENTS_PER_BATCH:
            break
        msg = ev.get("message", "")
        # Cheap in-batch dedupe before touching DynamoDB.
        fp = logparse.fingerprint(service, logparse.parse_message(msg)["_text"])
        if fp in seen:
            continue
        seen.add(fp)
        out = process_log_event(service, log_group, msg)
        if out:
            done.append(out)
    return {"incidents": done}
