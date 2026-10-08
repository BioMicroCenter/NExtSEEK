"""A Container-CC turn takes the pre-run's vocabulary into its CCTurn and its container (plan 04, piece 3).

The start waits at most ``CC_WAIT_S`` past staging; a vocabulary that is late still reaches the row when it
finishes (the first op finds it there, or fills it itself: Task 9). With NESSIE_PARSER_START=early the pre-run's plan
is stored under the user's question, only on a chat with no earlier turn and no evaluation switch. No model is called.
"""
from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from chat_nextseek import vocabulary as vocabulary_mod
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.cc import prerun
from NessieAI.ns import turn_memory as tm
from NessieAI.router import router as cc_router
from NessieAI.tests.cc.turn_harness import Adapter, cc_seams, decision, drive, rows
from nextseek_api.assistant.models_db import CCTurn

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _prerun_on(settings):
    settings.NESSIE_VOCAB_PRERUN = "on"


@pytest.fixture(autouse=True)
def _own_pool(monkeypatch):
    """The hand-off writes to the database from the pool thread; a pool of its own keeps that connection out of the
    module's shared pool, whose threads later tests run non-database pre-runs on."""
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=prerun.PRERUN_WORKERS, thread_name_prefix="nessie-vocab")
    monkeypatch.setattr(prerun, "_POOL", pool)
    monkeypatch.setattr(prerun, "_ADMIT", threading.BoundedSemaphore(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE))
    yield
    pool.shutdown(wait=True, cancel_futures=True)


def _cc(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_run_cc_turn(**kw):
        seen.update(kw)
        seen["started_at"] = time.monotonic()
        kw["send_event"]("query_complete", {"reply": "done", "total_cost_usd": 0.1})
    cc_seams(monkeypatch, tmp_path, fake_run_cc_turn)
    return seen


def _entity(monkeypatch, gate=None):
    def fake(session, config, query, **kw):
        if gate is not None:
            assert gate.wait(10)
        return EntityAgentOutput(keywords=[query])
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", fake)


def _closed_once(events, outcome) -> bool:
    """'Reading your question' is followed by exactly one closing 'Vocabulary ready' with this outcome, from the turn
    thread (review W1-3: the chat closes the first step on that label alone)."""
    ready = [e for e in events.named("prelude_step") if e["data"]["label"] == prerun.VOCABULARY_READY]
    labels = events.labels()
    return (len(ready) == 1 and ready[0]["data"].get("outcome") == outcome
            and labels.index(prerun.READING_YOUR_QUESTION) < labels.index(prerun.VOCABULARY_READY)
            and ready[0]["thread"] == threading.current_thread().name)


def test_a_first_cc_turn_gets_the_vocabulary_in_its_row_and_its_container(monkeypatch, tmp_path):
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    seen = _cc(monkeypatch, tmp_path)
    _entity(monkeypatch)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    assert seen["vocabulary_slot"]._pending["keywords"] == ["how many mice"]   # offered before the start
    assert seen["previous_turns"] is False
    turn = CCTurn.objects.get(task=task)
    assert tm.get_vocabulary(turn)["keywords"] == ["how many mice"]
    assert _closed_once(events, prerun.COMPLETED)


def test_the_cc_start_waits_at_most_the_wait_and_a_late_vocabulary_still_reaches_the_row(monkeypatch, tmp_path):
    """Review focus 4 (Container-CC half)."""
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    seen = _cc(monkeypatch, tmp_path)
    gate = threading.Event()
    _entity(monkeypatch, gate)
    monkeypatch.setattr(prerun, "CC_WAIT_S", 0.2)
    settled: list = []
    real_hand = prerun.hand_to_turn
    monkeypatch.setattr(prerun, "hand_to_turn", lambda *a, **k: settled.append(real_hand(*a, **k)) or settled[-1])

    t0 = time.monotonic()
    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    assert _closed_once(events, prerun.LATE), "W1-3: the turn gave up on it, and 'Reading your question' still closes"
    assert seen["vocabulary_slot"]._pending is None
    assert seen["started_at"] - t0 < 5
    turn = CCTurn.objects.get(task=task)
    assert tm.get_vocabulary(turn) is None
    gate.set()
    # Wait for the pool thread's hand-off, not by polling: SQLite's shared cache fails a read that meets a write.
    assert settled and settled[0].wait(10)
    assert tm.get_vocabulary(turn)["keywords"] == ["how many mice"]
    # T3: the late vocabulary also reaches the running turn's slot, to be written for the hook's PostToolUse note
    assert seen["vocabulary_slot"]._pending["keywords"] == ["how many mice"]


def test_an_early_plan_is_stored_under_the_users_question_on_a_first_turn(monkeypatch, tmp_path, settings):
    settings.NESSIE_PARSER_START = "early"
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    _cc(monkeypatch, tmp_path)
    _entity(monkeypatch)
    monkeypatch.setattr("chat_nextseek.portable.parser_agent",
                        lambda s, c, text, e: ParserPlan(mode="graph_query", intent_summary=text))

    drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    turn = CCTurn.objects.get(task=task)
    assert tm.get_plan(turn, "how many mice")["mode"] == "graph_query"


def test_no_early_plan_is_stored_when_the_chat_has_history(monkeypatch, tmp_path, settings):
    settings.NESSIE_PARSER_START = "early"
    log = [{"turn_id": 1, "ts": "t", "mode": "cc", "user_query": "q", "assistant_reply": "a",
            "router_choice": "container_cc", "status": "completed"}]
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}", extra_state={"chat_log": log})
    _cc(monkeypatch, tmp_path)
    _entity(monkeypatch)
    monkeypatch.setattr("chat_nextseek.portable.parser_agent",
                        lambda s, c, text, e: ParserPlan(mode="graph_query", intent_summary=text))

    drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC),
          adapter=Adapter(chat_log=log))

    assert CCTurn.objects.get(task=task).plans == {}


def test_a_failed_prerun_still_closes_the_reading_step_on_a_cc_turn(monkeypatch, tmp_path):
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    seen = _cc(monkeypatch, tmp_path)

    def failing(session, config, query, **kw):
        raise RuntimeError("entity model down")
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", failing)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    assert seen["vocabulary_slot"]._pending is None
    assert _closed_once(events, prerun.FAILED)


def test_a_prerun_that_never_started_leaves_no_reading_step_to_close_on_a_cc_turn(monkeypatch, tmp_path, settings):
    settings.NESSIE_VOCAB_PRERUN = "off"
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    seen = _cc(monkeypatch, tmp_path)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))

    assert seen["vocabulary_slot"] is None
    assert events.labels() == [prerun.CHOOSING_AN_ENGINE], "skipped: no 'Reading your question', so nothing to close"


def test_a_queued_prerun_given_up_on_by_a_cc_turn_still_closes_the_reading_step(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    user, chat, task = rows(f"vh-{uuid.uuid4().hex[:8]}")
    seen = _cc(monkeypatch, tmp_path)
    gate = threading.Event()
    _entity(monkeypatch, gate)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nessie-vocab")
    monkeypatch.setattr(prerun, "_POOL", pool)
    monkeypatch.setattr(prerun, "_ADMIT", threading.BoundedSemaphore(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE))
    monkeypatch.setattr(prerun, "CC_WAIT_S", 0.2)
    blocker = prerun.start_prerun(None, SimpleNamespace(), "another turn", skip=False)
    try:
        events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_CC))
        assert seen["vocabulary_slot"]._pending is None
        assert _closed_once(events, prerun.CANCELLED)
        assert [e["data"]["outcome"] for e in events.named("vocabulary_prerun")] == [prerun.CANCELLED]
    finally:
        gate.set()
        blocker.result(10)
        pool.shutdown(wait=True, cancel_futures=True)
