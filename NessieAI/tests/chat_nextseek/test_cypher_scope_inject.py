"""
What the prover injects, exactly, and the properties every injection keeps.

Goldens pin the output text for a dozen statements (spec section 5.6). Where the model's WHERE could raise, the
clauses guard it (``CASE WHEN <clauses> THEN (...) ELSE false END``) so it is never evaluated on a node outside the
scope; conjuncts that cannot raise stay outside for Neo4j's index seeks. The properties hold for every accepted
statement in the battery: the output is the input plus insertions only (deleting the inserted spans gives the input
back byte for byte), every generated name starts with __scope, the parameters are the input's plus the one scope
parameter, an admin gets the input back unchanged, and an empty project set binds []. strip_hidden turns a
non-admin's graph values into plain maps without the hidden sample properties (section 5.7).

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 5.6, 5.7 and 11.1.
"""
from __future__ import annotations

import re

import pytest

from chat_nextseek import cypher_scope
from chat_nextseek.cypher_scope import (
    PATH_CLAUSE_TEMPLATE,
    PROJECT_CLAUSE_TEMPLATE,
    SCOPE_CLAUSE_TEMPLATE,
    Scoped,
    scope_cypher,
    strip_hidden,
)
from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope

from NessieAI.tests.chat_nextseek.graph_scope.battery import ACCEPTED, TAUGHT, hidden_variants

CALLER = GraphScope.for_projects([13, 2], source="test")
NOBODY = GraphScope.for_projects([], source="test")
ADMIN = GraphScope.admin("test")

SP = "$__scope_projects"


def clause(k: int, var: str) -> str:
    return f"any(__scope_p{k} IN {var}.project_ids WHERE __scope_p{k} IN {SP})"


def path_clause(m: int, p: int, path: str) -> str:
    return (f"all(__scope_m{m} IN nodes({path}) WHERE any(__scope_p{p} IN __scope_m{m}.project_ids "
            f"WHERE __scope_p{p} IN {SP}))")


def test_templates_are_the_spec_text():
    assert SCOPE_CLAUSE_TEMPLATE == "any({element} IN {var}.project_ids WHERE {element} IN ${param})"
    assert PROJECT_CLAUSE_TEMPLATE == "{var}.id IN ${param}"
    assert PATH_CLAUSE_TEMPLATE == (
        "all({node} IN nodes({path}) WHERE any({element} IN {node}.project_ids WHERE {element} IN ${param}))")
    assert cypher_scope.MAX_CYPHER_CHARS == 20_000
    assert cypher_scope.MAX_NESTING == 32


GOLDENS = [
    (
        "exists_lineage",
        "MATCH (s:T_SLD) WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(:T_MUS) } RETURN count(s) AS n",
        "MATCH (s:T_SLD) WHERE CASE WHEN " + clause(2, "s") + " THEN (EXISTS { MATCH __scope_path1 = "
        "(s)-[:DERIVED_FROM*1..12]->(:T_MUS) WHERE " + path_clause(1, 1, "__scope_path1") + " }) ELSE false END "
        "RETURN count(s) AS n",
    ),
    (
        "optional_parent",
        "MATCH (s:T_TIS {uuid: $uid}) OPTIONAL MATCH (s)-[:DERIVED_FROM]->(parent:Sample) RETURN s.uuid, parent.uuid",
        "MATCH (s:T_TIS {uuid: $uid}) WHERE " + clause(1, "s") + " OPTIONAL MATCH (s)-[:DERIVED_FROM]->(parent:Sample)"
        " WHERE " + clause(2, "parent") + " RETURN s.uuid, parent.uuid",
    ),
    (
        "fulltext_with_where",
        "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score WHERE s:T_MUS "
        "RETURN s.uuid AS uuid ORDER BY score DESC LIMIT 5000",
        "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score WHERE (s:T_MUS) AND "
        + clause(1, "s") + " RETURN s.uuid AS uuid ORDER BY score DESC LIMIT 5000",
    ),
    (
        "fulltext_without_where",
        "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score RETURN node.uuid AS uuid, score",
        "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score WHERE " + clause(1, "node")
        + " RETURN node.uuid AS uuid, score",
    ),
    (
        "study_disjunction",
        "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
        "WHERE toLower(st.title) CONTAINS toLower($project)\n"
        "   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation) WHERE toLower(inv.title) CONTAINS "
        "toLower($project) }\n"
        "RETURN count(DISTINCT s) AS n",
        "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
        "WHERE CASE WHEN " + clause(1, "s") + " THEN (toLower(st.title) CONTAINS toLower($project)\n"
        "   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation) WHERE toLower(inv.title) CONTAINS "
        "toLower($project) }) ELSE false END\n"
        "RETURN count(DISTINCT s) AS n",
    ),
    (
        "project_node",
        "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE toLower(p.title) CONTAINS toLower($project) "
        "RETURN count(DISTINCT s) AS n",
        "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE CASE WHEN " + clause(1, "s") + f" AND p.id IN {SP} "
        "THEN (toLower(p.title) CONTAINS toLower($project)) ELSE false END RETURN count(DISTINCT s) AS n",
    ),
    (
        "anonymous_parent",
        "MATCH (c:T_SLD)-[:DERIVED_FROM]->(:T_MUS) RETURN count(DISTINCT c) AS n",
        "MATCH (c:T_SLD)-[:DERIVED_FROM]->(__scope_n1:T_MUS) WHERE " + clause(1, "c") + " AND "
        + clause(2, "__scope_n1") + " RETURN count(DISTINCT c) AS n",
    ),
    (
        "unlabelled_child",
        "MATCH (s:T_MUS)<-[:DERIVED_FROM]-(c) RETURN c.uuid AS uuid",
        "MATCH (s:T_MUS)<-[:DERIVED_FROM]-(c) WHERE " + clause(1, "s") + " AND " + clause(2, "c")
        + " RETURN c.uuid AS uuid",
    ),
    (
        "model_path_name",
        "MATCH p = (s:T_SLD)-[:DERIVED_FROM*1..3]->(m:T_MUS) RETURN length(p) AS hops, m.uuid AS uuid",
        "MATCH p = (s:T_SLD)-[:DERIVED_FROM*1..3]->(m:T_MUS) WHERE " + path_clause(1, 1, "p")
        + " RETURN length(p) AS hops, m.uuid AS uuid",
    ),
    (
        "count_subquery",
        "MATCH (s:T_MUS) RETURN s.uuid AS uuid, COUNT { (s)<-[:DERIVED_FROM]-(:Sample) } AS children",
        "MATCH (s:T_MUS) WHERE " + clause(1, "s") + " RETURN s.uuid AS uuid, COUNT { MATCH (s)<-[:DERIVED_FROM]-"
        "(__scope_n1:Sample) WHERE " + clause(2, "__scope_n1") + " } AS children",
    ),
    (
        "bare_exists_with_where",
        "MATCH (s:T_SLD) WHERE EXISTS { (s)-[:DERIVED_FROM]->(m:T_MUS) WHERE m.Strain = $strain } "
        "RETURN count(s) AS n",
        "MATCH (s:T_SLD) WHERE CASE WHEN " + clause(2, "s") + " THEN (EXISTS { MATCH (s)-[:DERIVED_FROM]->(m:T_MUS) "
        "WHERE (m.Strain = $strain) AND " + clause(1, "m") + " }) ELSE false END RETURN count(s) AS n",
    ),
    (
        "comma_list",
        "MATCH (a:T_MUS), (b:T_SLD) WHERE a.uuid = b.Parent RETURN count(*) AS n",
        "MATCH (a:T_MUS), (b:T_SLD) WHERE (a.uuid = b.Parent) AND " + clause(1, "a") + " AND " + clause(2, "b")
        + " RETURN count(*) AS n",
    ),
    (
        "comment_after_pattern",
        "MATCH (s:T_TIS) // tissues\nRETURN count(*) AS n",
        "MATCH (s:T_TIS) WHERE " + clause(1, "s") + " // tissues\nRETURN count(*) AS n",
    ),
    (
        "person_and_project",
        "MATCH (p:Person)-[:MEMBER_OF]->(proj:Project) RETURN proj.title AS project, count(p) AS people",
        f"MATCH (p:Person)-[:MEMBER_OF]->(proj:Project) WHERE proj.id IN {SP} "
        "RETURN proj.title AS project, count(p) AS people",
    ),
    (
        "backticked_variable",
        "MATCH (`my s`:T_TIS) RETURN `my s`.uuid AS uuid",
        "MATCH (`my s`:T_TIS) WHERE " + clause(1, "`my s`") + " RETURN `my s`.uuid AS uuid",
    ),
    (
        "reference_needs_nothing",
        "MATCH (s:T_TIS) WITH s MATCH (s)-[:IN_STUDY]->(st:Study) RETURN st.title AS t",
        "MATCH (s:T_TIS) WHERE " + clause(1, "s") + " WITH s MATCH (s)-[:IN_STUDY]->(st:Study) RETURN st.title AS t",
    ),
]


@pytest.mark.parametrize("name, cypher, expected", GOLDENS, ids=[g[0] for g in GOLDENS])
def test_injection_golden(name, cypher, expected):
    out = scope_cypher(cypher, {}, CALLER)
    assert isinstance(out, Scoped), getattr(out, "reasons", out)
    assert out.cypher == expected


# --------------------------------------------------------------------------- #
# Properties over every accepted statement
# --------------------------------------------------------------------------- #

ACCEPTED_TEXTS = [(c.id, c.cypher, c.params) for c in TAUGHT + ACCEPTED] + [
    (v[0], v[1], {}) for v in hidden_variants() if not v[2]]

_CARRIER = re.compile(r"^(\(|MATCH |__scope_(?:path|n)\d+(?: = )?| WHERE .*|\) AND .*|CASE WHEN .* THEN \(|"
                      r"\) ELSE false END)$", re.DOTALL)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIXED_WORDS = {"any", "all", "nodes", "IN", "WHERE", "AND", "MATCH", "project_ids", "id",
                "CASE", "WHEN", "THEN", "ELSE", "false", "END"}


@pytest.mark.parametrize("name, cypher, params", ACCEPTED_TEXTS, ids=[a[0] for a in ACCEPTED_TEXTS])
def test_output_is_the_input_plus_insertions_only(name, cypher, params):
    out, insertions = cypher_scope._scope_with_insertions(cypher, params, CALLER)
    assert isinstance(out, Scoped)
    rebuilt, last = [], 0
    for offset, text in insertions:
        assert offset >= last
        rebuilt.append(cypher[last:offset])
        rebuilt.append(text)
        last = offset
    rebuilt.append(cypher[last:])
    assert "".join(rebuilt) == out.cypher
    # Deleting the inserted spans gives the input back byte for byte.
    stripped, cursor = [], 0
    pieces = sorted(insertions, key=lambda item: item[0])
    for offset, _ in pieces:
        stripped.append(cypher[cursor:offset])
        cursor = offset
    stripped.append(cypher[cursor:])
    assert "".join(stripped) == cypher
    for _, text in insertions:
        assert _CARRIER.match(text), text


@pytest.mark.parametrize("name, cypher, params", ACCEPTED_TEXTS, ids=[a[0] for a in ACCEPTED_TEXTS])
def test_every_generated_name_is_reserved(name, cypher, params):
    out, insertions = cypher_scope._scope_with_insertions(cypher, params, CALLER)
    input_words = set(_WORD.findall(cypher))
    for _, text in insertions:
        for word in _WORD.findall(text):
            assert word in _FIXED_WORDS or word.startswith("__scope") or word in input_words, (word, text)
    generated = {w for _, text in insertions for w in _WORD.findall(text) if w.startswith("__scope")}
    assert all(w == "__scope_projects" or re.fullmatch(r"__scope_(?:p|m|n|path)\d+", w) for w in generated)


@pytest.mark.parametrize("name, cypher, params", ACCEPTED_TEXTS, ids=[a[0] for a in ACCEPTED_TEXTS])
def test_parameters_are_the_input_plus_the_scope(name, cypher, params):
    out = scope_cypher(cypher, params, CALLER)
    assert out.parameters == {**params, SCOPE_PARAM: [2, 13]}
    assert SCOPE_PARAM not in params


@pytest.mark.parametrize("name, cypher, params", ACCEPTED_TEXTS, ids=[a[0] for a in ACCEPTED_TEXTS])
def test_admin_gets_the_input_back(name, cypher, params):
    out = scope_cypher(cypher, params, ADMIN)
    assert isinstance(out, Scoped)
    assert (out.cypher, out.parameters, out.decision) == (cypher, params, "admin")


def test_an_empty_project_set_injects_an_empty_list():
    out = scope_cypher("MATCH (s:T_TIS) RETURN count(*) AS n", {}, NOBODY)
    assert isinstance(out, Scoped)
    assert out.parameters == {SCOPE_PARAM: []}
    assert clause(1, "s") in out.cypher


def test_generated_names_are_never_reused_in_one_statement():
    out = scope_cypher(
        "MATCH (a:T_MUS)<-[:DERIVED_FROM]-(:T_TIS)<-[:DERIVED_FROM]-(:T_SLD) "
        "WHERE EXISTS { (a)-[:DERIVED_FROM*1..3]->(:Sample) } AND EXISTS { (a)<-[:DERIVED_FROM*1..3]-(:Sample) } "
        "RETURN count(*) AS n", {}, CALLER)
    assert isinstance(out, Scoped)
    bound = (re.findall(r"(?:any|all)\((__scope_[pm]\d+) IN", out.cypher)
             + re.findall(r"(__scope_path\d+) = ", out.cypher)
             + re.findall(r"\((__scope_n\d+)[:)]", out.cypher))
    assert len(bound) == len(set(bound)), bound
    assert {name[:10] for name in bound} >= {"__scope_p1", "__scope_m1", "__scope_n1", "__scope_pa"}


def test_input_parameters_are_not_mutated():
    params = {"uid": "X"}
    scope_cypher("MATCH (s:Sample {uuid: $uid}) RETURN s.id AS id", params, CALLER)
    assert params == {"uid": "X"}


# --------------------------------------------------------------------------- #
# strip_hidden
# --------------------------------------------------------------------------- #

class FakeNode:
    def __init__(self, labels, props, element_id="4:x:1"):
        self.labels = frozenset(labels)
        self.element_id = element_id
        self._props = dict(props)

    def items(self):
        return self._props.items()


class FakeRelationship:
    def __init__(self, rel_type, props, element_id="5:x:1"):
        self.type = rel_type
        self.element_id = element_id
        self._props = dict(props)
        self.start_node = None
        self.end_node = None

    def items(self):
        return self._props.items()


class FakePath:
    def __init__(self, nodes, relationships):
        self.nodes = tuple(nodes)
        self.relationships = tuple(relationships)
        self.start_node = self.nodes[0]
        self.end_node = self.nodes[-1]


def _sample(uuid):
    return FakeNode(["Sample", "T_TIS"], {"uuid": uuid, "parent_titles": ["foreign"],
                                          "parent_title_hashes": ["h"], "Organ": "Lung"})


def test_strip_hidden_node():
    assert strip_hidden(_sample("A")) == {"uuid": "A", "Organ": "Lung"}


def test_strip_hidden_relationship_and_path():
    rel = FakeRelationship("DERIVED_FROM", {"internal_assay_title": "Staining"})
    assert strip_hidden(rel) == {"internal_assay_title": "Staining"}
    path = FakePath([_sample("A"), _sample("B")], [rel])
    assert strip_hidden(path) == {
        "nodes": [{"uuid": "A", "Organ": "Lung"}, {"uuid": "B", "Organ": "Lung"}],
        "relationships": [{"internal_assay_title": "Staining"}],
    }


def test_strip_hidden_walks_lists_tuples_and_maps():
    rows = [{"s": _sample("A"), "kids": [_sample("B"), (_sample("C"),)], "n": 3, "m": {"x": _sample("D")}}]
    assert strip_hidden(rows) == [{
        "s": {"uuid": "A", "Organ": "Lung"},
        "kids": [{"uuid": "B", "Organ": "Lung"}, ({"uuid": "C", "Organ": "Lung"},)],
        "n": 3,
        "m": {"x": {"uuid": "D", "Organ": "Lung"}},
    }]


@pytest.mark.parametrize("value", [None, 1, 2.5, "parent_titles", True, [], {}, ("a",)])
def test_strip_hidden_passes_other_values(value):
    assert strip_hidden(value) == value


def test_strip_hidden_keeps_a_plain_map_key_named_like_a_hidden_property():
    # An output alias of that name is fine (spec 5.5); only graph values are stripped.
    assert strip_hidden({"parent_titles": "an alias"}) == {"parent_titles": "an alias"}
