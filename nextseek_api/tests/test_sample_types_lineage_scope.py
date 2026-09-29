"""
Project scope on the two lineage endpoints of ``nextseek_api/services/sample_types.py``:
``GET sampletypes/{uid}/child_types/`` and ``POST sample_types/get_parents/parents_by_child_types/``.

A caller who is not a superuser sees only samples in their projects, and lineage stops at the edge of those projects:
the sample named in the request and every sample on the DERIVED_FROM path must pass graph_search's scope clause
(``nextseek_api/graph_search/query.py``). The scope comes from ``graph_search.scope.resolve_scope``; a caller it cannot
resolve sees nothing. A superuser's statement carries no scope.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from nextseek_api.graph_search.query import _LINEAGE_PATH_SCOPE, _SCOPE_MATCH
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

MEMBER = Scope(is_admin=False, person_id=7, project_ids=(2,))
NO_PROJECTS = Scope(is_admin=False, person_id=8, project_ids=())
ADMIN = Scope(is_admin=True, person_id=None, project_ids=())

_MOD = "nextseek_api.services.sample_types"


def _request(method="get", data=None, superuser=False):
    factory = APIRequestFactory()
    req = getattr(factory, method)("/")
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = superuser
    req.user = user
    if data is not None:
        req.data = data
    return req


def _driver(mock_gd, records):
    driver = MagicMock()
    mock_gd.driver.return_value.__enter__ = Mock(return_value=driver)
    mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
    driver.execute_query.return_value = (records, MagicMock(), ["type"])
    return driver


def _statement_and_params(driver):
    args, kwargs = driver.execute_query.call_args
    return args[0], kwargs


# --------------------------------------------------------------------------- child_types


def _children_viewset():
    from nextseek_api.services.sample_types import SampleTypeChildrenViewSet
    vs = SampleTypeChildrenViewSet()
    vs.kwargs = {}
    vs.format_kwarg = None
    return vs


@pytest.fixture
def child_types_env():
    with patch(f"{_MOD}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch(f"{_MOD}._resolve_sample_uid_to_seek_id", return_value="42"), \
            patch(f"{_MOD}.GraphDatabase") as mock_gd, \
            patch(f"{_MOD}.Sample_types") as mock_st, \
            patch(f"{_MOD}.resolve_scope") as mock_scope:
        mock_st.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
        ]
        yield mock_gd, mock_scope


def test_child_types_for_a_member_scopes_the_sample_and_every_node_on_the_path(child_types_env):
    mock_gd, mock_scope = child_types_env
    mock_scope.return_value = MEMBER
    driver = _driver(mock_gd, [{"type": "TIS"}])

    resp = _children_viewset().child_types(_request(), uid="42")

    assert resp.status_code == 200
    assert [item["title"] for item in resp.data["child_types"]] == ["TIS"]
    statement, params = _statement_and_params(driver)
    assert _SCOPE_MATCH in statement
    assert _LINEAGE_PATH_SCOPE in statement
    assert "lineage_path = (s)<-[:DERIVED_FROM*1..]-(child)" in statement
    assert params["projects"] == [2]
    assert params["id"] == 42


def test_child_types_for_a_sample_outside_the_callers_projects_is_not_found(child_types_env):
    mock_gd, mock_scope = child_types_env
    mock_scope.return_value = MEMBER
    _driver(mock_gd, [])

    resp = _children_viewset().child_types(_request(), uid="42")

    assert resp.status_code == 404


def test_child_types_for_a_visible_sample_with_no_visible_child_is_empty(child_types_env):
    mock_gd, mock_scope = child_types_env
    mock_scope.return_value = MEMBER
    _driver(mock_gd, [{"type": None}])

    resp = _children_viewset().child_types(_request(), uid="42")

    assert resp.status_code == 200
    assert resp.data["total"] == 0


@pytest.mark.parametrize("failure", [ScopeUnavailable("no person"), RuntimeError("membership read failed")])
def test_child_types_for_an_unresolved_caller_is_not_found_and_reads_no_graph(child_types_env, failure):
    mock_gd, mock_scope = child_types_env
    mock_scope.side_effect = failure

    resp = _children_viewset().child_types(_request(), uid="42")

    assert resp.status_code == 404
    mock_gd.driver.assert_not_called()


def test_child_types_for_a_caller_with_no_projects_is_not_found_and_reads_no_graph(child_types_env):
    mock_gd, mock_scope = child_types_env
    mock_scope.return_value = NO_PROJECTS

    resp = _children_viewset().child_types(_request(), uid="42")

    assert resp.status_code == 404
    mock_gd.driver.assert_not_called()


def test_child_types_for_a_superuser_carries_no_scope(child_types_env):
    mock_gd, mock_scope = child_types_env
    mock_scope.return_value = ADMIN
    driver = _driver(mock_gd, [])

    resp = _children_viewset().child_types(_request(superuser=True), uid="42")

    assert resp.status_code == 200
    assert resp.data["total"] == 0
    statement, params = _statement_and_params(driver)
    assert "project_ids" not in statement and "$projects" not in statement
    assert "projects" not in params


# --------------------------------------------------------------------------- parents_by_child_types


def _parents_viewset():
    from nextseek_api.services.sample_types import SamplesByChildTypesViewSet
    vs = SamplesByChildTypesViewSet()
    vs.kwargs = {}
    vs.format_kwarg = None
    return vs


@pytest.fixture
def parents_env():
    with patch(f"{_MOD}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch(f"{_MOD}.GraphDatabase") as mock_gd, \
            patch(f"{_MOD}.Sample_types") as mock_st, \
            patch(f"{_MOD}.resolve_scope") as mock_scope:
        mock_st.objects.filter.return_value.values.return_value = [
            {"title": "MUS", "description": "Mouse"},
        ]
        yield mock_gd, mock_scope


@pytest.mark.parametrize("body", [
    {"child_sample_types": ["TIS"]},
    {"child_sample_types": ["TIS"], "parent_sample_type_filters": ["MUS"]},
], ids=["no-parent-filter", "parent-filter"])
def test_parents_for_a_member_scope_every_node_on_the_path(parents_env, body):
    mock_gd, mock_scope = parents_env
    mock_scope.return_value = MEMBER
    driver = _driver(mock_gd, [{"id": 1, "uuid": "MUS-230204BBB-4", "type": "MUS"}])

    resp = _parents_viewset().parents_by_child_types(_request("post", data=body))

    assert resp.status_code == 200
    assert resp.data["total"] == 1
    statement, params = _statement_and_params(driver)
    assert "lineage_path = (c:Sample)-[:DERIVED_FROM*1..]->(p)" in statement
    assert _LINEAGE_PATH_SCOPE in statement
    assert params["projects"] == [2]


@pytest.mark.parametrize("scope", [NO_PROJECTS, ScopeUnavailable("no person"), RuntimeError("read failed")],
                         ids=["no-projects", "unavailable", "read-failure"])
def test_parents_for_a_caller_who_sees_nothing_return_nothing_and_read_no_graph(parents_env, scope):
    mock_gd, mock_scope = parents_env
    if isinstance(scope, Exception):
        mock_scope.side_effect = scope
    else:
        mock_scope.return_value = scope

    resp = _parents_viewset().parents_by_child_types(_request("post", data={"child_sample_types": ["TIS"]}))

    assert resp.status_code == 200
    assert resp.data["total"] == 0
    mock_gd.driver.assert_not_called()


def test_parents_for_a_superuser_carry_no_scope(parents_env):
    mock_gd, mock_scope = parents_env
    mock_scope.return_value = ADMIN
    driver = _driver(mock_gd, [{"id": 1, "uuid": "MUS-230204BBB-4", "type": "MUS"}])

    resp = _parents_viewset().parents_by_child_types(
        _request("post", data={"child_sample_types": ["TIS"]}, superuser=True))

    assert resp.status_code == 200
    assert resp.data["total"] == 1
    statement, params = _statement_and_params(driver)
    assert "project_ids" not in statement and "$projects" not in statement
    assert "projects" not in params
