"""Output contract for the analysis, shared by the agent and its tests."""

from __future__ import annotations

import os
from typing import Any

CONFIDENCE_LEVELS = ("low", "medium", "high")

# JSON Schema draft-07 (the version the Claude Agent SDK validates against).
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "root_cause",
        "fix_suggestion",
        "files",
        "confidence",
        "insufficient_evidence",
    ],
    "properties": {
        "root_cause": {
            "type": "string",
            "description": "One or two sentences: the most likely root cause.",
        },
        "fix_suggestion": {
            "type": "string",
            "description": "Two to three sentences describing the fix. Do not include a patch.",
        },
        "files": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository root."},
                    "line": {"type": ["integer", "null"], "minimum": 1},
                },
            },
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "insufficient_evidence": {"type": "boolean"},
    },
}

_MAX_TEXT = 1500


def insufficient(reason: str) -> dict[str, Any]:
    """A valid 'cannot determine' answer."""
    return {
        "root_cause": f"Could not determine the root cause: {reason}"[:_MAX_TEXT],
        "fix_suggestion": "No fix suggested. Investigate manually.",
        "files": [],
        "confidence": "low",
        "insufficient_evidence": True,
    }


def normalize(raw: Any, repo_root: str | None = None) -> dict[str, Any]:
    """Coerce model output into the contract and mark which cited files really exist.

    The SDK already validates against ANALYSIS_SCHEMA; this is defense in depth so the
    orchestrator never has to trust the shape of what comes out of the sandbox.
    """
    if not isinstance(raw, dict):
        return insufficient("agent returned no structured output")

    confidence = raw.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "low"

    files: list[dict[str, Any]] = []
    for item in (raw.get("files") or [])[:10]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        path = _relative(item["path"].strip(), repo_root)
        line = item.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            line = None
        entry: dict[str, Any] = {"path": path[:500], "line": line}
        if repo_root:
            entry["exists_in_repo"] = _exists_inside(repo_root, path)
        files.append(entry)

    result = {
        "root_cause": str(raw.get("root_cause") or "")[:_MAX_TEXT],
        "fix_suggestion": str(raw.get("fix_suggestion") or "")[:_MAX_TEXT],
        "files": files,
        "confidence": confidence,
        "insufficient_evidence": bool(raw.get("insufficient_evidence", False)),
    }
    if not result["root_cause"] or not result["fix_suggestion"]:
        return insufficient("agent output was missing required text")
    # Cited files that do not exist are a hallucination signal: never report "high" then.
    if files and repo_root and not any(f["exists_in_repo"] for f in files) and confidence == "high":
        result["confidence"] = "medium"
    return result


def _relative(path: str, repo_root: str | None) -> str:
    """Report paths relative to the repo root, whatever form the model used."""
    if repo_root and os.path.isabs(path):
        root = os.path.realpath(repo_root)
        real = os.path.realpath(path)
        if real.startswith(root + os.sep):
            return os.path.relpath(real, root)
    while path.startswith("./"):
        path = path[2:]
    return path


def _exists_inside(repo_root: str, rel_path: str) -> bool:
    root = os.path.realpath(repo_root)
    candidate = os.path.realpath(os.path.join(root, rel_path))
    return (candidate == root or candidate.startswith(root + os.sep)) and os.path.isfile(candidate)
