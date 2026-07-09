from unittest.mock import MagicMock, patch

from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.schemas import EntityAgentOutput, GraphAgentPlan


def _graph_config():
    cfg = MagicMock()
    cfg.NEO4J_SCHEMA = {}
    cfg.GRAPH_AGENT_SYSTEM_PROMPT = "sys"
    cfg.PROTOCOL_SCHEMA = {}
    cfg.ASSAY_SAMPLE_CONNECTIONS = {}
    cfg.get_agent_model.return_value = (MagicMock(), "model", 0)
    return cfg


def test_graph_agent_threads_refine_context_into_messages():
    captured = {}

    def fake_call(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return GraphAgentPlan(cypher="MATCH (s:Sample) RETURN s", explanation="x", parameters={})

    refine = "Previous graph query context:\nPrior Cypher:\nMATCH (s:Sample) RETURN s LIMIT 5"
    with patch.object(graph_mod, "call_llm_structured", side_effect=fake_call):
        graph_mod.graph_agent(
            _graph_config(), "same thing but only males", EntityAgentOutput(), {},
            refine_context=refine,
        )
    blob = "\n".join(m["content"] for m in captured["messages"])
    assert "Previous graph query context" in blob
    assert "MATCH (s:Sample) RETURN s LIMIT 5" in blob


from chat_nextseek import orchestrator as orch


def test_build_graph_refine_context_uses_prior_cypher_and_query():
    last_bundle = {
        "user_query": "samples in the GBM study",
        "mode": "graph_query",
        "graph_plan": {"cypher": "MATCH (s:Sample)-[:IN_STUDY]->(:Study {title:'GBM'}) RETURN s"},
    }
    ctx = orch._build_graph_refine_context(last_bundle)
    assert "samples in the GBM study" in ctx
    assert "MATCH (s:Sample)-[:IN_STUDY]->(:Study {title:'GBM'}) RETURN s" in ctx


def test_build_graph_refine_context_handles_missing_fields():
    ctx = orch._build_graph_refine_context({})
    assert "[none]" in ctx
