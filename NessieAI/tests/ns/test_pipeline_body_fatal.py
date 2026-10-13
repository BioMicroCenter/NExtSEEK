"""An LLMFatalError that escapes the orchestrator still ends the turn with a query_error.

``LLMFatalError`` is a ``BaseException`` so agent code cannot swallow it, and the two
pipeline bodies caught only ``Exception``. ``run_query`` and ``run_query_plan`` turn it
into a reply themselves, but ``run_pipeline_launch`` calls the pipeline agent unguarded,
and since fix 5 a pipeline tool loop that stalls or refuses twice raises it. The body's
thread then died with no terminal event and the ``QueryTask`` stayed ``running`` for
ever. Both bodies now answer it with the same ``query_error`` the orchestrator's own
fatal handlers emit (``chat_nextseek.failure_replies``), save the turn, and (SSE) end
the stream.
"""
from __future__ import annotations

import queue

import pytest

from chat_nextseek.llm_clients import LLMFatalError

MOVE = {"agent": "pipeline_agent", "from": "us.anthropic.claude-opus-4-7",
        "to": "us.anthropic.claude-sonnet-4-6", "reason": "timeout"}
RAW = "All tool-capable providers exhausted: agent 'pipeline_agent': timeout: LLM call timed out after 120 seconds"
TRIED_TWO = ("The AI models we use were unavailable (we tried a second one as well), so I could not finish your "
             "question. Please ask again in a few minutes.")


class _Req:
    def __init__(self, mode):
        self.mode = mode
        self.query = "launch rnaseq on those samples"


def _run(monkeypatch, body, mode, fatal, *, pre_error=None):
    from NessieAI.ns import turn as turn_mod

    def _raise(adapter, chat_config, query, send_event, credentials=None, **kw):
        if pre_error is not None:
            send_event("query_error", pre_error)
        raise fatal

    for name in ("run_query", "run_query_plan", "run_pipeline_launch"):
        monkeypatch.setattr(turn_mod, name, _raise)
    saved = []
    monkeypatch.setattr(turn_mod, "_save_session_or_report", lambda *a, **k: saved.append(True))

    events: list[tuple[str, dict]] = []
    kwargs = dict(adapter=object(), chat_config=object(), req=_Req(mode),
                  send_event=lambda e, d: events.append((e, d)), api_user="u", api_pass="p",
                  chat_session=object(), resolved_session_id="sid")
    stream = queue.Queue()
    if body == "sse":
        turn_mod.run_sse_pipeline(event_queue=stream, **kwargs)
    else:
        turn_mod.run_async_pipeline(**kwargs)
    return events, saved, stream


CASES = [("async", "pipeline"), ("async", "standard"), ("async", "plan"), ("sse", "standard"), ("sse", "plan")]


@pytest.mark.parametrize("body, mode", CASES)
def test_an_unavailable_fatal_ends_the_turn_with_the_plain_query_error(monkeypatch, body, mode):
    fatal = LLMFatalError(RAW, agent="pipeline_agent", unavailable=True, model_fallback=[MOVE])

    events, saved, stream = _run(monkeypatch, body, mode, fatal)

    errors = [d for e, d in events if e == "query_error"]
    assert errors == [{"error": TRIED_TWO, "reason": "model_unavailable", "detail": RAW,
                       "agent": "pipeline_agent", "fatal": True, "model_fallback": [MOVE],
                       "session_id": "sid"}]
    assert saved == [True], "the turn is saved, as on the Exception path"
    if body == "sse":
        assert stream.get_nowait() is None, "the SSE stream still gets its sentinel"


@pytest.mark.parametrize("body, mode", CASES)
def test_a_fatal_that_is_not_unavailability_keeps_its_raw_text(monkeypatch, body, mode):
    raw = "Unrecoverable LLM error: agent 'pipeline_agent', model 'm': 400 malformed request"
    events, saved, _ = _run(monkeypatch, body, mode, LLMFatalError(raw, agent="pipeline_agent"))

    errors = [d for e, d in events if e == "query_error"]
    assert errors == [{"error": raw, "agent": "pipeline_agent", "fatal": True, "session_id": "sid"}]
    assert saved == [True]


@pytest.mark.parametrize("body", ["async", "sse"])
def test_a_fatal_already_reported_is_not_reported_twice(monkeypatch, body):
    first = {"error": "already told", "agent": "pipeline_agent"}
    events, _, _ = _run(monkeypatch, body, "standard",
                        LLMFatalError(RAW, agent="pipeline_agent", unavailable=True), pre_error=first)

    assert [d for e, d in events if e == "query_error"] == [first]


def test_the_agent_defaults_when_the_fatal_names_none(monkeypatch):
    events, _, _ = _run(monkeypatch, "async", "pipeline", LLMFatalError(RAW, unavailable=True))
    (error,) = [d for e, d in events if e == "query_error"]
    assert error["agent"] == "unknown"


def test_a_fatal_that_escaped_a_collected_turn_reports_what_the_turn_spent(monkeypatch):
    """The entry point's cost collector rides out on the exception (turn_spend.collects_turn),
    so the query_error that ends the turn carries its cost like a query_complete would."""
    fatal = LLMFatalError(RAW, agent="pipeline_agent", unavailable=True, model_fallback=[MOVE])
    fatal.turn_record = {"total_cost_usd": 0.0123, "cost_partial": True, "models_used": [],
                         "model_fallback": [MOVE], "cost": {"calls": []}}
    events, _, _ = _run(monkeypatch, "async", "pipeline", fatal)
    (error,) = [d for e, d in events if e == "query_error"]
    assert error["total_cost_usd"] == 0.0123 and error["cost_partial"] is True
    assert error["models_used"] == [] and error["model_fallback"] == [MOVE]
    assert "cost" not in error, "the breakdown belongs in a debug payload, which a query_error has not"
