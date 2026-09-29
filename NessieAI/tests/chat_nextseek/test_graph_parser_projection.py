"""P5 (PilotAPOC/review/PROPOSALS.md): a variant with project_parser_plan hands the graph agent only the resolved
entities and filters of the parser plan, never the parser's REST-oriented prose.

In the 60 reviewed turns of run full-a the plan the graph agent received carried REST prose in three fields:
``notes`` (41 of 60 name advanced_search, 15 assert no relationship traversal is needed), ``endpoint_candidates``
(52 of 60 list a REST path) and ``intent_summary`` (1 of 60). The default path is unchanged byte for byte; only
a config copy with ``PROJECT_PARSER_PLAN is True`` projects. Every test stubs the model and the catalog.
"""
from __future__ import annotations

import json
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek import prompt_variants as pv
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan

# The shape of a real forced graph turn's plan (runs/full-a, advanced.female_mice), prose paraphrased.
PLAN = ParserPlan(
    mode="graph_query",
    target_endpoint=None,
    intent_summary="Find female mouse samples via advanced_search on the Sex attribute.",
    filters={"sampletype_code": "MUS", "keywords": ["female"]},
    resolved={"sampletypes": [{"code": "MUS", "name": "Mouse"}], "keywords": ["female"]},
    endpoint_candidates=["/nextseek_api/samples/advanced_search/"],
    notes=("Attribute search on MUS. No study/investigation scope, no relationship traversal needed, so "
           "advanced_search is the correct endpoint. | forced to graph by the evaluation switch "
           "(parser chose new_search)"),
    previous_api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "body": {"sampletype": "MUS"}},
    previous_user_query="How many mice are there?",
    metadata={"anything": "else"},
).model_dump()

PROSE = ("advanced_search", "/nextseek_api/", "no relationship traversal", "evaluation switch",
         "Find female mouse samples", "How many mice are there?", "anything")

SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(gcat.TypeIndexRow(title="MUS", label="T_MUS", name="Mouse", clade="Source", sample_count=10,
                             deprecated=False, attributes_with_values=1),),
    guard=MappingProxyType({"T_MUS": frozenset({"Sex"})}),
)
VOCAB = gcat.Vocabulary(investigation_titles=(), project_titles=(), study_titles=(), published_studies=(),
                        assay_titles=(), protocol_titles=(), assay_connections=())


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: VOCAB)


def _config(project=None):
    c = MagicMock()
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    if project is not None:
        c.PROJECT_PARSER_PLAN = project
    return c


def _upstream(monkeypatch, config, plan=PLAN, entity=None):
    seen = []

    def fake(**kwargs):
        seen.append(kwargs["messages"])
        return GraphAgentPlan(cypher="", explanation="x", parameters={})

    monkeypatch.setattr(graph_mod, "call_llm_structured", fake)
    graph_mod.graph_agent(config, "How many female mice are there?", entity or {"keywords": ["female"]}, plan)
    return seen[0][2]["content"]


# --------------------------------------------------------------------------- the projection


def test_the_projection_keeps_exactly_the_entities_and_the_filters():
    out = graph_mod.project_parser_plan(PLAN)
    assert out == {"resolved": PLAN["resolved"], "filters": PLAN["filters"]}
    assert graph_mod.PARSER_PLAN_KEPT == ("resolved", "filters")


def test_every_parser_plan_field_is_either_kept_or_dropped_on_purpose():
    """A new ParserPlan field must be sorted into one list or the other before it reaches the graph agent."""
    kept, dropped = set(graph_mod.PARSER_PLAN_KEPT), set(graph_mod.PARSER_PLAN_DROPPED)
    assert not kept & dropped
    assert kept | dropped == set(ParserPlan.model_fields)


def test_the_measured_prose_fields_are_dropped():
    for name in ("notes", "endpoint_candidates", "intent_summary", "target_endpoint", "previous_api_plan"):
        assert name in graph_mod.PARSER_PLAN_DROPPED


def test_a_plan_missing_a_kept_field_projects_what_it_has():
    assert graph_mod.project_parser_plan({"notes": "x", "filters": {"keywords": ["a"]}}) == {
        "filters": {"keywords": ["a"]}}


# --------------------------------------------------------------------------- what the graph agent receives


def test_a_projecting_config_sends_entities_and_filters_and_none_of_the_prose(monkeypatch):
    text = _upstream(monkeypatch, _config(project=True))
    assert text == (graph_mod.PROJECTED_PLAN_HEADING + "\n"
                    + json.dumps({"resolved": PLAN["resolved"], "filters": PLAN["filters"]}, indent=2))
    for phrase in PROSE:
        assert phrase not in text, phrase
    assert '"MUS"' in text and '"female"' in text


@pytest.mark.parametrize("project", [None, False])
def test_the_default_sends_the_whole_plan_byte_for_byte(monkeypatch, project):
    text = _upstream(monkeypatch, _config(project=project))
    assert text == ("PARSER PLAN (from Parser Agent — routing intent + resolved entities + filters):\n"
                    + json.dumps(PLAN, indent=2))


def test_a_mock_config_does_not_project(monkeypatch):
    """A MagicMock's PROJECT_PARSER_PLAN is a truthy mock, not True: the default path must stand."""
    text = _upstream(monkeypatch, _config())
    assert text.startswith("PARSER PLAN (from Parser Agent")


@pytest.mark.parametrize("project", [True, False])
def test_without_a_plan_the_entities_path_is_unchanged(monkeypatch, project):
    text = _upstream(monkeypatch, _config(project=project), plan=None, entity={"keywords": ["female"]})
    assert text == "RESOLVED ENTITIES (from Entity Agent — no parser plan available):\n" + json.dumps(
        {"keywords": ["female"]}, indent=2)


def test_a_variant_with_project_parser_plan_projects_end_to_end(monkeypatch, tmp_path):
    root = tmp_path / "variants"
    (root / "v2_apoc").mkdir(parents=True)
    (root / "v2_apoc" / "variant.json").write_text(json.dumps({"project_parser_plan": True}), encoding="utf-8")
    base = _config(project=False)
    base.PROMPTS_DIR = str(tmp_path)

    variant_config = pv.apply_variant(base, "v2_apoc", variants_dir=root)

    assert _upstream(monkeypatch, variant_config).startswith(graph_mod.PROJECTED_PLAN_HEADING)
    assert _upstream(monkeypatch, base).startswith("PARSER PLAN (from Parser Agent")
