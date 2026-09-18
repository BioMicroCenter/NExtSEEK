"""Where a prompt variant reaches the pipeline beyond the prompts themselves.

- ``graph_schema_structure.txt``: ``graph_context.render_graph_context`` takes the structure text, and the graph
  agent passes the variant's (``GRAPH_SCHEMA_STRUCTURE`` on the config copy); a default config sends the file.
- The record: every NS turn's debug payload carries ``prompt_variant`` and ``prompt_variant_files`` (both None
  on a default turn), beside the ``parser_plan`` whose ``mode`` is the route the parser chose, so a scorer reads
  both off the same ``query_complete`` payload.

Everything is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek import graph_context as gctx
from chat_nextseek import orchestrator
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.schemas import EntityAgentOutput, GraphAgentPlan, ParserPlan

SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(gcat.TypeIndexRow(title="TIS", label="T_TIS", name="Tissue", clade="Source", sample_count=10,
                             deprecated=False, attributes_with_values=1),),
    guard=MappingProxyType({"T_TIS": frozenset({"Organ"})}),
)
VOCAB = gcat.Vocabulary(investigation_titles=(), project_titles=(), study_titles=(), published_studies=(),
                        assay_titles=(), protocol_titles=(), assay_connections=())


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: VOCAB)


def _graph_config():
    c = MagicMock()
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _schema_message(monkeypatch, config):
    seen = {}

    def fake(**kwargs):
        seen["messages"] = kwargs["messages"]
        return GraphAgentPlan(cypher="", explanation="x", parameters={})

    monkeypatch.setattr(graph_mod, "call_llm_structured", fake)
    graph_mod.graph_agent(config, "how many tissue samples", {}, None)
    return seen["messages"][1]["content"]


# --------------------------------------------------------------------------- graph_schema_structure.txt


def test_render_graph_context_sends_the_file_by_default():
    text = gctx.render_graph_context(SNAPSHOT, [])
    assert text.startswith(gctx.load_structure())


def test_render_graph_context_sends_a_given_structure_instead():
    text = gctx.render_graph_context(SNAPSHOT, [], structure="V2 STRUCTURE")
    assert text.startswith("V2 STRUCTURE")
    assert gctx.load_structure() not in text


def test_a_default_config_sends_the_structure_file(monkeypatch, live):
    schema = _schema_message(monkeypatch, _graph_config())
    assert gctx.load_structure() in schema


def test_a_variant_config_sends_its_structure(monkeypatch, live):
    config = _graph_config()
    config.GRAPH_SCHEMA_STRUCTURE = "V2 STRUCTURE TEXT"
    schema = _schema_message(monkeypatch, config)
    assert "V2 STRUCTURE TEXT" in schema
    assert gctx.load_structure() not in schema


def test_the_graph_schema_op_uses_the_same_structure(live):
    config = MagicMock()
    config.GRAPH_SCHEMA_STRUCTURE = "V2 STRUCTURE TEXT"
    assert graph_mod.graph_schema_snapshot(config)["schema"].startswith("V2 STRUCTURE TEXT")
    assert graph_mod.graph_schema_snapshot(MagicMock())["schema"].startswith(gctx.load_structure())


# --------------------------------------------------------------------------- the debug record


def _run_query(config):
    """One NS turn whose parser answers `unsupported`, the earliest return that carries the debug payload."""
    with patch.object(orchestrator.pipeline_agent, "is_active", return_value=False), \
            patch.object(orchestrator, "_ensure_query_log_dir", return_value="/tmp/log"), \
            patch.object(orchestrator, "ArtifactStore"), \
            patch.object(orchestrator, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orchestrator, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orchestrator, "parser_agent",
                         return_value=ParserPlan(mode="unsupported", notes="not a data question")), \
            patch.object(orchestrator, "append_turn"):
        return orchestrator.run_query({}, config, "hello")


class _Config:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []


def test_a_default_turn_records_no_variant():
    payload = _run_query(_Config())
    assert payload["debug"]["prompt_variant"] is None
    assert payload["debug"]["prompt_variant_files"] is None
    assert payload["debug"]["parser_plan"]["mode"] == "unsupported"


def test_a_variant_turn_records_its_name_and_files():
    config = _Config()
    config.PROMPT_VARIANT = "v2"
    config.PROMPT_VARIANT_FILES = {"graph_agent.txt": "v2/graph_agent.txt"}

    payload = _run_query(config)

    assert payload["debug"]["prompt_variant"] == "v2"
    assert payload["debug"]["prompt_variant_files"] == {"graph_agent.txt": "v2/graph_agent.txt"}
    assert payload["debug"]["parser_plan"]["mode"] == "unsupported"
