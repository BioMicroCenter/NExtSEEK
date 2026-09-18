"""P6a: a query shape the graph agent's Cypher must not run, checked before the query does.

P6a, a variable-length path over DERIVED_FROM (``-[:DERIVED_FROM*1..8]->``, and the quantified forms
``-[:DERIVED_FROM]->{1,8}`` and ``->+``). The bound is ``cypher_text.APOC_PATH_MAX_LEVEL`` (12): the longest
DERIVED_FROM chain in the graph is 11 hops, so ``*1..12`` reaches every ancestor and every descendant and a repair
that complies loses nothing. The rule:

- a literal maximum from 1 to 12 passes when at least one end is anchored: a sample type label (``T_<code>``) or
  ``type`` predicate, a uuid or id pin, a predicate that narrows the end, a fulltext hit, or a fixed-length
  neighbour of any of those;
- no maximum, a parameter maximum or a maximum above 12 passes only when BOTH ends carry a type or a pin;
- ``IS NOT NULL``, ``IS NULL``, ``<>`` and anything under ``NOT`` narrow nothing, and a disjunction narrows only when
  every branch does.

Every shape here is synthetic.
"""
from __future__ import annotations

import pytest

from chat_nextseek import cypher_text
from chat_nextseek.agents import graph as graph_mod

MAX = cypher_text.APOC_PATH_MAX_LEVEL


def kinds(cypher, parameters=None) -> list[str]:
    return [problem.kind for problem in graph_mod.query_shape_problems(cypher, parameters)]


def passes(cypher, parameters=None) -> bool:
    return graph_mod.query_shape_problems(cypher, parameters) == []


def test_the_bound_is_the_apoc_path_bound_of_twelve():
    assert MAX == 12


# ------------------------------------------------------------------------------------------- P6a: the bound


@pytest.mark.parametrize("hops", ["*1..12", "*0..12", "*..12", "*3", "*12", "*1..8", "* 1 .. 12", "*1..1"])
def test_a_maximum_from_one_to_twelve_passes_with_one_anchored_end(hops):
    assert passes(f"MATCH (d:T_D_SEQ)-[:DERIVED_FROM{hops}]->(p:Sample) RETURN count(DISTINCT p) AS n")


@pytest.mark.parametrize("hops", ["*", "*..", "*0..", "*1..", "*2..", "* 1 ..", "*1..13", "*1..99999",
                                  "*0..2147483647", "*20", "*13"])
def test_no_maximum_or_one_above_twelve_is_refused_with_one_anchored_end(hops):
    cypher = f"MATCH (d:T_D_SEQ)-[:DERIVED_FROM{hops}]->(p:Sample) RETURN count(DISTINCT p) AS n"
    assert kinds(cypher) == ["unbounded_path"]


def test_a_parameter_maximum_is_no_maximum():
    cypher = "MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..$hops]->(p:Sample) RETURN count(DISTINCT p) AS n"
    problems = graph_mod.query_shape_problems(cypher, {"hops": 4})
    assert [p.kind for p in problems] == ["unbounded_path"] and "parameter" in problems[0].detail


def test_a_parameter_in_the_relationship_map_is_not_a_parameter_maximum():
    cypher = "MATCH (d:T_D_SEQ)-[r:DERIVED_FROM*1..4 {internal_assay_title: $a}]->(p:Sample) RETURN count(*) AS n"
    assert passes(cypher, {"a": "x"})


@pytest.mark.parametrize("hops", ["*", "*1..", "*0..", "*1..99999", "*0..2147483647"])
def test_no_maximum_passes_when_both_ends_carry_a_type_label(hops):
    assert passes(f"MATCH (d:T_D_IMG)-[:DERIVED_FROM{hops}]->(o:T_OOC) RETURN count(DISTINCT d) AS n")


def test_no_maximum_passes_when_both_ends_are_pinned_by_uuid():
    assert passes("MATCH (a:Sample {uuid: $a})-[:DERIVED_FROM*]->(b:Sample {uuid: $b}) RETURN count(*) AS n",
                  {"a": "X-1", "b": "X-2"})


@pytest.mark.parametrize("fn", ["shortestPath", "allShortestPaths"])
def test_a_shortest_path_between_two_pinned_ends_passes_unbounded(fn):
    inline = f"MATCH p = {fn}((a:Sample {{uuid: $a}})-[:DERIVED_FROM*]-(b:Sample {{uuid: $b}})) RETURN length(p) AS n"
    earlier = (f"MATCH (a:Sample {{uuid: $a}}), (b:Sample {{uuid: $b}}) "
               f"OPTIONAL MATCH p = {fn}((a)-[:DERIVED_FROM*]-(b)) RETURN count(p) AS n")
    by_id = (f"MATCH (a:Sample), (b:Sample) WHERE a.id = 1 AND b.id = 2 "
             f"MATCH p = {fn}((a)-[:DERIVED_FROM*]-(b)) RETURN length(p) AS n")
    assert passes(inline) and passes(earlier) and passes(by_id)


def test_a_shortest_path_with_an_unanchored_end_is_still_checked():
    cypher = "MATCH p = shortestPath((a:Sample {uuid: $a})-[:DERIVED_FROM*]-(b:Sample)) RETURN length(p) AS n"
    assert kinds(cypher) == ["unbounded_path"]


def test_one_pinned_end_is_not_enough_for_no_maximum():
    """The design: an unbounded path needs both ends. `*1..12` is the lossless repair (the longest chain is 11)."""
    cypher = ("MATCH (s:Sample {uuid: $uid}) MATCH path = (s)-[:DERIVED_FROM*1..]->(a:Sample) "
              "RETURN a.uuid AS uuid, length(path) AS distance")
    assert kinds(cypher) == ["unbounded_path"]
    assert passes(cypher.replace("*1..]", f"*1..{MAX}]"))


@pytest.mark.parametrize("anchor", [
    "WHERE a.type = $t AND b.type = $u",
    "WHERE a.type IN $ts AND b.type IN ['NHP']",
    "WHERE a:T_D_SEQ AND b:T_NHP",
    "WHERE a.uuid = $a AND b.uuid IN $bs",
    "WHERE a.id = 7 AND b:T_NHP",
    "WHERE (a:T_D_SEQ OR a:T_D_IMG) AND b.type = 'NHP'",
])
def test_type_and_pin_predicates_anchor_an_unbounded_path(anchor):
    cypher = f"MATCH (a:Sample)-[:DERIVED_FROM*1..]->(b:Sample) {anchor} RETURN count(*) AS n"
    assert passes(cypher, {"t": "D.SEQ", "u": "NHP", "ts": ["D.SEQ"], "a": "X-1", "bs": ["X-2"]})


def test_an_inline_type_map_anchors_both_ends():
    cypher = "MATCH (:Sample {type: 'D.SEQ'})-[:DERIVED_FROM*1..]->(n:Sample {type: 'NHP'}) RETURN count(DISTINCT n)"
    assert passes(cypher)


def test_the_and_logic_pattern_types_both_ends_through_aliased_parameters():
    """The collect/size pattern the committed prompt teaches: both ends typed, so it passes unbounded."""
    cypher = ("WITH $child_types AS child_types, $parent_types AS parent_types\n"
              "MATCH (p:Sample)\nWHERE p.type IN parent_types\n"
              "MATCH (c:Sample)-[:DERIVED_FROM*1..]->(p)\nWHERE c.type IN child_types\n"
              "WITH p, collect(DISTINCT c.type) AS matched_types, child_types\n"
              "WHERE size(matched_types) = size(child_types)\nRETURN p.id AS id, p.uuid AS uuid")
    assert passes(cypher, {"child_types": ["D.SEQ", "D.FLOW"], "parent_types": ["NHP"]})


def test_the_ancestor_pattern_with_a_weak_start_is_refused_unbounded_and_passes_bounded():
    """The two-part ancestor pattern the committed prompt teaches: the start is only filtered, not typed."""
    cypher = ("MATCH (child:Sample)-[r:DERIVED_FROM]->(assay_parent:Sample)\nWHERE r.internal_assay_title = $assay\n"
              "MATCH (assay_parent)-[:DERIVED_FROM*0..]->(ancestor:Sample)\nWHERE ancestor.type = $ancestor_type\n"
              "RETURN DISTINCT ancestor.id AS id")
    assert kinds(cypher) == ["unbounded_path"]
    assert passes(cypher.replace("*0..]", f"*0..{MAX}]"))


def test_two_exists_subqueries_with_typed_ends_pass_unbounded():
    cypher = ("MATCH (m:T_MUS)\nWHERE EXISTS {\n  MATCH (s:T_D_SEQ)-[:DERIVED_FROM*1..]->(m)\n} AND EXISTS {\n"
              "  MATCH (i:T_D_IMG)-[:DERIVED_FROM*1..]->(m)\n}\nRETURN m.id AS id")
    assert passes(cypher)


def test_a_single_hop_is_not_a_variable_length_path():
    assert passes("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE r.internal_assay_title = $a RETURN count(*)")


@pytest.mark.parametrize("cypher", [
    "WITH [1, 2, 3] AS xs RETURN [x IN xs | x * 2] AS doubled",
    "MATCH (s:T_TIS) RETURN count(*) * 2 AS n",
    "RETURN 2 * (3 + 4) AS n",
    "MATCH (s:T_TIS) RETURN size(s.project_ids) + (1) AS n",
])
def test_arithmetic_and_lists_are_not_paths(cypher):
    assert passes(cypher)


def test_an_empty_or_missing_cypher_has_no_shape_problems():
    assert passes("") and passes(None) and passes("   ")


# ------------------------------------------------------------------- P6a: quantified path patterns (Cypher 5)


@pytest.mark.parametrize("pattern", [
    "(a:Sample)-[:DERIVED_FROM]->+(b:Sample)",
    "(a:Sample)-[:DERIVED_FROM]->{1,}(b:Sample)",
    "(a:Sample)-[:DERIVED_FROM]->*(b:Sample)",
    "(a:Sample)-->+(b:Sample)",
    "((a:Sample)-[:DERIVED_FROM]->(b:Sample)){1,99}",
])
def test_a_quantified_path_with_no_anchor_and_no_bound_is_refused(pattern):
    assert kinds(f"MATCH {pattern} RETURN count(*) AS n") == ["unbounded_path"]


@pytest.mark.parametrize("pattern", [
    "(a:T_D_SEQ)-[:DERIVED_FROM]->{1,12}(b:Sample)",
    "(a:T_D_SEQ)-[:DERIVED_FROM]->{,8}(b:Sample)",
    "(a:T_D_SEQ)-[:DERIVED_FROM]->{3}(b:Sample)",
    "(a:T_D_SEQ)-[:DERIVED_FROM]->+(b:T_NHP)",
    "(x:T_D_SEQ) ((a)-[:DERIVED_FROM]->(b)){1,12} (y:Sample)",
])
def test_a_quantified_path_follows_the_same_rule(pattern):
    assert passes(f"MATCH {pattern} RETURN count(*) AS n")


def test_a_quantified_path_between_bare_samples_with_a_bound_is_unanchored():
    assert kinds("MATCH (a:Sample)-[:DERIVED_FROM]->{1,8}(b:Sample) RETURN count(*) AS n") == ["unanchored_path"]


# ------------------------------------------------------------------------------------------ P6a: anchors


def test_a_bounded_path_between_two_bare_sample_ends_is_unanchored():
    assert kinds("MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN count(*) AS n") == ["unanchored_path"]


@pytest.mark.parametrize("where", [
    "WHERE a.uuid IS NOT NULL",
    "WHERE a.Organ IS NULL",
    "WHERE a.type <> 'NHP'",
    "WHERE a.type != 'NHP'",
    "WHERE NOT a:T_NHP",
    "WHERE NOT a.Organ = 'x'",
    "WHERE a.Organ = $o OR a.uuid IS NOT NULL",
    "WHERE a.Organ = $o OR b.uuid IS NOT NULL",
    "WHERE a.search_text CONTAINS ''",
    "WHERE a.search_text =~ '.*'",
])
def test_predicates_that_narrow_nothing_do_not_anchor(where):
    cypher = f"MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) {where} RETURN count(*) AS n"
    assert kinds(cypher, {"o": "x"}) == ["unanchored_path"]


@pytest.mark.parametrize("where", [
    "WHERE toLower(a.Organ) = toLower($o)",
    "WHERE toLower(a.search_text) CONTAINS 'foo bar'",
    "WHERE a.Age > 30",
    "WHERE a.Organ = $o OR a.Organ = 'spleen'",
    "WHERE (a.Organ = $o) AND b.uuid IS NOT NULL",
    "WHERE b.Organ STARTS WITH 'lu'",
])
def test_a_predicate_that_narrows_one_end_anchors_a_bounded_path(where):
    cypher = f"MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) {where} RETURN count(*) AS n"
    assert passes(cypher, {"o": "lung"})


def test_a_narrowing_predicate_does_not_anchor_an_unbounded_path():
    cypher = "MATCH (a:Sample)-[:DERIVED_FROM*1..]->(b:T_NHP) WHERE toLower(a.Organ) = 'lung' RETURN count(*) AS n"
    assert kinds(cypher) == ["unbounded_path"]


def test_an_anchor_carried_by_a_with_alias_is_seen():
    cypher = "MATCH (x:T_D_SEQ) WITH x AS a MATCH (a)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN count(*) AS n"
    assert passes(cypher)


def test_anchors_introduced_by_a_later_clause_are_seen():
    bounded = "MATCH (a)-[:DERIVED_FROM*1..8]->(b) WHERE a:T_D_SEQ RETURN count(*) AS n"
    unbounded = "MATCH (a)-[:DERIVED_FROM*]->(b) MATCH (a:T_D_SEQ) WHERE b.uuid = $u RETURN count(*) AS n"
    assert passes(bounded) and passes(unbounded, {"u": "X-1"})


def test_an_earlier_binding_that_is_itself_unanchored_does_not_anchor():
    cypher = "MATCH (a:Sample) MATCH (a)-[:DERIVED_FROM*1..8]->(b:Sample) RETURN count(*) AS n"
    assert kinds(cypher) == ["unanchored_path"]


def test_a_fixed_length_neighbour_of_an_anchored_node_anchors_a_bounded_path():
    cypher = ("MATCH (d:T_D_FLOW)-[r:DERIVED_FROM]->(p:Sample)\nMATCH (p)-[:DERIVED_FROM*0..8]->(n:Sample)\n"
              "RETURN count(DISTINCT d) AS n")
    assert passes(cypher)


def test_a_filtered_relationship_anchors_its_ends_for_a_bounded_path_only():
    bounded = ("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE r.internal_assay_title = $a "
               "MATCH (p)-[:DERIVED_FROM*0..8]->(q:Sample) RETURN count(*) AS n")
    unbounded = bounded.replace("*0..8]->(q:Sample)", "*0..]->(q:T_TIS)")
    assert passes(bounded) and kinds(unbounded) == ["unbounded_path"]


def test_a_fulltext_hit_anchors_a_bounded_path():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS parent\n"
              "MATCH (child:Sample)-[:DERIVED_FROM*1..8]->(parent)\nRETURN count(DISTINCT child) AS n")
    assert passes(cypher, {"q": "foo"})


def test_an_unbounded_path_from_a_filtered_join_to_a_typed_fulltext_hit_is_refused():
    """A typed fulltext hit on one end and a relationship-filtered sample on the other: only one end is strong."""
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', 'foo') YIELD node AS parent, score "
              "WHERE parent:T_TIS MATCH (child:Sample)-[r:DERIVED_FROM]->(ancestor:Sample) "
              "WHERE toLower(r.protocol_title) CONTAINS 'bar' MATCH (ancestor)-[:DERIVED_FROM*0..]->(parent) "
              "RETURN DISTINCT parent.id AS id")
    assert kinds(cypher) == ["unbounded_path"]


def test_one_problem_per_path_in_the_order_written():
    cypher = ("MATCH (a:Sample)-[:DERIVED_FROM*1..3]->(b:Sample) "
              "MATCH (c:T_TIS)-[:DERIVED_FROM*]->(d:Sample) RETURN count(*) AS n")
    assert kinds(cypher) == ["unanchored_path", "unbounded_path"]


# ----------------------------------------------------------------------------------------- P6a: the messages


def _message(cypher, parameters=None) -> str:
    return graph_mod._shape_repair_message(graph_mod.query_shape_problems(cypher, parameters))


def test_the_path_repair_names_twelve_and_the_eleven_hop_chain_and_no_other_bound():
    message = _message("MATCH (d:T_D_SEQ)-[:DERIVED_FROM*0..]->(p:Sample) RETURN count(*) AS n")
    assert f"*1..{MAX}" in message and f"*0..{MAX}" in message and "11 hops" in message
    assert "[:DERIVED_FROM*0..]" in message
    for wrong in ("*1..8", "*1..6", "*1..10"):
        assert wrong not in message


def test_the_path_repair_for_a_bound_above_twelve_says_so():
    message = _message("MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..99999]->(p:Sample) RETURN count(*) AS n")
    assert "99999" in message and f"above {MAX}" in message


def test_the_anchor_repair_names_the_anchors_and_what_does_not_anchor():
    message = _message("MATCH (a:Sample)-[:DERIVED_FROM*1..8]->(b:Sample) WHERE a.uuid IS NOT NULL RETURN count(*)")
    assert "T_" in message and "uuid" in message and "IS NOT NULL" in message


def test_the_path_repair_ends_by_asking_for_the_query_again():
    assert "Regenerate the Cypher" in _message("MATCH (a:Sample)-[:DERIVED_FROM*]->(b:Sample) RETURN count(*)")


def test_the_path_refusal_says_what_was_wrong_without_claiming_the_query_cannot_return():
    problems = graph_mod.query_shape_problems("MATCH (d:T_D_SEQ)-[:DERIVED_FROM*1..]->(p:Sample) RETURN count(*)")
    refusal = graph_mod._shape_refusal(problems)
    assert refusal.startswith("Graph agent could not produce valid Cypher")
    assert "[:DERIVED_FROM*1..]" in refusal and "both ends" in refusal and "does not return" not in refusal


def test_refused_query_shapes_is_one_line_per_problem():
    lines = graph_mod.refused_query_shapes("MATCH (a:Sample)-[:DERIVED_FROM*]->(b:Sample) RETURN count(*)")
    assert len(lines) == 1 and lines[0].startswith("unbounded path [:DERIVED_FROM*]")
