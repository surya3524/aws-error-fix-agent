"""Orchestrator tests with fake AWS clients and a fake Azure DevOps download."""

import base64
import gzip
import io
import json
import os
import zipfile

import pytest
from botocore.exceptions import ClientError

import azure_devops
import handler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def sample_line():
    with open(os.path.join(ROOT, "samples", "sample-error.json")) as fh:
        return fh.read().strip()


def event(messages, message_type="DATA_MESSAGE"):
    payload = {"messageType": message_type, "logGroup": "/aws/lambda/orders-api", "logStream": "s",
               "logEvents": [{"id": str(i), "timestamp": 0, "message": m} for i, m in enumerate(messages)]}
    return {"awslogs": {"data": base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()}}


class FakeDynamo:
    def __init__(self):
        self.items, self.updates = {}, []

    def put_item(self, TableName, Item, ConditionExpression, ExpressionAttributeValues):
        fp = Item["fingerprint"]["S"]
        now = int(ExpressionAttributeValues[":now"]["N"])
        if fp in self.items and int(self.items[fp]["expires_at"]["N"]) >= now:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[fp] = Item

    def update_item(self, **kw):
        self.updates.append(kw)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)][:MaxKeys]}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://bucket.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


class FakeAgentCore:
    def __init__(self, response):
        self.response, self.calls, self.stopped = response, [], []

    def invoke_agent_runtime(self, **kw):
        self.calls.append(kw)
        return {"response": io.BytesIO(json.dumps(self.response).encode())}

    def stop_runtime_session(self, **kw):
        self.stopped.append(kw)


class FakeSNS:
    def __init__(self):
        self.messages = []

    def publish(self, **kw):
        self.messages.append(kw)


class FakeSecrets:
    def get_secret_value(self, SecretId):
        return {"SecretString": "fake-pat\n"}


ANALYSIS = {"root_cause": "Division by zero.", "fix_suggestion": "Guard zero units. Skip the average.",
            "files": [{"path": "orders/pricing.py", "line": 14, "exists_in_repo": True}],
            "confidence": "high", "insufficient_evidence": False}


@pytest.fixture
def fakes(monkeypatch):
    f = {
        "dynamodb": FakeDynamo(), "s3": FakeS3(), "sns": FakeSNS(), "secretsmanager": FakeSecrets(),
        "bedrock-agentcore": FakeAgentCore({"status": "ok", "analysis": ANALYSIS, "meta": {}}),
    }
    monkeypatch.setattr(handler, "_clients", f)
    monkeypatch.setattr(handler, "_pat_cache", {})
    for k, v in {"TABLE_NAME": "t", "BUCKET": "b", "TOPIC_ARN": "arn:topic", "SECRET_ID": "sec",
                 "RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x", "AZDO_ORG": "org",
                 "AZDO_PROJECT": "proj", "AZDO_REPO": "orders"}.items():
        monkeypatch.setattr(handler, k, v)
    downloads = []

    def fake_download(org, project, repo, version, vtype, pat, max_bytes):
        downloads.append((version, vtype, pat))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("orders/pricing.py", "x")
        return buf.getvalue()

    monkeypatch.setattr(handler.azure_devops, "download_zip", fake_download)
    f["downloads"] = downloads
    return f


def test_end_to_end_happy_path(fakes):
    out = handler.handler(event([sample_line()]), None)
    assert len(out["incidents"]) == 1
    call = fakes["bedrock-agentcore"].calls[0]
    assert len(call["runtimeSessionId"]) >= 33
    payload = json.loads(call["payload"])
    assert payload["repo"]["archive_url"].startswith("https://")
    assert payload["repo"]["commit_sha"] == "3f2c9a1e7b6d5c4b3a29180716f5e4d3c2b1a090"
    assert "jane.doe@example.com" not in payload["error"]["message"]
    assert fakes["downloads"] == [("3f2c9a1e7b6d5c4b3a29180716f5e4d3c2b1a090", "commit", "fake-pat")]
    assert fakes["bedrock-agentcore"].stopped  # microVM released
    msg = fakes["sns"].messages[0]
    assert len(msg["Subject"]) <= 100 and "orders/pricing.py:14" in msg["Message"]
    assert any(k.startswith("results/") for k in fakes["s3"].objects)
    assert fakes["dynamodb"].updates[-1]["ExpressionAttributeValues"][":s"]["S"] == "DONE"


def test_duplicates_are_skipped(fakes):
    handler.handler(event([sample_line()]), None)
    out = handler.handler(event([sample_line()]), None)
    assert out["incidents"] == []
    assert len(fakes["bedrock-agentcore"].calls) == 1


def test_in_batch_dedupe_and_cap(fakes):
    other = 'Traceback (most recent call last):\n  File "/var/task/x.py", line 1, in f\nKeyError: \'k\''
    out = handler.handler(event([sample_line(), sample_line(), other]), None)
    assert len(out["incidents"]) == 1  # MAX_INCIDENTS_PER_BATCH defaults to 1


def test_snapshot_cached_per_commit(fakes):
    handler.handler(event([sample_line()]), None)
    fakes["dynamodb"].items.clear()  # pretend cooldown expired
    handler.handler(event([sample_line()]), None)
    assert len(fakes["downloads"]) == 1


def test_no_sha_falls_back_to_branch(fakes):
    line = json.loads(sample_line())
    line.pop("commit_sha")
    handler.handler(event([json.dumps(line)]), None)
    assert fakes["downloads"][0][:2] == ("main", "branch")


def test_control_message_skipped(fakes):
    assert handler.handler(event([], "CONTROL_MESSAGE"), None) == {"skipped": "CONTROL_MESSAGE"}


def test_failure_marks_incident_failed(fakes, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("azure down")
    monkeypatch.setattr(handler.azure_devops, "download_zip", broken)
    with pytest.raises(RuntimeError):
        handler.handler(event([sample_line()]), None)
    assert fakes["dynamodb"].updates[-1]["ExpressionAttributeValues"][":s"]["S"] == "FAILED"


def test_azure_url_shape():
    url = azure_devops.build_items_zip_url("my org", "shop", "orders", "a" * 40, "commit")
    assert url.startswith("https://dev.azure.com/my%20org/shop/_apis/git/repositories/orders/items?")
    assert "versionDescriptor.versionType=commit" in url and "recursionLevel=Full" in url
    with pytest.raises(ValueError):
        azure_devops.build_items_zip_url("o", "p", "r", "x", "weird")


def test_azure_rejects_non_zip(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def fake_urlopen(req, timeout):
        seen["auth"] = req.headers["Authorization"]
        return Resp(b"<html>sign in</html>")

    monkeypatch.setattr(azure_devops.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError):
        azure_devops.download_zip("o", "p", "r", "main", "branch", "pat", 1000)
    assert base64.b64decode(seen["auth"].split()[1]) == b":pat"
