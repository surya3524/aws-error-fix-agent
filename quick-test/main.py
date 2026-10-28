"""Quick-test agent for AgentCore Runtime.

Reads an error message from the invocation payload, explores the bundled
sample_app/ with read-only tools, and returns a short root cause + fix
suggestion as JSON. Uses the Bedrock Converse API with tool use.
"""
import json
import os
import re
from pathlib import Path

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from botocore.exceptions import ClientError

CODE_ROOT = (Path(__file__).parent / "sample_app").resolve()
MAX_TURNS = 10
MAX_ERROR_CHARS = 8000  # caps token spend per request

SYSTEM_PROMPT = (
    "You are debugging a production error. Use the list_files and read_file "
    "tools to inspect the code before answering. When done, reply with ONLY a "
    "JSON object: {\"root_cause\": str, \"fix_suggestion\": str (2-3 "
    "sentences), \"files\": [{\"path\": str, \"line\": int}], \"confidence\": "
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


def run_tool(name, tool_input):
    if name == "list_files":
        return "\n".join(
            str(p.relative_to(CODE_ROOT)) for p in sorted(CODE_ROOT.rglob("*")) if p.is_file()
        )
    if name == "read_file":
        path = (CODE_ROOT / tool_input.get("path", "")).resolve()
        # Read-only access, confined to the codebase directory
        if CODE_ROOT not in path.parents or not path.is_file():
            return "error: file not found"
        lines = path.read_text().splitlines()
        return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))
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
                results.append({
                    "toolResult": {
                        "toolUseId": tool["toolUseId"],
                        "content": [{"text": run_tool(tool["name"], tool["input"])}],
                    }
                })
        messages.append({"role": "user", "content": results})

    return {"error": f"No answer after {MAX_TURNS} turns"}


if __name__ == "__main__":
    app.run()
