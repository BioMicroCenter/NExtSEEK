"""SCH-F13: a committed-schema fallback is rendered in the live schema's shape, so it differs from a live turn only in
freshness.

When the live catalog cannot be read, the graph agent's schema message and the ``graph-schema`` op's ``schema`` used
to be ``json.dumps`` of the committed ``context/neo4j_schema.json``, a shape no live turn ever sends. Both now read one
renderer (``graph_mod._render_committed_schema``): the structure text, a sample type index built from the committed
file's ``vocabulary.sampletype_titles``, and the committed Sample property names as one names-only block, within
``graph_context.BUDGET_BYTES``. Only ``sampletype_titles`` is read from the committed ``vocabulary``, for every caller:
project and study titles were read over every project and never reach the schema text.

The fallback guard also knows every relationship property of ``V11_RELATIONSHIP_PROPERTIES``, so a committed file
captured before ``DERIVED_FROM.internal_assay_titles`` existed does not refuse a query that reads it.

The committed file here is a small fixture shaped like the v1.2 capture. Every test drives a fake LLM client and a
patched catalog reader; nothing reaches Neo4j or a model.
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek import graph_context as gctx
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.schemas import GraphAgentPlan

LIVE_HEADING = ("GRAPH SCHEMA (v1.2 structure, sample type index and the resolved sample types; this is the "
                "schema):\n")
FETCHED_AT = "2026-09-01T00:00:00Z"
CODES = ("D.SEQ", "MUS", "T.TIS")
INDEX_LINES = ("D.SEQ :T_D_SEQ, sample count unknown", "MUS :T_MUS, sample count unknown",
               "T.TIS :T_T_TIS, sample count unknown")
PROPERTIES_HEADING = "## Sample properties (names only)"
COMMITTED = {
    "fetched_at": FETCHED_AT,
    "schema_version": "1.2",
    "node_labels": ["Sample", "SampleType", "Project", "Study"],
    "relationship_types": ["DERIVED_FROM", "OF_TYPE", "IN_PROJECT", "IN_STUDY"],
    "node_properties": {
        "Sample": ["id", "uuid", "type", "Organ", "Analyte_Catalog#", "2nd_Pass", "Odd`Name"],
        "SampleType": ["title", "label"],
    },
    # no internal_assay_titles: a capture from before the plural list (the guard fold-in below)
    "relationship_properties": {"DERIVED_FROM": ["internal_assay_title", "protocol_title"]},
    "vocabulary": {"project_titles": ["ProjX"], "study_titles": ["StudyY"], "sampletype_titles": list(CODES)},
}
PROPERTY_BLOCK = PROPERTIES_HEADING + "\nid, uuid, type, Organ, `Analyte_Catalog#`, `2nd_Pass`, `Odd``Name`"
FOREIGN = ("ProjX", "StudyY")

ADMIN = GraphScope.admin("test")
NOT_ADMIN = {
    "project_2": GraphScope.for_projects([2], source="test"),
    "no_projects": GraphScope.for_projects([], source="test"),
    "no_scope": None,
}


def _config(scope=ADMIN, committed=COMMITTED):
    c = MagicMock()
    c.GRAPH_SCOPE = scope
    c.NEO4J_SCHEMA = committed
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = {"protocol_titles": ["Old protocol"]}
    c.ASSAY_SAMPLE_CONNECTIONS = {"connections": [{"assay": "Old assay", "parent_type": "RNA",
                                                   "child_type": "D.SEQ"}]}
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _unavailable(*args, **kwargs):
    raise gcat.CatalogUnavailable("graph down in this test")


@pytest.fixture
def down(monkeypatch):
    for name in ("get_snapshot", "get_type_details", "get_vocabulary"):
        monkeypatch.setattr(gcat, name, _unavailable)


class FakeLLM:
    """Returns the given plans in order (the last one repeats) and records every call."""

    def __init__(self, *cyphers):
        self.cyphers = list(cyphers)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        cypher = self.cyphers[min(len(self.calls), len(self.cyphers)) - 1]
        return GraphAgentPlan(cypher=cypher, explanation="model explanation", parameters={})

    def schema_message(self, call=0):
        return self.calls[call]["messages"][1]["content"]

    def blob(self, call=0):
        return "\n".join(m["content"] for m in self.calls[call]["messages"])


def _index_lines(text):
    """The lines under the type index heading, up to the next blank line."""
    return text.split(gctx.render_type_index(()) + "\n", 1)[1].split("\n\n", 1)[0].splitlines()


def _turn(monkeypatch, config, cypher="MATCH (s:Sample) RETURN count(*) AS n",
          query="which study, protocol and assay made these samples"):
    llm = FakeLLM(cypher)
    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    plan = graph_mod.graph_agent(config, query, {}, None)
    return plan, llm


# --- the graph agent's schema message -------------------------------------------------------------------------------


def test_the_fallback_message_is_the_live_shape_not_a_json_dump(monkeypatch, down):
    plan, llm = _turn(monkeypatch, _config())
    assert plan.context_mode == "fallback"
    message = llm.schema_message()
    assert message.startswith(LIVE_HEADING), "the heading is the live one: the renderer sends the same sections"
    assert gctx.load_structure() in message
    assert gctx.render_type_index(()) in message, "the index heading is the live one"
    for line in INDEX_LINES:
        assert line in message, line
    assert PROPERTY_BLOCK in message
    assert "node_properties" not in message and '"vocabulary"' not in message, "no JSON dump"
    assert message.index(gctx.load_structure()) < message.index(INDEX_LINES[0]) < message.index(PROPERTIES_HEADING)


def test_the_fallback_message_is_the_renderer_text_under_the_live_heading(monkeypatch, down):
    config = _config()
    _, llm = _turn(monkeypatch, config)
    assert llm.schema_message() == LIVE_HEADING + graph_mod._render_committed_schema(config)


def test_the_fallback_message_sends_a_variant_structure(monkeypatch, down):
    config = _config()
    config.GRAPH_SCHEMA_STRUCTURE = "V2 STRUCTURE TEXT"
    _, llm = _turn(monkeypatch, config)
    message = llm.schema_message()
    assert message.startswith(LIVE_HEADING + "V2 STRUCTURE TEXT\n\n")
    assert gctx.load_structure() not in message
    assert INDEX_LINES[0] in message and PROPERTY_BLOCK in message


def test_an_index_line_claims_no_attribute_count_it_cannot_know():
    # The committed file does not know how many attributes of a type hold a value; render_type_index would write
    # "0 attributes with values" for every row, telling the model every type is empty.
    text = graph_mod._render_committed_schema(_config())
    assert _index_lines(text) == list(INDEX_LINES)
    assert "0 attributes with values" not in text


def test_the_index_keeps_the_files_order_and_skips_what_is_not_a_code():
    committed = dict(COMMITTED, vocabulary={"sampletype_titles": ["MUS", "", None, 7, "D.SEQ", "MUS", " TIS "]})
    text = graph_mod._render_committed_schema(_config(committed=committed))
    assert _index_lines(text) == [
        "MUS :T_MUS, sample count unknown", "D.SEQ :T_D_SEQ, sample count unknown", "TIS :T_TIS, sample count unknown"]


# --- the graph-schema op --------------------------------------------------------------------------------------------


def test_the_op_fallback_schema_is_the_same_renderer(monkeypatch, down):
    config = _config()
    out = graph_mod.graph_schema_snapshot(config, types=["TIS"], question="which protocol")
    assert out["schema"] == graph_mod._render_committed_schema(config)
    _, llm = _turn(monkeypatch, config)
    assert llm.schema_message() == LIVE_HEADING + out["schema"], "the op and the agent read the same text"


def test_the_op_fallback_keeps_every_other_key(down):
    out = graph_mod.graph_schema_snapshot(_config(), types=["TIS", "NOPE"], question="which protocol")
    schema = out.pop("schema")
    assert "node_properties" not in schema
    assert out == {
        "source": "fallback",
        "schema_version": None,
        "catalog_hash": None,
        "synced_at": None,
        "sample_types": 0,
        "resolved_types": [],
        "unknown_types": ["TIS", "NOPE"],
        "vocabulary": out["vocabulary"],
        "unavailable_reason": "graph down in this test",
        "fallback_fetched_at": FETCHED_AT,
    }
    assert "Old protocol" in out["vocabulary"], "an admin's committed vocabulary blocks are unchanged"


# --- who is shown what ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scope", list(NOT_ADMIN.values()), ids=list(NOT_ADMIN))
def test_a_non_admin_is_shown_the_type_codes_and_no_other_vocabulary(monkeypatch, down, scope):
    config = _config(scope)
    _, llm = _turn(monkeypatch, config)
    out = graph_mod.graph_schema_snapshot(config, types=["TIS"], question="which study, protocol and assay")
    for text in (llm.schema_message(), out["schema"]):
        for line in INDEX_LINES:
            assert line in text, line
        assert PROPERTY_BLOCK in text
    for text in (llm.blob(), json.dumps(out)):
        for value in FOREIGN + ("Old protocol", "Old assay"):
            assert value not in text, value


def test_an_admin_schema_text_reads_only_the_type_codes_from_the_vocabulary(monkeypatch, down):
    # Ruling: the renderer reads sampletype_titles and nothing else from the committed vocabulary, for every caller.
    config = _config(ADMIN)
    _, llm = _turn(monkeypatch, config)
    out = graph_mod.graph_schema_snapshot(config, question="which study")
    for text in (llm.schema_message(), out["schema"]):
        for value in FOREIGN:
            assert value not in text, value


# --- a missing or malformed committed file --------------------------------------------------------------------------

MALFORMED = {
    "none": None,
    "empty": {},
    "not_a_dict": ["Sample"],
    "no_vocabulary": {"node_properties": {"Sample": ["id"]}},
    "vocabulary_not_a_dict": {"vocabulary": ["D.SEQ"]},
    "no_sampletype_titles": {"vocabulary": {"project_titles": ["ProjX"]}},
    "sampletype_titles_not_a_list": {"vocabulary": {"sampletype_titles": "D.SEQ"}},
    "no_node_properties": {"vocabulary": {"sampletype_titles": ["D.SEQ"]}},
    "node_properties_not_a_dict": {"node_properties": ["id"]},
    "sample_properties_not_a_list": {"node_properties": {"Sample": "id"}},
    "a_magicmock": MagicMock(),
}


@pytest.mark.parametrize("committed", list(MALFORMED.values()), ids=list(MALFORMED))
def test_a_missing_or_malformed_file_renders_without_raising(committed):
    text = graph_mod._render_committed_schema(_config(committed=committed))
    assert text.startswith(gctx.load_structure() + "\n\n" + gctx.render_type_index(()))
    assert "ProjX" not in text


def test_a_file_with_no_type_codes_says_so():
    text = graph_mod._render_committed_schema(_config(committed={}))
    assert gctx.render_type_index(()) + "\nThe committed capture lists no sample types." in text
    assert PROPERTIES_HEADING not in text, "no property names, no property block"


@pytest.mark.parametrize("committed", [None, {}, MagicMock()], ids=["none", "empty", "a_magicmock"])
def test_the_agent_and_the_op_survive_a_missing_file(monkeypatch, down, committed):
    config = _config(committed=committed)
    plan, llm = _turn(monkeypatch, config)
    assert plan.context_mode == "fallback"
    assert llm.schema_message().startswith(LIVE_HEADING + gctx.load_structure())
    out = graph_mod.graph_schema_snapshot(config)
    assert out["source"] == "fallback" and out["schema"].startswith(gctx.load_structure())


# --- the budget -----------------------------------------------------------------------------------------------------


def test_a_small_capture_is_sent_whole():
    text = graph_mod._render_committed_schema(_config())
    assert len(text.encode("utf-8")) <= gctx.BUDGET_BYTES
    assert "... and" not in text


def test_the_property_block_is_cut_to_the_budget_and_says_how_many_are_left_out():
    names = [f"Attribute_{n:05d}_with_a_long_descriptive_name" for n in range(2000)]
    committed = dict(COMMITTED, node_properties={"Sample": names})
    text = graph_mod._render_committed_schema(_config(committed=committed))
    assert len(text.encode("utf-8")) <= gctx.BUDGET_BYTES
    assert text.startswith(gctx.load_structure() + "\n\n" + gctx.render_type_index(()))
    for line in INDEX_LINES:
        assert line in text, line
    block = text.split(PROPERTIES_HEADING + "\n", 1)[1].rstrip("\n")
    shown = [name for name in names if name in block]
    assert shown == names[:len(shown)] and 0 < len(shown) < len(names), "the first names are kept, in order"
    assert block.endswith(f", ... and {len(names) - len(shown)} more")


def test_the_structure_and_the_index_are_always_sent():
    codes = [f"C{n:05d}" for n in range(3000)]
    committed = {"vocabulary": {"sampletype_titles": codes}, "node_properties": {"Sample": ["id", "uuid"]}}
    text = graph_mod._render_committed_schema(_config(committed=committed))
    assert text.startswith(gctx.load_structure())
    assert all(f"{code} :T_{code}, sample count unknown" in text for code in codes)
    assert text.rstrip("\n").endswith(PROPERTIES_HEADING + "\n... and 2 more")


# --- the fallback guard (the Task 6 fold-in) ------------------------------------------------------------------------


def test_the_fallback_guard_knows_internal_assay_titles_the_capture_lacks(monkeypatch, down):
    assert "internal_assay_titles" not in json.dumps(COMMITTED)
    cypher = ("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) "
              "WHERE $assay IN coalesce(r.internal_assay_titles, []) RETURN count(DISTINCT c) AS n")
    plan, llm = _turn(monkeypatch, _config(), cypher=cypher, query="how many samples came from this assay")
    assert plan.context_mode == "fallback"
    assert plan.cypher == cypher
    assert len(llm.calls) == 1, "no repair round"


def test_the_fallback_guard_still_refuses_an_unknown_property(monkeypatch, down):
    plan, llm = _turn(monkeypatch, _config(), cypher="MATCH (s:Sample) WHERE s.Lab = 'x' RETURN s.id AS id")
    assert plan.cypher == ""
    assert len(llm.calls) == 2
    assert re.search(r"\['Lab'\]", llm.blob(1))


# --- the live path is unchanged -------------------------------------------------------------------------------------


def test_the_live_message_is_the_rendered_catalog_under_the_same_heading(monkeypatch):
    snapshot = gcat.CatalogSnapshot(catalog_hash="h1", synced_at=None, has_usage=False, index=(), guard={})
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: snapshot)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: gcat.Vocabulary((), (), (), (), (), (), ()))
    plan, llm = _turn(monkeypatch, _config())
    assert plan.context_mode == "catalog"
    assert llm.schema_message() == LIVE_HEADING + gctx.render_graph_context(snapshot, [])
