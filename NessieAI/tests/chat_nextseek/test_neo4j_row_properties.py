"""
Graph rows keep the caller's property filtering for every value type the driver returns.

The values are the installed driver's own ``neo4j.graph`` Node, Relationship and Path, built with its constructors,
not duck-typed stand-ins. Each test goes through ``tool_neo4j_query`` with a fake driver that records every statement,
so the real ``_read_rows`` runs. For a caller who is not an admin, a node's properties outside
``graph_scope.HIDDEN_SAMPLE_PROPERTIES`` come back, whether the node is a column, sits in a list or lies on a path; an
admin gets every property, and a path as before. Every Neo4j temporal still leaves as its ISO string. No database is
reached.
"""
from __future__ import annotations

import json
import sys
import types
from collections.abc import Mapping
from types import SimpleNamespace

import neo4j as _real_neo4j
import pytest
from neo4j.graph import Graph, Node, Path
from neo4j.time import Date, Duration

from chat_nextseek.graph_scope import HIDDEN_SAMPLE_PROPERTIES, GraphScope, with_scope
from chat_nextseek.helpers.tools import neo4j as tool_module
from chat_nextseek.helpers.tools.neo4j import tool_neo4j_query

MEMBER = GraphScope.for_projects([3, 1], source="test")
ADMIN = GraphScope.admin("test")

NODE_QUERY = "MATCH (s:T_SLD) RETURN s"
LIST_QUERY = "MATCH (s:T_SLD) RETURN collect(s) AS kids"
PATH_QUERY = "MATCH p = (s:T_SLD)-[:DERIVED_FROM]->(t:T_TIS) RETURN p"
DURATION_QUERY = "MATCH (s:T_SLD) RETURN s, duration.between(date(s.a), date(s.b)) AS took"

# One value per property the scope module decides a caller who is not an admin does not get.
HIDDEN = {name: [f"{name} value"] for name in sorted(HIDDEN_SAMPLE_PROPERTIES)}


# --------------------------------------------------------------------------- #
# Real driver values
# --------------------------------------------------------------------------- #

def _visible(n: int) -> dict:
    """What every caller gets from node ``n``, as it leaves the tool."""
    return {"uuid": f"SLD-{n}", "Organ": "Lung", "created": "2024-06-10"}


def _node(graph: Graph, n: int, labels=("Sample", "T_SLD")) -> Node:
    properties = {"uuid": f"SLD-{n}", "Organ": "Lung", "created": Date(2024, 6, 10), **HIDDEN}
    return Node(graph, f"4:x:{n}", n, labels, properties)


def _path(graph: Graph) -> Path:
    start, end = _node(graph, 1), _node(graph, 2, ("Sample", "T_TIS"))
    rel = graph.relationship_type("DERIVED_FROM")(graph, "5:x:1", 1,
                                                  {"internal_assay_title": "Staining", "at": Date(2024, 6, 11)})
    # The endpoints are set the way the driver's own hydration sets them for a path it reads.
    rel._start_node, rel._end_node = start, end
    return Path(start, rel)


# --------------------------------------------------------------------------- #
# The recording fake driver
# --------------------------------------------------------------------------- #

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return SimpleNamespace(counters=None)


class _Session:
    """Records every statement; the total probe answers ``total``."""

    def __init__(self, rows, total=None):
        self.rows, self.total = rows, total
        self.statements = []

    def execute_read(self, fn, *args, **kwargs):
        session = self

        class _Tx:
            def run(self, cypher, parameters=None, **_kw):
                session.statements.append(cypher)
                return _Result([{"__total": session.total}] if "__total" in cypher else session.rows)
        return fn(_Tx(), *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def run(monkeypatch):
    """``run(scope, cypher, rows)``: the tool's result for ``rows`` handed back by a fake driver."""
    def call(scope, cypher, rows):
        session = _Session(rows)
        driver = SimpleNamespace(session=lambda **kw: session, close=lambda: None)
        module = types.ModuleType("neo4j")
        module.GraphDatabase = SimpleNamespace(driver=lambda *a, **k: driver)
        module.unit_of_work = _real_neo4j.unit_of_work
        monkeypatch.setitem(sys.modules, "neo4j", module)
        base = SimpleNamespace(NEO4J_URI="bolt://graph:7687", NEO4J_USER="u", NEO4J_PASSWORD="p",
                               NEO4J_DATABASE="neo4j")
        out = tool_neo4j_query(with_scope(base, scope), cypher, {})
        assert out["ok"] is True, out.get("error")
        assert session.statements, "the fake driver ran nothing"
        return out
    return call


# --------------------------------------------------------------------------- #
# A node as a column
# --------------------------------------------------------------------------- #

def test_a_members_node_properties_stay_filtered_after_conversion(run):
    out = run(MEMBER, NODE_QUERY, [{"s": _node(Graph(), 1)}])

    assert out["data"] == [{"s": _visible(1)}]
    assert out["count"] == 1
    json.dumps(out["data"])


def test_an_admins_node_keeps_every_property_after_conversion(run):
    out = run(ADMIN, NODE_QUERY, [{"s": _node(Graph(), 1)}])

    assert out["data"] == [{"s": {**_visible(1), **HIDDEN}}]
    json.dumps(out["data"])


# --------------------------------------------------------------------------- #
# Nodes inside a list
# --------------------------------------------------------------------------- #

def test_a_members_nodes_in_a_list_stay_filtered(run):
    graph = Graph()
    out = run(MEMBER, LIST_QUERY, [{"kids": [_node(graph, 1), _node(graph, 2)]}])

    assert out["data"] == [{"kids": [_visible(1), _visible(2)]}]
    json.dumps(out["data"])


def test_an_admins_nodes_in_a_list_keep_every_property(run):
    graph = Graph()
    out = run(ADMIN, LIST_QUERY, [{"kids": [_node(graph, 1), _node(graph, 2)]}])

    assert out["data"] == [{"kids": [{**_visible(1), **HIDDEN}, {**_visible(2), **HIDDEN}]}]


# --------------------------------------------------------------------------- #
# Nodes on a path
# --------------------------------------------------------------------------- #

def test_a_members_path_nodes_stay_filtered_and_their_dates_convert(run):
    out = run(MEMBER, PATH_QUERY, [{"p": _path(Graph())}])

    assert out["data"] == [{"p": {
        "nodes": [_visible(1), _visible(2)],
        "relationships": [{"internal_assay_title": "Staining", "at": "2024-06-11"}],
    }}]
    json.dumps(out["data"])


def test_an_admins_path_is_returned_as_before(run):
    path = _path(Graph())
    out = run(ADMIN, PATH_QUERY, [{"p": path}])

    assert out["data"][0]["p"] is path


# --------------------------------------------------------------------------- #
# Durations are tuples to Python; they still leave as ISO strings
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("scope", [MEMBER, ADMIN], ids=["member", "admin"])
def test_a_duration_converts_for_every_caller(run, scope):
    node = Node(Graph(), "4:x:1", 1, ("Sample", "T_SLD"), {"uuid": "SLD-1", "span": Duration(days=3)})
    out = run(scope, DURATION_QUERY, [{"s": node, "took": Duration(days=2)}])

    assert out["data"] == [{"s": {"uuid": "SLD-1", "span": "P3D"}, "took": "P2D"}]
    json.dumps(out["data"])


# --------------------------------------------------------------------------- #
# The order guard: a member's rows are filtered before anything converts them
# --------------------------------------------------------------------------- #

def _nodes_in(value) -> list:
    """Every driver Node reachable in ``value``: itself, inside maps, lists and tuples, and on a path."""
    if isinstance(value, Node):
        return [value]
    if isinstance(value, Path):
        return list(value.nodes)
    if isinstance(value, Mapping):
        return [node for item in value.values() for node in _nodes_in(item)]
    if isinstance(value, (list, tuple)):
        return [node for item in value for node in _nodes_in(item)]
    return []


@pytest.fixture
def conversion_log(monkeypatch):
    """Wraps the tool's ``plain_value``: records every call and every Node that reached one."""
    real = tool_module.plain_value
    log = SimpleNamespace(calls=0, nodes=[])

    def guarded(value):
        log.calls += 1
        log.nodes.extend(_nodes_in(value))
        return real(value)

    monkeypatch.setattr(tool_module, "plain_value", guarded)
    return log


def _shapes():
    graph = Graph()
    return [
        (NODE_QUERY, [{"s": _node(graph, 1)}]),
        (LIST_QUERY, [{"kids": [_node(graph, 2), _node(graph, 3)]}]),
        (PATH_QUERY, [{"p": _path(graph)}]),
    ]


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node", "list", "path"])
def test_no_node_reaches_conversion_before_a_members_filter(run, conversion_log, index):
    cypher, rows = _shapes()[index]
    run(MEMBER, cypher, rows)

    assert conversion_log.calls, "the rows never reached plain_value"
    assert conversion_log.nodes == []


def test_the_conversion_guard_sees_an_admins_node(run, conversion_log):
    # The guard can see a Node when one reaches plain_value: an admin's rows are converted as they come.
    cypher, rows = _shapes()[0]
    run(ADMIN, cypher, rows)

    assert {node.element_id for node in conversion_log.nodes} == {"4:x:1"}
