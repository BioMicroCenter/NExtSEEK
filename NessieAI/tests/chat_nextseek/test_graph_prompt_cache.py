"""T2 (2026-10-12): a 1h Bedrock cache point after the graph agent's static head.

The graph agent's prompt and the schema head (heading, structure, sample type index) are the same for every
question; the resolved types' sections, the parser plan, the vocabulary and the question vary. The marker is the
``cache_point`` key on the head message, the parser's T6 mechanism (``BedrockClient._convert_messages`` joins the
system messages with a blank line and puts the cache point after the marked one; ``_call_with_recovery`` strips the
marker on a move). The two schema parts joined with a blank line equal the one schema message the agent read before.
No network: ``call_llm_structured`` is a stub that records the messages.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_context
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.schemas.graph import GraphAgentPlan

HEADING = graph_context.RESOLVED_TYPES_HEADING
STRUCTURE = "# Graph structure\n\n(:Sample)-[:DERIVED_FROM]->(:Sample)"
INDEX = "## Sample type index\n\nTIS Tissue Sample n=1000\nNHP Non Human Primate n=50"


def _schema(sections: list[str] | None) -> str:
    """What graph_context._assemble renders: structure, index, then the heading and the sections when any resolved."""
    parts = [STRUCTURE, INDEX]
    if sections:
        parts.append(HEADING + "Tissue Sample (attribute names only)")
        parts += sections
    return "\n\n".join(parts) + "\n"


def _context(sections):
    return graph_mod.CatalogContext(snapshot=SimpleNamespace(schema_version="1.3"), schema=_schema(sections),
                                    vocabulary="VOCAB")


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _fake(**kw):
        calls.append([dict(m) for m in kw["messages"]])
        return GraphAgentPlan(cypher="MATCH (s:Sample) RETURN count(s)", explanation="x")

    monkeypatch.setattr(graph_mod, "call_llm_structured", _fake)
    return calls


def _run(monkeypatch, question, sections, plan):
    config = MagicMock()
    config.GRAPH_AGENT_SYSTEM_PROMPT = "PROMPT"
    config.PROJECT_PARSER_PLAN = False
    config.get_agent_model.return_value = (object(), "model", None)
    monkeypatch.setattr(graph_mod, "resolve_catalog_context", lambda *a, **k: _context(sections))
    try:
        graph_mod.graph_agent(config, question, {"keywords": [question]}, plan)
    except Exception:  # a guard after the call may trip on the stub's plan; only the request matters here
        pass


PLAN_A = {"resolved": {"sample_types": ["TIS"]}, "filters": {"organ": "lung"}}
PLAN_B = {"resolved": {"sample_types": ["NHP"]}, "filters": {}}


def test_the_head_carries_the_marker_and_the_sections_follow_it(sent, monkeypatch):
    _run(monkeypatch, "lung tissue", ["### Tissue Sample\n\nOrgan [str] n=900"], PLAN_A)
    msgs = sent[0]
    assert msgs[0] == {"role": "system", "content": "PROMPT"}
    assert msgs[1]["cache_point"] == "1h"
    assert msgs[1]["content"] == graph_mod.schema_heading("1.3") + STRUCTURE + "\n\n" + INDEX
    assert msgs[2]["content"].startswith(HEADING) and msgs[2]["content"].endswith("Organ [str] n=900\n")
    assert msgs[3]["content"].startswith("PARSER PLAN")
    assert msgs[4]["content"].startswith(graph_mod.VOCABULARY_HEADING)
    assert msgs[-1] == {"role": "user", "content": "lung tissue"}
    assert [i for i, m in enumerate(msgs) if m.get("cache_point")] == [1]


def test_the_head_is_byte_equal_across_questions_plans_and_resolved_types(sent, monkeypatch):
    _run(monkeypatch, "lung tissue", ["### Tissue Sample\n\nOrgan [str] n=900"], PLAN_A)
    _run(monkeypatch, "monkeys", ["### Non Human Primate\n\nSpecies [str] n=50"], PLAN_B)
    assert json.dumps(sent[0][:2]) == json.dumps(sent[1][:2])
    assert sent[0][2]["content"] != sent[1][2]["content"]


def test_with_no_resolved_type_the_whole_schema_message_carries_the_marker(sent, monkeypatch):
    _run(monkeypatch, "anything", None, None)
    msgs = sent[0]
    assert msgs[1]["cache_point"] == "1h"
    assert msgs[1]["content"] == graph_mod.schema_heading("1.3") + _schema(None)
    assert msgs[2]["content"].startswith("RESOLVED ENTITIES")
    assert sum(1 for m in msgs if m.get("cache_point")) == 1


def test_the_two_parts_joined_by_a_blank_line_equal_the_one_schema_message(sent, monkeypatch):
    sections = ["### Tissue Sample\n\nOrgan [str] n=900", "Left out to fit the context budget: NHP (see the type index)."]
    _run(monkeypatch, "lung tissue", sections, PLAN_A)
    msgs = sent[0]
    assert msgs[1]["content"] + "\n\n" + msgs[2]["content"] == graph_mod.schema_heading("1.3") + _schema(sections)
