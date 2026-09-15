"""
D3: entity_tree reads the graph with READ routing.

`Driver.execute_query` defaults to `routing_=RoutingControl.WRITE`, which runs the
statement in a write transaction. entity_tree's three reads (edges, edge attributes,
lineage) only read, so each passes `routing_=RoutingControl.READ` and the server
refuses a write. The patched driver asserts the keyword on every call, and a source
scan keeps a future `execute_query` call from forgetting it.
"""
from __future__ import annotations

import ast
import inspect
import json
from unittest.mock import MagicMock, Mock, patch

import neo4j
from rest_framework.test import APIRequestFactory

READ = neo4j.RoutingControl.READ


def _request(method="get", data=None):
    factory = APIRequestFactory()
    if data is not None:
        req = getattr(factory, method)("/", data=json.dumps(data), content_type="application/json")
        req.data = data
    else:
        req = getattr(factory, method)("/")
    user = MagicMock()
    user.is_authenticated = True
    req.user = user
    req.query_params = req.GET
    return req


def _viewset():
    from nextseek_api.services.entity_tree import EntityTreeViewSet

    vs = EntityTreeViewSet()
    vs.kwargs = {}
    vs.format_kwarg = None
    return vs


def _driver_under(mock_gd):
    driver = MagicMock()
    mock_gd.driver.return_value.__enter__ = Mock(return_value=driver)
    mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
    return driver


def _routing_of_every_call(driver):
    calls = driver.execute_query.call_args_list
    assert calls, "execute_query was never called"
    return [c.kwargs.get("routing_") for c in calls]


@patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
@patch("nextseek_api.services.entity_tree.GraphDatabase")
def test_edges_read_with_read_routing(mock_gd, _):
    driver = _driver_under(mock_gd)
    driver.execute_query.return_value = (
        [{"source": "NHP", "target": "PAV", "annotation": "Patient Visit"}], MagicMock(), [],
    )

    resp = _viewset().list_edges(_request())

    assert resp.status_code == 200
    assert _routing_of_every_call(driver) == [READ]


@patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
@patch("nextseek_api.services.entity_tree.GraphDatabase")
@patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[])
def test_edge_attributes_read_with_read_routing(_sql, mock_gd, _auth):
    driver = _driver_under(mock_gd)
    driver.execute_query.return_value = (
        [{"source": "NHP", "target": "PAV", "annotation": "Patient Visit", "internal_assay_id": 16}],
        MagicMock(),
        [],
    )

    resp = _viewset().list_edge_attributes(_request())

    assert resp.status_code == 200
    assert _routing_of_every_call(driver) == [READ]


@patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000000"}])
def test_a_single_lineage_reads_with_read_routing(_sql):
    driver = MagicMock()
    graph = MagicMock()
    graph.nodes = []
    graph.relationships = []
    driver.execute_query.return_value = graph

    _viewset()._fetch_single_lineage(driver, 42, "neo4j", "NHP-1", "42")

    assert _routing_of_every_call(driver) == [READ]
    assert driver.execute_query.call_args.kwargs["database_"] == "neo4j"


@patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
@patch("nextseek_api.services.samples._resolve_uid_to_seek_id", side_effect=["42", "43"])
@patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000000"}])
@patch("nextseek_api.services.entity_tree.GraphDatabase")
def test_the_lineage_endpoint_reads_every_tree_with_read_routing(mock_gd, _sql, _resolve, _auth):
    driver = _driver_under(mock_gd)
    graph = MagicMock()
    graph.nodes = []
    graph.relationships = []
    driver.execute_query.return_value = graph

    resp = _viewset().lineage(_request("post", {"sample_ids": ["NHP-1", "NHP-2"]}))

    assert resp.status_code == 200
    assert _routing_of_every_call(driver) == [READ, READ]


def test_every_execute_query_call_in_the_module_passes_read_routing():
    """Three calls today; a new one without READ routing fails here."""
    from nextseek_api.services import entity_tree

    tree = ast.parse(inspect.getsource(entity_tree))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_query"
    ]
    assert len(calls) == 3
    for call in calls:
        routing = [kw.value for kw in call.keywords if kw.arg == "routing_"]
        assert len(routing) == 1, f"execute_query at line {call.lineno} has no routing_"
        assert ast.unparse(routing[0]) == "neo4j.RoutingControl.READ", ast.unparse(routing[0])
