"""The graph agent's context: the rendered catalog when it is live, the committed JSON when it is not (spec 4.2 to 4.3).

The committed JSON carries a vocabulary read over every project (study, investigation and protocol titles, assay
connections), so only an admin is sent it; anyone else gets the committed structure alone
(docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 8). The configs below are admins unless a test says
otherwise.

Every test drives a fake LLM client and a patched catalog reader; nothing reaches Neo4j or a model.
"""

import importlib.util
import json
import re
import time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek import graph_context as gctx
from chat_nextseek import orchestrator as orch
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.agents import system as system_mod
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.schemas import EntityAgentOutput, GraphAgentPlan, ParserPlan, SystemAgentOutput

FALLBACK_SCHEMA = {
    "fetched_at": "2026-08-21T00:00:00Z",
    "node_properties": {"Sample": ["uuid", "type", "id"], "Study": ["title"]},
    "relationship_properties": {"DERIVED_FROM": ["internal_assay_title", "protocol_title"]},
    "vocabulary": {"investigation_titles": ["FallbackOnly"]},
}


def _label(title):
    return "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title)


def _index_row(title, n):
    return gcat.TypeIndexRow(title=title, label=_label(title), name=f"{title} name", clade="Source",
                             sample_count=n, deprecated=False, attributes_with_values=2)


def _detail(title, attributes, n):
    rows = tuple(gcat.AttributeRow(title=a, value_type="string", declared=True, needs_backticks=False,
                                   sample_count=5, meaning=None, unit_key=None, role="data") for a in attributes)
    return gcat.TypeDetail(title=title, label=_label(title), name=f"{title} name", summary=f"The {title} type.",
                           clade="Source", sample_count=n, curated_parents=None, curated_children=None,
                           attributes=rows, never_filled=0)


SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(_index_row("TIS", 100), _index_row("D.SEQ", 50), _index_row("MUS", 7)),
    guard=MappingProxyType({
        "T_TIS": frozenset({"Organ"}),
        "T_D_SEQ": frozenset({"Sequencer"}),
        "T_MUS": frozenset({"Strain"}),
    }),
)
DETAILS = {
    "TIS": _detail("TIS", ["Organ"], 100),
    "D.SEQ": _detail("D.SEQ", ["Sequencer"], 50),
    "MUS": _detail("MUS", ["Strain"], 7),
}
VOCAB = gcat.Vocabulary(
    investigation_titles=("Impact",), project_titles=("BioMicroCenter",), study_titles=("A lung paper",),
    published_studies=({"title": "A lung paper", "doi": "10.1/x", "pmid": "1"},),
    assay_titles=("Bulk RNA Sequencing",), protocol_titles=("P-proto",),
    assay_connections=({"assay": "Bulk RNA Sequencing", "parent_type": "RNA", "child_type": "D.SEQ"},),
)

GOOD = "MATCH (s:T_TIS) WHERE s.Organ = $organ RETURN s.id AS id, s.uuid AS uuid, s.type AS type"


ADMIN = GraphScope.admin("test")
# Callers who are not admins: a member of one project, a member of none, and a config that carries no scope.
NOT_ADMIN = {
    "project_2": GraphScope.for_projects([2], source="test"),
    "no_projects": GraphScope.for_projects([], source="test"),
    "no_scope": None,
}
COMMITTED_VOCABULARY = ("FallbackOnly", "Old protocol", "Old assay")


def _config(scope=ADMIN):
    c = MagicMock()
    c.GRAPH_SCOPE = scope
    c.NEO4J_SCHEMA = FALLBACK_SCHEMA
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = {"protocol_titles": ["Old protocol"]}
    c.ASSAY_SAMPLE_CONNECTIONS = {"connections": [{"assay": "Old assay", "parent_type": "RNA",
                                                   "child_type": "D.SEQ"}]}
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _unavailable(*args, **kwargs):
    raise gcat.CatalogUnavailable("graph down in this test")


@pytest.fixture
def live(monkeypatch):
    seen = {"details": []}

    def details(config, titles):
        titles = list(titles)
        seen["details"].append(titles)
        return [DETAILS[t] for t in titles if t in DETAILS]

    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", details)
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: VOCAB)
    return seen


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

    def blob(self, call=0):
        return "\n".join(m["content"] for m in self.calls[call]["messages"])


def run(monkeypatch, llm, query="how many tissue samples have Organ Lung", **kwargs):
    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    config = _config(kwargs.pop("scope", ADMIN))
    return graph_mod.graph_agent(config, query, kwargs.pop("entity", {}), kwargs.pop("plan", None), **kwargs)


# --- which context is sent ------------------------------------------------------------------------------------------


def test_a_live_catalog_sends_the_rendering(monkeypatch, live):
    llm = FakeLLM(GOOD)
    out = run(monkeypatch, llm, entity={"sampletypes": [{"code": "TIS"}]})
    assert out.cypher == GOOD
    assert out.context_mode == graph_mod.CONTEXT_CATALOG == "catalog"
    schema = llm.calls[0]["messages"][1]["content"]
    assert gctx.load_structure() in schema
    assert gctx.render_type_index(SNAPSHOT.index) in schema
    assert "### TIS :T_TIS" in schema
    blob = llm.blob()
    assert "node_properties" not in blob
    assert "Old assay" not in blob and "Old protocol" not in blob


def test_an_unavailable_catalog_sends_the_committed_json(monkeypatch, down):
    llm = FakeLLM(GOOD.replace("s:T_TIS", "s:Sample"))
    out = run(monkeypatch, llm, query="which protocol and assay made these samples")
    assert out.context_mode == graph_mod.CONTEXT_FALLBACK == "fallback"
    blob = llm.blob()
    assert '"node_properties"' in blob
    assert "Old protocol" in blob and "Old assay" in blob
    assert "FallbackOnly" in blob
    assert "## Sample types" not in blob


@pytest.mark.parametrize("scope", list(NOT_ADMIN.values()), ids=list(NOT_ADMIN))
def test_an_unavailable_catalog_sends_a_non_admin_the_committed_structure_only(monkeypatch, down, scope):
    llm = FakeLLM(GOOD.replace("s:T_TIS", "s:Sample"))
    out = run(monkeypatch, llm, query="which study, protocol and assay made these samples", scope=scope)
    assert out.context_mode == "fallback"
    blob = llm.blob()
    assert '"node_properties"' in blob and '"relationship_properties"' in blob
    for value in COMMITTED_VOCABULARY:
        assert value not in blob, value


def test_a_failed_type_detail_read_falls_back_for_the_whole_turn(monkeypatch, live):
    monkeypatch.setattr(gcat, "get_type_details", _unavailable)
    llm = FakeLLM("MATCH (s:Sample) RETURN count(*) AS n")
    out = run(monkeypatch, llm, entity={"sampletypes": [{"code": "TIS"}]})
    assert out.context_mode == "fallback"
    assert '"node_properties"' in llm.blob()


def test_an_unexpected_catalog_error_falls_back(monkeypatch, live):
    def broken(config):
        raise ValueError("not a CatalogUnavailable")

    monkeypatch.setattr(gcat, "get_snapshot", broken)
    out = run(monkeypatch, FakeLLM("MATCH (s:Sample) RETURN count(*) AS n"))
    assert out.context_mode == "fallback"


def test_type_codes_come_from_the_parser_plan_first(monkeypatch, live):
    plan = ParserPlan(mode="graph_query", intent_summary="x",
                      resolved=EntityAgentOutput(sampletypes=[{"code": "D.SEQ"}]),
                      filters={"sampletype_code": "MUS"})
    run(monkeypatch, FakeLLM(GOOD), entity={"sampletypes": [{"code": "TIS"}, {"code": "NOPE"}]}, plan=plan)
    assert live["details"] == [["D.SEQ", "MUS", "TIS"]]


def test_without_a_plan_the_entity_codes_are_used(monkeypatch, live):
    run(monkeypatch, FakeLLM(GOOD), entity=EntityAgentOutput(sampletypes=[{"code": "TIS"}]))
    assert live["details"] == [["TIS"]]


@pytest.mark.parametrize("query, present, absent", [
    ("how many tissue samples have Organ Lung", ["INVESTIGATION TITLES", "PROJECT TITLES"],
     ["STUDY TITLES", "ASSAY TITLES", "PROTOCOL TITLES"]),
    ("which samples underwent an assay", ["INVESTIGATION TITLES", "ASSAY TITLES", "ASSAY-SAMPLE CONNECTIONS"],
     ["STUDY TITLES", "PROTOCOL TITLES"]),
    ("which paper used these samples", ["STUDY TITLES", "PUBLISHED STUDIES"], ["ASSAY TITLES"]),
    ("samples made with which protocol", ["PROTOCOL TITLES"], ["STUDY TITLES", "ASSAY TITLES"]),
])
def test_vocabulary_blocks_are_gated_by_the_question(monkeypatch, live, query, present, absent):
    llm = FakeLLM(GOOD)
    run(monkeypatch, llm, query=query)
    blob = llm.blob()
    assert gctx.render_vocabulary(VOCAB, query) in blob
    for heading in present:
        assert heading in blob
    for heading in absent:
        assert heading not in blob


# --- the guards in the repair loop ----------------------------------------------------------------------------------


def test_an_attribute_the_type_lacks_is_repaired_once_then_refused(monkeypatch, live):
    bad = "MATCH (s:T_TIS) WHERE s.Sequencer = 'x' RETURN s.id AS id"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2
    assert "TIS.Sequencer" in llm.calls[1]["messages"][-1]["content"]
    assert out.cypher == ""
    assert "TIS.Sequencer" in out.explanation
    assert out.context_mode == "catalog"


def test_a_whole_node_return_is_repaired_once_then_refused(monkeypatch, live):
    bad = "MATCH (s:T_TIS) RETURN s LIMIT 5"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2
    repair = llm.calls[1]["messages"][-1]["content"]
    assert "whole node s" in repair and "s.id" in repair and "count(*)" in repair
    assert out.cypher == ""
    assert "whole node s" in out.explanation


def test_a_successful_repair_is_returned(monkeypatch, live):
    llm = FakeLLM("MATCH (s:T_TIS) RETURN s", GOOD)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2
    assert out.cypher == GOOD
    assert out.context_mode == "catalog"


def test_the_repair_names_every_problem_once(monkeypatch, live):
    bad = "MATCH (s:T_TIS) WHERE s.Sequencer = 'x' AND s.Sequencer <> 'y' RETURN s"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    repair = llm.calls[1]["messages"][-1]["content"]
    assert repair.count("TIS.Sequencer") == 1
    assert "whole node s" in repair
    assert "TIS.Sequencer" in out.explanation and "whole node s" in out.explanation


def test_the_fallback_keeps_the_type_blind_guard_and_no_whole_node_guard(monkeypatch, down):
    llm = FakeLLM("MATCH (s:Sample) RETURN s")
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 1 and out.cypher == "MATCH (s:Sample) RETURN s"
    bad = "MATCH (s:Sample) WHERE s.Lab = 'x' RETURN s.id"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == "" and "Lab" in out.explanation
    assert out.context_mode == "fallback"


def test_every_plan_carries_the_context_mode(monkeypatch, live):
    out = run(monkeypatch, FakeLLM(""))
    assert out.cypher == "" and out.context_mode == "catalog"

    def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(graph_mod, "call_llm_structured", boom)
    out = graph_mod.graph_agent(_config(), "q", {}, None)
    assert out.cypher == "" and out.context_mode == "catalog"


def test_the_context_mode_defaults_to_none_on_the_schema():
    assert GraphAgentPlan(cypher="x").context_mode is None


# --- the orchestrator records the context ---------------------------------------------------------------------------


def _turn(monkeypatch, tmp_path, plan):
    monkeypatch.setattr(orch, "graph_agent", lambda *args, **kwargs: plan)
    monkeypatch.setattr(orch, "tool_neo4j_query",
                        lambda config, cypher, params=None: {"ok": True, "data": [{"n": 3}], "count": 1})
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *args, **kwargs: "There are 3.")
    monkeypatch.setattr(orch, "append_turn", lambda *args, **kwargs: None)
    config = MagicMock()
    config.MODEL_MODE = "test"
    debug = {}
    payload = orch._execute_graph_turn(
        config=config, session={}, user_text="how many", entity_result=EntityAgentOutput(),
        plan=ParserPlan(mode="graph_query", intent_summary="count"), log_dir=str(tmp_path),
        artifact_store=MagicMock(register_path=MagicMock(return_value=None)),
        send_event=lambda *args, **kwargs: None, debug_payload=debug, t_total_start=time.perf_counter(),
    )
    return payload, debug


def test_execute_graph_turn_records_the_graph_context(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher="MATCH (s:T_TIS) RETURN count(*) AS n", context_mode="catalog")
    payload, debug = _turn(monkeypatch, tmp_path, plan)
    assert debug["graph_context"] == "catalog"
    assert payload["debug"]["graph_context"] == "catalog"


def test_execute_graph_turn_records_the_context_of_a_refused_plan(monkeypatch, tmp_path):
    plan = GraphAgentPlan(cypher="", explanation="refused", context_mode="fallback")
    payload, debug = _turn(monkeypatch, tmp_path, plan)
    assert payload["debug"]["graph_context"] == "fallback"


# --- the system agent and the MCP resource --------------------------------------------------------------------------


def _system_config(scope=ADMIN):
    c = MagicMock()
    c.GRAPH_SCOPE = scope
    c.FULL_SAMPLETYPES_MAP, c.FULL_ASSAYS_MAP, c.FULL_PROJECTS_MAP = {}, {}, {}
    c.MIN_SAMPLETYPES, c.MIN_ASSAYS, c.MIN_API_ENDPOINTS = [], [], []
    c.CAPABILITIES_DOC = "caps"
    c.NEO4J_SCHEMA = FALLBACK_SCHEMA
    c.SYSTEM_AGENT_SYSTEM_PROMPT = "system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def _system_schema_block(monkeypatch, scope=ADMIN):
    seen = {}

    def fake(**kwargs):
        seen["messages"] = kwargs["messages"]
        return SystemAgentOutput(mode="get_capabilities", narrative="ok")

    monkeypatch.setattr(system_mod, "call_llm_structured", fake)
    system_mod.system_agent(_system_config(scope), "what is a tissue sample", {"sampletypes": [{"code": "TIS"}]},
                            ParserPlan(mode="system_question"))
    return next(m["content"] for m in seen["messages"] if m["content"].startswith("GRAPH_SCHEMA"))


def test_the_system_agent_sends_the_rendering_when_the_catalog_is_live(monkeypatch, live):
    block = _system_schema_block(monkeypatch)
    assert gctx.render_type_index(SNAPSHOT.index) in block
    assert "### TIS :T_TIS" in block
    assert "node_properties" not in block


def test_the_system_agent_sends_the_json_when_the_catalog_is_down(monkeypatch, down):
    block = _system_schema_block(monkeypatch)
    assert '"node_properties"' in block
    assert "FallbackOnly" in block


@pytest.mark.parametrize("scope", list(NOT_ADMIN.values()), ids=list(NOT_ADMIN))
def test_the_system_agent_sends_a_non_admin_the_committed_structure_only(monkeypatch, down, scope):
    block = _system_schema_block(monkeypatch, scope)
    assert '"node_properties"' in block
    assert "FallbackOnly" not in block


def _mcp_server():
    pytest.importorskip("mcp.server.fastmcp")
    path = graph_mod.__file__.rsplit("/src/chat_nextseek/", 1)[0] + "/mcp_server.py"
    spec = importlib.util.spec_from_file_location("chat_nextseek_mcp_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_mcp_neo4j_schema_resource_in_both_states(monkeypatch, tmp_path):
    server = _mcp_server()
    (tmp_path / "neo4j_schema.json").write_text('{"from": "the committed file"}', encoding="utf-8")
    monkeypatch.setattr(server, "_cfg", lambda: SimpleNamespace(CONTEXT_DIR=str(tmp_path)))

    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    text = server.context_resource("neo4j-schema")
    assert gctx.load_structure() in text
    assert gctx.render_type_index(SNAPSHOT.index) in text
    assert "the committed file" not in text

    monkeypatch.setattr(gcat, "get_snapshot", _unavailable)
    assert server.context_resource("neo4j-schema") == '{"from": "the committed file"}'


# --- the graph-schema op's read-only projection ----------------------------------------------------------------------
# graph_schema_snapshot is what the nextseek-graph-schema op returns, so the CC agent reads the deployed graph
# instead of a snapshot baked into its image. It spends no model call: the catalog reads and the renderers only.


def test_the_schema_snapshot_is_the_live_catalog(live):
    out = graph_mod.graph_schema_snapshot(_config(), types=["TIS"], question="which assays")
    assert out["source"] == graph_mod.CONTEXT_CATALOG == "catalog"
    assert out["schema_version"] == SNAPSHOT.schema_version
    assert out["catalog_hash"] == "h1"
    assert out["sample_types"] == 3
    assert out["resolved_types"] == ["TIS"]
    assert out["unknown_types"] == []
    assert out["unavailable_reason"] is None
    assert gctx.load_structure() in out["schema"]
    assert gctx.render_type_index(SNAPSHOT.index) in out["schema"]
    assert "Organ" in out["schema"], "the resolved type's attributes must be in the text"
    assert "Bulk RNA Sequencing" in out["vocabulary"], "the assay word gate must have fired"
    assert live["details"] == [["TIS"]]


def test_the_schema_snapshot_names_an_unknown_type_instead_of_guessing(live):
    out = graph_mod.graph_schema_snapshot(_config(), types=["TIS", "NOPE"])
    assert out["resolved_types"] == ["TIS"]
    assert out["unknown_types"] == ["NOPE"]
    assert live["details"] == [["TIS"]], "an unknown code must not reach the catalog read"


def test_the_schema_snapshot_renders_no_type_section_when_none_is_asked_for(live):
    out = graph_mod.graph_schema_snapshot(_config())
    assert out["resolved_types"] == []
    assert "Resolved sample types" not in out["schema"]
    assert live["details"] == []
    assert "INVESTIGATION TITLES" in out["vocabulary"]
    assert "ASSAY TITLES" not in out["vocabulary"], "no question, so no keyword-gated block"


def test_the_schema_snapshot_falls_back_loudly_when_the_graph_is_down(down):
    out = graph_mod.graph_schema_snapshot(_config(), types=["TIS"], question="which protocol")
    assert out["source"] == graph_mod.CONTEXT_FALLBACK == "fallback"
    assert out["schema_version"] is None
    assert out["catalog_hash"] is None
    assert out["sample_types"] == 0
    assert "graph down in this test" in out["unavailable_reason"]
    assert out["fallback_fetched_at"] == "2026-08-21T00:00:00Z", (
        "how stale the committed file is must be in the answer, not left to be guessed"
    )
    assert '"node_properties"' in out["schema"]
    assert out["resolved_types"] == []
    assert out["unknown_types"] == ["TIS"]
    assert "Old protocol" in out["vocabulary"]
    assert "FallbackOnly" in out["schema"]


@pytest.mark.parametrize("scope", list(NOT_ADMIN.values()), ids=list(NOT_ADMIN))
def test_the_schema_snapshot_fallback_sends_a_non_admin_the_committed_structure_only(down, scope):
    out = graph_mod.graph_schema_snapshot(_config(scope), types=["TIS"], question="which study, protocol and assay")
    assert out["source"] == graph_mod.CONTEXT_FALLBACK
    assert out["fallback_fetched_at"] == "2026-08-21T00:00:00Z"
    assert '"node_properties"' in out["schema"]
    assert out["vocabulary"] == ""
    for value in COMMITTED_VOCABULARY:
        assert value not in json.dumps(out), value


def test_a_catalog_defect_falls_back_rather_than_raising(monkeypatch, live):
    def boom(config):
        raise RuntimeError("catalog defect")

    monkeypatch.setattr(gcat, "get_vocabulary", boom)
    out = graph_mod.graph_schema_snapshot(_config())
    assert out["source"] == graph_mod.CONTEXT_FALLBACK
    assert "RuntimeError: catalog defect" in out["unavailable_reason"]


def test_the_schema_snapshot_is_exported_as_portable():
    from chat_nextseek import portable

    assert portable.graph_schema_snapshot is graph_mod.graph_schema_snapshot
    assert "graph_schema_snapshot" in portable.__all__
