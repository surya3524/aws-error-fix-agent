import os

import pytest

import schema

GOOD = {
    "root_cause": "Division by zero when all items have quantity 0.",
    "fix_suggestion": "Guard against zero units. Return None when there are no units.",
    "files": [{"path": "./orders/pricing.py", "line": 14}],
    "confidence": "high",
    "insufficient_evidence": False,
}


def make_repo(tmp_path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "pricing.py").write_text("x")
    return str(tmp_path)


def test_normalize_good(tmp_path):
    repo = make_repo(tmp_path)
    out = schema.normalize(GOOD, repo)
    assert out["files"] == [{"path": "orders/pricing.py", "line": 14, "exists_in_repo": True}]
    assert out["confidence"] == "high"


def test_absolute_path_made_relative(tmp_path):
    repo = make_repo(tmp_path)
    raw = {**GOOD, "files": [{"path": os.path.join(repo, "orders", "pricing.py"), "line": 3}]}
    assert schema.normalize(raw, repo)["files"][0]["path"] == os.path.join("orders", "pricing.py")


def test_hallucinated_files_downgrade_confidence(tmp_path):
    repo = make_repo(tmp_path)
    raw = {**GOOD, "files": [{"path": "nope.py", "line": 1}]}
    out = schema.normalize(raw, repo)
    assert out["confidence"] == "medium"
    assert out["files"][0]["exists_in_repo"] is False


def test_bad_values_coerced():
    raw = {**GOOD, "confidence": "certain", "files": [{"path": "a.py", "line": -3}, "junk", {"line": 2}]}
    out = schema.normalize(raw)
    assert out["confidence"] == "low"
    assert out["files"] == [{"path": "a.py", "line": None}]


def test_missing_output_is_insufficient():
    assert schema.normalize(None)["insufficient_evidence"] is True
    assert schema.normalize({"confidence": "high"})["insufficient_evidence"] is True


def test_schema_is_draft7_compatible():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft7Validator.check_schema(schema.ANALYSIS_SCHEMA)
    jsonschema.Draft7Validator(schema.ANALYSIS_SCHEMA).validate(GOOD)
    jsonschema.Draft7Validator(schema.ANALYSIS_SCHEMA).validate(schema.insufficient("x"))
