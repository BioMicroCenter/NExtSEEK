"""The whole-node guard under a variant that allows APOC procedures, and the graph agent's one repair.

``whole_node_returns(cypher, procedures)`` learns, only when ``procedures`` (the variant's ``allowed_procedures``) is
not empty, that a procedure's ``YIELD node`` is a Sample node, ``YIELD path`` a path over samples and ``YIELD nodes`` a
list of them, and that APOC functions which serialise a node (``apoc.any.properties``, ``apoc.convert.toJson``,
``apoc.agg.first``, ``apoc.map.fromNodes``, ...) ship every attribute as surely as ``properties(s)`` does.
``[n IN nodes(path) | n.uuid]``, ``last(nodes(path)).uuid`` and ``length(path)`` stay allowed: they return names.

Without a variant nothing changes: the same text gives the same answer as before, and the default allowlist refuses
the APOC call itself when the query runs. The graph agent runs the property guard, the whole-node guard and the
procedure guard together and repairs once for all of them, then re-checks all of them, so a repair cannot trade one
problem for another unchecked one (the phase 9 verifier's finding on the first P6a).
"""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.agents.graph import whole_node_returns
from chat_nextseek.schemas import GraphAgentPlan

APOC = frozenset({"apoc.path.subgraphNodes", "apoc.path.spanningTree", "apoc.path.expandConfig"})

SUB = ("MATCH (x:Sample {uuid: $uid}) CALL apoc.path.subgraphNodes(x, {relationshipFilter: '<DERIVED_FROM', "
       "labelFilter: '+Sample', minLevel: 1, maxLevel: 12}) ")
TREE = ("MATCH (x:Sample {uuid: $uid}) CALL apoc.path.spanningTree(x, {relationshipFilter: 'DERIVED_FROM>', "
        "labelFilter: '+Sample', minLevel: 1, maxLevel: 12}) ")
EXPAND = ("MATCH (a:Sample {uuid: $a}), (b:Sample {uuid: $b}) CALL apoc.path.expandConfig(a, {relationshipFilter: "
          "'DERIVED_FROM', terminatorNodes: [b], uniqueness: 'NODE_GLOBAL', maxLevel: 12, limit: 1}) ")


@pytest.mark.parametrize("cypher, expected", [
    (SUB + "YIELD node RETURN node", ["node"]),
    (SUB + "YIELD node AS d RETURN d", ["d"]),
    (SUB + "YIELD node RETURN collect(node) AS xs", ["node"]),
    (SUB + "YIELD node RETURN node {.*}", ["node"]),
    (SUB + "YIELD node RETURN properties(node) AS p", ["node"]),
    (SUB + "YIELD node WITH node AS d RETURN d", ["d"]),
    (SUB + "YIELD node RETURN *", ["node", "x"]),
    (TREE + "YIELD path RETURN path", ["path"]),
    (TREE + "YIELD path RETURN collect(path) AS ps", ["path"]),
    (TREE + "YIELD path RETURN nodes(path) AS chain", ["path"]),
    (TREE + "YIELD path RETURN [n IN nodes(path) WHERE n:T_TIS] AS tissues", ["path"]),
    (TREE + "YIELD path RETURN [n IN nodes(path) | n] AS chain", ["path"]),
    (TREE + "YIELD path RETURN last(nodes(path)) AS d", ["path"]),
    (TREE + "YIELD path WITH path AS p RETURN p", ["p"]),
    (TREE + "YIELD path UNWIND nodes(path) AS n RETURN n", ["n"]),
    (EXPAND + "YIELD path AS p RETURN p", ["p"]),
    ("MATCH (x:Sample) CALL apoc.path.subgraphAll(x, {relationshipFilter: 'DERIVED_FROM>', maxLevel: 2}) "
     "YIELD nodes, relationships RETURN nodes", ["nodes"]),
    ("MATCH (x:Sample) CALL apoc.path.subgraphAll(x, {relationshipFilter: 'DERIVED_FROM>', maxLevel: 2}) "
     "YIELD nodes UNWIND nodes AS n RETURN n", ["n"]),
    ("MATCH (s:T_TIS) RETURN apoc.any.properties(s) AS p", ["s"]),
    ("MATCH (s:T_TIS) RETURN apoc.convert.toJson(s) AS j", ["s"]),
    ("MATCH (s:T_TIS) RETURN apoc.convert.toSortedJsonMap(s) AS j", ["s"]),
    ("MATCH (s:T_TIS) RETURN apoc.agg.first(s) AS one", ["s"]),
    ("MATCH (s:T_TIS) RETURN apoc.agg.maxItems(s, s.RIN) AS top", ["s"]),
    (SUB + "YIELD node RETURN apoc.agg.slice(node, 0, 5) AS some", ["node"]),
    ("RETURN apoc.map.fromNodes('Sample', 'uuid') AS m", ["apoc.map.fromNodes(...)"]),
    ("MATCH p = (c:T_TIS)-[:DERIVED_FROM]->(m) RETURN apoc.agg.graph(p) AS g", ["apoc.agg.graph(...)"]),
    (TREE + "YIELD path RETURN apoc.path.elements(path) AS e", ["apoc.path.elements(...)"]),
])
def test_apoc_whole_node_shapes_are_found_under_the_variant(cypher, expected):
    assert whole_node_returns(cypher, APOC) == expected


@pytest.mark.parametrize("cypher", [
    SUB + "YIELD node RETURN node.uuid AS uuid, node.type AS type ORDER BY uuid",
    SUB + "YIELD node RETURN node.type AS type, count(*) AS n ORDER BY n DESC",
    SUB + "YIELD node RETURN count(node) AS n",
    SUB + "YIELD node WHERE node:T_D_SEQ RETURN count(DISTINCT node) AS n",
    TREE + "YIELD path RETURN [n IN nodes(path) | n.uuid] AS chain, length(path) AS depth",
    TREE + "YIELD path RETURN last(nodes(path)).uuid AS uuid, length(path) AS depth",
    TREE + "YIELD path RETURN nodes(path)[-1].type AS type, count(*) AS n",
    TREE + "YIELD path RETURN size(nodes(path)) AS n",
    TREE + "YIELD path WITH path WHERE any(n IN nodes(path) WHERE n:T_MUS) RETURN count(*) AS n",
    TREE + "YIELD path UNWIND nodes(path) AS n RETURN DISTINCT n.uuid AS uuid",
    TREE + "YIELD path RETURN reduce(acc = '', n IN nodes(path) | acc + n.uuid + ' ') AS chain",
    EXPAND + "YIELD path RETURN length(path) AS hops, [n IN nodes(path) | n.uuid + ' ' + n.type] AS via",
    "MATCH (s:T_TIS) RETURN apoc.any.property(s, 'Organ') AS organ",
    "MATCH (s:T_TIS) RETURN apoc.agg.first(s.uuid) AS one",
    "MATCH (s:T_TIS) RETURN apoc.coll.frequencies(collect(s.Organ)) AS f",
    "MATCH (s:T_TIS) RETURN apoc.text.clean(s.Organ) AS organ, count(*) AS n",
])
def test_names_and_counts_pass_under_the_variant(cypher):
    assert whole_node_returns(cypher, APOC) == []


@pytest.mark.parametrize("cypher", [
    SUB + "YIELD node RETURN node",
    TREE + "YIELD path RETURN path",
    "MATCH (s:T_TIS) RETURN apoc.any.properties(s) AS p",
    "RETURN apoc.map.fromNodes('Sample', 'uuid') AS m",
    "MATCH (s:T_TIS) RETURN s",
    "MATCH (s:T_TIS) RETURN s.id, s.Organ",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node RETURN node",
    "MATCH p = (c:T_TIS)-[:DERIVED_FROM*1..3]->(m) RETURN [n IN nodes(p) | n.uuid]",
])
def test_without_a_variant_the_whole_node_guard_is_unchanged(cypher):
    """No procedures, no change: the default path reads exactly as before this guard learned APOC.

    That includes the gap the variant closes: with the APOC plugin loaded, ``apoc.any.properties(s)`` and
    ``apoc.map.fromNodes`` pass the default guard. The default graph prompt never mentions APOC.
    """
    before = {
        SUB + "YIELD node RETURN node": [],
        TREE + "YIELD path RETURN path": [],
        "MATCH (s:T_TIS) RETURN apoc.any.properties(s) AS p": [],
        "RETURN apoc.map.fromNodes('Sample', 'uuid') AS m": [],
        "MATCH (s:T_TIS) RETURN s": ["s"],
        "MATCH (s:T_TIS) RETURN s.id, s.Organ": [],
        "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node RETURN node": ["node"],
        "MATCH p = (c:T_TIS)-[:DERIVED_FROM*1..3]->(m) RETURN [n IN nodes(p) | n.uuid]": ["p"],
    }
    assert whole_node_returns(cypher) == before[cypher]
    assert whole_node_returns(cypher, ()) == before[cypher]
    assert whole_node_returns(cypher, frozenset()) == before[cypher]


def test_the_property_guard_reads_an_apoc_query_as_before():
    """The property guard does not learn YIELD: a yielded node is of unknown label and may read any known name."""
    snapshot = gcat.CatalogSnapshot(catalog_hash="h", synced_at=None, has_usage=False, index=(),
                                    guard=MappingProxyType({"T_TIS": frozenset({"Organ"})}))
    assert graph_mod.catalog_unknown_properties(SUB + "YIELD node RETURN node.uuid, node.Organ", snapshot) == []
    assert graph_mod.catalog_unknown_properties(SUB + "YIELD node RETURN node.Invented", snapshot) == \
        ["node.Invented"]


# --- the graph agent: one repair for every problem, then a re-check of every guard ----------------------------------

SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(gcat.TypeIndexRow(title="TIS", label="T_TIS", name="Tissue", clade="Source", sample_count=10,
                             deprecated=False, attributes_with_values=1),),
    guard=MappingProxyType({"T_TIS": frozenset({"Organ"})}),
)
VOCAB = gcat.Vocabulary(investigation_titles=(), project_titles=(), study_titles=(), published_studies=(),
                        assay_titles=(), protocol_titles=(), assay_connections=())

BOUNDED = SUB + "YIELD node RETURN node.type AS type, count(*) AS n ORDER BY n DESC"
UNBOUNDED = ("MATCH (x:Sample {uuid: $uid}) CALL apoc.path.subgraphNodes(x, {relationshipFilter: '<DERIVED_FROM'}) "
             "YIELD node RETURN count(node) AS n")
NO_FILTER = "MATCH (x:Sample {uuid: $uid}) CALL apoc.path.subgraphNodes(x, {maxLevel: 4}) YIELD node RETURN count(*)"


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(gcat, "get_snapshot", lambda config: SNAPSHOT)
    monkeypatch.setattr(gcat, "get_type_details", lambda config, titles: [])
    monkeypatch.setattr(gcat, "get_vocabulary", lambda config: VOCAB)


@pytest.fixture
def down(monkeypatch):
    def unavailable(*args, **kwargs):
        raise gcat.CatalogUnavailable("down")
    for name in ("get_snapshot", "get_type_details", "get_vocabulary"):
        monkeypatch.setattr(gcat, name, unavailable)


class FakeLLM:
    def __init__(self, *cyphers):
        self.cyphers, self.calls = list(cyphers), []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return GraphAgentPlan(cypher=self.cyphers[min(len(self.calls), len(self.cyphers)) - 1],
                              explanation="model explanation", parameters={})

    def repair(self) -> str:
        return self.calls[1]["messages"][-1]["content"]


def _config(procedures):
    c = MagicMock()
    c.NEO4J_SCHEMA = {"node_properties": {"Sample": ["uuid", "type", "id", "Organ"]}}
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    if procedures is not None:
        c.EXTRA_ALLOWED_PROCEDURES = frozenset(procedures)
    return c


def run(monkeypatch, llm, procedures=APOC):
    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    return graph_mod.graph_agent(_config(procedures), "how many samples descend from TIS-1", {}, None)


def test_a_bounded_call_passes_without_a_repair(monkeypatch, live):
    llm = FakeLLM(BOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 1 and out.cypher == BOUNDED


@pytest.mark.parametrize("bad, words", [(UNBOUNDED, ["no maxLevel"]), (NO_FILTER, ["no relationshipFilter"])])
def test_an_unsafe_call_is_repaired_once_then_refused(monkeypatch, live, bad, words):
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2
    repair = llm.repair()
    for word in words:
        assert word in repair and word in out.explanation
    assert "maxLevel from 1 to 12" in repair and "'<DERIVED_FROM'" in repair and "NODE_GLOBAL" in repair
    assert out.cypher == "" and out.context_mode == "catalog"


def test_a_repair_that_bounds_the_call_is_returned(monkeypatch, live):
    llm = FakeLLM(UNBOUNDED, BOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == BOUNDED


def test_the_repair_is_rechecked_by_the_property_guard(monkeypatch, live):
    hallucinated = BOUNDED.replace("RETURN node.type AS type", "MATCH (t:T_TIS) WHERE t.Invented = 1 RETURN node.type AS type")
    llm = FakeLLM(UNBOUNDED, hallucinated)
    out = run(monkeypatch, llm)
    assert out.cypher == "" and "TIS.Invented" in out.explanation


def test_the_repair_is_rechecked_by_the_procedure_guard(monkeypatch, live):
    llm = FakeLLM("MATCH (s:T_TIS) WHERE s.Nope = 1 RETURN s.id", UNBOUNDED)
    out = run(monkeypatch, llm)
    assert out.cypher == "" and "no maxLevel" in out.explanation


def test_a_yielded_node_returned_whole_is_repaired(monkeypatch, live):
    bad = SUB + "YIELD node RETURN node"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert "whole node node" in llm.repair() and out.cypher == ""


def test_a_procedure_the_variant_does_not_allow_is_repaired(monkeypatch, live):
    bad = "CALL apoc.meta.schema() YIELD value RETURN value"
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    assert "not a procedure you may call here" in llm.repair() and out.cypher == ""


def test_one_repair_names_every_kind_of_problem(monkeypatch, live):
    bad = UNBOUNDED.replace("RETURN count(node) AS n", "MATCH (t:T_TIS) WHERE t.Nope = 1 RETURN node")
    llm = FakeLLM(bad, bad)
    out = run(monkeypatch, llm)
    repair = llm.repair()
    assert "TIS.Nope" in repair and "whole node node" in repair and "no maxLevel" in repair
    assert len(llm.calls) == 2


def test_the_fallback_schema_path_also_checks_the_call(monkeypatch, down):
    llm = FakeLLM(UNBOUNDED, UNBOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and "no maxLevel" in llm.repair()
    assert out.cypher == "" and out.context_mode == "fallback" and "no maxLevel" in out.explanation


@pytest.mark.parametrize("procedures", [None, ()])
def test_without_a_variant_the_agent_is_unchanged(monkeypatch, live, procedures):
    """No procedures: no procedure check and no APOC whole-node rule; the tool refuses the call when it runs."""
    for cypher in (UNBOUNDED, NO_FILTER, SUB + "YIELD node RETURN node"):
        llm = FakeLLM(cypher)
        out = run(monkeypatch, llm, procedures)
        assert len(llm.calls) == 1 and out.cypher == cypher


def test_without_a_variant_the_fallback_is_unchanged(monkeypatch, down):
    """The type-blind fallback guard reads `apoc.path` as a property named `path`, as it always has; nothing new."""
    llm = FakeLLM(UNBOUNDED, UNBOUNDED)
    out = run(monkeypatch, llm, None)
    repair = llm.repair()
    assert len(llm.calls) == 2
    assert repair.startswith("The previous Cypher referenced properties that do not exist on any node or "
                             "relationship: ['path']")
    assert "maxLevel" not in repair and "maxLevel" not in out.explanation
