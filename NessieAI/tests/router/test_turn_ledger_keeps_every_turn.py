"""The per-turn ledger keeps every turn, failed ones included, and ties each row to its QueryTask.

The turn number used to be ``len(chat_log) + 1``, read at route time. Four ordinary things leave the chat log the
same length for the next turn: a turn that dies before its chat-log append reaches the row (a killed worker, a
Container-CC timeout that publishes no terminal event, a failed session save), a chat past the 50-entry chat-log
cap, and two turns of one chat in flight at once. The next turn then computed a number already taken, the unique
``(session, turn_number)`` constraint refused its row, and the writer logged the collision and dropped the row.
Nothing tied a surviving row to the QueryTask it described either, so the turns a diagnosis most needs to read were
the ones the ledger could not find or could not join.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession, QueryTask, TurnLedger
from NessieAI.router import policy
from NessieAI.router import router as cc_router
from NessieAI.router import turn_ledger

pytestmark = pytest.mark.django_db

CHAT_LOG_CAP = 50


def _user():
    return get_user_model().objects.create_user(username=f"every-{uuid.uuid4().hex[:8]}", password="x")


def _session(user=None, chat_log=None):
    return ChatSession.objects.create(user=user or _user(), extra_state={"chat_log": list(chat_log or [])})


def _task(session, query="q", status="running"):
    return QueryTask.objects.create(session=session, user=session.user, query=query, status=status)


def _decision(route=cc_router.ROUTE_NS, source="baml"):
    return cc_router.RouteDecision(
        route=route, model_class=None, model_id=None, reasoning="test", source=source,
    )


def _numbers(session):
    return list(TurnLedger.objects.filter(session=session).order_by("turn_number")
                .values_list("turn_number", "route"))


# ------------------------------------------------------------------ numbering


def test_the_turn_after_one_that_died_before_its_chat_log_append_keeps_its_row():
    session = _session()
    policy._record_ledger_row(session, _decision(cc_router.ROUTE_CC))   # dies: chat_log never grows
    policy._record_ledger_row(session, _decision(cc_router.ROUTE_NS))

    assert _numbers(session) == [(1, cc_router.ROUTE_CC), (2, cc_router.ROUTE_NS)]


def test_turns_past_the_chat_log_cap_each_keep_a_row():
    """The chat log keeps the newest 50 entries, so its length stops growing at the cap."""
    session = _session(chat_log=[{"turn_id": f"t{i}"} for i in range(CHAT_LOG_CAP)])
    for _ in range(3):
        policy._record_ledger_row(session, _decision())

    assert [n for n, _ in _numbers(session)] == [51, 52, 53]


def test_two_turns_of_one_chat_in_flight_at_once_each_keep_a_row():
    """Each request loads its own copy of the session, so both read the same chat-log length."""
    session = _session()
    first, second = ChatSession.objects.get(pk=session.pk), ChatSession.objects.get(pk=session.pk)
    policy._record_ledger_row(first, _decision(cc_router.ROUTE_CC))
    policy._record_ledger_row(second, _decision(cc_router.ROUTE_NS))

    assert _numbers(session) == [(1, cc_router.ROUTE_CC), (2, cc_router.ROUTE_NS)]


def test_the_number_still_follows_the_chat_log_when_the_ledger_is_behind_it():
    """A chat whose earlier turns predate the ledger keeps its turn numbers aligned with its chat log."""
    session = _session(chat_log=[{"turn_id": "t0"}, {"turn_id": "t1"}, {"turn_id": "t2"}])
    policy._record_ledger_row(session, _decision())

    assert [n for n, _ in _numbers(session)] == [4]


def test_a_concurrent_writer_that_takes_the_number_first_does_not_cost_this_turn_its_row(monkeypatch):
    """The race between reading the next number and inserting it: the loser takes the number after."""
    session = _session()
    real_next = turn_ledger.next_turn_number
    raced = []

    def _racing_next(session_id, floor=1):
        number = real_next(session_id, floor)
        if not raced:
            raced.append(number)
            TurnLedger.objects.create(session=session, turn_number=number,
                                      route=cc_router.ROUTE_CC, route_source="baml")
        return number

    monkeypatch.setattr(turn_ledger, "next_turn_number", _racing_next)
    row = turn_ledger.record_next_turn(str(session.session_id), 1, cc_router.ROUTE_NS, "baml", None, None)

    assert raced == [1]
    assert row.turn_number == 2
    assert _numbers(session) == [(1, cc_router.ROUTE_CC), (2, cc_router.ROUTE_NS)]


def test_an_explicit_number_that_is_taken_still_raises_a_collision():
    """record_turn keeps its strict contract: only the allocating writer moves to the next number."""
    session = _session()
    turn_ledger.record_turn(str(session.session_id), 1, cc_router.ROUTE_NS, "baml", None, None)
    with pytest.raises(turn_ledger.LedgerCollision):
        turn_ledger.record_turn(str(session.session_id), 1, cc_router.ROUTE_NS, "baml", None, None)


# ------------------------------------------------------------------ the QueryTask link


def test_each_row_is_linked_to_its_query_task():
    session = _session()
    failed, answered = _task(session, "first", "error"), _task(session, "second", "completed")
    policy._record_ledger_row(session, _decision(cc_router.ROUTE_CC), query_task=failed)
    policy._record_ledger_row(session, _decision(cc_router.ROUTE_NS), query_task=answered)

    rows = list(TurnLedger.objects.filter(session=session).order_by("turn_number"))
    assert [(r.turn_number, r.query_task_id) for r in rows] == [(1, failed.pk), (2, answered.pk)]
    assert list(failed.ledger_rows.values_list("turn_number", flat=True)) == [1]
    assert TurnLedger.objects.get(query_task__task_id=answered.task_id).turn_number == 2


def test_a_row_written_without_a_task_is_still_recorded():
    session = _session()
    policy._record_ledger_row(session, _decision())

    row = TurnLedger.objects.get(session=session)
    assert row.query_task is None


def test_deleting_the_task_keeps_the_ledger_row():
    session = _session()
    task = _task(session)
    policy._record_ledger_row(session, _decision(), query_task=task)
    task.delete()

    row = TurnLedger.objects.get(session=session)
    assert row.turn_number == 1 and row.query_task is None


# ------------------------------------------------------------------ through the real turn


class _SyncThreading:
    """Stands in for the ``threading`` module inside NessieAI.cc.turn: runs the turn inline."""

    class Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()


def _run_ns_turn(monkeypatch, session, task, run_query):
    """One routed NS turn through start_task with the real ledger write and session adapter."""
    from nextseek_api.assistant.session_adapter import DictSessionAdapter
    from NessieAI.cc import turn

    monkeypatch.setattr(turn, "threading", _SyncThreading)
    monkeypatch.setattr(turn, "_select_chat_config", lambda request, r: SimpleNamespace())
    monkeypatch.setattr(turn, "_decide_route", lambda *a, **k: _decision(cc_router.ROUTE_NS))
    monkeypatch.setattr(turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(turn, "run_query", run_query)

    events = []
    turn.start_task(
        SimpleNamespace(user=session.user), SimpleNamespace(query=task.query, mode="standard"),
        force_cc=False, chat_session=session, query_task=task,
        send_event=lambda ev, data: events.append(ev), adapter=DictSessionAdapter(session),
        api_user="caller", api_pass="caller-pw", resolved_session_id=str(session.session_id),
    )
    return events


def test_a_turn_that_publishes_nothing_and_the_next_turn_each_leave_a_row_tied_to_their_task(monkeypatch):
    """The failed turn is the one a diagnosis reads first, and the turn after it must not overwrite or lose either."""
    session = _session()

    silent = _task(session, "first")
    events = _run_ns_turn(monkeypatch, session, silent, lambda *a, **k: None)   # no terminal event, no chat_log append
    assert "query_complete" not in events and "query_error" not in events

    answered = _task(session, "second")
    reloaded = ChatSession.objects.get(pk=session.pk)    # the ViewSet loads the session fresh per request
    _run_ns_turn(monkeypatch, reloaded, answered,
                 lambda adapter, config, query, send_event, **k: send_event("query_complete", {"reply": "ok"}))

    rows = list(TurnLedger.objects.filter(session=session).order_by("turn_number"))
    assert [r.turn_number for r in rows] == [1, 2]
    assert [r.query_task_id for r in rows] == [silent.pk, answered.pk]


# ------------------------------------------------------------------ the session inventory


def test_the_session_inventory_joins_each_ledger_row_to_its_task():
    from nextseek_api.assistant import session_debug

    session = _session()
    task = _task(session, status="error")
    policy._record_ledger_row(session, _decision(), query_task=task)
    policy._record_ledger_row(session, _decision())

    out = session_debug.collect(session)
    assert [(r["turn_number"], r["task_id"]) for r in out["ledger"]] == [(1, str(task.task_id)), (2, None)]
    assert out["tasks"][0]["task_id"] == str(task.task_id)
