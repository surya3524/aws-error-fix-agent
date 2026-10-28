"""Quick-test agent for AgentCore Runtime.

Reads an error message from the invocation payload, explores the codebase
with read-only tools, and returns a short root cause + fix suggestion as JSON.
Uses the Bedrock Converse API with tool use.

Codebase location:
- CODE_BUCKET set   -> s3://CODE_BUCKET/CODE_PREFIX/ (read-only, e.g. a COBOL mirror)
- CODE_BUCKET unset -> the bundled sample_app/ folder
"""
import json
import os
import posixpath
import re
from pathlib import Path

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.exceptions import ClientError

CODE_ROOT = (Path(__file__).parent / "sample_app").resolve()
CODE_BUCKET = os.environ.get("CODE_BUCKET", "")
CODE_PREFIX = os.environ.get("CODE_PREFIX", "").strip("/")
MAX_TURNS = 10
MAX_ERROR_CHARS = 8000  # caps token spend per request
MAX_FILE_BYTES = 200_000  # larger files are truncated before going to the model
MAX_LISTED_FILES = 2000
MAX_SEARCH_HITS = 50

SYSTEM_PROMPT = (
    "You are debugging a production error. Use the list_files, search_code and "
    "read_file tools to inspect the code before answering. The code may be "
    "Python or COBOL (programs, copybooks, JCL). For COBOL abends, map the "
    "abend code to its usual cause (for example S0C7 = invalid numeric data in "
    "a numeric field, S0C4 = storage/addressing error) and find the field, "
    "copybook and statement involved. When done, reply with ONLY a JSON "
    "object: {\"root_cause\": str, \"fix_suggestion\": str (2-3 sentences), "
    "\"files\": [{\"path\": str, \"line\": int}], \"confidence\": "
    "\"low\"|\"medium\"|\"high\"}. If the code does not explain the error, "
    "say so in root_cause and use confidence \"low\"."
)

TOOLS = {
    "tools": [
        {
            "toolSpec": {
                "name": "list_files",
                "description": "List all files in the codebase (relative paths).",
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        },
        {
            "toolSpec": {
                "name": "search_code",
                "description": (
                    "Case-insensitive text search across all files. Returns "
                    "matching lines as path:line: text. Use it to find a field, "
                    "paragraph, program or copybook name."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "read_file",
                "description": "Read a file from the codebase, with line numbers.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    }
                },
            }
        },
    ]
}

app = BedrockAgentCoreApp()
# botocore reads AWS_DEFAULT_REGION, not AWS_REGION; without a region the
# client raises at import time and the runtime never becomes healthy.
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def clean_path(path):
    """Relative POSIX path inside the codebase, or None if it tries to escape."""
    path = posixpath.normpath(str(path).replace("\\", "/")).lstrip("/")
    if path in ("", ".") or path == ".." or path.startswith("../"):
        return None
    return path


def list_paths():
    if not CODE_BUCKET:
        return [str(p.relative_to(CODE_ROOT)) for p in sorted(CODE_ROOT.rglob("*")) if p.is_file()]
    prefix = f"{CODE_PREFIX}/" if CODE_PREFIX else ""
    paths = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=CODE_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/"):
                paths.append(obj["Key"][len(prefix):])
            if len(paths) >= MAX_LISTED_FILES:
                return paths
    return paths


def read_text(path):
    """File contents (truncated to MAX_FILE_BYTES), or None if it doesn't exist."""
    path = clean_path(path)
    if path is None:
        return None
    if not CODE_BUCKET:
        full = (CODE_ROOT / path).resolve()
        # Read-only access, confined to the codebase directory
        if CODE_ROOT not in full.parents or not full.is_file():
            return None
        data = full.read_bytes()[:MAX_FILE_BYTES]
    else:
        key = f"{CODE_PREFIX}/{path}" if CODE_PREFIX else path
        try:
            obj = s3.get_object(Bucket=CODE_BUCKET, Key=key, Range=f"bytes=0-{MAX_FILE_BYTES - 1}")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "InvalidRange"):
                return None
            raise
        data = obj["Body"].read()
    return data.decode("utf-8", errors="replace")


def run_tool(name, tool_input):
    if name == "list_files":
        return "\n".join(list_paths()) or "(no files)"
    if name == "read_file":
        text = read_text(tool_input.get("path", ""))
        if text is None:
            return "error: file not found"
        return "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1))
    if name == "search_code":
        needle = str(tool_input.get("text", "")).lower()
        if not needle:
            return "error: text is required"
        hits = []
        for path in list_paths():
            for i, line in enumerate((read_text(path) or "").splitlines(), 1):
                if needle in line.lower():
                    hits.append(f"{path}:{i}: {line.strip()}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits + ["(more matches not shown)"])
        return "\n".join(hits) or "no matches"
    return f"error: unknown tool {name}"


@app.entrypoint
def invoke(payload):
    model_id = payload.get("model_id") or os.environ.get("MODEL_ID")
    if not model_id:
        return {"error": "Set MODEL_ID env var or pass model_id in the payload"}
    error_text = payload.get("error")
    if not error_text:
        return {"error": "Payload must include an 'error' field"}
    if len(error_text) > MAX_ERROR_CHARS:
        return {"error": f"'error' is longer than {MAX_ERROR_CHARS} characters"}

    messages = [{"role": "user", "content": [{"text": f"Error log:\n{error_text}"}]}]
    for _ in range(MAX_TURNS):
        try:
            response = bedrock.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=TOOLS,
            )
        except ClientError as e:
            # Return the Bedrock error instead of a bare 500 from the runtime
            return {"error": str(e)}
        message = response["output"]["message"]

        # Converse rejects history whose tool names don't match this pattern,
        # so a malformed name from the model would fail the next call.
        for block in message["content"]:
            if "toolUse" in block:
                name = block["toolUse"]["name"]
                print(f"tool call: {name!r}")
                if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                    block["toolUse"]["name"] = re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "invalid_tool"
        messages.append(message)

        if response["stopReason"] != "tool_use":
            text = "".join(block.get("text", "") for block in message["content"])
            try:
                return json.loads(text[text.find("{"): text.rfind("}") + 1])
            except ValueError:
                return {"raw_answer": text}

        results = []
        for block in message["content"]:
            if "toolUse" in block:
                tool = block["toolUse"]
                try:
                    output = run_tool(tool["name"], tool["input"])
                except Exception as e:  # e.g. S3 AccessDenied; let the model see it
                    print(f"tool {tool['name']} failed: {e!r}")
                    output = f"error: tool failed ({type(e).__name__})"
                results.append({
                    "toolResult": {
                        "toolUseId": tool["toolUseId"],
                        "content": [{"text": output}],
                    }
                })
        messages.append({"role": "user", "content": results})

    return {"error": f"No answer after {MAX_TURNS} turns"}


if __name__ == "__main__":
    app.run()
