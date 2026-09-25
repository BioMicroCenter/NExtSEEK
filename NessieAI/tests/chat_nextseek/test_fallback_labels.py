"""The graph, API, system and plan-evaluator calls find their provider chain.

``call_llm_structured`` looks the fallback chain up by ``agent_label or log_label``, and
these four passed only a log label that is not a catalog key (``graph_agent``,
``graph_agent_repair``, ``api_agent``, ``system_agent``, ``plan_evaluator``). The chain
lookup found no agent of that name in any profile and returned nothing, so a timeout, a
503 or an empty body on the graph agent, which runs on almost every NS turn, never
moved to another model (operator ruling 2026-09-25: they move to their chain's first
entry, Sonnet 4.6 under the shipped profile).

Each call now passes its catalog key as ``agent_label`` and keeps its log label, which
is still the name the ledger records. The primary model and client are the ones
``get_agent_model(<catalog key>)`` returns, exactly as before.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents import api as api_mod
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.agents import system as system_mod
from chat_nextseek.agents.planner import evaluator as evaluator_mod
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.llm_clients import LLMResponse, LLMServiceUnavailableError
from chat_nextseek.schemas import (
    APIRequestPlan,
    GraphAgentPlan,
    ParserPlan,
    PlanEvaluatorOutput,
    SystemAgentOutput,
)
from chat_nextseek.schemas.schema_helper import call_llm_structured

PRIMARY_CLIENT = object()
PRIMARY_MODEL = "primary-model-from-get_agent_model"


class _Capture:
    """Stands in for call_llm_structured and records the keyword arguments of each call."""

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _model_lookup(expected_key):
    def get_agent_model(label):
        assert label == expected_key, f"the primary is looked up under {label!r}, not {expected_key!r}"
        return PRIMARY_CLIENT, PRIMARY_MODEL, None
    return get_agent_model


def _assert_labelled(call, *, key, log_label):
    assert call["agent_label"] == key, "the chain is looked up by the catalog key"
    assert call["log_label"] == log_label, "the ledger keeps the log label"
    assert call["client"] is PRIMARY_CLIENT, "the primary client is unchanged"
    assert call["model_name"] == PRIMARY_MODEL, "the primary model is unchanged"


# ---------------------------------------------------------------------------- the four calls

def test_the_api_agent_passes_its_catalog_key(monkeypatch):
    capture = _Capture(APIRequestPlan(endpoint="/x/", method="GET", requestBody={}, queryParameters={}, notes=""))
    monkeypatch.setattr(api_mod, "call_llm_structured", capture)
    config = MagicMock()
    config.API_AGENT_SYSTEM_PROMPT = "sys"
    config.MIN_API_ENDPOINTS = []
    config.FALLBACK_API_ENDPOINTS = []
    config.get_schema_for_endpoint.return_value = {"method": "GET"}
    config.get_agent_model.side_effect = _model_lookup("api")

    api_mod.api_agent_build_request(config, {"target_endpoint": "/x/"})

    _assert_labelled(capture.calls[0], key="api", log_label="api_agent")


def test_the_system_agent_passes_its_catalog_key(monkeypatch):
    capture = _Capture(SystemAgentOutput(mode="get_capabilities", narrative="ok"))
    monkeypatch.setattr(system_mod, "call_llm_structured", capture)
    monkeypatch.setattr(system_mod, "live_catalog_context", lambda *a, **k: None)
    monkeypatch.setattr(system_mod.graph_catalog, "committed_schema", lambda config: {})
    config = MagicMock()
    config.MIN_SAMPLETYPES = []
    config.MIN_ASSAYS = []
    config.MIN_API_ENDPOINTS = []
    config.CAPABILITIES_DOC = "caps"
    config.SYSTEM_AGENT_SYSTEM_PROMPT = "sys"
    config.get_agent_model.side_effect = _model_lookup("system")

    system_mod.system_agent(config, "what can you do", {}, ParserPlan(mode="system_question"))

    _assert_labelled(capture.calls[0], key="system", log_label="system_agent")


def test_the_plan_evaluator_passes_its_catalog_key(monkeypatch):
    capture = _Capture(PlanEvaluatorOutput(answered_query=True, execution_consistent=True,
                                           overall_status="success"))
    monkeypatch.setattr(evaluator_mod, "call_llm_structured", capture)
    config = MagicMock()
    config.EVALUATOR_V1_SYSTEM_PROMPT = "sys"
    config.get_agent_model.side_effect = _model_lookup("evaluator")

    evaluator_mod.plan_evaluator_agent(config, "q", {}, {}, {}, {})

    _assert_labelled(capture.calls[0], key="evaluator", log_label="plan_evaluator")


GOOD = "MATCH (s:T_TIS) WHERE s.Organ = $organ RETURN s.id AS id, s.uuid AS uuid, s.type AS type"


def _graph_config():
    c = MagicMock()
    c.GRAPH_SCOPE = GraphScope.admin("test")
    c.NEO4J_SCHEMA = {"fetched_at": "2026-08-21T00:00:00Z",
                      "node_properties": {"Sample": ["uuid", "type", "id"]},
                      "relationship_properties": {}, "vocabulary": {}}
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = {"protocol_titles": []}
    c.ASSAY_SAMPLE_CONNECTIONS = {"connections": []}
    c.get_agent_model.side_effect = _model_lookup("graph")
    return c


def _catalog_down(*args, **kwargs):
    raise gcat.CatalogUnavailable("graph down in this test")


def test_the_graph_agent_passes_its_catalog_key(monkeypatch):
    for name in ("get_snapshot", "get_type_details", "get_vocabulary"):
        monkeypatch.setattr(gcat, name, _catalog_down)
    capture = _Capture(GraphAgentPlan(cypher=GOOD, explanation="x", parameters={}))
    monkeypatch.setattr(graph_mod, "call_llm_structured", capture)

    graph_mod.graph_agent(_graph_config(), "how many tissue samples have Organ Lung", {}, None)

    assert capture.calls, "the graph agent never called the model"
    _assert_labelled(capture.calls[0], key="graph", log_label="graph_agent")


def test_every_graph_agent_call_site_passes_the_catalog_key():
    """The graph agent calls the model through one nested helper for its first call and
    its repair call (log labels graph_agent and graph_agent_repair); the helper must carry
    the catalog key for both."""
    import inspect

    src = inspect.getsource(graph_mod.graph_agent)
    helper = src[src.index("def call(prompt: str, log_label: str)"):]
    helper = helper[:helper.index("\n    try:")]
    assert 'agent_label="graph"' in helper
    assert "log_label=log_label" in helper


# ---------------------------------------------------------------------------- the move itself

class _Client:
    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={"stop_reason": "end_turn"})


@pytest.mark.parametrize("key, log_label", [
    ("graph", "graph_agent"), ("graph", "graph_agent_repair"), ("api", "api_agent"),
    ("system", "system_agent"), ("evaluator", "plan_evaluator"),
])
def test_a_503_moves_to_the_catalog_keys_chain_and_the_ledger_keeps_the_log_label(tmp_path, key, log_label):
    primary = _Client("gcp", [LLMServiceUnavailableError("503 UNAVAILABLE")])
    fallback = _Client("bedrock", ['{"mode": "graph_query"}'])
    config = MagicMock()
    config.LOG_DIR = str(tmp_path)
    config._CATALOG_KEY = "default"
    config._THINKING_BUDGET_MAP = {None: None}
    config.LLM_MODEL = "unused"
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {key: {"provider": "anth", "model": "sonnet-fallback", "thinking_level": None}},
    }

    plan = call_llm_structured(config, "q", ParserPlan, system="s", client=primary, model_name="flash",
                               agent_label=key, log_label=log_label)

    assert plan.mode == "graph_query"
    assert fallback.calls == ["sonnet-fallback"]
    ledger = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert [e["agent"] for e in ledger] == [log_label, log_label]
    assert [e["model"] for e in ledger] == ["flash", "sonnet-fallback"]


def test_a_log_label_alone_still_names_both_the_chain_and_the_ledger(tmp_path):
    """Every other structured agent passes a log label that IS its catalog key (parser,
    entity, reporter, ...): nothing about those calls changes."""
    primary = _Client("gcp", [LLMServiceUnavailableError("503")])
    fallback = _Client("bedrock", ['{"mode": "graph_query"}'])
    config = MagicMock()
    config.LOG_DIR = str(tmp_path)
    config._CATALOG_KEY = "default"
    config._THINKING_BUDGET_MAP = {None: None}
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {"entity": {"provider": "anth", "model": "sonnet-fallback", "thinking_level": None}},
    }

    call_llm_structured(config, "q", ParserPlan, system="s", client=primary, model_name="flash",
                        log_label="entity")

    ledger = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    assert {e["agent"] for e in ledger} == {"entity"}
    assert fallback.calls == ["sonnet-fallback"]
