"""Agent-side tests. The Claude Agent SDK `query` is mocked: no model calls."""

import asyncio
import base64
import os

import pytest
from claude_agent_sdk import ResultMessage

import analyzer
import main as agent_main
from make_payload import build, tar_repo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAMPLE_REPO = os.path.join(ROOT, "samples", "buggy-app")
SAMPLE_ERROR = os.path.join(ROOT, "samples", "sample-error.json")

ANSWER = {
    "root_cause": "average_unit_price divides by total units, which is 0 when every line has quantity 0.",
    "fix_suggestion": "Return early when total_units is 0. Skip avg_unit_price for empty baskets.",
    "files": [{"path": "orders/pricing.py", "line": 14}],
    "confidence": "high",
    "insufficient_evidence": False,
}


def result(subtype="success", structured=None):
    return ResultMessage(subtype=subtype, duration_ms=10, duration_api_ms=5, is_error=subtype != "success",
                         num_turns=3, session_id="s", total_cost_usd=0.01, structured_output=structured)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def model_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model-placeholder")
    monkeypatch.setattr(agent_main, "WORK_ROOT", str(tmp_path / "work"))


# ---------------------------------------------------------------- path guard

def guard_decision(repo, tool, tool_input):
    guard = analyzer.make_path_guard(str(repo))
    out = run(guard({"tool_name": tool, "tool_input": tool_input}, "id", {"signal": None}))
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "pass")


def test_guard_allows_reads_inside_repo(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert guard_decision(tmp_path, "Read", {"file_path": str(tmp_path / "a.py")}) == "pass"
    assert guard_decision(tmp_path, "Read", {"file_path": "a.py"}) == "pass"
    assert guard_decision(tmp_path, "Grep", {"pattern": "def ", "path": str(tmp_path)}) == "pass"
    assert guard_decision(tmp_path, "Glob", {"pattern": "**/*.py"}) == "pass"
    assert guard_decision(tmp_path, "StructuredOutput", {"root_cause": "x"}) == "pass"


@pytest.mark.parametrize("tool,tool_input", [
    ("Read", {"file_path": "/etc/passwd"}),
    ("Read", {"file_path": "../outside.txt"}),
    ("Read", {"file_path": "/proc/self/environ"}),
    ("Grep", {"pattern": "AKIA", "path": "/"}),
    ("Grep", {"pattern": "x", "glob": "../**"}),
    ("Glob", {"pattern": "/etc/*"}),
    ("Glob", {"pattern": "../../**"}),
    ("Bash", {"command": "env"}),
    ("WebFetch", {"url": "https://example.com"}),
    ("Write", {"file_path": "a.py", "content": "x"}),
])
def test_guard_denies_everything_else(tmp_path, tool, tool_input):
    assert guard_decision(tmp_path, tool, tool_input) == "deny"


def test_guard_denies_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    os.symlink("/etc", repo / "etc-link")
    assert guard_decision(repo, "Read", {"file_path": str(repo / "etc-link" / "passwd")}) == "deny"


# ---------------------------------------------------------------- options

def test_options_are_read_only(tmp_path):
    opts = analyzer.build_options(str(tmp_path))
    assert opts.tools == ["Read", "Grep", "Glob"]
    assert opts.permission_mode == "dontAsk"
    assert "Bash" in opts.disallowed_tools and "WebFetch" in opts.disallowed_tools
    assert opts.setting_sources == []
    assert opts.output_format["type"] == "json_schema"
    assert opts.env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert opts.model == "test-model-placeholder"


def test_options_require_model(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_MODEL")
    with pytest.raises(RuntimeError):
        analyzer.build_options(str(tmp_path))


# ---------------------------------------------------------------- analyze + entrypoint

def fake_query(messages, seen=None):
    async def _q(prompt, options):
        if seen is not None:
            seen.update(prompt=prompt, options=options)
        for m in messages:
            yield m
    return _q


def test_analyze_success(monkeypatch, tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "pricing.py").write_text("x")
    seen = {}
    monkeypatch.setattr(analyzer, "query", fake_query([result(structured=ANSWER)], seen))
    out = run(analyzer.analyze({"message": "ZeroDivisionError"}, str(tmp_path), "abc"))
    assert out["analysis"]["confidence"] == "high"
    assert out["analysis"]["files"][0]["exists_in_repo"] is True
    assert out["meta"]["num_turns"] == 3
    assert "<error_log>" in seen["prompt"] and "untrusted" in seen["prompt"].lower()


def test_analyze_max_turns_is_insufficient(monkeypatch, tmp_path):
    monkeypatch.setattr(analyzer, "query", fake_query([result(subtype="error_max_turns")]))
    out = run(analyzer.analyze({"message": "x"}, str(tmp_path)))
    assert out["analysis"]["insufficient_evidence"] is True
    assert out["analysis"]["confidence"] == "low"


def test_analyze_sdk_exception_is_insufficient(monkeypatch, tmp_path):
    async def boom(prompt, options):
        raise RuntimeError("CLI crashed")
        yield  # pragma: no cover
    monkeypatch.setattr(analyzer, "query", boom)
    out = run(analyzer.analyze({"message": "x"}, str(tmp_path)))
    assert out["analysis"]["insufficient_evidence"] is True


def test_entrypoint_with_sample_repo(monkeypatch):
    captured = {}

    async def fake_analyze(error, repo_root, sha):
        captured["files"] = sorted(os.listdir(os.path.join(repo_root, "orders")))
        captured["error"] = error["message"]
        return {"analysis": ANSWER, "meta": {}}

    monkeypatch.setattr(agent_main, "analyze", fake_analyze)
    payload = build(SAMPLE_REPO, SAMPLE_ERROR, "test")
    out = run(agent_main.invoke(payload))
    assert out["status"] == "ok" and out["analysis"] == ANSWER
    assert "pricing.py" in captured["files"]
    assert "ZeroDivisionError" in captured["error"]
    assert "jane.doe@example.com" not in captured["error"]  # redacted before it reaches the agent


def test_entrypoint_bad_archive_returns_insufficient():
    payload = {"incident_id": "inc-1", "error": {"message": "x"},
               "repo": {"archive_b64": base64.b64encode(b"garbage").decode()}}
    out = run(agent_main.invoke(payload))
    assert out["status"] == "ok"
    assert out["analysis"]["insufficient_evidence"] is True


def test_entrypoint_validates_input():
    assert run(agent_main.invoke({"incident_id": "../../x", "error": {"message": "x"}}))["status"] == "error"
    assert run(agent_main.invoke({"incident_id": "ok", "error": {}}))["status"] == "error"


def test_tar_repo_skips_vcs_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    (tmp_path / "a.py").write_text("x")
    import io
    import tarfile
    names = tarfile.open(fileobj=io.BytesIO(tar_repo(str(tmp_path)))).getnames()
    assert names == ["a.py"]
