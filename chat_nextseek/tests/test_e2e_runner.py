"""Runner tests — use a stub orchestrator to avoid real LLM calls."""
from pathlib import Path
from unittest.mock import patch

import pytest

from e2e.catalog import Catalog, Family, Variant, Turn, PassCriterion
from e2e.runner import run_variant


def _stub_run_query(session, config, query: str) -> dict:
    """Stub that mimics orchestrator.run_query — returns a fixed debug dict + reply."""
    return {
        "reply": f"echo: {query}",
        "debug": {
            "parser_plan": {"mode": "new_search"},
            "entity_result": {"sampletypes": [{"code": "MUS"}], "assays": [], "projects": []},
            "api_result_meta": {"ok": True},
            "api_plan": {"endpoint": "/samples/advanced_search/", "method": "POST",
                         "requestBody": {"filter_searchText": "NDMA"}},
        },
    }


def test_run_variant_single_turn_pass(tmp_path: Path, monkeypatch):
    variant = Variant(
        family="search_advanced", id="adv.basic", name="basic",
        turns=[Turn(label="main", query="Find me mice treated with NDMA",
                    pass_criteria=[
                        PassCriterion(field="parser_plan.mode", op="eq", value="new_search"),
                        PassCriterion(field="api_ok", op="true"),
                    ])]
    )

    class _FakeConfig:
        SESSION_DB_TYPE = "sqlite"
        SESSION_DB_PATH = str(tmp_path / "session.sqlite")

    with patch("e2e.runner.run_query", side_effect=_stub_run_query), \
         patch("e2e.runner._make_session", return_value={}):
        result = run_variant(variant, _FakeConfig(), tmp_path, pace_seconds=0)

    assert result["status"] == "passed"
    assert result["id"] == "adv.basic"
    assert (tmp_path / "adv.basic" / "turns" / "main" / "query.txt").read_text() == "Find me mice treated with NDMA"
    assert "echo:" in (tmp_path / "adv.basic" / "turns" / "main" / "reply.txt").read_text()


def test_run_variant_fail_records_failed_criteria(tmp_path: Path):
    variant = Variant(
        family="search_advanced", id="adv.fail", name="fail",
        turns=[Turn(label="main", query="x", pass_criteria=[
            PassCriterion(field="parser_plan.mode", op="eq", value="graph_query"),  # wrong
        ])]
    )

    class _FakeConfig:
        SESSION_DB_TYPE = "sqlite"
        SESSION_DB_PATH = str(tmp_path / "session.sqlite")

    with patch("e2e.runner.run_query", side_effect=_stub_run_query), \
         patch("e2e.runner._make_session", return_value={}):
        result = run_variant(variant, _FakeConfig(), tmp_path, pace_seconds=0)

    assert result["status"] == "failed"
    # failed_criteria contains entries like "main: parser_plan.mode"
    assert any("parser_plan.mode" in fc for fc in result["failed_criteria"])


def test_run_variant_multi_turn_shares_session(tmp_path: Path):
    captured_sessions = []

    def _stub_capturing(session, config, query):
        captured_sessions.append(id(session))
        return _stub_run_query(session, config, query)

    variant = Variant(
        family="refine_and_recall", id="rr.chain", name="chain",
        turns=[
            Turn(label="t1", query="Find mice", pass_criteria=[]),
            Turn(label="t2", query="How many?", pass_criteria=[]),
        ]
    )

    class _FakeConfig:
        SESSION_DB_TYPE = "sqlite"
        SESSION_DB_PATH = str(tmp_path / "session.sqlite")

    with patch("e2e.runner.run_query", side_effect=_stub_capturing), \
         patch("e2e.runner._make_session", return_value={}):
        run_variant(variant, _FakeConfig(), tmp_path, pace_seconds=0)

    assert len(captured_sessions) == 2
    assert captured_sessions[0] == captured_sessions[1], "multi-turn must share session"


def test_run_variant_writes_debug_json(tmp_path: Path):
    """debug.json artifact is the full result['debug'] dict."""
    import json
    variant = Variant(
        family="search_advanced", id="adv.debug", name="debug",
        turns=[Turn(label="main", query="x", pass_criteria=[])]
    )

    class _FakeConfig:
        SESSION_DB_TYPE = "sqlite"
        SESSION_DB_PATH = str(tmp_path / "session.sqlite")

    with patch("e2e.runner.run_query", side_effect=_stub_run_query), \
         patch("e2e.runner._make_session", return_value={}):
        run_variant(variant, _FakeConfig(), tmp_path, pace_seconds=0)

    debug_path = tmp_path / "adv.debug" / "turns" / "main" / "debug.json"
    assert debug_path.exists()
    debug = json.loads(debug_path.read_text())
    assert debug["parser_plan"]["mode"] == "new_search"


def test_run_variant_handles_orchestrator_exception(tmp_path: Path):
    """If orchestrator.run_query raises, variant is marked failed with EXCEPTION note."""
    def _raise(session, config, query):
        raise RuntimeError("orchestrator boom")

    variant = Variant(
        family="search_advanced", id="adv.boom", name="boom",
        turns=[Turn(label="main", query="x", pass_criteria=[])]
    )

    class _FakeConfig:
        SESSION_DB_TYPE = "sqlite"
        SESSION_DB_PATH = str(tmp_path / "session.sqlite")

    with patch("e2e.runner.run_query", side_effect=_raise), \
         patch("e2e.runner._make_session", return_value={}):
        result = run_variant(variant, _FakeConfig(), tmp_path, pace_seconds=0)

    assert result["status"] == "failed"
    assert any("EXCEPTION" in fc for fc in result["failed_criteria"])
    # error.txt artifact written
    assert (tmp_path / "adv.boom" / "turns" / "main" / "error.txt").exists()


def test_run_main_empty_catalog_clean_exit(tmp_path: Path, monkeypatch):
    """If catalog has no eligible variants, run_main returns 0 cleanly."""
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text('{"version":"1.0","families":{"f":{"description":"","variants":[]}}}')

    # ChatConfig isn't called when there's nothing to run, but patch to be safe
    # in case the import line itself fails (e.g., missing .env)
    from e2e.runner import run_main
    rc = run_main(catalog_path, ratio=1.0, seed=1, out_root=tmp_path)
    assert rc == 0
