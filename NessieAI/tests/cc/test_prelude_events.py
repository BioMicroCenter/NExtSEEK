"""The pre-run starts with the router, and only the turn thread reports it (plan 04, piece 3).

``start_task`` starts the vocabulary pre-run before it asks the router, sends "Reading your question" and "Choosing
an engine", and "Vocabulary ready" once it sees the vocabulary in, then ``route_decided`` with the router's and the
vocabulary's times. Every event comes from the turn thread, never the pool's. No model is called.
"""
from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from chat_nextseek import vocabulary as vocabulary_mod
from chat_nextseek.helpers import suggestions as sg
from chat_nextseek.schemas import EntityAgentOutput
from NessieAI.cc import prerun
from NessieAI.cc import turn as cc_turn
from NessieAI.router import router as cc_router
from NessieAI.tests.cc.turn_harness import Adapter, decision, drive, rows

pytestmark = pytest.mark.django_db(transaction=True)

READING, CHOOSING, READY = prerun.READING_YOUR_QUESTION, prerun.CHOOSING_AN_ENGINE, prerun.VOCABULARY_READY
RERUN = {"mode": "direct", "cypher": "MATCH (s:T_PAT) RETURN count(s) AS n", "parameters": {}}


@pytest.fixture(autouse=True)
def _prerun_on(settings):
    settings.NESSIE_VOCAB_PRERUN = "on"


@pytest.fixture
def entity(monkeypatch):
    """A resolve_vocabulary that answers at once (or when ``gate`` is set) and records its config."""
    state = {"calls": 0, "configs": [], "gate": None, "done": threading.Event()}

    def fake(session, config, query, **kw):
        state["calls"] += 1
        state["configs"].append(config)
        if state["gate"] is not None:
            assert state["gate"].wait(10)
        state["done"].set()
        return EntityAgentOutput(keywords=[query])
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", fake)
    return state


def _ns_turn(monkeypatch, record: dict):
    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        record.update(kw)
        if "vocabulary" in kw:
            kw["vocabulary"].result(10)
        send_event("query_complete", {"reply": "ok", "bundle_id": None})
    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)


def _user():
    return rows(f"pe-{uuid.uuid4().hex[:8]}")


def test_the_prerun_starts_before_the_router_and_every_event_comes_from_the_turn_thread(monkeypatch, entity):
    user, chat, task = _user()
    record: dict = {}
    _ns_turn(monkeypatch, record)
    started: list = []
    real_start = prerun.start_prerun
    monkeypatch.setattr(prerun, "start_prerun", lambda *a, **k: started.append(real_start(*a, **k)) or started[-1])

    def decide(*a, **k):
        # The router "takes" until the pre-run has finished: done() only, which announces nothing.
        assert entity["done"].wait(10), "the pre-run ran while the router was deciding"
        deadline = time.monotonic() + 10
        while not started[0].done() and time.monotonic() < deadline:
            time.sleep(0.01)
        return decision(cc_router.ROUTE_NS)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=None, decide=decide)

    assert events.order()[:4] == [READING, CHOOSING, READY, "route_decided"]
    (rd,) = events.named("route_decided")
    assert isinstance(rd["data"]["router_elapsed_s"], float) and rd["data"]["router_elapsed_s"] >= 0
    assert isinstance(rd["data"]["vocabulary_elapsed_s"], float)
    turn_thread = threading.current_thread().name
    assert {e["thread"] for e in events} == {turn_thread}
    assert record["vocabulary"].result(0).keywords == ["how many mice"]


def test_a_slow_prerun_is_announced_by_the_ns_turn_when_it_takes_it(monkeypatch, entity):
    user, chat, task = _user()
    entity["gate"] = threading.Event()
    record: dict = {}

    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        entity["gate"].set()
        kw["vocabulary"].result(10)
        send_event("query_complete", {"reply": "ok", "bundle_id": None})
    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS))

    order = events.order()
    assert order.index("route_decided") < order.index(READY)
    assert events.named("route_decided")[0]["data"]["vocabulary_elapsed_s"] is None
    assert {e["thread"] for e in events} == {threading.current_thread().name}


def test_the_prerun_runs_on_the_ns_turns_config_bound_to_the_callers_own_login(monkeypatch, entity):
    user, chat, task = _user()
    _ns_turn(monkeypatch, {})

    drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS),
          graph_scope={"is_admin": False, "project_ids": [1]})

    (config,) = entity["configs"]
    assert (config.API_USER, config.API_PASS) == ("caller", "caller-pw")
    from chat_nextseek.graph_scope import SCOPE_ATTR
    assert list(getattr(config, SCOPE_ATTR).project_ids) == [1]


@pytest.mark.parametrize("case", ["use_prod", "switch_off", "no_login", "wizard"])
def test_no_prerun_starts_when_the_turn_will_not_use_it(monkeypatch, entity, settings, case):
    user, chat, task = _user()
    record: dict = {}
    _ns_turn(monkeypatch, record)
    kw: dict = {}
    if case == "use_prod":
        kw["use_prod"] = True
    elif case == "switch_off":
        settings.NESSIE_VOCAB_PRERUN = "off"
    elif case == "no_login":
        kw["api_user"] = None
    else:
        kw["adapter"] = Adapter(pipeline_agent={"active": True})

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS), **kw)

    assert entity["calls"] == 0
    assert events.labels() == [CHOOSING]
    assert "vocabulary" not in record
    assert events.named("route_decided")[0]["data"]["vocabulary_elapsed_s"] is None


def test_a_chip_click_starts_no_prerun_and_leaves_the_offer_for_run_query(monkeypatch, entity):
    """Review focus 5."""
    user, chat, task = _user()
    chip = {"query": "Only RNA-Seq?", "rerun": RERUN}
    adapter = Adapter(chat_log=[{"turn_id": 2, "user_query": "q", "mode": "graph_query", "status": "completed"}])
    adapter[sg.SESSION_KEY] = {"for_turn": 2, "items": [chip]}
    seen = {}

    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        seen["offer"] = session.get(sg.SESSION_KEY)
        send_event("query_complete", {"reply": "ok", "bundle_id": None})
    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)

    drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS), query="Only RNA-Seq?",
          adapter=adapter)

    assert entity["calls"] == 0
    assert seen["offer"]["items"] == [chip]


def test_an_unrelated_turn_with_a_prerun_still_gets_its_canned_reply(monkeypatch, entity):
    user, chat, task = _user()
    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_UNRELATED))
    (done,) = events.named("query_complete")
    assert done["data"]["reply"] == cc_router.UNRELATED_CANNED_TEXT
    assert not events.named("query_error")


@pytest.fixture
def small_pool(monkeypatch):
    """A one-worker pre-run pool and a fresh admission count, for this test alone."""
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nessie-vocab")
    admit = threading.BoundedSemaphore(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE)
    monkeypatch.setattr(prerun, "_POOL", pool)
    monkeypatch.setattr(prerun, "_ADMIT", admit)
    yield admit
    pool.shutdown(wait=True, cancel_futures=True)


def test_the_turn_reports_its_prerun_outcome_once_right_before_its_answer(monkeypatch, entity):
    user, chat, task = _user()
    _ns_turn(monkeypatch, {})

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS))

    (report,) = events.named("vocabulary_prerun")
    order = events.order()
    assert order.index("vocabulary_prerun") == order.index("query_complete") - 1
    assert report["data"]["outcome"] == prerun.COMPLETED
    assert isinstance(report["data"]["elapsed_s"], float)
    assert report["data"]["duplicate_entity_calls"] == 0
    assert report["thread"] == threading.current_thread().name
    (ready,) = [e for e in events.named("prelude_step") if e["data"]["label"] == READY]
    assert ready["data"]["outcome"] == prerun.COMPLETED


def test_a_prerun_still_queued_at_the_turns_end_is_cancelled_and_reported(monkeypatch, entity, small_pool):
    user, chat, task = _user()
    entity["gate"] = threading.Event()
    blocker = prerun.start_prerun(None, SimpleNamespace(), "another turn's question", skip=False)
    deadline = time.monotonic() + 10
    while entity["calls"] < 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_UNRELATED))

    (report,) = events.named("vocabulary_prerun")
    assert report["data"]["outcome"] == prerun.CANCELLED
    assert report["data"]["duplicate_entity_calls"] == 0
    entity["gate"].set()
    assert blocker.result(10) is not None
    assert entity["calls"] == 1, "the cancelled pre-run never called the entity agent"


def test_a_full_pool_skips_the_prerun_and_the_ns_turn_resolves_it_itself(monkeypatch, entity, small_pool):
    user, chat, task = _user()
    for _ in range(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE):
        assert small_pool.acquire(blocking=False)
    record: dict = {}
    _ns_turn(monkeypatch, record)
    try:
        events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS))
    finally:
        for _ in range(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE):
            small_pool.release()

    assert entity["calls"] == 0
    assert events.labels() == [CHOOSING], "no 'Reading your question' for a pre-run that never started"
    assert "vocabulary" not in record, "the NS turn resolves the vocabulary itself, as today"
    (report,) = events.named("vocabulary_prerun")
    assert report["data"]["outcome"] == prerun.SKIPPED


def test_a_failed_prerun_then_the_turns_own_resolution_is_one_duplicate(monkeypatch, entity):
    user, chat, task = _user()

    def failing(session, config, query, **kw):
        entity["calls"] += 1
        raise RuntimeError("entity model down")
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", failing)

    def fake_run_query(session, config, query, send_event, credentials=None, **kw):
        deadline = time.monotonic() + 10
        while not kw["vocabulary"].done() and time.monotonic() < deadline:  # running, not still queued
            time.sleep(0.01)
        assert vocabulary_mod.take(kw["vocabulary"]) is None  # the turn then resolves it itself
        send_event("query_complete", {"reply": "ok", "bundle_id": None})
    monkeypatch.setattr(cc_turn, "run_query", fake_run_query)

    events = drive(monkeypatch, user=user, chat=chat, task=task, route=decision(cc_router.ROUTE_NS))

    (report,) = events.named("vocabulary_prerun")
    assert report["data"]["outcome"] == prerun.FAILED
    assert report["data"]["duplicate_entity_calls"] == 1
