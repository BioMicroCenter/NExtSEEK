"""
Project scope on ``POST /nextseek_api/entity_tree/lineage/`` (``EntityTreeViewSet.lineage``).

The endpoint walks DERIVED_FROM both ways from each caller-supplied sample and returns every node's id, uuid and type
and every edge's assay and protocol titles. It applies the rule the sample type lineage endpoints apply
(``nextseek_api/services/sample_types.py``): a caller who is not a superuser sees only samples in their projects, and
lineage stops at the edge of those projects, because the named sample and every sample on each path must pass
graph_search's scope clause. A root the caller may not see answers exactly as a sample that does not exist. The scope
comes from ``graph_search.scope.resolve_scope``; a caller it cannot resolve, or who has no projects, reads no graph.
A superuser's statement carries no scope. The throwaway-Neo4j lane
(``NessieAI/tests/chat_nextseek/graph_scope/test_entity_tree_lineage_lane.py``) proves the statement on a real graph.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from nextseek_api.graph_search.query import _LINEAGE_PATH_SCOPE, _SCOPE_MATCH
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

MEMBER = Scope(is_admin=False, person_id=7, project_ids=(2,))
NO_PROJECTS = Scope(is_admin=False, person_id=8, project_ids=())
ADMIN = Scope(is_admin=True, person_id=None, project_ids=())

_MOD = "nextseek_api.services.entity_tree"


def _request(sample_ids, superuser=False):
    req = APIRequestFactory().post("/")
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = superuser
    req.user = user
    req.data = {"sample_ids": list(sample_ids)}
    req.query_params = req.GET  # the node paginator reads it
    return req


def _viewset():
    from nextseek_api.services.entity_tree import EntityTreeViewSet
    vs = EntityTreeViewSet()
    vs.kwargs = {}
    vs.format_kwarg = None
    return vs


def _node(node_id, uuid, node_type):
    node = MagicMock()
    node._properties = {"id": node_id, "uuid": uuid, "type": node_type}
    return node


def _rel(child, parent):
    rel = MagicMock()
    rel._properties = {"internal_assay_id": "16", "internal_assay_title": "Visit", "protocol_title": None,
                       "protocol_id": None}
    rel.start_node = MagicMock()
    rel.start_node._properties = child._properties
    rel.end_node = MagicMock()
    rel.end_node._properties = parent._properties
    return rel


def _graph(nodes, rels):
    result = MagicMock()
    result.nodes = nodes
    result.relationships = rels
    return result


NHP = _node(42, "NHP-1", "NHP")
TIS = _node(43, "TIS-1", "TIS")
VISIBLE_TREE = _graph([NHP, TIS], [_rel(TIS, NHP)])
EMPTY = _graph([], [])


@pytest.fixture
def env():
    """resolve_scope, the UID resolver, Neo4j and the clade colour lookup stubbed; hand back the mocks."""
    resolve = {"NHP-1": "42", "TIS-FOREIGN": "77", "TIS-X": None}
    with patch(f"{_MOD}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch("nextseek_api.services.samples._resolve_uid_to_seek_id",
                  side_effect=lambda value: resolve.get(value, value if str(value).isdigit() else None)), \
            patch(f"{_MOD}._run_sql_query", return_value=[{"color": "#AAA"}]), \
            patch(f"{_MOD}.GraphDatabase") as mock_gd, \
            patch(f"{_MOD}.resolve_scope", create=True) as mock_scope:
        driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_scope.return_value = MEMBER
        yield driver, mock_scope, resolve


def _statement_and_params(driver, call=-1):
    args, kwargs = driver.execute_query.call_args_list[call]
    return args[0], kwargs


def test_a_member_walks_with_the_scope_on_the_root_and_on_every_path(env):
    driver, _, _ = env
    driver.execute_query.return_value = VISIBLE_TREE

    resp = _viewset().lineage(_request(["NHP-1"]))

    assert resp.status_code == 200
    tree = resp.data["trees"][0]
    assert tree["resolved_id"] == "42" and tree["warning"] is None
    assert {n["uuid"] for n in tree["nodes"]} == {"NHP-1", "TIS-1"}
    assert tree["total_rels"] == 1
    statement, params = _statement_and_params(driver)
    assert _SCOPE_MATCH in statement
    assert statement.count(_LINEAGE_PATH_SCOPE) == 2
    assert "lineage_path = (s)-[:DERIVED_FROM*0..]->(parent)" in statement
    assert "lineage_path = (s)<-[:DERIVED_FROM*0..]-(child)" in statement
    assert params["projects"] == [2]
    assert params["id"] == 42


def test_a_root_outside_the_callers_projects_answers_exactly_as_an_unknown_sample(env):
    driver, _, resolve = env
    driver.execute_query.return_value = EMPTY          # the scoped statement returns nothing for a foreign root

    resolve["TIS-X"] = "77"                            # exists, in another project
    foreign = _viewset().lineage(_request(["TIS-X"])).data
    resolve["TIS-X"] = None                            # does not exist at all
    unknown = _viewset().lineage(_request(["TIS-X"])).data

    assert foreign == unknown
    tree = foreign["trees"][0]
    assert tree["resolved_id"] is None and tree["total_nodes"] == 0 and tree["nodes"] == [] and tree["rels"] == []
    assert tree["warning"] == "Sample not found: TIS-X"


def test_a_foreign_numeric_id_answers_as_an_unknown_one(env):
    driver, _, _ = env
    driver.execute_query.return_value = EMPTY

    tree = _viewset().lineage(_request(["77"])).data["trees"][0]

    assert tree == {"input_id": "77", "resolved_id": None, "total_nodes": 0, "total_rels": 0, "nodes": [],
                    "rels": [], "warning": "Sample not found: 77"}


def test_a_failed_walk_for_a_member_does_not_echo_the_resolved_id(env):
    driver, _, _ = env
    driver.execute_query.side_effect = RuntimeError("boom")

    tree = _viewset().lineage(_request(["TIS-FOREIGN"])).data["trees"][0]

    assert tree["resolved_id"] is None
    assert tree["warning"].startswith("Query failed")


def test_a_mixed_request_keeps_the_visible_tree_and_hides_the_foreign_one(env):
    driver, _, _ = env
    driver.execute_query.side_effect = [VISIBLE_TREE, EMPTY]

    trees = _viewset().lineage(_request(["NHP-1", "TIS-FOREIGN"])).data["trees"]

    assert trees[0]["resolved_id"] == "42" and trees[0]["total_nodes"] == 2
    assert trees[1] == {"input_id": "TIS-FOREIGN", "resolved_id": None, "total_nodes": 0, "total_rels": 0,
                        "nodes": [], "rels": [], "warning": "Sample not found: TIS-FOREIGN"}


@pytest.mark.parametrize("scope", [NO_PROJECTS, ScopeUnavailable("no person"), RuntimeError("membership read failed")],
                         ids=["no-projects", "no-person", "read-failed"])
def test_a_caller_who_sees_nothing_reads_no_graph(env, scope):
    driver, mock_scope, _ = env
    if isinstance(scope, Scope):
        mock_scope.return_value = scope
    else:
        mock_scope.side_effect = scope

    resp = _viewset().lineage(_request(["NHP-1", "TIS-X"]))

    assert resp.status_code == 200
    assert [t["warning"] for t in resp.data["trees"]] == ["Sample not found: NHP-1", "Sample not found: TIS-X"]
    assert all(t["resolved_id"] is None for t in resp.data["trees"])
    driver.execute_query.assert_not_called()


def test_a_superuser_walks_unscoped(env):
    driver, mock_scope, _ = env
    mock_scope.return_value = ADMIN
    driver.execute_query.return_value = VISIBLE_TREE

    resp = _viewset().lineage(_request(["NHP-1"], superuser=True))

    assert resp.data["trees"][0]["resolved_id"] == "42"
    statement, params = _statement_and_params(driver)
    assert "project_ids" not in statement
    assert "projects" not in params
    assert "MATCH parents=(s)-[r1:DERIVED_FROM*0..]->(parent)" in statement


def test_a_superuser_still_sees_no_data_found_for_an_id_missing_from_the_graph(env):
    driver, mock_scope, _ = env
    mock_scope.return_value = ADMIN
    driver.execute_query.return_value = EMPTY

    tree = _viewset().lineage(_request(["77"], superuser=True)).data["trees"][0]

    assert tree["resolved_id"] == "77"
    assert tree["warning"] == "No data found for sample ID: 77"
