"""The planner's reporter step hands run_reporter_summary the lab names the user wrote, not the code-matched ones (F3)."""
import types

from chat_nextseek.agents.planner import tools


def _run(monkeypatch, matches):
    seen = {}
    plan = types.SimpleNamespace(
        uids=[], reporter_mode=None, report_type=None, project=None, summary_mode="samples",
        model_dump=lambda: {}, model_copy=lambda update: plan)
    monkeypatch.setattr(tools, "reporter_agent", lambda *a, **k: plan)
    monkeypatch.setattr(tools, "_resolve_step_inputs", lambda step, ctx: ({}, []))

    def fake_summary(config, rplan, log_dir, **kw):
        seen.update(kw)
        return {"ok": True, "rows_returned": 1}, {}, {}

    monkeypatch.setattr(tools, "run_reporter_summary", fake_summary)
    step = types.SimpleNamespace(execution=types.SimpleNamespace(
        tool_query="", filters={}, parser_candidate_id=None, metadata={}, report_mode=None, report_type=None))
    tools._plan_tool_reporter(object(), None, step, "report for lab X", {"lab_matches": matches}, None, {})
    return seen


def test_a_name_match_passes_its_name(monkeypatch):
    assert _run(monkeypatch, [{"name": "Northfield", "rule": "name"}])["lab_names"] == ["Northfield"]


def test_a_code_match_passes_no_name(monkeypatch):
    assert _run(monkeypatch, [{"name": "Bend", "rule": "code"}])["lab_names"] == []
