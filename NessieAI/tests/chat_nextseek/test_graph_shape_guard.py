"""P6a and P6b: two query shapes the graph agent's Cypher must not run, checked before the query does.

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

P6b, an unscoped fulltext call. The index analyses its term: it lowercases it, splits it at spaces and punctuation
and matches each word anywhere in a sample's text, so a term of two or more words cannot keep them side by side,
whether they are written side by side, joined by a hyphen or joined by ``AND``, ``&&`` or ``+``. Unscoped, the call
answers from the whole database. It passes when its hits are scoped (a type, a pin or a narrowing predicate on the
hit or on a variable equated with it) or when its term is one word, one quoted phrase, or an explicit ``OR`` of those
(which asks for the union and loses no adjacency). A term the guard cannot read (a parameter the plan does not bind,
an expression it cannot evaluate) is refused when the call is unscoped: it is resolved through a literal, a
parameter, ``WITH $q AS t``, ``UNWIND $terms AS t``, ``$terms[0]``, string concatenation and
``toLower``/``toUpper``/``trim``, and refused otherwise.

Every shape here is synthetic.
"""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from chat_nextseek import cypher_text
from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.schemas import GraphAgentPlan

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


# -------------------------------------------------------------------------------- P6b: the term


FT = "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node RETURN count(node) AS n"


@pytest.mark.parametrize("term", [
    "foo-bar", "foo bar", "foo AND bar", "foo && bar", "+foo +bar", "foo-bar OR foobar", "(foo AND bar) OR baz",
    "foo NOT bar", "foo -bar", "\"foo bar\" AND baz", "foo  OR  bar baz",
])
def test_an_unscoped_term_of_several_words_is_refused(term):
    assert kinds(FT, {"q": term}) == ["unscoped_fulltext"]


@pytest.mark.parametrize("term", [
    "foo", "Foo", "foo*", "foo~", "foo~2", "foo^2", "fo?o", "search_text:foo", "foo_bar", "\"foo bar\"",
    "\"foo-bar\"", "\"foo bar\"~2", "foo OR bar", "foo || bar", "foo OR bar OR baz",
    "\"foo bar\" OR \"foo-bar\" OR foobar", "(foo OR bar)", "  foo  ",
])
def test_an_unscoped_term_of_one_word_one_phrase_or_an_or_of_those_passes(term):
    assert passes(FT, {"q": term})


def test_an_inline_literal_term_is_read():
    assert kinds("CALL db.index.fulltext.queryNodes('sample_search_text', 'foo-bar') YIELD node "
                 "RETURN count(node) AS n") == ["unscoped_fulltext"]
    assert passes("CALL db.index.fulltext.queryNodes(\"sample_search_text\", \"foo\") YIELD node "
                  "RETURN count(node) AS n")


@pytest.mark.parametrize("cypher, params, expected", [
    ("WITH $q AS t CALL db.index.fulltext.queryNodes('sample_search_text', t) YIELD node RETURN count(node)",
     {"q": "foo bar"}, ["unscoped_fulltext"]),
    ("WITH $q AS t CALL db.index.fulltext.queryNodes('sample_search_text', t) YIELD node RETURN count(node)",
     {"q": "foo"}, []),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', $terms[0]) YIELD node RETURN count(node)",
     {"terms": ["foo-bar", "baz"]}, ["unscoped_fulltext"]),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', $terms[1]) YIELD node RETURN count(node)",
     {"terms": ["foo-bar", "baz"]}, []),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', $a + ' ' + $b) YIELD node RETURN count(node)",
     {"a": "foo", "b": "bar"}, ["unscoped_fulltext"]),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', '\"' + $p + '\"') YIELD node RETURN count(node)",
     {"p": "foo bar"}, []),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', toLower($q)) YIELD node RETURN count(node)",
     {"q": "Foo-Bar"}, ["unscoped_fulltext"]),
    ("UNWIND $terms AS t CALL db.index.fulltext.queryNodes('sample_search_text', t) YIELD node "
     "RETURN t, count(node)", {"terms": ["foo", "bar baz"]}, ["unscoped_fulltext"]),
    ("UNWIND $terms AS t CALL db.index.fulltext.queryNodes('sample_search_text', t) YIELD node "
     "RETURN t, count(node)", {"terms": ["foo", "bar"]}, []),
    ("WITH $q AS t WITH t AS u CALL db.index.fulltext.queryNodes('sample_search_text', u) YIELD node "
     "RETURN count(node)", {"q": "foo bar"}, ["unscoped_fulltext"]),
])
def test_a_term_reached_through_an_expression_is_resolved(cypher, params, expected):
    assert kinds(cypher, params) == expected


@pytest.mark.parametrize("cypher, params", [
    (FT, None),
    (FT, {"other": "foo"}),
    (FT, {"q": ["foo"]}),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', apoc.text.join($ts, ' ')) YIELD node "
     "RETURN count(node)", {"ts": ["foo"]}),
    ("MATCH (s:T_TIS) WITH s.Organ AS t CALL db.index.fulltext.queryNodes('sample_search_text', t) YIELD node "
     "RETURN count(node)", {}),
])
def test_an_unscoped_term_the_guard_cannot_read_is_refused(cypher, params):
    problems = graph_mod.query_shape_problems(cypher, params)
    assert [p.kind for p in problems] == ["unscoped_fulltext"]
    assert "cannot be read" in problems[0].detail


def test_a_scoped_call_passes_whatever_its_term():
    cypher = FT.replace("YIELD node", "YIELD node WHERE node:T_D_SEQ")
    assert passes(cypher, {"q": "foo-bar"}) and passes(cypher, None)


# -------------------------------------------------------------------------------- P6b: the scope


@pytest.mark.parametrize("cypher", [
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score WHERE node:T_D_IMG RETURN node.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD score, node WHERE node:T_D_IMG RETURN node.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score WHERE s:T_AB RETURN s.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score MATCH (s:T_D_IMG) WHERE s = node "
    "RETURN s.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score MATCH (s:T_D_IMG {uuid: node.uuid}) "
    "RETURN s.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node WHERE node:T_D_SEQ OR node:T_D_PCR "
    "RETURN count(DISTINCT node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s WHERE s.type IN ['TIS', 'CEL'] "
    "RETURN count(DISTINCT s)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node WHERE node.type = $t RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
    "WHERE toLower(node.search_text) CONTAINS toLower($p) RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
    "MATCH (node)-[:IN_STUDY]->(st:Study) WHERE st.id = $sid RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node MATCH (node:T_TIS) RETURN count(node)",
])
def test_a_scoped_call_passes(cypher):
    assert passes(cypher, {"q": "foo bar", "t": "TIS", "p": "foo bar", "sid": 3})


@pytest.mark.parametrize("cypher", [
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score RETURN node.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD score, node RETURN node.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node WHERE node.uuid IS NOT NULL "
    "RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node WHERE node:Sample RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score MATCH (s:Sample) WHERE s = node "
    "RETURN s.id",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
    "MATCH (node)-[:IN_PROJECT]->(p:Project) RETURN count(node)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD score RETURN count(*)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q)",
    "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
    "WHERE node:T_TIS OR node.uuid IS NOT NULL RETURN count(node)",
])
def test_an_unscoped_call_is_refused(cypher):
    assert kinds(cypher, {"q": "foo bar"}) == ["unscoped_fulltext"]


def test_a_query_with_no_fulltext_call_is_not_a_fulltext_problem():
    assert passes("MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS 'foo-bar' RETURN count(s) AS n")


def test_a_fulltext_call_inside_a_union_subquery_is_checked():
    cypher = ("CALL () { CALL db.index.fulltext.queryNodes('sample_search_text', 'foo bar') YIELD node "
              "RETURN node AS s UNION MATCH (a:T_TIS) RETURN a AS s } WITH DISTINCT s RETURN count(s) AS n")
    assert kinds(cypher) == ["unscoped_fulltext"]


# ----------------------------------------------------------------------------------------- P6b: the messages


def test_the_fulltext_repair_names_the_words_and_both_ways_out():
    message = _message(FT, {"q": "Foo-Bar"})
    assert "'foo', 'bar'" in message
    assert "toLower(s.search_text) CONTAINS" in message
    assert "YIELD node WHERE node:T_" in message
    assert "OR" in message and "quoted phrase" in message


def test_the_fulltext_repair_for_an_unreadable_term_says_how_to_make_it_readable():
    message = _message(FT, {})
    assert "cannot be read" in message and "parameter" in message


def test_the_fulltext_repair_ends_by_asking_for_the_query_again():
    assert "Regenerate the Cypher" in _message(FT, {"q": "a b"})


def test_the_fulltext_refusal_names_the_call_and_the_words():
    refusal = graph_mod._shape_refusal(graph_mod.query_shape_problems(FT, {"q": "foo bar"}))
    assert refusal.startswith("Graph agent could not produce valid Cypher")
    assert "queryNodes" in refusal and "'foo', 'bar'" in refusal


# --------------------------------------------------------------------------- the graph agent: one repair round

SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(gcat.TypeIndexRow(title="TIS", label="T_TIS", name="Tissue", clade="Source", sample_count=10,
                             deprecated=False, attributes_with_values=1),
           gcat.TypeIndexRow(title="NHP", label="T_NHP", name="Primate", clade="Source", sample_count=10,
                             deprecated=False, attributes_with_values=1)),
    guard=MappingProxyType({"T_TIS": frozenset({"Organ"}), "T_NHP": frozenset({"Organ"})}),
)
VOCAB = gcat.Vocabulary(investigation_titles=(), project_titles=(), study_titles=(), published_studies=(),
                        assay_titles=(), protocol_titles=(), assay_connections=())

UNBOUNDED = "MATCH (a:Sample)-[:DERIVED_FROM*1..]->(b:T_NHP) RETURN count(DISTINCT a) AS n"
BOUNDED = f"MATCH (a:Sample)-[:DERIVED_FROM*1..{MAX}]->(b:T_NHP) RETURN count(DISTINCT a) AS n"
BOUNDED_HALLUCINATED = (f"MATCH (a:T_TIS)-[:DERIVED_FROM*1..{MAX}]->(b:T_NHP) WHERE a.Hallucinated = $o "
                        "RETURN count(*) AS n")
UNBOUNDED_HALLUCINATED = "MATCH (a:T_TIS)-[:DERIVED_FROM*1..]->(b:Sample) WHERE a.Hallucinated = $o RETURN count(*)"
UNSCOPED = "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node RETURN count(node) AS n"


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
    """Answers each call with the next plan; the last one repeats."""

    def __init__(self, *plans):
        self.plans, self.calls = [p if isinstance(p, tuple) else (p, {}) for p in plans], []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        cypher, params = self.plans[min(len(self.calls), len(self.plans)) - 1]
        return GraphAgentPlan(cypher=cypher, explanation="model explanation", parameters=dict(params))

    def repair(self) -> str:
        return self.calls[1]["messages"][-1]["content"]


def _config():
    c = MagicMock()
    c.NEO4J_SCHEMA = {"node_properties": {"Sample": ["uuid", "type", "id", "Organ", "search_text"]}}
    c.GRAPH_AGENT_SYSTEM_PROMPT = "graph system prompt"
    c.PROTOCOL_SCHEMA = None
    c.ASSAY_SAMPLE_CONNECTIONS = None
    c.get_agent_model.return_value = (MagicMock(), "model", None)
    return c


def run(monkeypatch, llm):
    monkeypatch.setattr(graph_mod, "call_llm_structured", llm)
    return graph_mod.graph_agent(_config(), "how many samples descend from a primate", {}, None)


@pytest.mark.parametrize("mode", ["live", "down"])
def test_a_well_shaped_query_is_returned_after_one_call(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(BOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 1 and out.cypher == BOUNDED


@pytest.mark.parametrize("mode", ["live", "down"])
def test_an_unbounded_path_is_repaired_once(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(UNBOUNDED, BOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == BOUNDED
    assert f"*1..{MAX}" in llm.repair() and "11 hops" in llm.repair()


@pytest.mark.parametrize("mode", ["live", "down"])
def test_a_repair_that_keeps_the_shape_is_refused(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(UNBOUNDED, UNBOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == ""
    assert out.explanation.startswith("Graph agent could not produce valid Cypher")
    assert "[:DERIVED_FROM*1..]" in out.explanation
    assert out.context_mode == ("catalog" if mode == "live" else "fallback")


@pytest.mark.parametrize("mode", ["live", "down"])
def test_a_shape_repair_is_rechecked_by_the_property_guard(monkeypatch, request, mode):
    """The verifier's finding: a repair that bounds the path but invents a property must not reach Neo4j."""
    request.getfixturevalue(mode)
    llm = FakeLLM(UNBOUNDED, (BOUNDED_HALLUCINATED, {"o": "x"}))
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == "" and "Hallucinated" in out.explanation


@pytest.mark.parametrize("mode", ["live", "down"])
def test_a_property_repair_is_rechecked_by_the_shape_guard(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(("MATCH (a:T_TIS) WHERE a.Hallucinated = $o RETURN count(*)", {"o": "x"}), UNBOUNDED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == "" and "[:DERIVED_FROM*1..]" in out.explanation


@pytest.mark.parametrize("mode", ["live", "down"])
def test_one_repair_names_a_property_problem_and_a_shape_problem(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM((UNBOUNDED_HALLUCINATED, {"o": "x"}), BOUNDED)
    out = run(monkeypatch, llm)
    repair = llm.repair()
    assert len(llm.calls) == 2 and out.cypher == BOUNDED
    assert "Hallucinated" in repair and "[:DERIVED_FROM*1..]" in repair and f"*1..{MAX}" in repair


def test_an_unscoped_fulltext_call_is_repaired_with_the_plans_own_parameters(monkeypatch, live):
    scoped = UNSCOPED.replace("YIELD node", "YIELD node WHERE node:T_TIS")
    llm = FakeLLM((UNSCOPED, {"q": "foo-bar"}), (scoped, {"q": "foo-bar"}))
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == scoped and "'foo', 'bar'" in llm.repair()


def test_an_unscoped_fulltext_call_on_one_word_is_not_repaired(monkeypatch, live):
    llm = FakeLLM((UNSCOPED, {"q": "foo"}))
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 1 and out.cypher == UNSCOPED


def test_the_repair_is_rechecked_with_the_repaired_plans_parameters(monkeypatch, live):
    llm = FakeLLM((UNSCOPED, {"q": "foo bar"}), (UNSCOPED, {"q": "foo AND bar"}))
    out = run(monkeypatch, llm)
    assert out.cypher == "" and "'foo', 'bar'" in out.explanation


def test_a_repair_message_with_no_shape_problem_is_the_catalog_message_unchanged(monkeypatch, live):
    bad = "MATCH (a:T_TIS) WHERE a.Hallucinated = 1 RETURN count(*)"
    llm = FakeLLM(bad, BOUNDED)
    run(monkeypatch, llm)
    problems = graph_mod._property_problems(bad, SNAPSHOT)
    assert llm.repair() == graph_mod._catalog_repair_message(problems, [], SNAPSHOT, [])


# ------------------------------------------------------------------------------ assay_join (graph schema 1.3)
# Two samples on one Assay did not come from each other (spec 2026-09-25-graph-assay-nodes-design.md section 6.3):
# every form that pairs them through one Assay is refused, and a sample that reaches an Assay alone passes.


def test_the_assay_join_names_are_the_contracts_and_its_relationships_are_pinned():
    from chat_nextseek.agents import graph as graph_mod
    from chat_nextseek.graph_contract import schema
    assert graph_mod._ASSAY_LABEL is schema.ASSAY
    assert graph_mod._ASSAY_RELATIONSHIPS == frozenset({"INPUT_TO", "OUTPUT_OF"})   # policy, pinned by a literal

ASSAY_PARAMS = {"u": "X-1", "t": "x", "x": "a", "y": "b"}

ASSAY_JOINS = [
    ("one_pattern", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay)<-[:INPUT_TO]-(p:Sample) "
                    "RETURN c.uuid AS child, p.uuid AS parent"),
    ("comma_joined_parts", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay), (p:Sample)-[:INPUT_TO]->(a) "
                           "RETURN count(*) AS n"),
    ("separate_match_clauses", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) MATCH (p:T_TIS)-[:INPUT_TO]->(a) "
                               "RETURN count(DISTINCT c) AS n"),
    ("optional_match", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) OPTIONAL MATCH (p:Sample)-[:INPUT_TO]->(a) "
                       "RETURN c.uuid AS c, p.uuid AS p"),
    ("carried_by_with", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WITH c, a MATCH (p:Sample)-[:INPUT_TO]->(a) "
                        "RETURN count(*) AS n"),
    ("carried_by_with_alias", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WITH c, a AS run "
                              "MATCH (p:Sample)-[:INPUT_TO]->(run) RETURN count(*) AS n"),
    ("carried_by_with_star", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WITH * MATCH (p:Sample)-[:INPUT_TO]->(a) "
                             "RETURN count(*) AS n"),
    ("undirected", "MATCH (c:Sample)-[:OUTPUT_OF]-(a:Assay)-[:INPUT_TO]-(p:Sample) RETURN count(*) AS n"),
    ("alternated", "MATCH (c:Sample)-[:INPUT_TO|OUTPUT_OF]->(a:Assay)<-[:INPUT_TO|OUTPUT_OF]-(p:Sample) "
                   "RETURN count(*) AS n"),
    ("variable_length_between_samples", "MATCH (c:T_D_SEQ)-[:OUTPUT_OF|INPUT_TO*2]-(p:T_TIS) RETURN count(*) AS n"),
    ("variable_length_to_an_assay", "MATCH (c:Sample)-[*1..2]->(a:Assay)<-[*1..2]-(p:Sample) RETURN count(*) AS n"),
    ("untyped_arrows", "MATCH (c:Sample)-->(a:Assay)<--(p:Sample) RETURN count(*) AS n"),
    ("untyped_named", "MATCH (c:Sample)-[r1]->(a:Assay)<-[r2]-(p:Sample) RETURN count(*) AS n"),
    ("shortest_path", "MATCH (c:T_D_SEQ {uuid: $u}), (p:T_TIS) MATCH x = shortestPath((c)-[:INPUT_TO|OUTPUT_OF*]-(p)) "
                      "RETURN length(x) AS n"),
    ("shortest_path_to_the_assay", "MATCH (a:Assay {title: $t}) MATCH p1 = shortestPath((c:Sample)-[:OUTPUT_OF*]-(a)) "
                                   "MATCH p2 = shortestPath((p:Sample)-[:INPUT_TO*]-(a)) RETURN count(*) AS n"),
    ("unlabelled_assay_by_arrow", "MATCH (c:Sample)-[:OUTPUT_OF]->(a)<-[:INPUT_TO]-(p:Sample) RETURN count(*) AS n"),
    ("assay_labelled_in_where", "MATCH (c:Sample)--(a)--(p:Sample) WHERE a:Assay RETURN count(*) AS n"),
    ("anonymous_assay_and_sample", "MATCH (c:T_D_SEQ)-[:OUTPUT_OF]->(:Assay)<-[:INPUT_TO]-(:T_TIS) "
                                   "RETURN count(DISTINCT c) AS n"),
    ("unlabelled_samples", "MATCH (c)-[:OUTPUT_OF]->(a:Assay)<-[:INPUT_TO]-(p) RETURN count(*) AS n"),
    ("inside_a_subquery", "MATCH (c:Sample) WHERE EXISTS { (c)-[:OUTPUT_OF]->(a:Assay)<-[:INPUT_TO]-(p:T_TIS) } "
                          "RETURN count(c) AS n"),
    ("outer_samples_through_two_subqueries", "MATCH (c:Sample), (p:T_TIS), (a:Assay) WHERE EXISTS { "
                                             "(c)-[:OUTPUT_OF]->(a) } AND EXISTS { (p)-[:INPUT_TO]->(a) } "
                                             "RETURN count(*) AS n"),
    ("outer_sample_after_with_in_a_subquery", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WITH c, a MATCH (p:T_TIS) "
                                              "WHERE EXISTS { (p)-[:INPUT_TO]->(a) } RETURN count(*) AS n"),
    ("inside_a_count_subquery", "MATCH (a:Assay) RETURN a.title AS t, "
                                "COUNT { (c:Sample)-[:OUTPUT_OF]->(a)<-[:INPUT_TO]-(p:Sample) } AS pairs"),
    # one Assay node pattern written twice with the same inline map is one Assay
    ("same_map_twice_anonymous", "MATCH (i:Sample)-[:INPUT_TO]->(:Assay {title: $x}), "
                                 "(o:Sample)-[:OUTPUT_OF]->(:Assay {title:$x}) "
                                 "RETURN i.uuid AS input, o.uuid AS output"),
    ("same_map_twice_named", "MATCH (i:Sample)-[:INPUT_TO]->(a1:Assay {title: $x}) "
                             "MATCH (o:Sample)-[:OUTPUT_OF]->(a2:Assay {title: $x}) RETURN count(*) AS pairs"),
    # a COUNT, a COLLECT or a pattern comprehension gives one value per outer row: its local sample on the Assay an
    # outer sample reaches reads what the outer sample was "made from" through the Assay
    ("correlated_count", "MATCH (c:Sample {uuid: $u})-[:OUTPUT_OF]->(a:Assay) "
                         "RETURN a.title AS assay, COUNT { (p:Sample)-[:INPUT_TO]->(a) } AS parents"),
    ("correlated_collect", "MATCH (c:Sample {uuid: $u})-[:OUTPUT_OF]->(a:Assay) "
                           "RETURN COLLECT { MATCH (p:Sample)-[:INPUT_TO]->(a) RETURN p.uuid } AS made_from"),
    ("correlated_comprehension", "MATCH (c:Sample {uuid: $u})-[:OUTPUT_OF]->(a:Assay) "
                                 "RETURN [(p:Sample)-[:INPUT_TO]->(a) | p.uuid] AS made_from"),
]

ASSAY_SINGLES = [
    ("one_sample", "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay) WHERE a.title = $x RETURN count(DISTINCT s) AS n"),
    ("the_prompts_went_through", "MATCH (a:Assay) WHERE toLower(a.title) = toLower($x) OR toLower($x) IN "
                                 "[n IN a.other_names | toLower(n)] MATCH (s:Sample) WHERE EXISTS { "
                                 "(s)-[:INPUT_TO]->(a) } OR EXISTS { (s)-[:OUTPUT_OF]->(a) } "
                                 "RETURN count(DISTINCT s) AS n"),
    ("one_sample_both_roles", "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay), (s)-[:OUTPUT_OF]->(a) "
                              "RETURN count(DISTINCT s) AS n"),
    ("local_count_subqueries", "MATCH (a:Assay) RETURN a.title AS assay, COUNT { (s:Sample)-[:INPUT_TO]->(a) } AS "
                               "inputs, COUNT { (t:Sample)-[:OUTPUT_OF]->(a) } AS outputs"),
    ("local_exists_sample", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WHERE EXISTS { (p:T_TIS)-[:INPUT_TO]->(a) } "
                            "RETURN count(DISTINCT c) AS n"),
    ("local_pattern_comprehensions", "MATCH (a:Assay) RETURN a.title AS t, [(s:Sample)-[:INPUT_TO]->(a) | s.uuid] "
                                     "AS inputs, [(o:Sample)-[:OUTPUT_OF]->(a) | o.uuid] AS outputs"),
    ("two_different_assays", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay {title: $x}) "
                             "MATCH (p:Sample)-[:INPUT_TO]->(b:Assay {title: $y}) RETURN count(*) AS n"),
    ("with_that_ends_the_assay", "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay) WITH c "
                                 "MATCH (p:Sample)-[:INPUT_TO]->(a:Assay) RETURN count(*) AS n"),
    ("union_parts", "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay) RETURN s.uuid AS uuid UNION "
                    "MATCH (t:Sample)-[:OUTPUT_OF]->(a:Assay) RETURN t.uuid AS uuid"),
    ("lineage_beside_an_assay", "MATCH (c:T_SLD)-[:DERIVED_FROM]->(p:T_TIS) WHERE EXISTS { "
                                "(c)-[:OUTPUT_OF]->(:Assay {title: $x}) } RETURN count(DISTINCT c) AS n"),
    ("lineage_then_the_childs_assay", "MATCH (p:T_TIS)<-[:DERIVED_FROM]-(c:Sample)-[:OUTPUT_OF]->(a:Assay) "
                                      "WHERE a.title = $x RETURN count(DISTINCT c) AS n"),
    ("undirected_one_sample", "MATCH (s:Sample)-[:INPUT_TO]-(a:Assay) RETURN count(*) AS n"),
    ("untyped_assay_to_a_study", "MATCH (a:Assay)-->(st:Study) RETURN st.title AS t"),
    ("catalog_edges", "MATCH (t:SampleType)-[:ACCEPTED_BY]->(a:Assay)-[:GENERATES]->(u:SampleType) "
                      "RETURN t.title, u.title"),
    ("study_beside_the_assay", "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay), (s)-[:IN_STUDY]->(st:Study) "
                               "RETURN st.title AS study, count(DISTINCT s) AS n"),
    ("no_assay_at_all", "MATCH (c:Sample)-[:DERIVED_FROM]->(p:Sample) RETURN count(*) AS n"),
    ("untyped_path_between_samples", "MATCH (c:T_TIS {uuid: $u})-[*1..2]-(p:Sample) RETURN count(*) AS n"),
]


@pytest.mark.parametrize("cypher", [c for _, c in ASSAY_JOINS], ids=[n for n, _ in ASSAY_JOINS])
def test_samples_paired_through_one_assay_are_refused(cypher):
    assert "assay_join" in kinds(cypher, ASSAY_PARAMS), graph_mod.query_shape_problems(cypher, ASSAY_PARAMS)


@pytest.mark.parametrize("cypher", [c for _, c in ASSAY_SINGLES], ids=[n for n, _ in ASSAY_SINGLES])
def test_a_sample_that_reaches_an_assay_alone_passes(cypher):
    assert "assay_join" not in kinds(cypher, ASSAY_PARAMS), graph_mod.query_shape_problems(cypher, ASSAY_PARAMS)


PAIRED = "MATCH (c:T_TIS)-[:OUTPUT_OF]->(a:Assay)<-[:INPUT_TO]-(p:T_NHP) RETURN count(DISTINCT c) AS n"
ONE_SIDE = "MATCH (c:T_TIS) WHERE EXISTS { (c)-[:OUTPUT_OF]->(:Assay {title: $assay}) } RETURN count(DISTINCT c) AS n"


def test_the_assay_join_names_the_assay_and_its_samples():
    cypher = "MATCH (c:Sample)-[:OUTPUT_OF]->(a:Assay)<-[:INPUT_TO]-(p:Sample) RETURN c.uuid AS c, p.uuid AS p"
    (problem,) = graph_mod.query_shape_problems(cypher)
    assert (problem.kind, problem.text) == ("assay_join", "(a:Assay)")
    assert problem.detail.startswith("the samples c and p both reach it through INPUT_TO or OUTPUT_OF")
    assert graph_mod.refused_query_shapes(cypher) == [f"assay join (a:Assay): {problem.detail}"]


def test_an_anonymous_sample_and_an_unnamed_assay_are_named_by_their_patterns():
    anonymous = "MATCH (c:T_D_SEQ)-[:OUTPUT_OF]->(:Assay)<-[:INPUT_TO]-(:T_TIS) RETURN count(DISTINCT c) AS n"
    (problem,) = graph_mod.query_shape_problems(anonymous)
    assert problem.text == "(:Assay)" and "the samples c and (:T_TIS) both" in problem.detail
    hidden = "MATCH (c:T_D_SEQ)-[:OUTPUT_OF|INPUT_TO*2]-(p:T_TIS) RETURN count(*) AS n"
    assert [(p.kind, p.text) for p in graph_mod.query_shape_problems(hidden)] == [
        ("assay_join", "[:OUTPUT_OF|INPUT_TO*2]")]


def test_three_samples_on_one_assay_are_named_together():
    cypher = ("MATCH (a:Assay) MATCH (x:T_TIS)-[:INPUT_TO]->(a) MATCH (y:T_CEL)-[:INPUT_TO]->(a) "
              "MATCH (z:T_D_SEQ)-[:OUTPUT_OF]->(a) RETURN count(*) AS n")
    (problem,) = graph_mod.query_shape_problems(cypher)
    assert "the samples x, y and z all reach it" in problem.detail


def test_an_assay_join_is_reported_beside_a_path_problem_in_the_order_written():
    cypher = ("MATCH (a:Sample)-[:DERIVED_FROM*]->(b:Sample) "
              "MATCH (c:Sample)-[:OUTPUT_OF]->(x:Assay)<-[:INPUT_TO]-(p:Sample) RETURN count(*) AS n")
    assert kinds(cypher) == ["unbounded_path", "assay_join"]


def test_the_assay_join_repair_says_lineage_is_derived_from_and_how_to_ask_instead():
    message = _message(PAIRED)
    assert "Lineage is DERIVED_FROM only." in message
    assert "WHERE EXISTS { (s)-[:INPUT_TO]->(a) } OR EXISTS { (s)-[:OUTPUT_OF]->(a) }" in message
    assert "COUNT { } subquery" in message and message.endswith("answered from the graph.")
    assert "named by UID" in message and "RETURN DISTINCT o.uuid" in message and "seek_assay_ids" in message


# One sample named by UID bounds the pairing by its own Assay (round 4, ruling 1). Each shape runs for two made-up UIDs.
UID_PAIRINGS = [
    ("inline_pin_distinct_list", "MATCH (s:Sample {{uuid: '{uid}'}})-[:INPUT_TO|OUTPUT_OF]->(a:Assay)"
                                 "<-[:INPUT_TO|OUTPUT_OF]-(o:Sample) RETURN DISTINCT o.uuid AS uuid"),
    ("inline_pin_distinct_count", "MATCH (s:Sample {{uuid: $uid}})-[:OUTPUT_OF]->(a:Assay)<-[:OUTPUT_OF]-(o:Sample) "
                                  "WHERE o.uuid <> s.uuid RETURN count(DISTINCT o) AS n"),
    ("where_pin", "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay)<-[:INPUT_TO]-(o:Sample) WHERE s.uuid = '{uid}' "
                  "RETURN collect(DISTINCT o.uuid) AS uuids"),
    ("same_run", "MATCH (s:Sample {{uuid: '{uid}'}})-[r1:OUTPUT_OF]->(a:Assay)<-[r2:OUTPUT_OF]-(o:Sample) "
                 "WHERE any(i IN r1.seek_assay_ids WHERE i IN r2.seek_assay_ids) AND o <> s "
                 "RETURN DISTINCT o.uuid AS uuid"),
]
UIDS = ["ZZZ-990101ABC-1-PUB", "QQQ-770202XYZ-2-PUB"]


@pytest.mark.parametrize("uid", UIDS)
@pytest.mark.parametrize("cypher", [c for _, c in UID_PAIRINGS], ids=[n for n, _ in UID_PAIRINGS])
def test_samples_paired_through_an_assay_from_one_uid_named_sample_pass(cypher, uid):
    assert "assay_join" not in kinds(cypher.format(uid=uid), {"uid": uid}), \
        graph_mod.query_shape_problems(cypher.format(uid=uid), {"uid": uid})


_PAIR = "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay)<-[:INPUT_TO]-(o:Sample) "


@pytest.mark.parametrize("cypher", [
    # no UID: refused
    "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay)<-[:INPUT_TO]-(o:Sample) RETURN DISTINCT o.uuid AS uuid",
    # a UID but the rows are not distinct: refused
    "MATCH (s:Sample {uuid: $uid})-[:OUTPUT_OF]->(a:Assay)<-[:OUTPUT_OF]-(o:Sample) RETURN o.uuid AS uuid",
    # a UID list is not one named sample
    "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay)<-[:INPUT_TO]-(o:Sample) WHERE s.uuid IN $uids "
    "RETURN DISTINCT o.uuid AS uuid",
    # round 4 review F1: a uuid equality that is not a top-level AND conjunct pins nothing
    _PAIR + "WHERE s.uuid = $a OR o.uuid = $b RETURN DISTINCT o.uuid AS uuid",
    _PAIR + "WHERE s.uuid = 'YYY-990102DEF-2-PUB' OR o.uuid = $b RETURN DISTINCT o.uuid AS uuid",
    _PAIR + "WHERE s.uuid = $a OR s.type = 'TIS' RETURN DISTINCT o.uuid AS uuid",
    _PAIR + "WHERE NOT s.uuid = $a RETURN DISTINCT o.uuid AS uuid",
    _PAIR + "WHERE s.uuid = 'ZZZ-990101ABC-1-PUB' OR s.uuid CONTAINS 'PUB' RETURN DISTINCT o.uuid AS uuid",
    _PAIR + "WHERE s.uuid = $a AND o.type = 'TIS' OR o.type = 'MUS' RETURN DISTINCT o.uuid AS uuid",
    # one pinned sample bounds one other sample on its Assay, not two
    "MATCH (s {uuid:$a})-[:INPUT_TO]->(a)<-[:INPUT_TO]-(o), (a)<-[:OUTPUT_OF]-(p) RETURN DISTINCT o.uuid, p.uuid",
    # a pin inside CALL { } does not bound the pairing outside it
    "CALL { MATCH (s:Sample) WHERE s.uuid = $a RETURN count(*) AS c } " + _PAIR + "RETURN DISTINCT o.uuid AS uuid",
    # a DISTINCT inside CALL { } does not make the outer rows distinct
    "MATCH (s:Sample {uuid: $a})-[:INPUT_TO]->(a:Assay)<-[:INPUT_TO]-(o:Sample) "
    "CALL { MATCH (x:Sample) RETURN count(DISTINCT x) AS n } RETURN o.uuid AS uuid, n",
], ids=["no_uid", "uid_but_rows", "uid_list", "or_other_sample", "or_other_sample_literal", "or_type", "not_pin",
        "or_contains", "and_then_or", "three_on_one_assay", "pin_in_call", "distinct_in_call"])
def test_pairings_without_a_uid_or_a_distinct_result_stay_refused(cypher):
    params = {"uid": "ZZZ-990101ABC-1-PUB", "uids": ["a", "b"], "a": "ZZZ-990101ABC-1-PUB", "b": "YYY-990102DEF-2-PUB"}
    assert "assay_join" in kinds(cypher, params)


@pytest.mark.parametrize("cypher", [
    "MATCH (s:Sample {uuid:$a})-[:INPUT_TO]->(a:Assay)<-[:OUTPUT_OF]-(o:Sample) WHERE s.uuid = $a AND NOT o.type = 'TIS' "
    "RETURN count(DISTINCT o) AS n",
    "MATCH (s:Sample)-[:INPUT_TO]->(a:Assay)<-[:OUTPUT_OF]-(o:Sample) WHERE (s.uuid = $a) AND NOT o.type = 'TIS' "
    "RETURN count(DISTINCT o) AS n",
], ids=["inline_and_where", "where_conjunct"])
def test_a_uid_pin_beside_a_negated_condition_on_the_other_sample_passes(cypher):
    assert "assay_join" not in kinds(cypher, {"a": "ZZZ-990101ABC-1-PUB"})


def test_the_example_in_the_assay_join_repair_passes_the_guard():
    message = _message(PAIRED)
    example = message.split("`")[1]
    assert example.startswith("MATCH (s:Sample {uuid: $uid})")
    assert "assay_join" not in kinds(example, {"uid": "ZZZ-990101ABC-1-PUB"})


def test_the_assay_join_refusal_names_the_assay():
    refusal = graph_mod._shape_refusal(graph_mod.query_shape_problems(PAIRED))
    assert refusal.startswith("Graph agent could not produce valid Cypher; the query pairs samples through the Assay "
                              "(a:Assay): the samples c and p both reach it")
    assert refusal.endswith("; lineage is DERIVED_FROM.")


@pytest.mark.parametrize("mode", ["live", "down"])
def test_an_assay_join_is_repaired_once(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(PAIRED, (ONE_SIDE, {"assay": "Staining"}))
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == ONE_SIDE
    assert "Lineage is DERIVED_FROM only" in llm.repair() and "(a:Assay)" in llm.repair()


@pytest.mark.parametrize("mode", ["live", "down"])
def test_a_repair_that_still_pairs_through_an_assay_is_refused(monkeypatch, request, mode):
    request.getfixturevalue(mode)
    llm = FakeLLM(PAIRED, PAIRED)
    out = run(monkeypatch, llm)
    assert len(llm.calls) == 2 and out.cypher == ""
    assert "pairs samples through the Assay (a:Assay)" in out.explanation
    assert out.explanation.endswith("lineage is DERIVED_FROM.")


# ------------------------------------------------------------------------------ round 4: refusal replies and paths
PAIRED_ZETA = "MATCH (c:T_TIS)-[:OUTPUT_OF]->(zz:Assay)<-[:INPUT_TO]-(p:T_NHP) RETURN c.uuid AS c, p.uuid AS p"
WHOLE_PATH = "MATCH p = (a:T_TIS {uuid: $u})-[:DERIVED_FROM*1..3]->(b:T_NHP) RETURN p"
WHOLE_ROUTE = "MATCH route = (a:T_NHP {uuid: $u})-[:DERIVED_FROM*1..4]->(b:T_TIS) RETURN route"
PATH_ENDS = ("MATCH p = (a:T_TIS {uuid: $u})-[:DERIVED_FROM*1..3]->(b:T_NHP) "
             "RETURN length(p) AS hops, a.uuid AS from_uuid, b.uuid AS to_uuid")


@pytest.mark.parametrize("cypher,kind", [(PAIRED, "assay_join"), (PAIRED_ZETA, "assay_join"),
                                         (UNBOUNDED, "lineage_path"), (UNSCOPED, "fulltext")])
def test_a_refusal_carries_its_kind_and_both_cyphers_and_a_reply_of_its_own(monkeypatch, live, cypher, kind):
    out = run(monkeypatch, FakeLLM(cypher, cypher, cypher))
    assert out.cypher == "" and out.refusal_kinds[0] == kind
    assert out.attempted_cypher == cypher and out.repaired_cypher == cypher
    reply = graph_mod.refusal_reply(out.refusal_kinds)
    assert reply
    for leak in ("Graph agent", "Cypher", "catalog", "Reason", "guard", "INPUT_TO"):
        assert leak not in reply


def test_a_refusal_in_down_mode_carries_its_kind_too(monkeypatch, down):
    out = run(monkeypatch, FakeLLM(PAIRED, PAIRED))
    assert out.refusal_kinds == ["assay_join"] and out.attempted_cypher == PAIRED


def test_a_refusal_with_no_reply_of_its_own_gets_none():
    assert graph_mod.refusal_reply([]) is None and graph_mod.refusal_reply(["catalog"]) is None


@pytest.mark.parametrize("cypher,var", [(WHOLE_PATH, "p"), (WHOLE_ROUTE, "route")])
def test_the_whole_path_repair_names_length_and_node_ids_never_the_path(monkeypatch, live, cypher, var):
    llm = FakeLLM(cypher, PATH_ENDS.replace("p =", f"{var} =").replace("(p)", f"({var})"))
    run(monkeypatch, llm)
    repair = llm.repair()
    assert f"whole path {var}" in repair and f"length({var}) AS hops" in repair
    assert "a.uuid, b.uuid" in repair
    assert "whole node" not in repair and "s.id, s.uuid" not in repair


def test_a_node_variable_still_gets_the_node_hint(monkeypatch, live):
    llm = FakeLLM("MATCH (s:T_TIS) RETURN s", "MATCH (s:T_TIS) RETURN s.uuid AS uuid")
    run(monkeypatch, llm)
    assert "whole node s" in llm.repair() and "s.id, s.uuid" in llm.repair() and "whole path" not in llm.repair()


def test_a_path_returned_whole_or_through_nodes_is_flagged_and_its_ends_are_not():
    assert graph_mod.whole_node_returns(PATH_ENDS) == []
    assert graph_mod.whole_node_returns(WHOLE_PATH) == ["p"]
    assert graph_mod.whole_node_returns(WHOLE_PATH.replace("RETURN p", "RETURN nodes(p) AS ns")) == ["p"]


@pytest.mark.parametrize("uids", [("ZZZ-990101ABC-1-PUB", "ZZZ-990101ABD-1-PUB"), ("QQQ-770202XYZ-2-PUB", "QQQ-770202XYZ-3-PUB")])
def test_the_two_uid_related_recipe_passes_every_guard_and_the_members_scope(uids):
    from pathlib import Path
    from chat_nextseek.cypher_scope import Scoped, scope_cypher
    from chat_nextseek.graph_scope import GraphScope
    text = (Path(graph_mod.__file__).parent.parent / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("- **Are two named samples related**"))
    recipe = line.split("`")[1]
    params = {"uid_a": uids[0], "uid_b": uids[1]}
    assert graph_mod.whole_node_returns(recipe) == [] and graph_mod.query_shape_problems(recipe, params) == []
    out = scope_cypher(recipe, params, GraphScope.for_projects([3, 1], source="test"))
    assert isinstance(out, Scoped), getattr(out, "reasons", out)
