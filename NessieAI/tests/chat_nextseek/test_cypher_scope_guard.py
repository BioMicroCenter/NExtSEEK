"""
The scope clauses guard the model's own WHERE, so nothing in it that could raise is evaluated on a foreign node.

The prover once emitted ``WHERE (<model's predicate>) AND <clauses>``. Neo4j evaluated the model's predicate first, so
a predicate that raises on some value (``1/0`` inside a CASE, ``toLower`` of a number) failed the statement when a
node outside the caller's projects matched and returned a clean zero when none did: one bit about a foreign node per
turn (a review's two probes are below, and ``graph_scope/test_scope_error_oracle_lane.py`` proves the leak and the fix
on a real Neo4j). Now the span from the first conjunct that could raise to the last
becomes ``CASE WHEN <clauses> THEN (...) ELSE false END``. Conjuncts that cannot raise stay outside, because Neo4j
cannot seek an index on a predicate inside CASE: a UID lookup would become a scan of every sample.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 5.6.
"""
from __future__ import annotations

import pytest

from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope

CALLER = GraphScope.for_projects([2, 13], source="test")
SP = "$__scope_projects"


def clause(k: int, var: str) -> str:
    return f"any(__scope_p{k} IN {var}.project_ids WHERE __scope_p{k} IN {SP})"


def _scoped(cypher: str, params: dict | None = None) -> str:
    out = scope_cypher(cypher, params or {}, CALLER)
    assert isinstance(out, Scoped), getattr(out, "reasons", out)
    return out.cypher


# The review's two probes, with a synthetic UID: before the fix both ran as "WHERE (<predicate>) AND <clause>".
REVIEW_PROBES = [
    (
        "MATCH (s:Sample {uuid:'TIS-230102AAA-2'}) WHERE (CASE WHEN s.Organ STARTS WITH 'L' THEN 1/0 ELSE 1 END)=1 "
        "RETURN count(s)",
        "MATCH (s:Sample {uuid:'TIS-230102AAA-2'}) WHERE CASE WHEN " + clause(1, "s") + " THEN ((CASE WHEN s.Organ "
        "STARTS WITH 'L' THEN 1/0 ELSE 1 END)=1) ELSE false END RETURN count(s)",
    ),
    (
        "MATCH (s:Sample) WHERE s.uuid = 'TIS-230102AAA-2' AND toInteger(s.Organ) > 0 RETURN count(s)",
        "MATCH (s:Sample) WHERE s.uuid = 'TIS-230102AAA-2' AND CASE WHEN " + clause(1, "s") + " THEN "
        "(toInteger(s.Organ) > 0) ELSE false END RETURN count(s)",
    ),
]


@pytest.mark.parametrize("cypher, expected", REVIEW_PROBES, ids=["case_division", "seek_then_conversion"])
def test_the_reviews_probes_are_guarded(cypher, expected):
    assert _scoped(cypher) == expected


# Conjuncts that cannot raise: the WHERE keeps the (<predicate>) AND <clauses> form Neo4j can seek an index from.
HARMLESS = [
    ("s.uuid = $uid", {"uid": "X"}),
    ("s.uuid = 'X'", {}),
    ("'X' = s.uuid", {}),
    ("s.uuid <> 'X'", {}),
    ("s.uuid != 'X'", {}),
    ("s.Concentration > -1", {}),
    ("s.Concentration <= 2.5", {}),
    ("s.uuid STARTS WITH 'SLD-'", {}),
    ("s.uuid ENDS WITH '-PUB'", {}),
    ("s.title CONTAINS $term", {"term": "x"}),
    ("s.uuid IN ['A', 'B', $c]", {"c": "C"}),
    ("s.uuid IN []", {}),
    ("s.uuid IN $uids", {"uids": ["A"]}),
    ("s.uuid IN $uids", {"uids": None}),
    ("s.Vendor IS NOT NULL", {}),
    ("s.Vendor IS NULL", {}),
    ("s.flag = true", {}),
    ("s.flag = NULL", {}),
    ("s:T_TIS", {}),
    ("s:T_TIS:Sample", {}),
    ("s.uuid = $uid AND s.type = $type AND s.Vendor IS NOT NULL", {"uid": "X", "type": "TIS"}),
]


@pytest.mark.parametrize("where, params", HARMLESS, ids=[h[0] + str(sorted(h[1].items())) for h in HARMLESS])
def test_a_where_that_cannot_raise_keeps_the_seekable_form(where, params):
    out = _scoped(f"MATCH (s:Sample) WHERE {where} RETURN s.id AS id", params)
    assert out == f"MATCH (s:Sample) WHERE ({where}) AND {clause(1, 's')} RETURN s.id AS id"


def test_relationship_properties_and_two_pattern_variables_cannot_raise():
    out = _scoped("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE r.internal_assay_title = $a AND "
                  "p.uuid <> c.uuid RETURN count(*) AS n", {"a": "Staining"})
    assert "CASE WHEN" not in out
    assert out.startswith("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE (r.internal_assay_title = $a AND "
                          "p.uuid <> c.uuid) AND ")


def test_the_yielded_fulltext_node_and_its_score_cannot_raise():
    out = _scoped("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score "
                  "WHERE s:T_MUS AND score > 0.5 RETURN s.uuid AS uuid", {"q": "x"})
    assert "CASE WHEN" not in out
    assert "WHERE (s:T_MUS AND score > 0.5) AND " + clause(1, "s") in out


# Conjuncts that could raise, or whose operands the prover cannot vouch for: each is guarded.
RISKY = [
    ("toLower(s.Organ) = 'lung'", {}),
    ("s.Concentration / 2 > 1", {}),
    ("s.Concentration + 1 > 1", {}),
    ("(s.uuid = 'X')", {}),
    ("NOT s.uuid = 'X'", {}),
    ("s.flag", {}),
    ("s.uuid = 'X' OR s.uuid = 'Y'", {}),
    ("s.uuid =~ $pattern", {"pattern": "("}),
    ("s.uuid IN $uids", {"uids": "not a list"}),
    ("s.uuid IN $missing", {}),
    ("s.uuid IN s.Parents", {}),
    ("s.uuid IN [toLower($a)]", {"a": "X"}),
    ("s.Tags[0] = 'x'", {}),
    ("s.uuid STARTS WITH $a + '-PUB'", {"a": "X"}),
    ("any(v IN [s.Treatment1] WHERE v = 'x')", {}),
    ("EXISTS { (s)-[:DERIVED_FROM]->(:Sample) }", {}),
    ("CASE WHEN s.x = 1 THEN true ELSE false END", {}),
    ("s.uuid = 'X' XOR s.uuid = 'Y'", {}),
    ("1 < s.Concentration < 3", {}),
]


@pytest.mark.parametrize("where, params", RISKY, ids=[r[0] + str(sorted(r[1].items())) for r in RISKY])
def test_a_where_that_could_raise_is_guarded(where, params):
    out = _scoped(f"MATCH (s:Sample) WHERE {where} RETURN s.id AS id", params)
    guarded = f"MATCH (s:Sample) WHERE CASE WHEN {clause(1, 's')} THEN ({where}) ELSE false END RETURN s.id AS id"
    if where.startswith("EXISTS"):
        assert out.startswith(f"MATCH (s:Sample) WHERE CASE WHEN {clause(2, 's')} THEN (EXISTS {{ MATCH ")
        assert out.endswith(") ELSE false END RETURN s.id AS id")
    else:
        assert out == guarded


def test_the_guard_spans_first_to_last_risky_conjunct_and_leaves_the_rest_outside():
    where = ("s.uuid IN $uids AND toLower(s.Organ) = 'lung' AND s.type = $t AND toFloat(s.Weight) > 2 "
             "AND s.Vendor IS NOT NULL")
    out = _scoped(f"MATCH (s:Sample) WHERE {where} RETURN s.id AS id", {"uids": ["A"], "t": "TIS"})
    assert out == ("MATCH (s:Sample) WHERE s.uuid IN $uids AND CASE WHEN " + clause(1, "s") + " THEN "
                   "(toLower(s.Organ) = 'lung' AND s.type = $t AND toFloat(s.Weight) > 2) ELSE false END "
                   "AND s.Vendor IS NOT NULL RETURN s.id AS id")


def test_a_property_of_a_name_bound_earlier_is_guarded():
    """Only this pattern's own nodes and single relationships are known to be graph values: a name bound earlier may
    be anything, and reading a property of a number raises."""
    out = _scoped("UNWIND $rows AS c MATCH (s:Sample) WHERE s.uuid = c.uid RETURN s.id AS id", {"rows": []})
    assert "CASE WHEN " + clause(1, "s") + " THEN (s.uuid = c.uid) ELSE false END" in out
    out = _scoped("MATCH (a:T_MUS) WITH a MATCH (a)<-[:DERIVED_FROM]-(s:T_TIS) WHERE a.Strain = 'x' "
                  "RETURN count(s) AS n")
    assert "CASE WHEN " + clause(2, "s") + " THEN (a.Strain = 'x') ELSE false END" in out


def test_a_variable_length_relationship_and_a_path_are_not_property_holders():
    out = _scoped("MATCH p = (s:T_SLD)-[r:DERIVED_FROM*1..3]->(m:T_MUS) WHERE m.Strain = 'x' AND "
                  "length(p) = 2 RETURN count(*) AS n")
    assert "WHERE m.Strain = 'x' AND CASE WHEN " in out and " THEN (length(p) = 2) ELSE false END" in out


def test_an_optional_match_keeps_its_meaning_under_the_guard():
    out = _scoped("MATCH (s:T_TIS {uuid: $uid}) OPTIONAL MATCH (s)-[:DERIVED_FROM]->(p:Sample) "
                  "WHERE toLower(p.Organ) = 'lung' RETURN s.uuid, p.uuid", {"uid": "X"})
    assert out == ("MATCH (s:T_TIS {uuid: $uid}) WHERE " + clause(1, "s") + " OPTIONAL MATCH "
                   "(s)-[:DERIVED_FROM]->(p:Sample) WHERE CASE WHEN " + clause(2, "p") + " THEN "
                   "(toLower(p.Organ) = 'lung') ELSE false END RETURN s.uuid, p.uuid")


def test_the_guard_keeps_a_comment_after_the_predicate():
    out = _scoped("MATCH (s:T_TIS) WHERE toLower(s.Organ) = 'lung' // tissues\nRETURN count(*) AS n")
    assert out == ("MATCH (s:T_TIS) WHERE CASE WHEN " + clause(1, "s") + " THEN (toLower(s.Organ) = 'lung') "
                   "ELSE false END // tissues\nRETURN count(*) AS n")
