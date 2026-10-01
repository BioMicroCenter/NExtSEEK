"""A real provider error must reach the user instead of "Internal pipeline error".

``run_query`` emits a ``query_error`` carrying the provider's own message and then
re-raises; both SSE and async pipeline bodies caught that and emitted a SECOND,
generic ``query_error``. The generic one arrives last, so that is what the chat panel
rendered. Production turns 463/464 (wesselr, 2026-09-04) lost
``503 UNAVAILABLE ... This model is currently experiencing high demand`` exactly this
way, and the review recorded them as "the user got no answer" with no visible cause.
"""
from __future__ import annotations

import pytest

from NessieAI.ns.turn import _error_tracking_send_event


def test_wrapper_records_that_an_error_was_already_reported():
    seen = []
    wrapped, state = _error_tracking_send_event(lambda e, d: seen.append((e, d)))

    assert state["sent"] is False
    wrapped("agent_started", {"agent": "graph"})
    assert state["sent"] is False

    wrapped("query_error", {"error": "503 UNAVAILABLE", "agent": "graph"})
    assert state["sent"] is True
    assert seen == [
        ("agent_started", {"agent": "graph"}),
        ("query_error", {"error": "503 UNAVAILABLE", "agent": "graph"}),
    ]


def test_wrapper_passes_every_event_through_unchanged():
    """The wrapper observes; it must not filter, reorder or rewrite."""
    seen = []
    wrapped, _ = _error_tracking_send_event(lambda e, d: seen.append((e, d)))
    for name in ("agent_started", "agent_complete", "search_started", "query_complete"):
        wrapped(name, {"n": name})
    assert [e for e, _ in seen] == [
        "agent_started", "agent_complete", "search_started", "query_complete",
    ]


@pytest.mark.parametrize("body", ["sse", "async"])
def test_pipeline_body_does_not_emit_a_second_generic_error(monkeypatch, body):
    """End to end over the pipeline body: one query_error, and it is the real one."""
    from NessieAI.ns import turn as turn_mod

    def _boom(adapter, chat_config, query, send_event, credentials=None):
        send_event("query_error", {"error": "503 UNAVAILABLE ... high demand", "agent": "graph"})
        raise RuntimeError("re-raised after reporting")

    monkeypatch.setattr(turn_mod, "run_query", _boom)
    monkeypatch.setattr(turn_mod, "_save_session_or_report", lambda *a, **k: None)

    events: list[tuple[str, dict]] = []

    class _Req:
        mode = "standard"
        query = "how many NHP samples"

    kwargs = dict(
        adapter=object(), chat_config=object(), req=_Req(),
        send_event=lambda e, d: events.append((e, d)),
        api_user="u", api_pass="p", chat_session=object(),
        resolved_session_id="sid",
    )
    if body == "sse":
        import queue

        turn_mod.run_sse_pipeline(event_queue=queue.Queue(), **kwargs)
    else:
        turn_mod.run_async_pipeline(**kwargs)

    errors = [d for e, d in events if e == "query_error"]
    assert len(errors) == 1, f"expected exactly one query_error, got {errors}"
    assert "503" in errors[0]["error"]
    assert errors[0]["error"] != "Internal pipeline error"


@pytest.mark.parametrize("body", ["sse", "async"])
def test_generic_error_still_fires_when_nothing_was_reported(monkeypatch, body):
    """The generic message is the correct fallback for a crash before any reporting,
    so removing it entirely would leave those turns with no terminal event at all."""
    from NessieAI.ns import turn as turn_mod

    def _boom(adapter, chat_config, query, send_event, credentials=None):
        raise RuntimeError("died before reporting anything")

    monkeypatch.setattr(turn_mod, "run_query", _boom)
    monkeypatch.setattr(turn_mod, "_save_session_or_report", lambda *a, **k: None)

    events: list[tuple[str, dict]] = []

    class _Req:
        mode = "standard"
        query = "q"

    kwargs = dict(
        adapter=object(), chat_config=object(), req=_Req(),
        send_event=lambda e, d: events.append((e, d)),
        api_user="u", api_pass="p", chat_session=object(),
        resolved_session_id="sid",
    )
    if body == "sse":
        import queue

        turn_mod.run_sse_pipeline(event_queue=queue.Queue(), **kwargs)
    else:
        turn_mod.run_async_pipeline(**kwargs)

    errors = [d for e, d in events if e == "query_error"]
    assert len(errors) == 1
    assert errors[0]["error"] == "Internal pipeline error"
    assert errors[0]["session_id"] == "sid"


def test_an_error_whose_send_failed_is_not_counted_as_sent():
    """If forwarding the real query_error raises (a DB write, a payload that cannot be
    serialized), the error never went out: the caller's generic query_error must still be
    sent, or the turn ends with no terminal event and the task stays running."""
    def failing_send(event_type, data):
        raise RuntimeError("db write failed")

    wrapped, state = _error_tracking_send_event(failing_send)
    with pytest.raises(RuntimeError):
        wrapped("query_error", {"error": "503 UNAVAILABLE", "agent": "graph"})
    assert state["sent"] is False
