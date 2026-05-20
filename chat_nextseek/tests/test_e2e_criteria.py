"""Pass-criterion DSL tests."""
from pathlib import Path

import pytest

from e2e.catalog import PassCriterion
from e2e.criteria import check_pass, resolve_field


# ── Field resolution ─────────────────────────────────────────────────────

def test_resolve_dot_notation():
    debug = {"parser_plan": {"mode": "new_search"}}
    assert resolve_field(debug, "parser_plan.mode") == "new_search"


def test_resolve_dot_nav_sample_type_alias():
    """sample_type and sampletype are aliased per-part in dot-notation."""
    debug = {"api_plan": {"requestBody": {"sampletype": "MUS"}}}
    # Path uses sample_type; resolver should fall through to sampletype.
    assert resolve_field(debug, "api_plan.requestBody.sample_type") == "MUS"

    debug2 = {"api_plan": {"requestBody": {"sample_type": "NHP"}}}
    # Path uses sampletype; resolver should fall through to sample_type.
    assert resolve_field(debug2, "api_plan.requestBody.sampletype") == "NHP"


def test_resolve_entity_sampletype_codes_alias():
    debug = {"entity_result": {"sampletypes": [{"code": "MUS"}, {"code": "NHP"}]}}
    assert resolve_field(debug, "entity_sampletype_codes") == ["MUS", "NHP"]


def test_resolve_api_ok_alias():
    debug = {"api_result_meta": {"ok": True}}
    assert resolve_field(debug, "api_ok") is True


def test_resolve_session_field(tmp_path):
    debug = {}
    session = {"nfcore_wizard": {"step": "pipeline", "active": True, "selection": {"uids": [1, 2, 3]}}}
    assert resolve_field(debug, "wizard.step", session=session) == "pipeline"
    assert resolve_field(debug, "wizard.active", session=session) is True
    assert resolve_field(debug, "wizard.uid_count", session=session) == 3


def test_resolve_last_reply_strips_html():
    assert resolve_field({}, "last_reply", last_reply="<p>hello <b>world</b></p>") == "hello world"


def test_resolve_api_artifact_existence(tmp_path):
    run_root = tmp_path / "run_root"
    (run_root / "files").mkdir(parents=True)
    (run_root / "files" / "samplesheet.csv").write_text("header\nrow1\nrow2\n")
    assert resolve_field({}, "api_artifact.samplesheet.csv", run_root=run_root) is True
    assert resolve_field({}, "api_artifact.missing.csv", run_root=run_root) is False


def test_resolve_api_artifact_rows_gte(tmp_path):
    run_root = tmp_path / "run_root"
    (run_root / "files").mkdir(parents=True)
    (run_root / "files" / "samplesheet.csv").write_text("header\nrow1\nrow2\nrow3\n")
    assert resolve_field({}, "api_artifact.samplesheet.csv.rows_gte", run_root=run_root) == 3


# ── Check ops ────────────────────────────────────────────────────────────

def test_check_eq_pass():
    pc = PassCriterion(field="x", op="eq", value="y")
    ok, _ = check_pass({"x": "y"}, [pc])
    assert ok


def test_check_eq_fail():
    pc = PassCriterion(field="x", op="eq", value="y")
    ok, results = check_pass({"x": "z"}, [pc])
    assert not ok
    assert "expected 'y'" in results[0]["reason"]


def test_check_contains_list():
    pc = PassCriterion(field="x", op="contains", value="MUS")
    ok, _ = check_pass({"x": ["MUS", "NHP"]}, [pc])
    assert ok


def test_check_contains_string():
    pc = PassCriterion(field="x", op="contains", value="advanced")
    ok, _ = check_pass({"x": "/samples/advanced_search/"}, [pc])
    assert ok


def test_check_true_op():
    pc = PassCriterion(field="api_ok", op="true")
    ok, _ = check_pass({"api_result_meta": {"ok": True}}, [pc])
    assert ok


def test_check_gte_op():
    pc = PassCriterion(field="x", op="gte", value=5)
    ok, _ = check_pass({"x": 7}, [pc])
    assert ok


def test_check_mentions_case_insensitive():
    pc = PassCriterion(field="last_reply", op="mentions", value="NDMA")
    ok, _ = check_pass({}, [pc], last_reply="we found 12 mice treated with ndma")
    assert ok


def test_check_matches_re():
    pc = PassCriterion(field="last_reply", op="matches_re", value=r"\b\d+ mice\b")
    ok, _ = check_pass({}, [pc], last_reply="found 12 mice in the database")
    assert ok


def test_check_unknown_op_reports_in_result():
    # Pydantic-level validation catches unknown ops before they reach check_pass,
    # but if a runtime injects a malformed criterion as a dict the resolver must
    # report a clear error.
    pc = {"field": "x", "op": "bogus", "value": "y"}
    ok, results = check_pass({}, [pc])
    assert not ok
    assert "unknown op" in results[0]["reason"]


def test_check_all_pass_required():
    pcs = [
        PassCriterion(field="x", op="eq", value="y"),
        PassCriterion(field="z", op="eq", value="ok"),
    ]
    ok, _ = check_pass({"x": "y", "z": "no"}, pcs)
    assert not ok
