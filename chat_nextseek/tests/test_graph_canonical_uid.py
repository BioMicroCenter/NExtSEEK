"""`nhp.UID` must be rewritten to `nhp.uuid` before the query runs.

repro.cypher_uid_dot (task 855, seed-0 run) routed to graph correctly with both
UIDs bound as $uids, then filtered on `WHERE nhp.UID IN $uids` and returned 0 with
a confident negative. The 2026-07-24 run asked the identical question, used
`uuid`, and returned the correct six D.SEQ-220823SHA-1..6-PUB records.

The property guard is NOT at fault, and the first triage of this was wrong on that
point: `UID` really is listed under node_properties.Sample in the cached Neo4j
schema, so unknown_cypher_properties correctly did not flag it. The contradiction
is between that schema and prompts/graph_agent.txt, which states "Sample nodes have
exactly three properties: uuid, type, id" and "The canonical UID property is `uuid`
(lowercase)". The model believed the schema.

`UID` appears on no other label (Study/Investigation/SampleType carry only
title/id/description/project_id), so the rewrite is unambiguous.

Deterministic rewrite rather than another prompt line, for the same reason
repair_optional_match_filters is deterministic: the prompt already says this and
the model does it anyway.
"""
from unittest.mock import MagicMock, patch

from chat_nextseek.agents.graph import graph_agent, canonicalize_sample_uid_property
from chat_nextseek.schemas import GraphAgentPlan

# Mirrors the real cached schema, which advertises BOTH spellings on Sample.
SCHEMA = {"node_properties": {
    "Sample": ["uuid", "type", "id", "UID", "Scientist"],
    "Study": ["title", "id", "description"],
    "Investigation": ["title", "id", "project_id"],
}}


def _config():
    c = MagicMock()
    c.NEO4J_SCHEMA = SCHEMA
    c.GRAPH_AGENT_SYSTEM_PROMPT = "system prompt"
    c.PROTOCOL_SCHEMA = None
    c.ASSAY_SAMPLE_CONNECTIONS = None
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def test_uid_property_read_is_rewritten_to_uuid():
    cypher = ("MATCH (seq:Sample)-[:DERIVED_FROM*1..]->(nhp:Sample)\n"
              "WHERE nhp.UID IN $uids AND seq.type = $type\n"
              "RETURN seq.uuid AS uuid, nhp.UID AS source_uid")
    out, notes = canonicalize_sample_uid_property(cypher)

    assert ".UID" not in out
    assert "nhp.uuid IN $uids" in out
    assert "nhp.uuid AS source_uid" in out
    assert notes


def test_lowercase_uuid_is_left_alone():
    cypher = "MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS uuid"
    out, notes = canonicalize_sample_uid_property(cypher)

    assert out == cypher
    assert notes == []


def test_a_uid_inside_a_string_literal_is_not_rewritten():
    """`.UID` in a quoted value is data, not a property read."""
    cypher = "MATCH (s:Sample) WHERE s.Scientist = 'a.UID name' RETURN s"
    out, _ = canonicalize_sample_uid_property(cypher)

    assert out == cypher


def test_an_aliased_return_column_named_uid_is_not_rewritten():
    """`AS UID` names an output column; only `<var>.UID` reads a property."""
    cypher = "MATCH (s:Sample) RETURN s.uuid AS UID"
    out, _ = canonicalize_sample_uid_property(cypher)

    assert out == cypher


def test_graph_agent_applies_the_rewrite_end_to_end():
    """The task 855 shape must not reach Neo4j with `.UID` on it."""
    plan = GraphAgentPlan(
        cypher="MATCH (seq:Sample)-[:DERIVED_FROM*1..]->(nhp:Sample) "
               "WHERE nhp.UID IN $uids AND seq.type = $type RETURN seq.uuid AS uuid",
        explanation="find sequencing data",
        parameters={"type": "D.SEQ", "uids": ["NHP-220524FLY-1-PUB"]})
    with patch("chat_nextseek.agents.graph.call_llm_structured", return_value=plan):
        out = graph_agent(_config(), user_query="Find sequencing data for NHP-220524FLY-1-PUB.",
                          entity_result={})

    assert "nhp.uuid IN $uids" in out.cypher
    assert ".UID" not in out.cypher
