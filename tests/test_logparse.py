import base64
import gzip
import json
import os

import logparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def sample_line():
    with open(os.path.join(ROOT, "samples", "sample-error.json")) as fh:
        return fh.read().strip()


def encode(payload):
    return {"awslogs": {"data": base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()}}


def test_decode_subscription_event_roundtrip():
    payload = {"messageType": "DATA_MESSAGE", "logGroup": "/g", "logEvents": [{"id": "1", "message": "x"}]}
    assert logparse.decode_subscription_event(encode(payload)) == payload


def test_parse_json_line_collects_stack():
    parsed = logparse.parse_message(sample_line())
    assert "ZeroDivisionError" in parsed["_text"]
    assert 'File "/var/task/orders/pricing.py", line 14' in parsed["_text"]


def test_parse_plain_text():
    assert logparse.parse_message("  boom  ")["_text"] == "boom"


def test_fingerprint_ignores_line_numbers_and_ids():
    text = logparse.parse_message(sample_line())["_text"]
    shifted = text.replace("line 14", "line 99").replace("A101", "B777")
    assert logparse.fingerprint("svc", text) == logparse.fingerprint("svc", shifted)


def test_fingerprint_differs_by_exception_and_service():
    a = 'Traceback:\n  File "/a.py", line 1, in f\nKeyError: x'
    b = 'Traceback:\n  File "/a.py", line 1, in f\nValueError: x'
    assert logparse.fingerprint("s", a) != logparse.fingerprint("s", b)
    assert logparse.fingerprint("s1", a) != logparse.fingerprint("s2", a)


def test_fingerprint_java_and_node_frames():
    java = "java.lang.NullPointerException: x\n\tat com.acme.Foo.bar(Foo.java:42)\n\tat com.acme.Main.main(Main.java:3)"
    node = "TypeError: x is undefined\n    at handler (/var/task/index.js:10:5)"
    assert len(logparse.fingerprint("s", java)) == 32
    assert logparse.fingerprint("s", node) != logparse.fingerprint("s", java)


def test_redact_common_secrets():
    text = (
        "password=hunter2 api_key: abcd1234 Authorization: Bearer abcdefghijklmnop "
        "AKIAABCDEFGHIJKLMNOP postgres://app:s3cret@db:5432/x jane@example.com 4111 1111 1111 1111"
    )
    out = logparse.redact(text)
    for secret in ("hunter2", "abcd1234", "abcdefghijklmnop", "AKIAABCDEFGHIJKLMNOP", "s3cret", "jane@example.com", "4111 1111"):
        assert secret not in out, secret


def test_redact_keeps_stack_trace_useful():
    text = logparse.parse_message(sample_line())["_text"]
    out = logparse.redact(text)
    assert "pricing.py" in out and "line 14" in out
    assert "jane.doe@example.com" not in out


def test_commit_sha():
    sha = "3f2c9a1e7b6d5c4b3a29180716f5e4d3c2b1a090"
    assert logparse.commit_sha({"commit_sha": sha.upper()}, "commit_sha") == sha
    assert logparse.commit_sha({"commit_sha": "abc123"}, "commit_sha") is None
    assert logparse.commit_sha({}, "commit_sha") is None
