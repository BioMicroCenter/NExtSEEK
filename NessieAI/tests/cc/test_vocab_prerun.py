"""The vocabulary pre-run runs on a pool thread and only computes (plan 04, piece 3).

It resolves the vocabulary inside its own cost collector and failed-model scope, closes its database connection at
start and end, sends no event, and hands out its result only when asked. No model is called: resolve_vocabulary and
the parser are faked.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from chat_nextseek import call_scope, model_prices, turn_spend
from chat_nextseek import vocabulary as vocabulary_mod
from chat_nextseek.llm_clients import LLMFatalError, LLMResponse, LLMTimeoutError
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.cc import prerun
from NessieAI.ns import turn_memory as tm
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

FLASH = "gemini-3.5-flash"
USAGE = {"prompt_tokens": 4000, "completion_tokens": 100, "thoughts_tokens": 300, "cached_tokens": 1000}
CONFIG = SimpleNamespace(MIN_SAMPLETYPES=[], MIN_ASSAYS=[])


def _paid_entity(release: threading.Event | None = None, *, strike: bool = True, threads: list | None = None):
    """A resolve_vocabulary that makes one priced call, marks one failed model and records its thread."""
    def fake(session, config, query, *, diagnostics=None, **kw):
        if threads is not None:
            threads.append(threading.current_thread().name)
        if release is not None:
            assert release.wait(10)
        turn_spend.record_call({"agent": "entity", "provider": "gcp", "model": FLASH, "attempt": 1, "outcome": "ok"},
                               resp=LLMResponse(content="x", raw=None, usage=dict(USAGE), model=FLASH,
                                                provider="gcp", metadata={}))
        if strike:
            call_scope.current().mark_failed(("bedrock", "sonnet"), reason="unavailable", agent="entity")
        if diagnostics is not None:
            diagnostics["sampletype_codes"] = ["MUS"]
        return EntityAgentOutput(keywords=[query])
    return fake


@pytest.fixture
def fresh_pool(monkeypatch):
    """A pool and an admission count of the module's own sizes, for this test alone (the module's are shared)."""
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=prerun.PRERUN_WORKERS, thread_name_prefix="nessie-vocab")
    monkeypatch.setattr(prerun, "_POOL", pool)
    monkeypatch.setattr(prerun, "_ADMIT", threading.BoundedSemaphore(prerun.PRERUN_WORKERS + prerun.PRERUN_QUEUE))
    yield pool
    pool.shutdown(wait=True, cancel_futures=True)


def test_a_skipped_prerun_never_starts():
    p = prerun.start_prerun(None, CONFIG, "q", skip=True)
    assert p.started is False and p.done() is False and p.result(0) is None
    assert p.spend_usd == 0.0 and p.strikes == []
    assert p.outcome == prerun.SKIPPED and p.final_outcome() == prerun.SKIPPED and p.ran is False


def test_nine_at_once_admits_eight_and_skips_the_ninth_at_once(monkeypatch, fresh_pool):
    release = threading.Event()
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release, strike=False))

    t0 = time.monotonic()
    runs = [prerun.start_prerun(None, CONFIG, f"q{i}", skip=False) for i in range(9)]
    assert time.monotonic() - t0 < 1, "admission never waits"

    assert [p.started for p in runs] == [True] * 8 + [False]
    assert runs[8].outcome == prerun.SKIPPED and runs[8].result(0) is None
    release.set()
    assert all(p.result(10) is not None for p in runs[:8])
    assert [p.outcome for p in runs[:8]] == [prerun.COMPLETED] * 8
    assert prerun.start_prerun(None, CONFIG, "again", skip=False).started, "slots come back when pre-runs finish"


def test_the_prerun_runs_under_its_own_deadline(monkeypatch):
    seen = {}

    def fake(session, config, query, **kw):
        seen["left"] = call_scope.current().remaining()
        return EntityAgentOutput(keywords=[query])
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", fake)

    prerun.start_prerun(None, CONFIG, "q", skip=False).result(10)

    assert prerun.PRERUN_DEADLINE_S == 60.0
    assert 55.0 < seen["left"] <= prerun.PRERUN_DEADLINE_S


def test_a_queued_prerun_is_cancelled_and_frees_its_place(monkeypatch, fresh_pool):
    release = threading.Event()
    calls: list = []
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release, strike=False, threads=calls))
    running = [prerun.start_prerun(None, CONFIG, f"r{i}", skip=False) for i in range(prerun.PRERUN_WORKERS)]
    queued = prerun.start_prerun(None, CONFIG, "queued", skip=False)
    deadline = time.monotonic() + 10
    while len(calls) < prerun.PRERUN_WORKERS and time.monotonic() < deadline:
        time.sleep(0.01)

    assert queued.cancel() is True
    assert queued.outcome == prerun.CANCELLED and queued.done() and queued.result(0) is None
    assert queued.ran is False
    assert running[0].cancel() is False, "one already running is not cancelled"
    release.set()
    assert all(p.result(10) is not None for p in running)
    assert len(calls) == prerun.PRERUN_WORKERS, "the cancelled one never called the entity agent"
    assert queued.final_outcome() == prerun.CANCELLED


def test_each_outcome_is_recorded(monkeypatch):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(strike=False))
    completed = prerun.start_prerun(None, CONFIG, "q", skip=False)
    assert completed.result(10) is not None and completed.outcome == prerun.COMPLETED

    def fatal(session, config, query, **kw):
        raise LLMFatalError("both models failed", reason="timeout", unavailable=True)
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", fatal)
    failed = prerun.start_prerun(None, CONFIG, "q", skip=False)
    assert failed.result(10) is None and failed.outcome == prerun.FAILED

    release = threading.Event()
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release, strike=False))
    late = prerun.start_prerun(None, CONFIG, "q", skip=False)
    assert late.result(0.1) is None and late.outcome == prerun.LATE
    release.set()
    late._future.result(10)
    assert late.outcome == prerun.LATE, "the turn went on without it: it stays late once it finishes"

    release2 = threading.Event()
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release2, strike=False))
    unasked = prerun.start_prerun(None, CONFIG, "q", skip=False)
    assert unasked.outcome is None, "running and not given up on"
    assert unasked.final_outcome() == prerun.LATE, "still running at the turn's end"
    release2.set()
    assert set(prerun.OUTCOMES) == {"completed", "late", "skipped", "failed", "cancelled"}


def test_duplicate_entity_calls_count_every_resolution_after_the_first(monkeypatch):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(strike=False))
    ran = prerun.start_prerun(None, CONFIG, "q", skip=False)
    ran.result(10)
    assert ran.ran is True
    assert ran.duplicate_entity_calls(0) == 0
    ran.note_resolved_in_turn()
    assert ran.turn_resolutions == 1 and ran.duplicate_entity_calls(ran.turn_resolutions) == 1
    skipped = prerun.start_prerun(None, CONFIG, "q", skip=True)
    assert skipped.duplicate_entity_calls(1) == 0 and skipped.duplicate_entity_calls(3) == 2


def test_it_resolves_on_a_pool_thread_with_its_own_spend_and_strikes(monkeypatch):
    threads: list = []
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(threads=threads))

    p = prerun.start_prerun(None, CONFIG, "how many mice", skip=False)
    out = p.result(10)

    assert out.keywords == ["how many mice"]
    assert threads and threads[0].startswith("nessie-vocab")
    assert p.done() and isinstance(p.elapsed_s, float)
    assert p.spend_usd == pytest.approx(model_prices.call_cost(FLASH, USAGE).cost_usd, abs=1e-6)
    assert p.spend_partial is False
    assert p.strikes == [["bedrock", "sonnet", "unavailable"]]
    assert p.diagnostics == {"sampletype_codes": ["MUS"]}
    assert p.plan is None
    assert turn_spend.current() is None and call_scope.current() is None, "nothing leaks into the caller"


def test_the_pool_thread_closes_its_database_connection_at_start_and_end(monkeypatch):
    closes: list = []
    monkeypatch.setattr(prerun, "close_old_connections", lambda: closes.append(threading.current_thread().name))
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity())

    prerun.start_prerun(None, CONFIG, "q", skip=False).result(10)

    assert len(closes) >= 2 and all(name.startswith("nessie-vocab") for name in closes)


def test_result_waits_at_most_its_timeout_and_announces_once_in_the_callers_thread(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release))
    p = prerun.start_prerun(None, CONFIG, "q", skip=False)
    announced: list = []
    p.announce = lambda: announced.append(threading.current_thread().name)

    t0 = time.monotonic()
    assert p.result(0.2) is None
    assert time.monotonic() - t0 < 2
    release.set()
    assert p.result(10).keywords == ["q"]
    assert p.result(0).keywords == ["q"]
    assert announced == [threading.current_thread().name], "announced once, by the caller, never by the pool"


def test_a_failed_prerun_hands_out_nothing_but_keeps_its_strikes(monkeypatch):
    def fatal(session, config, query, **kw):
        call_scope.current().mark_failed(("gcp", FLASH), reason="timeout", agent="entity")
        raise LLMFatalError("both models failed", reason="timeout", unavailable=True)
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", fatal)

    p = prerun.start_prerun(None, CONFIG, "q", skip=False)

    assert p.result(10) is None and p.done()
    assert p.strikes == [["gcp", FLASH, "timeout"]]


def test_an_early_parser_runs_only_when_asked_and_on_the_snapshot(monkeypatch):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(strike=False))
    seen = {}

    def parser(session, config, text, entity):
        seen["session"] = session
        return ParserPlan(mode="graph_query", intent_summary=text)
    monkeypatch.setattr("chat_nextseek.portable.parser_agent", parser)
    live = {"results_history": [{"id": 1}], "chat_log": [{"turn_id": 1}], "pending_suggestions": {"x": 1}}

    snapshot = prerun.session_snapshot(live)
    p = prerun.start_prerun(snapshot, CONFIG, "mice", skip=False, early_parser=True)
    p.result(10)

    assert p.plan["mode"] == "graph_query" and p.plan["intent_summary"] == "mice"
    assert seen["session"] == {"results_history": [{"id": 1}], "chat_log": [{"turn_id": 1}]}
    assert seen["session"]["results_history"] is not live["results_history"]
    plain = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    assert plain.result(10) is not None and plain.plan is None, "no early parser unless asked"


def test_the_switches_read_their_settings(settings):
    settings.NESSIE_VOCAB_PRERUN = "off"
    assert prerun.enabled() is False
    settings.NESSIE_VOCAB_PRERUN = "on"
    assert prerun.enabled() is True
    settings.NESSIE_PARSER_START = "early"
    assert prerun.parser_start() == "early"
    settings.NESSIE_PARSER_START = "anything else"
    assert prerun.parser_start() == "after_route"


def _turn(query="q") -> CCTurn:
    user = get_user_model().objects.create_user(f"pr-{uuid.uuid4().hex[:8]}", password="x")
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query=query, status="running")
    return CCTurn.objects.create(task=task, user=user, chat=chat,
                                 pass_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest())


@pytest.mark.django_db(transaction=True)
def test_handing_a_finished_prerun_to_a_turn_stores_everything_at_once(monkeypatch, fresh_pool):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity())
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    p.result(10)

    settled = prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False)

    assert settled.is_set()
    assert tm.get_vocabulary(turn)["keywords"] == ["mice"]
    assert tm.load_strikes(turn) == [["bedrock", "sonnet", "unavailable"]]
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal(str(round(model_prices.call_cost(FLASH, USAGE).cost_usd, 6)))
    assert row.plans == {}


@pytest.mark.django_db(transaction=True)
def test_the_slot_is_offered_the_vocabulary_the_turn_kept_when_an_op_stored_one_first(monkeypatch, fresh_pool):
    """T1 checks a plan's lab codes against the stored vocabulary, so the agent's note shows that one, not the
    pre-run's that lost the store."""
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity())
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    p.result(10)
    assert tm.store_vocabulary(turn, {"keywords": ["the op's"]})
    offered: list = []

    assert prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False,
                               slot=SimpleNamespace(offer=offered.append)).is_set()
    assert offered == [{"keywords": ["the op's"]}]


@pytest.mark.django_db(transaction=True)
def test_a_prerun_estimate_reaches_the_turn(monkeypatch, fresh_pool):
    """Round 6: an entity call that timed out is priced as an estimate and the turn says so."""
    def hung_entity(session, config, query, *, diagnostics=None, **kw):
        turn_spend.record_call({"agent": "entity", "provider": "gcp", "model": FLASH, "attempt": 1,
                                "outcome": "timeout", "prompt_chars": 40_000},
                               err=LLMTimeoutError("timed out"))
        return EntityAgentOutput(keywords=[query])
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", hung_entity)
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    p.result(10)
    assert p.spend_estimated is True and p.spend_partial is False
    assert prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False).is_set()
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_estimated is True and row.ops_cost_partial is False and row.ops_cost_usd > 0


@pytest.mark.django_db(transaction=True)
def test_a_failed_vocabulary_write_still_records_the_spend_and_a_failed_spend_write_marks_the_cost_partial(
        monkeypatch, fresh_pool):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity())
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    p.result(10)

    def boom(*a, **k):
        raise RuntimeError("row gone")
    monkeypatch.setattr(tm, "store_vocabulary", boom)
    settled = prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False)
    assert settled.is_set()
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal(str(round(model_prices.call_cost(FLASH, USAGE).cost_usd, 6)))
    assert row.ops_cost_partial is False, "the spend write itself succeeded"
    assert tm.load_strikes(turn) == [["bedrock", "sonnet", "unavailable"]]

    turn2 = _turn("mice")
    monkeypatch.setattr(tm, "add_spend", boom)
    assert prerun.hand_to_turn(p, turn2, user_question="mice", store_early_plan=False).is_set()
    assert CCTurn.objects.get(pk=turn2.pk).ops_cost_partial is True, "a lost spend write never reads complete"


@pytest.mark.django_db(transaction=True)
def test_a_failed_strikes_write_in_the_hand_off_marks_the_cost_partial_and_keeps_the_rest(monkeypatch, fresh_pool):
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity())
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    p.result(10)
    assert p.strikes, "the pre-run marked a failed model"

    def boom(*a, **k):
        raise RuntimeError("row gone")
    monkeypatch.setattr(tm, "merge_strikes", boom)
    assert prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False).is_set()
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal(str(round(model_prices.call_cost(FLASH, USAGE).cost_usd, 6)))
    assert row.ops_cost_partial is True, "a lost strikes write never reads complete"
    assert tm.get_vocabulary(turn)["keywords"] == ["mice"], "the vocabulary write is independent of the lost one"


@pytest.mark.django_db(transaction=True)
def test_a_late_prerun_reaches_the_turn_from_its_pool_thread(monkeypatch, fresh_pool):
    release = threading.Event()
    entered: list = []
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release, threads=entered))
    turn = _turn("mice")
    p = prerun.start_prerun(None, CONFIG, "mice", skip=False)
    deadline = time.monotonic() + 10
    while not entered and time.monotonic() < deadline:  # running, not still queued: a queued one is cancelled
        time.sleep(0.01)

    settled = prerun.hand_to_turn(p, turn, user_question="mice", store_early_plan=False)
    assert not settled.is_set()
    release.set()

    assert settled.wait(10)
    assert tm.get_vocabulary(turn)["keywords"] == ["mice"]


@pytest.mark.django_db(transaction=True)
def test_handing_a_queued_prerun_to_a_turn_cancels_it(monkeypatch, fresh_pool):
    release = threading.Event()
    monkeypatch.setattr(vocabulary_mod, "resolve_vocabulary", _paid_entity(release, strike=False))
    running = [prerun.start_prerun(None, CONFIG, f"r{i}", skip=False) for i in range(prerun.PRERUN_WORKERS)]
    turn = _turn("mice")
    queued = prerun.start_prerun(None, CONFIG, "mice", skip=False)

    settled = prerun.hand_to_turn(queued, turn, user_question="mice", store_early_plan=False)

    assert settled.is_set(), "nothing is left to settle"
    assert queued.outcome == prerun.CANCELLED
    assert tm.get_vocabulary(turn) is None, "the first op that needs it resolves it"
    release.set()
    assert all(p.result(10) is not None for p in running)
