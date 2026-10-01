"""A fallback to the committed graph schema is loud: a WARNING naming the reason, and a field on the turn's debug.

When the live catalog cannot be read, the graph agent (and the system agent, and the ``graph-schema`` op) falls back to
the committed ``context/neo4j_schema.json``. Every downstream answer is then shaped by a capture of a graph that may no
longer exist, so the fallback must say so where an operator looks:

- a WARNING on the ``chat_nextseek.agents.graph`` logger naming why (Neo4j down, the catalog read failed, a schema
  version the reader does not accept, a defect while rendering) and how old the committed capture is;
- ``GraphAgentPlan.context_fallback`` (set by code, never shown to the model) carrying the same two facts;
- ``debug["graph_context_fallback"]`` on the turn, which the harness reads, and a ``schema_fallback`` line in the graph
  agent's ``agent_complete`` summary, which is what the debug panel shows.

A live catalog adds none of these. Every agent and tool is stubbed; nothing reaches Neo4j or a model.
"""
from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek import orchestrator as orch
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.agents import system as system_mod
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.llm_clients import pydantic_to_tool_schema
from chat_nextseek.schemas import EntityAgentOutput, GraphAgentPlan, ParserPlan, SystemAgentOutput

GRAPH_LOGGER = "chat_nextseek.agents.graph"
FETCHED_AT = "2026-08-21T00:00:00Z"
COMMITTED = {
    "fetched_at": FETCHED_AT,
    "node_properties": {"Sample": ["uuid", "type", "id"]},
    "relationship_properties": {"DERIVED_FROM": ["protocol_title"]},
}
SNAPSHOT = gcat.CatalogSnapshot(catalog_hash="h1", synced_at=None, has_usage=False, index=(), guard={})
CYPHER = "MATCH (s:Sample) RETURN count(*) AS n"


def _config():
    c = MagicMock()
    c.GRAPH_SCOPE = GraphScope.admin("test")
    c.NEO4J_SCHEMA = COMMITTED
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = {}
    c.ASSAY_SAMPLE_CONNECTIONS = {}
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _raise(exc):
    def reader(*args, **kwargs):
        raise exc
    return reader


def _catalog_fails(monkeypatch, exc):
    for name in ("get_snapshot", "get_type_details", "get_vocabulary"):
        monkeypatch.setattr(gcat, name, _raise(exc))


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: gcat.Vocabulary((), (), (), (), (), (), ()))


def _agent(monkeypatch, cypher=CYPHER):
    monkeypatch.setattr(graph_mod, "call_llm_structured",
                        lambda **kwargs: GraphAgentPlan(cypher=cypher, explanation="x", parameters={}))
    return graph_mod.graph_agent(_config(), "how many samples", {}, None)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == GRAPH_LOGGER and r.levelno == logging.WARNING]


# --- the graph agent ------------------------------------------------------------------------------------------------

REASONS = [
    pytest.param(gcat.CatalogUnavailable("graph catalog read failed: ServiceUnavailable: connection refused"),
                 "graph catalog read failed: ServiceUnavailable: connection refused", id="neo4j-down"),
    pytest.param(gcat.CatalogUnavailable("GraphMeta.schema_version is '1.0'; this reader needs 1.1 or later"),
                 "GraphMeta.schema_version is '1.0'; this reader needs 1.1 or later", id="version-too-old"),
    pytest.param(gcat.CatalogUnavailable("the graph has no GraphMeta node, so it was never synced to v1.1"),
                 "the graph has no GraphMeta node, so it was never synced to v1.1", id="never-synced"),
    pytest.param(ValueError("a renderer defect"), "graph catalog context failed: ValueError: a renderer defect",
                 id="catalog-defect"),
]


@pytest.mark.parametrize("exc, reason", REASONS)
def test_a_fallback_is_logged_as_a_warning_naming_the_reason(monkeypatch, caplog, exc, reason):
    _catalog_fails(monkeypatch, exc)
    with caplog.at_level(logging.WARNING, logger=GRAPH_LOGGER):
        plan = _agent(monkeypatch)
    assert plan.context_mode == "fallback"
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert reason in warnings[0]
    assert FETCHED_AT in warnings[0], "the warning must say how old the committed capture is"


@pytest.mark.parametrize("exc, reason", REASONS)
def test_a_fallback_plan_carries_the_reason_and_the_capture_date(monkeypatch, exc, reason):
    _catalog_fails(monkeypatch, exc)
    plan = _agent(monkeypatch)
    assert plan.context_fallback == {"unavailable_reason": reason, "fallback_fetched_at": FETCHED_AT}


def test_a_committed_file_with_no_capture_date_says_so(monkeypatch, caplog):
    _catalog_fails(monkeypatch, gcat.CatalogUnavailable("down"))
    monkeypatch.setattr(graph_mod, "call_llm_structured",
                        lambda **kwargs: GraphAgentPlan(cypher=CYPHER, explanation="x", parameters={}))
    config = _config()
    config.NEO4J_SCHEMA = {"node_properties": {"Sample": ["id"]}}
    with caplog.at_level(logging.WARNING, logger=GRAPH_LOGGER):
        plan = graph_mod.graph_agent(config, "how many samples", {}, None)
    assert plan.context_fallback == {"unavailable_reason": "down", "fallback_fetched_at": None}
    assert "unknown" in _warnings(caplog)[0]


def test_every_return_path_of_a_fallback_turn_carries_it(monkeypatch):
    _catalog_fails(monkeypatch, gcat.CatalogUnavailable("down"))
    expected = {"unavailable_reason": "down", "fallback_fetched_at": FETCHED_AT}

    assert _agent(monkeypatch, cypher="").context_fallback == expected, "the empty plan"
    bad = "MATCH (s:Sample) WHERE s.Lab = 'x' RETURN s.id"
    refused = _agent(monkeypatch, cypher=bad)
    assert refused.cypher == "" and refused.context_fallback == expected, "the guard's refusal"

    monkeypatch.setattr(graph_mod, "call_llm_structured", _raise(RuntimeError("provider down")))
    errored = graph_mod.graph_agent(_config(), "q", {}, None)
    assert errored.cypher == "" and errored.context_fallback == expected, "the model call's failure"


def test_a_live_catalog_is_silent(monkeypatch, caplog, live):
    with caplog.at_level(logging.WARNING, logger=GRAPH_LOGGER):
        plan = _agent(monkeypatch, cypher="MATCH (s:Sample) RETURN count(*) AS n")
    assert plan.context_mode == "catalog"
    assert plan.context_fallback is None
    assert _warnings(caplog) == []


def test_the_model_is_never_shown_the_fallback_field():
    assert "context_fallback" not in GraphAgentPlan.model_json_schema().get("properties", {})
    assert "context_fallback" not in str(pydantic_to_tool_schema(GraphAgentPlan))
    assert GraphAgentPlan(cypher="x").context_fallback is None


# --- the other two readers of the same fallback ---------------------------------------------------------------------


def test_the_graph_schema_op_logs_its_fallback(monkeypatch, caplog):
    _catalog_fails(monkeypatch, gcat.CatalogUnavailable("graph down"))
    with caplog.at_level(logging.WARNING, logger=GRAPH_LOGGER):
        out = graph_mod.graph_schema_snapshot(_config())
    assert out["source"] == "fallback"
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "graph down" in warnings[0] and FETCHED_AT in warnings[0]


def test_the_system_agent_logs_its_fallback(monkeypatch, caplog):
    _catalog_fails(monkeypatch, gcat.CatalogUnavailable("graph down"))
    monkeypatch.setattr(system_mod, "call_llm_structured",
                        lambda **kwargs: SystemAgentOutput(mode="get_capabilities", narrative="ok"))
    config = _config()
    config.FULL_SAMPLETYPES_MAP, config.FULL_ASSAYS_MAP, config.FULL_PROJECTS_MAP = {}, {}, {}
    config.MIN_SAMPLETYPES, config.MIN_ASSAYS, config.MIN_API_ENDPOINTS = [], [], []
    config.CAPABILITIES_DOC = "caps"
    with caplog.at_level(logging.WARNING, logger=GRAPH_LOGGER):
        system_mod.system_agent(config, "what is a tissue sample", {}, ParserPlan(mode="system_question"))
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "graph down" in warnings[0]


# --- the turn: the debug payload and the debug panel ----------------------------------------------------------------

FALLBACK = {"unavailable_reason": "graph catalog read failed: ServiceUnavailable: connection refused",
            "fallback_fetched_at": FETCHED_AT}


def _turn(monkeypatch, tmp_path, plan):
    monkeypatch.setattr(orch, "graph_agent", lambda *args, **kwargs: plan)
    monkeypatch.setattr(orch, "tool_neo4j_query",
                        lambda config, cypher, params=None: {"ok": True, "data": [{"n": 3}], "count": 1})
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *args, **kwargs: "There are 3.")
    monkeypatch.setattr(orch, "append_turn", lambda *args, **kwargs: None)
    config = MagicMock()
    config.MODEL_MODE = "test"
    events = []
    payload = orch._execute_graph_turn(
        config=config, session={}, user_text="how many", entity_result=EntityAgentOutput(),
        plan=ParserPlan(mode="graph_query", intent_summary="count"), log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda name, data: events.append((name, data)), debug_payload={},
        t_total_start=time.perf_counter(),
    )
    summaries = [data["summary"] for name, data in events
                 if name == "agent_complete" and data.get("agent") == "graph"]
    return payload, summaries


def test_a_fallback_turn_records_why_on_its_debug_payload(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher=CYPHER, context_mode="fallback", context_fallback=FALLBACK)
    payload, summaries = _turn(monkeypatch, tmp_path, plan)
    assert payload["debug"]["graph_context"] == "fallback"
    assert payload["debug"]["graph_context_fallback"] == FALLBACK
    [summary] = summaries
    assert summary["cypher"] == CYPHER
    assert FALLBACK["unavailable_reason"] in summary["schema_fallback"]
    assert FETCHED_AT in summary["schema_fallback"]


def test_a_refused_fallback_turn_still_shows_it_in_the_debug_panel(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher="", explanation="refused", context_mode="fallback", context_fallback=FALLBACK)
    payload, summaries = _turn(monkeypatch, tmp_path, plan)
    assert payload["debug"]["graph_context_fallback"] == FALLBACK
    [summary] = summaries
    assert FALLBACK["unavailable_reason"] in summary["schema_fallback"]


def test_a_catalog_turn_adds_no_fallback_field(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher=CYPHER, context_mode="catalog")
    payload, summaries = _turn(monkeypatch, tmp_path, plan)
    assert payload["debug"]["graph_context"] == "catalog"
    assert "graph_context_fallback" not in payload["debug"]
    assert summaries == [{"cypher": CYPHER, "explanation": ""}]


def test_a_refused_catalog_turn_keeps_its_empty_summary(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher="", explanation="refused", context_mode="catalog")
    _, summaries = _turn(monkeypatch, tmp_path, plan)
    assert summaries == [None]
