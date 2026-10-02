"""Both models failed: the turn ends with the one approved text, whatever the failure was (F5.2, 2026-09-28).

Operator ruling D2 and D2b (T1): when a call's primary and its fallback both fail, the turn ends with
``MODELS_UNAVAILABLE_TRIED_TWO_REPLY``, including when both TIMED OUT. Before, a final timeout after the move left
the ladder as ``LLMTimeoutError``, which each agent turned into its own degraded answer: the parser "did not respond
in time", the graph agent "try naming the sample type..." (advice that cannot help), the system agent a line quoting
the parser's intent, the entity dropped its entities after up to 780 s, the reporter and the report writer carried on
with a guessed plan or an empty report, and the memory agent quoted both raw errors. The ladder now raises the same
``LLMFatalError(unavailable=True)`` a double 503 raises, with the move in ``model_fallback`` and ``reason`` naming the
failure that ended the call; that is a ``BaseException``, so the agents' ``except Exception`` no longer turn it into
anything, and the orchestrator's fatal handler (pinned in test_model_unavailable_reply.py) gives the text.

Three agents keep an answer built from real work: the chatter (the query ran, its count is real; its "busy" text is
unchanged), the follow-up (answers from the queries it ran, now ending with the approved outage sentence), and
seqera (its catalog-default launch plan needs no model). With no chain to move to (a profile not deployed), a final
timeout still leaves as ``LLMTimeoutError`` and the old per-agent answers, ``PLANNER_TIMEOUT_REPLY`` included, stay.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents import api as api_mod
from chat_nextseek.agents import entity as entity_mod
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.agents import memory as memory_mod
from chat_nextseek.agents import reporter as reporter_mod
from chat_nextseek.agents import seqera as seqera_mod
from chat_nextseek.agents import system as system_mod
from chat_nextseek.agents.followup import resolve_followup_outcome
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.llm_clients import LLMFatalError, LLMResponse, LLMServiceUnavailableError, LLMTimeoutError
from chat_nextseek.schemas import ParserPlan, ReportWriterPlan
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text


# --------------------------------------------------------------------------
# The ladder.
# --------------------------------------------------------------------------

class _Plan(BaseModel):
    mode: str = "unsupported"


class _Client:
    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={"stop_reason": "end_turn"})


def _config(primary, fallback, agent):
    config = MagicMock()
    config.LOG_DIR = None
    config._CATALOG_KEY = "default"
    config._THINKING_BUDGET_MAP = {None: None}
    config.LLM_MODEL = "unused"
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {agent: {"provider": "anth", "model": "fallback-1", "thinking_level": None}},
    }
    return config


@pytest.mark.parametrize("first", [LLMTimeoutError("20 s"), LLMServiceUnavailableError("503")], ids=["timeout", "503"])
def test_a_timeout_on_the_fallback_is_an_unavailable_fatal(first):
    primary = _Client("gcp", [first])
    fallback = _Client("bedrock", [LLMTimeoutError("90 s")])
    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_structured(_config(primary, fallback, "entity"), "q", _Plan, system="s", client=primary,
                            model_name="primary-model", agent_label="entity")
    fatal = excinfo.value
    assert fatal.unavailable is True
    assert fatal.reason == "timeout"
    assert [m["to"] for m in fatal.model_fallback] == ["fallback-1"]
    assert primary.calls == ["primary-model"] and fallback.calls == ["fallback-1"]


def test_the_text_path_ends_the_same_way():
    primary = _Client("gcp", [LLMTimeoutError("30 s")])
    fallback = _Client("bedrock", [LLMTimeoutError("90 s")])
    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_text(_config(primary, fallback, "chatter"), messages=[{"role": "user", "content": "hi"}],
                      client=primary, model_name="primary-model", agent_label="chatter")
    assert excinfo.value.unavailable is True and excinfo.value.reason == "timeout"


def test_with_no_chain_a_final_timeout_is_still_a_timeout():
    """The no-chain profiles keep their old per-agent answers, PLANNER_TIMEOUT_REPLY included."""
    primary = _Client("gcp", [LLMTimeoutError("t1"), LLMTimeoutError("t2")])
    config = _config(primary, primary, "entity")
    config.LLM_CLIENTS = {"gcp": primary}
    config.AGENT_MODEL_CATALOG = {}
    with pytest.raises(LLMTimeoutError):
        call_llm_structured(config, "q", _Plan, system="s", client=primary, model_name="primary-model",
                            agent_label="entity")


def test_every_fatal_the_ladder_raises_names_its_reason():
    primary = _Client("gcp", [LLMServiceUnavailableError("503")])
    fallback = _Client("bedrock", [LLMServiceUnavailableError("503 again")])
    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_structured(_config(primary, fallback, "entity"), "q", _Plan, system="s", client=primary,
                            model_name="primary-model", agent_label="entity")
    assert excinfo.value.reason == "unavailable"


def test_a_fatal_built_the_old_way_has_no_reason():
    assert LLMFatalError("boom").reason is None


# --------------------------------------------------------------------------
# The agents let it through (the orchestrator's fatal handler gives the text).
# --------------------------------------------------------------------------

BOTH_TIMED_OUT = LLMFatalError(
    "All provider fallbacks exhausted: agent 'x': timeout", agent="x", unavailable=True, reason="timeout",
    model_fallback=[{"agent": "x", "from": "gemini-3.5-flash", "to": "us.anthropic.claude-sonnet-4-6",
                     "reason": "timeout"}],
)


def _raise_both_timed_out(*args, **kwargs):
    raise BOTH_TIMED_OUT


def _lookup(expected):
    def get_agent_model(label):
        assert label == expected
        return object(), "primary-model", None
    return get_agent_model


def test_the_entity_lets_it_through(monkeypatch):
    monkeypatch.setattr(entity_mod, "call_llm_structured", _raise_both_timed_out)
    config = SimpleNamespace(ENTITY_SYSTEM_PROMPT="s", MIN_SAMPLETYPES=[], MIN_ASSAYS=[], MIN_PROJECTS=[],
                             LABS=None, get_agent_model=_lookup("entity"))
    with pytest.raises(LLMFatalError):
        entity_mod.entity_agent(config, "mice treated with NDMA", [], [], [])


def test_the_parser_lets_it_through(monkeypatch):
    """T1: a double timeout on the parser is "tried two", not PLANNER_TIMEOUT_REPLY."""
    from chat_nextseek import chat_memory
    from chat_nextseek.agents import parser as parser_mod

    monkeypatch.setattr(parser_mod, "call_llm_structured", _raise_both_timed_out)
    monkeypatch.setattr(parser_mod, "build_recent_results_summary", lambda session: "")
    monkeypatch.setattr(parser_mod, "_endpoints_for_prompt", lambda config, q: "[]")
    monkeypatch.setattr(chat_memory, "history_block", lambda session: "")
    config = SimpleNamespace(PARSER_SYSTEM_PROMPT="plan", MIN_GRAPH_SCHEMA={}, FORCE_PARSER_MODE=None,
                             get_agent_model=_lookup("parser"))
    with pytest.raises(LLMFatalError):
        parser_mod.parser_agent(object(), config, "q", {})


def test_the_graph_agent_lets_it_through(monkeypatch):
    for name in ("get_snapshot", "get_type_details", "get_vocabulary"):
        monkeypatch.setattr(gcat, name, lambda *a, **k: (_ for _ in ()).throw(gcat.CatalogUnavailable("down")))
    monkeypatch.setattr(graph_mod, "call_llm_structured", _raise_both_timed_out)
    c = MagicMock()
    c.GRAPH_SCOPE = GraphScope.admin("test")
    c.NEO4J_SCHEMA = {"fetched_at": "2026-08-21T00:00:00Z", "node_properties": {"Sample": ["uuid", "type", "id"]},
                      "relationship_properties": {}, "vocabulary": {}}
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = {"protocol_titles": []}
    c.ASSAY_SAMPLE_CONNECTIONS = {"connections": []}
    c.get_agent_model.side_effect = _lookup("graph")
    with pytest.raises(LLMFatalError):
        graph_mod.graph_agent(c, "how many tissue samples have Organ Lung", {}, None)


def test_the_api_agent_lets_it_through(monkeypatch):
    monkeypatch.setattr(api_mod, "call_llm_structured", _raise_both_timed_out)
    config = MagicMock()
    config.API_AGENT_SYSTEM_PROMPT = "sys"
    config.MIN_API_ENDPOINTS = []
    config.FALLBACK_API_ENDPOINTS = []
    config.get_schema_for_endpoint.return_value = {"method": "GET"}
    config.get_agent_model.side_effect = _lookup("api")
    with pytest.raises(LLMFatalError):
        api_mod.api_agent_build_request(config, {"target_endpoint": "/x/"})


def test_the_system_agent_lets_it_through(monkeypatch):
    monkeypatch.setattr(system_mod, "call_tools", _raise_both_timed_out)
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    monkeypatch.setattr(system_mod.graph_catalog, "committed_schema", lambda config: {})
    config = MagicMock()
    config.MIN_SAMPLETYPES = []
    config.MIN_ASSAYS = []
    config.MIN_API_ENDPOINTS = []
    config.CAPABILITIES_DOC = "caps"
    config.SYSTEM_AGENT_SYSTEM_PROMPT = "sys"
    config.get_agent_model.side_effect = _lookup("system")
    with pytest.raises(LLMFatalError):
        system_mod.system_agent(config, "what can you do", {}, ParserPlan(mode="system_question"))


def test_the_reporter_and_the_report_writer_let_it_through(monkeypatch):
    monkeypatch.setattr(reporter_mod, "call_llm_structured", _raise_both_timed_out)
    config = MagicMock()
    config.REPORTER_SYSTEM_PROMPT = "sys"
    config.REPORT_WRITER_SYSTEM_PROMPT = "sys"
    config.get_agent_model.return_value = (object(), "primary-model", None)
    with pytest.raises(LLMFatalError):
        reporter_mod.reporter_agent(config, "summarise project 2", {"report_mode": "summary"})
    with pytest.raises(LLMFatalError):
        reporter_mod.report_writer_agent(config, "write it", ReportWriterPlan(report_type="GEO"), {})


def test_the_memory_agent_lets_it_through(monkeypatch):
    """It used to answer with both raw errors quoted."""
    monkeypatch.setattr(memory_mod, "call_llm_structured", _raise_both_timed_out)
    monkeypatch.setattr(memory_mod, "call_llm_text", _raise_both_timed_out)
    config = MagicMock()
    config.LOG_DIR = None
    config.get_agent_model.return_value = (object(), "primary-model", None)
    bundle = {"id": 1, "user_query": "mice", "mode": "graph_query",
              "graph_result": {"ok": True, "count": 2, "data": [{"uid": "MUS-1"}, {"uid": "MUS-2"}]}}
    with pytest.raises(LLMFatalError):
        memory_mod.memory_agent_answer(config, "how many?", bundle, log_dir=None)


# --------------------------------------------------------------------------
# The three that keep an answer built from real work.
# --------------------------------------------------------------------------

def test_seqera_keeps_its_catalog_default_plan(monkeypatch):
    monkeypatch.setattr(seqera_mod, "call_llm_structured", _raise_both_timed_out)
    config = SimpleNamespace(SEQERA_AGENT_SYSTEM_PROMPT="s", get_agent_model=_lookup("seqera_agent"))
    plan = seqera_mod.seqera_agent(config, "run rnaseq", "rnaseq")
    assert plan.notes == "catalog default"


def test_seqera_still_lets_a_fatal_that_is_not_unavailability_through(monkeypatch):
    def _bad_request(*a, **k):
        raise LLMFatalError("Unrecoverable LLM error: 400 malformed request", agent="seqera_agent")

    monkeypatch.setattr(seqera_mod, "call_llm_structured", _bad_request)
    config = SimpleNamespace(SEQERA_AGENT_SYSTEM_PROMPT="s", get_agent_model=_lookup("seqera_agent"))
    with pytest.raises(LLMFatalError):
        seqera_mod.seqera_agent(config, "run rnaseq", "rnaseq")


FOLLOWUP_OUTAGE_ENDING = ("The AI model stopped answering before I could finish, so treat this as partial: "
                          "ask it again in a few minutes.")
STEP_LIMIT_ENDING = ("I ran out of steps before I could finish, so treat this as partial: "
                     "ask it again and I will answer it properly.")


@pytest.mark.parametrize("outcome_extra, ending", [
    ({"model_unavailable": True}, FOLLOWUP_OUTAGE_ENDING),
    ({}, STEP_LIMIT_ENDING),
], ids=["model-outage", "step-limit"])
def test_the_follow_up_ends_its_partial_answer_with_what_happened(outcome_extra, ending):
    queries = [{"question": "which have RNA", "result": {"ok": True, "count": 2, "examples": ["MUS-1"]}}]
    computes = [{"source": "stored", "result": {"ok": True, "count": 2}}]
    for outcome in ({"reply": None, "queries": queries, "computes": [], **outcome_extra},
                    {"reply": None, "queries": [], "computes": computes, **outcome_extra}):
        reply = resolve_followup_outcome(outcome)
        assert reply.endswith(ending)
        other = STEP_LIMIT_ENDING if ending == FOLLOWUP_OUTAGE_ENDING else FOLLOWUP_OUTAGE_ENDING
        assert other not in reply


def test_the_follow_up_outage_ending_is_the_approved_text():
    from chat_nextseek import failure_replies

    assert failure_replies.FOLLOWUP_MODEL_OUTAGE_PARTIAL == FOLLOWUP_OUTAGE_ENDING
    assert "\u2014" not in FOLLOWUP_OUTAGE_ENDING


def test_the_plan_mode_chatter_keeps_its_step_summary(monkeypatch):
    """The plan-mode chatter is a chatter too (D2): the plan ran, so its step summary answers when both models fail.
    A double timeout used to reach it as LLMTimeoutError, which it caught; it now arrives as LLMFatalError."""
    from chat_nextseek.agents import chatter as chatter_mod
    from chat_nextseek.schemas.planner import PlannerOutput, PlanStep

    monkeypatch.setattr(chatter_mod, "call_llm_text", _raise_both_timed_out)
    config = MagicMock()
    config.LOG_DIR = None
    config.get_agent_model.return_value = (object(), "primary-model", None)
    plan = PlannerOutput(intent_summary="mice treated with NDMA",
                         steps=[PlanStep(step_id=1, tool="graph_query", context_prompt="mice")])
    reply = chatter_mod.chatter_agent_plan(config, "mice treated with NDMA", plan,
                                           {1: {"ok": True, "output": {"count": 12, "data": []}}})
    assert reply.startswith("I executed a 1-step plan for your query: mice treated with NDMA.")
    assert "12 result(s)" in reply


def test_the_plan_mode_chatter_lets_a_bad_request_through(monkeypatch):
    from chat_nextseek.agents import chatter as chatter_mod
    from chat_nextseek.schemas.planner import PlannerOutput, PlanStep

    def _bad_request(*a, **k):
        raise LLMFatalError("Unrecoverable LLM error: 400 malformed request", agent="chatter")

    monkeypatch.setattr(chatter_mod, "call_llm_text", _bad_request)
    config = MagicMock()
    config.LOG_DIR = None
    config.get_agent_model.return_value = (object(), "primary-model", None)
    plan = PlannerOutput(intent_summary="x", steps=[PlanStep(step_id=1, tool="graph_query", context_prompt="x")])
    with pytest.raises(LLMFatalError):
        chatter_mod.chatter_agent_plan(config, "x", plan, {1: {"ok": True, "output": {"count": 1}}})
