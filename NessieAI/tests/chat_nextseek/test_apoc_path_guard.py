"""The APOC path guard: an allowed ``apoc.path.*`` call runs only in a shape that cannot run away (P6a, APOC form).

Measured on the local 1.2 graph on 2026-09-17: APOC's path expansion is NOT charged to the transaction memory cap.
``apoc.path.expandConfig`` from one sample with no relationshipFilter and the default RELATIONSHIP_PATH uniqueness
at maxLevel 4 ran the whole Neo4j JVM out of heap (``java.lang.OutOfMemoryError``) and left it refusing every
connection until it was restarted; ``subgraphNodes`` with no relationshipFilter reached 1,088,423 nodes at maxLevel 4
through OF_TYPE and IN_PROJECT. The 1g transaction cap and the 120 s timeout did not stop either. So the text check
is the only protection, and it requires, on every allowed ``apoc.path.*`` call:

- a literal configuration map;
- a literal integer ``maxLevel`` from 1 to ``APOC_PATH_MAX_LEVEL`` (12: the longest DERIVED_FROM chain is 11 hops);
- a literal ``relationshipFilter`` naming only DERIVED_FROM (a hub relationship reaches the whole graph in 2 hops);
- ``uniqueness: 'NODE_GLOBAL'`` on ``expandConfig`` (and no other value anywhere), no ``sequence`` (it replaces the
  relationshipFilter), and never the positional ``apoc.path.expand``, whose uniqueness cannot be set.

``write_clause`` refuses a call that breaks a rule (the tool's last line), and ``procedure_call_problems`` names every
problem for the graph agent's one repair. Neither changes anything for a turn without a variant: the default allowlist
holds no ``apoc.path`` procedure. Pure text: nothing here reaches Neo4j.
"""
from __future__ import annotations

import pytest

from chat_nextseek import cypher_text
from chat_nextseek.cypher_text import APOC_PATH_MAX_LEVEL, procedure_call_problems, write_clause

APOC = frozenset({"apoc.path.subgraphNodes", "apoc.path.spanningTree", "apoc.path.expandConfig"})

GOOD = ("MATCH (x:Sample {uuid: $uid}) "
        "CALL apoc.path.subgraphNodes(x, {relationshipFilter: '<DERIVED_FROM', labelFilter: '+Sample', "
        "minLevel: 1, maxLevel: 12}) YIELD node RETURN node.type AS type, count(*) AS n")


def call(proc: str, config: str, yields: str = "node") -> str:
    return f"MATCH (x:Sample {{uuid: $uid}}) CALL {proc}(x, {config}) YIELD {yields} RETURN count(*) AS n"


def sub(config: str) -> str:
    return call("apoc.path.subgraphNodes", config)


# --- the bound ------------------------------------------------------------------------------------------------------


def test_the_bound_is_the_longest_chain_plus_one():
    assert APOC_PATH_MAX_LEVEL == 12


def test_a_bounded_derived_from_call_passes():
    assert write_clause(GOOD, extra_procedures=APOC) is None
    assert procedure_call_problems(GOOD, APOC) == []


@pytest.mark.parametrize("level", [1, 4, 8, 11, 12])
def test_every_literal_level_from_one_to_twelve_passes(level):
    q = sub(f"{{relationshipFilter: 'DERIVED_FROM>', maxLevel: {level}}}")
    assert write_clause(q, extra_procedures=APOC) is None
    assert procedure_call_problems(q, APOC) == []


@pytest.mark.parametrize("level, reason", [
    ("13", "maxLevel 13 is outside 1 to 12"),
    ("0", "maxLevel 0 is outside 1 to 12"),
    ("100", "maxLevel 100 is outside 1 to 12"),
    ("-1", "maxLevel must be a literal integer from 1 to 12"),
    ("$depth", "maxLevel must be a literal integer from 1 to 12"),
    ("4 + 4", "maxLevel must be a literal integer from 1 to 12"),
    ("'8'", "maxLevel must be a literal integer from 1 to 12"),
    ("null", "maxLevel must be a literal integer from 1 to 12"),
])
def test_a_level_that_is_not_a_literal_from_one_to_twelve_is_refused(level, reason):
    q = sub(f"{{relationshipFilter: 'DERIVED_FROM>', maxLevel: {level}}}")
    refused = write_clause(q, extra_procedures=APOC)
    assert refused.startswith("CALL apoc.path.subgraphNodes (") and reason in refused
    assert any(reason in p for p in procedure_call_problems(q, APOC))


def test_a_missing_level_is_refused():
    q = sub("{relationshipFilter: 'DERIVED_FROM>'}")
    assert "no maxLevel" in write_clause(q, extra_procedures=APOC)
    assert any("no maxLevel" in p for p in procedure_call_problems(q, APOC))


def test_a_level_given_twice_must_be_valid_both_times():
    q = sub("{relationshipFilter: 'DERIVED_FROM>', maxLevel: 4, maxLevel: 40}")
    assert "maxLevel 40 is outside 1 to 12" in write_clause(q, extra_procedures=APOC)


def test_a_backticked_key_is_read():
    ok = sub("{`relationshipFilter`: 'DERIVED_FROM>', `maxLevel`: 6}")
    assert write_clause(ok, extra_procedures=APOC) is None
    bad = sub("{relationshipFilter: 'DERIVED_FROM>', `maxLevel`: 60}")
    assert "maxLevel 60" in write_clause(bad, extra_procedures=APOC)


def test_a_comment_cannot_hide_a_level():
    q = sub("{relationshipFilter: 'DERIVED_FROM>', /* maxLevel: 4, */ limit: 1}")
    assert "no maxLevel" in write_clause(q, extra_procedures=APOC)


# --- the relationship filter ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["'DERIVED_FROM>'", "'<DERIVED_FROM'", "'DERIVED_FROM'", '"DERIVED_FROM>"',
                                 "'<DERIVED_FROM|DERIVED_FROM>'", "' DERIVED_FROM> '"])
def test_a_derived_from_filter_passes(rel):
    assert write_clause(sub(f"{{relationshipFilter: {rel}, maxLevel: 3}}"), extra_procedures=APOC) is None


def test_a_missing_filter_is_refused_with_both_directions_named():
    q = sub("{maxLevel: 4}")
    refused = write_clause(q, extra_procedures=APOC)
    assert "no relationshipFilter" in refused
    problem = next(p for p in procedure_call_problems(q, APOC) if "relationshipFilter" in p)
    assert "'DERIVED_FROM>'" in problem and "'<DERIVED_FROM'" in problem


@pytest.mark.parametrize("rel", ["'>'", "'<'", "''", "'OF_TYPE'", "'IN_PROJECT>'", "'DERIVED_FROM>|OF_TYPE'",
                                 "'derived_from>'", "'DERIVED_FROM>,IN_STUDY>'", "'HAS_ATTRIBUTE'"])
def test_a_filter_naming_anything_but_derived_from_is_refused(rel):
    refused = write_clause(sub(f"{{relationshipFilter: {rel}, maxLevel: 4}}"), extra_procedures=APOC)
    assert refused is not None and "must name only DERIVED_FROM" in refused


@pytest.mark.parametrize("rel", ["$rel", "rel", "'DERIVED' + '_FROM>'", "toString('DERIVED_FROM>')"])
def test_a_filter_that_is_not_one_literal_string_is_refused(rel):
    refused = write_clause(sub(f"{{relationshipFilter: {rel}, maxLevel: 4}}"), extra_procedures=APOC)
    assert refused is not None and "relationshipFilter must be a literal string" in refused


def test_a_sequence_is_refused_because_it_replaces_the_filter():
    q = sub("{relationshipFilter: 'DERIVED_FROM>', sequence: 'Sample, OF_TYPE>', maxLevel: 4}")
    assert "sequence" in write_clause(q, extra_procedures=APOC)


# --- uniqueness -----------------------------------------------------------------------------------------------------


def test_expand_config_must_ask_for_node_global():
    base = "relationshipFilter: '<DERIVED_FROM', maxLevel: 12"
    missing = call("apoc.path.expandConfig", f"{{{base}}}", "path")
    assert "uniqueness" in write_clause(missing, extra_procedures=APOC)
    assert any("NODE_GLOBAL" in p for p in procedure_call_problems(missing, APOC))
    for bad in ("'RELATIONSHIP_PATH'", "'NODE_PATH'", "$u", "'node_global'"):
        q = call("apoc.path.expandConfig", f"{{{base}, uniqueness: {bad}}}", "path")
        assert "uniqueness" in write_clause(q, extra_procedures=APOC), bad
    for ok in ("'NODE_GLOBAL'", '"NODE_GLOBAL"'):
        q = call("apoc.path.expandConfig", f"{{{base}, uniqueness: {ok}}}", "path")
        assert write_clause(q, extra_procedures=APOC) is None, ok


def test_a_uniqueness_other_than_node_global_is_refused_on_every_procedure():
    q = sub("{relationshipFilter: 'DERIVED_FROM>', maxLevel: 4, uniqueness: 'RELATIONSHIP_PATH'}")
    assert "uniqueness" in write_clause(q, extra_procedures=APOC)
    ok = call("apoc.path.spanningTree", "{relationshipFilter: 'DERIVED_FROM>', maxLevel: 4}", "path")
    assert write_clause(ok, extra_procedures=APOC) is None


def test_the_positional_expand_is_refused_even_when_allowed():
    q = "MATCH (x:Sample {uuid: $u}) CALL apoc.path.expand(x, 'DERIVED_FROM>', null, 1, 4) YIELD path RETURN count(*)"
    refused = write_clause(q, extra_procedures=APOC | {"apoc.path.expand"})
    assert refused.startswith("CALL apoc.path.expand (") and "uniqueness cannot be set" in refused


# --- the configuration itself ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("q", [
    "MATCH (x:Sample) CALL apoc.path.subgraphNodes(x, $config) YIELD node RETURN count(*)",
    "MATCH (x:Sample) WITH x, {relationshipFilter: 'DERIVED_FROM>', maxLevel: 4} AS c "
    "CALL apoc.path.subgraphNodes(x, c) YIELD node RETURN count(*)",
    "MATCH (x:Sample) CALL apoc.path.subgraphNodes(x) YIELD node RETURN count(*)",
    "MATCH (x:Sample) CALL apoc.path.subgraphNodes(x, apoc.map.merge({}, {maxLevel: 4})) YIELD node RETURN 1",
])
def test_the_configuration_must_be_a_literal_map(q):
    refused = write_clause(q, extra_procedures=APOC)
    assert refused is not None and "literal configuration map" in refused


def test_a_call_without_parentheses_is_refused():
    refused = write_clause("CALL apoc.path.subgraphNodes YIELD node RETURN count(*)", extra_procedures=APOC)
    assert refused is not None and "parentheses" in refused


def test_a_call_inside_a_subquery_is_checked():
    q = ("MATCH (x:T_TIS) CALL (x) { CALL apoc.path.subgraphNodes(x, {relationshipFilter: 'DERIVED_FROM>'}) "
         "YIELD node RETURN count(node) AS n } RETURN sum(n)")
    assert "no maxLevel" in write_clause(q, extra_procedures=APOC)


def test_every_call_is_checked_and_each_problem_is_named():
    q = ("MATCH (a:Sample {uuid: $a}) CALL apoc.path.subgraphNodes(a, {relationshipFilter: 'DERIVED_FROM>', "
         "maxLevel: 12}) YIELD node WITH collect(node) AS ancA "
         "MATCH (b:Sample {uuid: $b}) CALL apoc.path.subgraphNodes(b, {maxLevel: 99}) YIELD node "
         "RETURN count(node) AS n")
    problems = procedure_call_problems(q, APOC)
    assert any("maxLevel 99" in p for p in problems)
    assert any("no relationshipFilter" in p for p in problems)
    assert all(p.startswith("CALL apoc.path.subgraphNodes: ") for p in problems)


def test_lowercase_call_is_checked():
    assert "no maxLevel" in write_clause(GOOD.replace("CALL", "call").replace("maxLevel: 12", "limit: 1"),
                                         extra_procedures=APOC)


# --- procedures the variant does not allow --------------------------------------------------------------------------


@pytest.mark.parametrize("proc", ["apoc.meta.schema", "apoc.path.subgraphAll", "apoc.stats.degrees",
                                  "apoc.convert.setJsonProperty", "apoc.cypher.run"])
def test_a_procedure_the_variant_does_not_allow_is_named_for_the_repair(proc):
    q = f"CALL {proc}() YIELD value RETURN value"
    problems = procedure_call_problems(q, APOC)
    assert problems and problems[0].startswith(f"CALL {proc}: not a procedure you may call here")
    assert "apoc.path.subgraphNodes" in problems[0] and "db.index.fulltext.queryNodes" in problems[0]
    assert write_clause(q, extra_procedures=APOC) == f"CALL {proc}"


def test_subqueries_and_the_fulltext_index_are_not_problems():
    q = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s WHERE s:T_TIS "
         "CALL (s) { MATCH (s)<-[:DERIVED_FROM]-(c) RETURN count(c) AS kids } RETURN s.uuid, kids")
    assert procedure_call_problems(q, APOC) == []


# --- the default path is unchanged ----------------------------------------------------------------------------------


@pytest.mark.parametrize("q", [GOOD, sub("{maxLevel: 400}"), sub("{relationshipFilter: '>', maxLevel: 4}"),
                               "CALL apoc.path.subgraphNodes YIELD node RETURN node"])
def test_without_a_variant_every_apoc_call_is_refused_exactly_as_before(q):
    assert write_clause(q) == "CALL apoc.path.subgraphNodes"
    assert write_clause(q, extra_procedures=()) == "CALL apoc.path.subgraphNodes"


def test_the_default_allowlist_holds_no_path_procedure():
    assert not any(p.startswith("apoc.") for p in cypher_text.ALLOWED_PROCEDURES)


def test_variant_procedures_reads_only_a_real_collection():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    assert cypher_text.variant_procedures(SimpleNamespace(EXTRA_ALLOWED_PROCEDURES=APOC)) == APOC
    assert cypher_text.variant_procedures(SimpleNamespace()) == frozenset()
    assert cypher_text.variant_procedures(MagicMock()) == frozenset()
    assert cypher_text.variant_procedures(None) == frozenset()
