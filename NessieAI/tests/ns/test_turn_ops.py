"""The ops of one Container-CC turn share its memory (plan 04, piece 3).

With a turn: the vocabulary is the turn's (the first op that finds none resolves it on the user's question and stores
it); the turn has one parser plan (the agent's, else the first parse), which an op sent without a plan reuses; a model that failed in one op is skipped in the next, on
another worker too; every op's spend is added to the turn. A reused plan still runs through the scoped Neo4j tool.
Without a turn nothing changes. Agents are faked; no model is called. The test database is SQLite in memory with a
shared cache, where two connections touching one table at the same moment fail at once, so the concurrency tests run
each turn-memory call under one lock (``serialize_turn_memory``) and overlap everything between the calls.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from chat_nextseek import call_scope, model_prices, turn_spend
from chat_nextseek import vocabulary as vocabulary_mod
from chat_nextseek.llm_clients import LLMResponse
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan
from NessieAI.ns import turn_memory as tm
from NessieAI.ns.granular import run_op
from NessieAI.tests.ns.test_turn_memory import serialize_turn_memory
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask

pytestmark = pytest.mark.django_db(transaction=True)

FLASH = "gemini-3.5-flash"
USAGE = {"prompt_tokens": 4000, "completion_tokens": 100, "thoughts_tokens": 300, "cached_tokens": 1000}
CONFIG = SimpleNamespace(MIN_SAMPLETYPES=[{"code": "MUS"}], MIN_ASSAYS=[])
COST = model_prices.call_cost(FLASH, USAGE).cost_usd


def _turn(query="How many mice?", vocabulary=None) -> CCTurn:
    user = get_user_model().objects.create_user(f"to-{uuid.uuid4().hex[:8]}", password="x")
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query=query, status="running")
    return CCTurn.objects.create(task=task, user=user, chat=chat, vocabulary=vocabulary,
                                 pass_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest())


def _paid(agent: str) -> None:
    turn_spend.record_call({"agent": agent, "provider": "gcp", "model": FLASH, "attempt": 1, "outcome": "ok"},
                           resp=LLMResponse(content="x", raw=None, usage=dict(USAGE), model=FLASH, provider="gcp",
                                            metadata={}))


class Agents:
    """The NS agents behind the ops, faked: counts, what each saw, and hooks for the concurrency tests."""

    def __init__(self, monkeypatch):
        self.entity_calls: list = []
        self.parser_calls: list = []
        self.graph_saw: list = []
        self.on_parser = None
        self.on_graph = None
        self.neo4j = MagicMock(return_value={"ok": True, "data": [{"n": 3}], "count": 1})
        monkeypatch.setattr(vocabulary_mod, "shortlist_catalog", lambda *a, **k: ([], [], {}))
        monkeypatch.setattr(vocabulary_mod, "entity_agent", self._entity)
        monkeypatch.setattr("chat_nextseek.portable.entity_agent", self._entity_legacy)
        monkeypatch.setattr("chat_nextseek.portable.parser_agent", self._parser)
        monkeypatch.setattr("chat_nextseek.portable.graph_agent", self._graph)

    def _entity(self, config, query, sampletypes, assays):
        self.entity_calls.append(query)
        _paid("entity")
        return EntityAgentOutput(keywords=[f"{query}#{threading.current_thread().name}"])

    def _entity_legacy(self, config, query):
        self.entity_calls.append(("legacy", query))
        return EntityAgentOutput(keywords=["legacy"])

    def _parser(self, session, config, text, entity):
        self.parser_calls.append(text)
        if self.on_parser:
            self.on_parser(text)
        return ParserPlan(mode="graph_query", intent_summary=text)

    def _graph(self, config, query, entity, plan, **kw):
        self.graph_saw.append((query, entity, plan))
        if self.on_graph:
            self.on_graph(query)
        _paid("graph_agent")
        return SimpleNamespace(model_dump=lambda: {"cypher": "MATCH (s:T_MUS) RETURN count(s) AS n",
                                                    "parameters": {}})

    def op(self, op, query, turn, **kw):
        return run_op(op, {"query": query, **kw}, config=CONFIG, session=None, write_gate=MagicMock(),
                      neo4j_exec=self.neo4j, turn=turn)


def test_two_graph_ops_of_one_turn_make_one_entity_call_on_the_users_question(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?")

    agents.op("graph", "mice by sex", turn)
    agents.op("graph", "mice by strain", CCTurn.objects.get(pk=turn.pk))

    assert agents.entity_calls == ["How many mice?"]
    stored = tm.get_vocabulary(turn)
    assert [saw[1].keywords for saw in agents.graph_saw] == [stored["keywords"], stored["keywords"]]
    assert tm.vocabulary_resolutions(turn) == 1


def test_two_first_ops_at_once_store_one_vocabulary_and_both_use_it(monkeypatch):
    """Review focus 3: each may compute it; the conditional store keeps the first, and the other op reads it back."""
    agents = Agents(monkeypatch)
    turn = _turn()
    both_computing = threading.Barrier(2, timeout=10)
    real_entity = agents._entity

    def entity(config, query, sampletypes, assays):
        out = real_entity(config, query, sampletypes, assays)
        both_computing.wait()
        return out
    monkeypatch.setattr(vocabulary_mod, "entity_agent", entity)
    lock = serialize_turn_memory(monkeypatch)
    errors: list = []

    def worker(question):
        try:
            with lock:
                stale = CCTurn.objects.get(pk=turn.pk)
            agents.op("graph", question, stale)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(q,), name=n) for q, n in (("q1", "w1"), ("q2", "w2"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors

    assert len(agents.entity_calls) == 2
    stored = tm.get_vocabulary(turn)["keywords"]
    assert [saw[1].keywords for saw in agents.graph_saw] == [stored, stored]
    assert tm.vocabulary_resolutions(turn) == 2, "both resolutions are counted (one is a duplicate)"


def test_two_plan_less_ops_of_a_turn_make_one_parser_call_and_the_second_uses_the_turns_plan(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary=EntityAgentOutput().model_dump(mode="json"))

    first = agents.op("graph", "mice by sex", CCTurn.objects.get(pk=turn.pk))
    second = agents.op("graph", "mice by strain, rephrased", CCTurn.objects.get(pk=turn.pk))

    assert agents.parser_calls == ["mice by sex"], "the agent rephrases: the turn's one plan serves both ops"
    assert agents.graph_saw[1][2].intent_summary == "mice by sex"
    assert (first["plan_source"], second["plan_source"]) == ("parser", "turn")
    assert agents.neo4j.call_count == 2, "a reused plan still runs through the scoped tool"
    assert list(CCTurn.objects.get(pk=turn.pk).plans) == [tm.TURN_PLAN_KEY]


def test_an_agent_plan_becomes_the_turns_plan_and_the_latest_one_replaces_it(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary=EntityAgentOutput().model_dump(mode="json"))
    plan = lambda summary: json.dumps({"intent_summary": summary, "filters": {"sampletype_code": "MUS"}})  # noqa: E731

    one = agents.op("graph", "q1", CCTurn.objects.get(pk=turn.pk), plan=plan("first"))
    two = agents.op("graph", "q2", CCTurn.objects.get(pk=turn.pk), plan=plan("second"))
    three = agents.op("graph", "q3", CCTurn.objects.get(pk=turn.pk))

    assert agents.parser_calls == []
    assert (one["plan_source"], two["plan_source"], three["plan_source"]) == ("agent", "agent", "turn")
    assert [saw[2].intent_summary for saw in agents.graph_saw] == ["first", "second", "second"]


def test_a_plan_is_checked_against_the_turns_stored_vocabulary(monkeypatch):
    from NessieAI.ns.granular import OpValidationError
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary=EntityAgentOutput(lab_codes=["WAD"]).model_dump(mode="json"))

    ok = agents.op("graph", "q", CCTurn.objects.get(pk=turn.pk),
                   plan=json.dumps({"filters": {"lab_codes": ["WAD"]}}))
    assert ok["plan_source"] == "agent"
    with pytest.raises(OpValidationError) as err:
        agents.op("graph", "q", CCTurn.objects.get(pk=turn.pk), plan=json.dumps({"filters": {"lab_codes": ["XYZ"]}}))
    assert err.value.field == "plan.filters.lab_codes"
    assert agents.parser_calls == []
    assert tm.get_turn_plan(turn)["filters"]["lab_codes"] == ["WAD"], "a refused plan does not replace the turn's"


def test_a_failed_parse_never_becomes_the_turns_plan_and_a_stored_one_counts_as_none(monkeypatch):
    """parser_agent returns (does not raise) an unsupported plan with metadata.failure on a timeout or a fatal error;
    a later plan-less op parses again instead of reusing that filterless plan."""
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary=EntityAgentOutput().model_dump(mode="json"))

    def parser(session, config, text, entity):
        agents.parser_calls.append(text)
        if text == "q1":
            return ParserPlan(notes="timed out", metadata={"failure": "transport_timeout"})
        return ParserPlan(mode="graph_query", intent_summary=text)
    monkeypatch.setattr("chat_nextseek.portable.parser_agent", parser)

    first = agents.op("graph", "q1", CCTurn.objects.get(pk=turn.pk))
    assert tm.get_turn_plan(turn) is None
    second = agents.op("graph", "q2", CCTurn.objects.get(pk=turn.pk))
    assert agents.parser_calls == ["q1", "q2"]
    assert (first["plan_source"], second["plan_source"]) == ("parser", "parser")
    assert tm.get_turn_plan(turn)["intent_summary"] == "q2"

    tm.set_turn_plan(turn, ParserPlan(metadata={"failure": "parse_error"}).model_dump(mode="json"))  # an older row's
    third = agents.op("graph", "q3", CCTurn.objects.get(pk=turn.pk))
    assert (third["plan_source"], agents.parser_calls[-1]) == ("parser", "q3")


def test_an_aggregate_part_refused_for_its_lab_code_leaves_the_turns_plan_alone(monkeypatch):
    from NessieAI.ns.granular import OpValidationError
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary=EntityAgentOutput(lab_codes=["WAD"]).model_dump(mode="json"))
    agents.op("graph", "q", CCTurn.objects.get(pk=turn.pk), plan=json.dumps({"intent_summary": "kept"}))

    with pytest.raises(OpValidationError) as err:
        agents.op("aggregate", "q", CCTurn.objects.get(pk=turn.pk), plan=json.dumps({"intent_summary": "new"}),
                  parts=json.dumps([{"question": "p", "filters": {"lab_codes": ["XYZ"]}}]))
    assert err.value.field == "parts[0].filters.lab_codes"
    assert tm.get_turn_plan(turn)["intent_summary"] == "kept"
    assert agents.parser_calls == []


def test_aggregate_closes_the_connection_its_pool_thread_opened_for_the_plan(monkeypatch):
    from unittest.mock import patch
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={"keywords": ["stored"]})
    closed: list = []

    with patch("django.db.connection") as conn:
        conn.close.side_effect = lambda: closed.append(threading.current_thread().name)
        agents.op("aggregate", "How many mice by sex?", turn)

    assert closed and all(name.startswith("nextseek-aggregate") for name in closed), closed


def test_two_first_ops_at_once_make_at_most_two_parser_calls_and_leave_one_turn_plan(monkeypatch):
    """Both ops find no plan and both parse (at most one parser call per concurrent first op); the row lock leaves one
    plan stored under the turn's key; a later op reuses it."""
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    both_parsing = threading.Barrier(2, timeout=10)
    agents.on_parser = lambda text: both_parsing.wait()
    lock = serialize_turn_memory(monkeypatch)
    errors: list = []

    def worker():
        try:
            with lock:
                stale = CCTurn.objects.get(pk=turn.pk)
            agents.op("graph", "mice by sex", stale)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors

    assert agents.parser_calls == ["mice by sex", "mice by sex"]
    with lock:
        plans = CCTurn.objects.get(pk=turn.pk).plans
    assert list(plans) == [tm.TURN_PLAN_KEY]
    agents.on_parser = None
    with lock:
        row = CCTurn.objects.get(pk=turn.pk)
    agents.op("graph", "something else", row)
    assert len(agents.parser_calls) == 2, "the stored plan is reused"


def test_the_graph_op_returns_its_parser_plan(monkeypatch):
    agents = Agents(monkeypatch)
    out = agents.op("graph", "mice by sex", _turn(vocabulary={}))
    assert out["parser_plan"]["mode"] == "graph_query"
    assert out["parser_plan"]["intent_summary"] == "mice by sex"


def test_a_strike_in_op_1_is_skipped_in_op_2_on_another_worker(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    seen = {}

    def mark(query):
        scope = call_scope.current()
        if query == "op one":
            scope.mark_failed(("gcp", FLASH), reason="timeout", agent="graph_agent")
        else:
            seen["op two"] = scope.failed(("gcp", FLASH))
    agents.on_graph = mark

    for question, name in (("op one", "worker-a"), ("op two", "worker-b")):
        t = threading.Thread(target=lambda q=question: (agents.op("graph", q, CCTurn.objects.get(pk=turn.pk)),
                                                         connection.close()), name=name)
        t.start()
        t.join(20)

    assert seen["op two"]["reason"] == "timeout"
    assert tm.load_strikes(turn) == [["gcp", FLASH, "timeout"]]


def test_each_ops_spend_is_added_to_the_turn(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    agents.op("graph", "mice by sex", turn)
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal(str(round(COST, 6)))
    assert row.ops_cost_partial is False


def _broken(*args, **kwargs):
    raise RuntimeError("the database went away")


def test_a_failed_strike_write_still_adds_the_spend_and_marks_the_cost_partial(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    agents.on_graph = lambda query: call_scope.current().mark_failed(("gcp", "model-x"), reason="timeout",
                                                                     agent="graph_agent")
    monkeypatch.setattr(tm, "merge_strikes", _broken)

    out = agents.op("graph", "mice by sex", turn)

    assert out["result"] is not None, "the op still answers"
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal(str(round(COST, 6)))
    assert row.ops_cost_partial is True


def test_a_failed_spend_write_marks_the_cost_partial_and_keeps_the_strikes(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    agents.on_graph = lambda query: call_scope.current().mark_failed(("gcp", "model-x"), reason="timeout",
                                                                     agent="graph_agent")
    monkeypatch.setattr(tm, "add_spend", _broken)

    agents.op("graph", "mice by sex", turn)

    row = CCTurn.objects.get(pk=turn.pk)
    assert row.ops_cost_usd == Decimal("0")
    assert row.ops_cost_partial is True, "never a confident complete cost after a lost write"
    assert tm.load_strikes(turn) == [["gcp", "model-x", "timeout"]]


def test_two_ops_at_once_on_different_workers_lose_no_strike_plan_or_cost(monkeypatch):
    """Review focus 3 (op half): both ops load the turn's strikes before either writes anything."""
    agents = Agents(monkeypatch)
    turn = _turn(vocabulary={})
    both_started = threading.Barrier(2, timeout=10)
    agents.on_parser = lambda text: both_started.wait()
    agents.on_graph = lambda query: call_scope.current().mark_failed(("gcp", f"model-{query}"), reason="timeout",
                                                                     agent="graph_agent")
    lock = serialize_turn_memory(monkeypatch)
    errors: list = []

    def worker(question):
        try:
            with lock:
                stale = CCTurn.objects.get(pk=turn.pk)
            agents.op("graph", question, stale)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(q,)) for q in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors

    assert sorted(s[1] for s in tm.load_strikes(turn)) == ["model-a", "model-b"]
    assert tm.get_turn_plan(turn), "one of the two plans is the turn's"
    assert CCTurn.objects.get(pk=turn.pk).ops_cost_usd == Decimal(str(round(2 * COST, 6)))


def test_the_entity_op_on_the_users_question_answers_with_the_turns_vocabulary(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={"keywords": ["stored"]})
    assert agents.op("entity", " How many  mice? ", turn)["keywords"] == ["stored"]
    assert agents.op("entity", "something else", turn)["keywords"] == ["legacy"]


def test_aggregate_reads_the_turns_vocabulary_and_reuses_the_turns_plan(monkeypatch):
    agents = Agents(monkeypatch)
    turn = _turn("How many mice?", vocabulary={"keywords": ["stored"]})

    agents.op("aggregate", "How many mice by sex?", turn)
    agents.op("aggregate", "How many mice by strain?", CCTurn.objects.get(pk=turn.pk))

    assert agents.entity_calls == []
    assert agents.parser_calls == ["How many mice by sex?"]
    assert agents.graph_saw[0][1].keywords == ["stored"]
    assert tm.vocabulary_resolutions(turn) == 0
    assert CCTurn.objects.get(pk=turn.pk).ops_in_flight == 0, "nothing was late: the slot came back at once"


def test_without_a_turn_the_ops_run_as_before(monkeypatch):
    agents = Agents(monkeypatch)
    agents.op("graph", "mice by sex", None)
    assert agents.entity_calls == [("legacy", "mice by sex")]
