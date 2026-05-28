from unittest.mock import MagicMock, patch

from chat_nextseek.agents.graph import (
    graph_agent,
    known_node_properties,
    known_relationship_properties,
    unknown_cypher_properties,
)
from chat_nextseek.schemas import GraphAgentPlan

SCHEMA = {"node_properties": {
    "Sample": ["UID", "type", "Organ", "Treatment"],
    "Study": ["title"],
    "Investigation": ["title"],
}}

REL_SCHEMA = {
    "node_properties": {"Sample": ["UID", "type", "Organ"]},
    "relationship_properties": {"DERIVED_FROM": ["internal_assay_title", "protocol_title"]},
}


def test_known_node_properties_union():
    props = known_node_properties(SCHEMA)
    assert {"UID", "type", "Organ", "title"} <= props
    assert "Lab" not in props


def test_unknown_property_flagged():
    cypher = "MATCH (s:Sample) WHERE toLower(s.Lab) CONTAINS 'kam' RETURN count(s)"
    assert unknown_cypher_properties(cypher, known_node_properties(SCHEMA)) == ["Lab"]


def test_real_properties_pass():
    cypher = "MATCH (s:Sample) WHERE s.type = 'OOC' AND s.Organ = 'lung' RETURN count(s)"
    assert unknown_cypher_properties(cypher, known_node_properties(SCHEMA)) == []


def test_known_relationship_properties_union():
    props = known_relationship_properties(REL_SCHEMA)
    assert props == {"internal_assay_title", "protocol_title"}


def test_known_relationship_properties_typed_map_shape():
    """Robustness: the {prop: type} shape (relationship_property_types) is also covered."""
    schema = {
        "relationship_property_types": {
            "DERIVED_FROM": {"internal_assay_title": ["String"], "protocol_id": ["Long"]},
        }
    }
    assert known_relationship_properties(schema) == {"internal_assay_title", "protocol_id"}


def test_relationship_property_not_flagged_with_combined_known():
    cypher = "MATCH (a)-[r:DERIVED_FROM]->(b) WHERE r.internal_assay_title = $x RETURN a"
    known = known_node_properties(REL_SCHEMA) | known_relationship_properties(REL_SCHEMA)
    assert unknown_cypher_properties(cypher, known) == []


def _config(schema=SCHEMA):
    c = MagicMock()
    c.NEO4J_SCHEMA = schema
    c.GRAPH_AGENT_SYSTEM_PROMPT = "system prompt"
    c.PROTOCOL_SCHEMA = None
    c.ASSAY_SAMPLE_CONNECTIONS = None
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def test_graph_agent_rejects_persistently_invented_property():
    """If both the first and the repair attempt reference an unknown property,
    graph_agent returns a graceful empty-cypher plan rather than running it."""
    bad = GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.Lab='x' RETURN s",
                         explanation="bad", parameters={})
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=bad):
        out = graph_agent(_config(), user_query="organ on chips in the kamm lab",
                          entity_result={})
    assert out.cypher == ""
    assert "Lab" in out.explanation


def test_graph_agent_returns_repaired_cypher_on_successful_repair():
    """First attempt uses an unknown property; the single repair returns valid
    Cypher, which graph_agent returns (not empty, not the bad one)."""
    bad = GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.Lab='x' RETURN s",
                         explanation="bad", parameters={})
    good = GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.type='OOC' RETURN count(s)",
                          explanation="ok", parameters={})
    with patch("chat_nextseek.agents.graph.call_llm_structured", side_effect=[bad, good]):
        out = graph_agent(_config(), user_query="how many OOC in the kamm lab", entity_result={})
    assert out.cypher == good.cypher


def test_graph_agent_keeps_valid_cypher():
    good = GraphAgentPlan(cypher="MATCH (s:Sample) WHERE s.type='OOC' RETURN count(s)",
                          explanation="ok", parameters={})
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=good):
        out = graph_agent(_config(), user_query="how many OOC samples",
                          entity_result={})
    assert out.cypher == good.cypher


def test_graph_agent_keeps_valid_relationship_property_cypher():
    """A Cypher that reads a real DERIVED_FROM relationship property
    (r.internal_assay_title) must not be flagged as unknown and rejected."""
    good = GraphAgentPlan(
        cypher="MATCH (a:Sample)-[r:DERIVED_FROM]->(b:Sample) "
               "WHERE r.internal_assay_title = $assay RETURN a",
        explanation="ok", parameters={"assay": "RNA-seq"})
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=good):
        out = graph_agent(_config(REL_SCHEMA), user_query="samples from RNA-seq assay",
                          entity_result={})
    assert out.cypher == good.cypher
