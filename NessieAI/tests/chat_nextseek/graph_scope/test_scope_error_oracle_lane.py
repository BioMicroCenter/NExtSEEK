"""
No error oracle on a node outside the caller's projects, proven on a real Neo4j.

Runs only under lane.sh (a private, throwaway Neo4j holding fixture_graph.py); elsewhere every test here skips.
The prover once ANDed its scope clause after the model's own WHERE: ``WHERE (<model's predicate>) AND <scope>``. Neo4j
may evaluate the model's predicate first, so a predicate that raises on some property value (a division by zero inside
a CASE, ``toLower`` of a number) failed the statement when a foreign node matched and returned a clean 0 when none did:
one bit of a foreign property, or of a foreign sample's existence, per turn. The scope clause now guards whatever could
raise, so for a caller who is not a member the probe must return the same clean rows as it would on a graph without the
foreign node. The control arm runs the same probe for a caller who may see the node and requires the error: without
it, a probe that never raises would pass vacuously.

The guard costs no index seek: Neo4j cannot seek on a predicate inside CASE, so the conjuncts that cannot raise stay
outside it, and the last test reads the plans to show a UID, a UID list, a UID prefix and a type still seek.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 5.6.
"""
from __future__ import annotations

import json
import os

import pytest

from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers import uid_check

URI = os.environ.get("GRAPH_SCOPE_NEO4J_URI", "")
PASSWORD = os.environ.get("GRAPH_SCOPE_NEO4J_PASSWORD", "")

pytestmark = pytest.mark.skipif(not (URI and PASSWORD), reason="the graph scope lane runs only under lane.sh")

# (id, statement, parameters, a caller the node is foreign to, a caller who may see it). Every probe raises exactly
# when a node its predicate reads exists and is visible to the statement; see fixture_graph.py for the values.
PROBES = [
    ("reviewer_case_division",
     "MATCH (s:Sample {uuid:'TIS-230102AAA-2'}) WHERE (CASE WHEN s.Organ STARTS WITH 'L' THEN 1/$zero ELSE 1 END)=1 "
     "RETURN count(s) AS n", {"zero": 0}, (2,), (1, 3)),
    ("reviewer_case_division_literal",
     "MATCH (s:Sample {uuid:'TIS-230102AAA-2'}) WHERE (CASE WHEN s.Organ STARTS WITH 'L' THEN 1/0 ELSE 1 END)=1 "
     "RETURN count(s)", {}, (2,), (1, 3)),
    ("seek_then_type_error",
     "MATCH (s:Sample) WHERE s.uuid = $uid AND toLower(s.Concentration) = 'x' RETURN count(s) AS n",
     {"uid": "MUS-230101AAA-1"}, (2,), (1, 3)),
    ("label_scan_type_error",
     "MATCH (s:T_MUS) WHERE toLower(s.Concentration) = 'x' RETURN count(s) AS n", {}, (2,), (1, 3)),
    ("optional_parent_type_error",
     "MATCH (s:Sample {uuid: 'SLD-230104AAA-4'}) OPTIONAL MATCH (s)-[:DERIVED_FROM]->(p:Sample) "
     "WHERE toLower(p.Concentration) = 'x' RETURN count(p) AS n", {}, (1, 3), (1, 2)),
    ("fulltext_type_error",
     "CALL db.index.fulltext.queryNodes('sample_search_text', 'alpha') YIELD node AS s "
     "WHERE toLower(s.Concentration) = 'x' RETURN count(s) AS n", {}, (2,), (1, 3)),
]


def _run(lane, cypher: str, params: dict, caller: tuple[int, ...]):
    out = scope_cypher(cypher, params, GraphScope.for_projects(list(caller), source="test"))
    assert isinstance(out, Scoped), getattr(out, "reasons", out)
    try:
        return "rows", lane.read(out.cypher, out.parameters), out.cypher
    except Exception as exc:  # noqa: BLE001 (the outcome under test)
        return "error", f"{type(exc).__name__}: {str(exc)[:200]}", out.cypher


@pytest.mark.parametrize("probe", PROBES, ids=[p[0] for p in PROBES])
def test_a_raising_predicate_is_never_evaluated_on_a_foreign_node(lane, probe):
    name, cypher, params, stranger, member = probe
    lane.reload()
    kind, payload, ran = _run(lane, cypher, params, member)
    assert kind == "error", f"{name}: the control did not raise for a caller who sees the node, so the probe proves " \
                            f"nothing: {payload}\n{ran}"
    kind, payload, ran = _run(lane, cypher, params, stranger)
    assert kind == "rows", f"{name}: the statement failed for a caller the node is foreign to (an oracle): " \
                           f"{payload}\n{ran}"
    assert all(value in (0, None) for row in payload for value in row.values()), (name, payload)


# The conjunct shapes the prover leaves outside the guard (cypher_scope._Parser.harmless), each run over every sample
# and relationship of the fixture with parameters of the wrong type: none may raise, or leaving it outside is unsafe.
# (The prover also accepts !=, which this Neo4j rejects as a syntax error for every caller alike.)
HARMLESS_SHAPES = [
    "s.Concentration = $p", "$p <> s.Organ", "s.Concentration < $p", "s.Organ <= $p",
    "s.Concentration > -1", "s.Organ >= $p", "s.Organ STARTS WITH $p", "s.Concentration ENDS WITH $p",
    "s.Organ CONTAINS $p", "s.Concentration CONTAINS 'x'", "s.Organ IN [$p, 1, 'x', s.Concentration]",
    "s.Organ IN $list", "s.Organ IN $none", "s.Tags IS NULL", "s.Concentration IS NOT NULL", "s:T_TIS",
    "s.flag = true", "s.Organ = NULL", "r.internal_assay_title = $p", "r.protocol_title STARTS WITH $p",
]
WRONG_TYPES = [5, 2.5, "x", True, [1, "a"], {"a": 1}, None]


@pytest.mark.parametrize("shape", HARMLESS_SHAPES)
def test_the_shapes_left_outside_the_guard_never_raise(lane, shape):
    lane.reload()
    statement = f"MATCH (s:Sample)-[r:DERIVED_FROM]->(:Sample) WHERE {shape} RETURN count(*) AS n"
    for value in WRONG_TYPES:
        lane.read(statement, {"p": value, "list": [value], "none": None})
        out = scope_cypher(statement, {"p": value, "list": [value], "none": None},
                           GraphScope.for_projects([2], source="test"))
        assert isinstance(out, Scoped) and "CASE WHEN" not in out.cypher, (shape, out)


# Open: the guard covers the WHERE of the pattern that binds a node. A later clause that reads the same node can still
# be evaluated before that node's scope clause, because Neo4j merges consecutive MATCH clauses, and pushes a simple
# WITH's WHERE down, into one selection and orders its predicates by cost. Closing it needs the prover to carry which
# names are scoped from clause to clause and guard every WHERE that reads one; strict xfail until then.
OPEN_PROBES = [
    ("later_match_reads_a_scoped_node",
     "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) MATCH (s)-[:IN_STUDY]->(st:Study) "
     "WHERE toLower(s.Concentration) = 'x' RETURN count(*) AS n", {}, (2,), (1, 3)),
    ("with_where_reads_a_scoped_node",
     "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) WITH s WHERE toLower(s.Concentration) = 'x' RETURN count(*) AS n",
     {}, (2,), (1, 3)),
]


@pytest.mark.xfail(strict=True, reason="a later clause reading a scoped node is not guarded yet")
@pytest.mark.parametrize("probe", OPEN_PROBES, ids=[p[0] for p in OPEN_PROBES])
def test_open_a_later_clause_reading_a_scoped_node(lane, probe):
    test_a_raising_predicate_is_never_evaluated_on_a_foreign_node(lane, probe)


# While that stays open, what the error says is held back instead: Neo4j's message for a value it cannot parse quotes
# the value, so through the tool a caller who is not an admin is told only the error's codes. The admin arm is the
# control: the same statement must raise with the foreign value in its message, or the stranger arm proves nothing. A
# statement Neo4j cannot even plan fails alike for everyone, and keeps its message so the graph agent can fix it.
FOREIGN_VALUE = "Ada Lovelace ZQF1"  # MUS-230101AAA-1's Scientist, project 1 only
TEXT_PROBES = [
    ("with_where_date",
     "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) WITH s WHERE date(s.Scientist) IS NULL RETURN count(*) AS n"),
    ("later_match_date",
     "MATCH (s:Sample {uuid: 'MUS-230101AAA-1'}) MATCH (s)-[:IN_STUDY]->(st:Study) "
     "WHERE date(s.Scientist) IS NULL RETURN count(*) AS n"),
]


def _tool(scope: GraphScope, cypher: str) -> dict:
    from types import SimpleNamespace

    from chat_nextseek.graph_scope import with_scope
    from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

    config = with_scope(SimpleNamespace(NEO4J_URI=URI, NEO4J_USER="neo4j", NEO4J_PASSWORD=PASSWORD,
                                        NEO4J_DATABASE="neo4j"), scope)
    return tool_neo4j_query(config, cypher, {})


@pytest.mark.parametrize("probe", TEXT_PROBES, ids=[p[0] for p in TEXT_PROBES])
def test_the_tool_never_hands_a_stranger_the_value_neo4j_quotes(lane, probe):
    from chat_nextseek.helpers.tools.neo4j import RUNTIME_ERROR_WITHHELD

    name, cypher = probe
    lane.reload()
    admin = _tool(GraphScope.admin("test"), cypher)
    assert admin["ok"] is False and FOREIGN_VALUE in admin["error"], (name, admin)
    for caller in ((2,), (3,), (1, 3)):
        out = _tool(GraphScope.for_projects(list(caller), source="test"), cypher)
        assert out["scope"]["decision"] == "proven", (name, caller, out["scope"])
        assert FOREIGN_VALUE not in json.dumps(out, default=str), (name, caller, out.get("error"))
        if not out["ok"]:
            assert out["error"].startswith(RUNTIME_ERROR_WITHHELD.split("{", 1)[0]), (name, caller, out["error"])


def test_a_statement_neo4j_cannot_plan_keeps_its_message_for_a_member(lane):
    from chat_nextseek.helpers.tools.neo4j import RUNTIME_ERROR_WITHHELD

    lane.reload()
    cypher = "MATCH (s:T_TIS) RETURN s.Organ + count(s) AS n"
    admin = _tool(GraphScope.admin("test"), cypher)
    member = _tool(GraphScope.for_projects([1], source="test"), cypher)
    assert admin["ok"] is False and member["ok"] is False, (admin, member)
    assert member["scope"]["decision"] == "proven", member["scope"]
    assert not member["error"].startswith(RUNTIME_ERROR_WITHHELD.split("{", 1)[0]), member["error"]
    assert "Neo.ClientError.Statement" in member["error"], member["error"]


# The indexes graph_sync creates on Sample (nextseek_api/graph_sync/cypher.py), and statements that seek them.
SEEK_INDEXES = ("CREATE INDEX sample_uuid IF NOT EXISTS FOR (s:Sample) ON (s.uuid)",
                "CREATE INDEX sample_type IF NOT EXISTS FOR (s:Sample) ON (s.type)")
SEEKS = [
    ("uid", "MATCH (s:Sample) WHERE s.uuid = $uid AND toLower(s.Organ) = 'lung' RETURN s.uuid AS u",
     {"uid": "TIS-230102AAA-2"}),
    ("uid_list", "MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS u", {"uids": ["TIS-230102AAA-2"]}),
    ("uid_prefix", "MATCH (s:Sample) WHERE s.uuid STARTS WITH 'SLD-' AND toFloat(s.PercentNecrosis) > 40 "
                   "RETURN count(s) AS n", {}),
    ("type", "MATCH (s:Sample) WHERE toLower(s.Organ) = 'lung' AND s.type = $t RETURN count(s) AS n", {"t": "TIS"}),
    ("uid_check", uid_check.CHECK_CYPHER, {"checks": [{"uid": "TIS-230102AAA-2-PUB", "base": "TIS-230102AAA-2"}]}),
]


def _operators(plan) -> list[str]:
    out = [plan["operatorType"] if isinstance(plan, dict) else plan.operator_type]
    for child in (plan.get("children", []) if isinstance(plan, dict) else plan.children):
        out += _operators(child)
    return out


@pytest.mark.parametrize("seek", SEEKS, ids=[s[0] for s in SEEKS])
def test_the_guard_leaves_the_index_seek_in_place(lane, seek):
    name, cypher, params = seek
    lane.reload()
    with lane.driver.session() as session:
        for statement in SEEK_INDEXES:
            session.run(statement).consume()
        session.run("CALL db.awaitIndexes(300)").consume()
        admin = _operators(session.run("EXPLAIN " + cypher, params).consume().plan)
        out = scope_cypher(cypher, params, GraphScope.for_projects([2], source="test"))
        assert isinstance(out, Scoped), getattr(out, "reasons", out)
        scoped = _operators(session.run("EXPLAIN " + out.cypher, out.parameters).consume().plan)
    assert any(op.startswith("NodeIndexSeek") for op in admin), (name, admin)
    assert any(op.startswith("NodeIndexSeek") for op in scoped), (name, scoped, out.cypher)
    assert not any(op.startswith("NodeByLabelScan") for op in scoped), (name, scoped, out.cypher)
