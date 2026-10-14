"""A crash inside a pipeline is an error the caller sees, never an answer.

Everything is stubbed: no LLM, no network, no DB.
"""
import json

import pytest

from chat_nextseek import orchestrator


class _Config:
    API_USER = "u"
    API_PASS = "p"


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_turn_vocabulary", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(orchestrator, "_ensure_query_log_dir", lambda *a, **k: tmp_path)

    def go(fn):
        events, session = [], {}
        with pytest.raises(Exception) as caught:
            fn(session, _Config(), "how many mice", lambda n, p: events.append((n, p)),
               credentials={"api_user": "u", "api_pass": "p"})
        return caught.value, events, session

    return go


def _last_turn(session):
    return session["chat_log"][-1]


def test_plan_crash_raises_and_reports_an_error_not_an_answer(monkeypatch, run):
    def boom(*a, **k):
        raise KeyError("x")

    monkeypatch.setattr(orchestrator, "multi_parser_agent", boom)
    exc, events, session = run(orchestrator.run_query_plan)
    assert isinstance(exc, KeyError)
    names = [n for n, _ in events]
    assert names.count("query_error") == 1
    assert "query_complete" not in names
    for name, payload in events:
        blob = json.dumps(payload, default=str)
        if name != "query_error":
            assert "unexpected error occurred" not in blob and "KeyError('x')" not in blob
    turn = _last_turn(session)
    assert turn["status"] == "error"
    assert turn["mode"] == "error_plan_pipeline"
    assert not turn["assistant_reply"]
    assert turn["error"] == "KeyError"


def test_standard_crash_also_leaves_an_error_turn(monkeypatch, run):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestrator, "_handle_pipeline_agent_turn", boom)
    exc, events, session = run(orchestrator.run_query)
    assert str(exc) == "boom"
    errors = [p for n, p in events if n == "query_error"]
    assert len(errors) == 1
    assert errors[0]["error"] == "boom" and "agent" in errors[0]
    assert "total_cost_usd" in errors[0]
    assert _last_turn(session)["status"] == "error"
    assert _last_turn(session)["error"] == "RuntimeError"
    assert exc.chat_log_turn_written is True
