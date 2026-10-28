"""Pure functions: decode CloudWatch Logs subscription payloads, fingerprint, redact."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from typing import Any


def decode_subscription_event(event: dict[str, Any]) -> dict[str, Any]:
    """CloudWatch Logs delivers {"awslogs": {"data": base64(gzip(json))}} to Lambda."""
    raw = base64.b64decode(event["awslogs"]["data"])
    return json.loads(gzip.decompress(raw))


def parse_message(message: str) -> dict[str, Any]:
    """Accept structured JSON log lines or plain text. Returns a dict with at least 'message'."""
    text = message.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                parts = [str(obj.get(k)) for k in ("message", "msg", "error", "exception", "stack_trace", "stack", "traceback") if obj.get(k)]
                obj["_text"] = "\n".join(parts) or text
                return obj
        except json.JSONDecodeError:
            pass
    return {"_text": text}


# Exception type: Python "FooError: msg", Java/C# "com.x.FooException: msg", JS "TypeError: msg".
_EXC_RE = re.compile(r"\b([A-Za-z_][\w.$]*(?:Error|Exception|Fault|Panic))\b")
# Top frame candidates for Python, Java/C#, Node.
_FRAME_RES = [
    re.compile(r'File "([^"]+)", line \d+, in (\S+)'),            # Python
    re.compile(r"at ([\w.$<>]+)\(([\w.]+):\d+\)"),                 # Java
    re.compile(r"at ([\w.<>]+) in ([^\s:]+):line \d+"),            # .NET
    re.compile(r"at (?:([\w.<>]+) )?\(?([^\s()]+?):\d+:\d+\)?"),   # Node
]


def fingerprint(service: str, text: str) -> str:
    """Stable ID for 'the same bug': service + exception type + innermost frame (no line numbers)."""
    exc = _EXC_RE.findall(text)
    exc_type = exc[-1] if exc else "UnknownError"
    frame = ""
    for rx in _FRAME_RES:
        frames = rx.findall(text)
        if frames:
            # Python prints innermost last; the others print innermost first.
            f = frames[-1] if rx is _FRAME_RES[0] else frames[0]
            frame = "|".join(x for x in f if x)
            break
    if not frame:
        # No stack: normalise the first line (digits/hex/uuids vary per occurrence).
        first = text.splitlines()[0] if text else ""
        frame = re.sub(r"[0-9a-fA-F-]{8,}|\d+", "#", first)[:200]
    key = f"{service}|{exc_type}|{frame}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


_REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{10,}"), r"\1 [REDACTED_TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|authorization)\b(\s*[=:]\s*|\"\s*:\s*\")([^\s\",;&]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/\s@]+):([^@\s/]+)@"), r"\1\2:[REDACTED]@"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_NUMBER]"),
]


def redact(text: str) -> str:
    """Best-effort removal of obvious secrets/PII before text leaves the trusted side."""
    for rx, repl in _REDACTIONS:
        text = rx.sub(repl, text)
    return text


# Full 40-hex SHA only: short SHAs are ambiguous and may not resolve as a commit version.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def commit_sha(obj: dict[str, Any], field: str) -> str | None:
    value = obj.get(field)
    if isinstance(value, str) and _SHA_RE.match(value):
        return value.lower()
    return None
