"""
Tests for nextseek_api/services/projects.py

Covers:
- _resolve_uid_to_seek_id() — numeric, DB lookup (single, multiple, none, None result, exception)
- ProjectProxyViewSet.list() — success, auth failure, HTML upstream (header & body), invalid JSON, validation failure
- ProjectProxyViewSet.retrieve() — success numeric, non-numeric resolved, non-numeric not found, pk fallback, auth failure, HTML upstream, invalid upstream
- ProjectProxyViewSet.create() — success, validation error, auth failure, HTML upstream, invalid upstream
- ProjectProxyViewSet.partial_update() — success numeric, uid resolved via DB, id mismatch, unresolved uid with body id, unresolved uid no body id, validation error, auth failure, HTML upstream, invalid upstream, pk fallback
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from nextseek_api.services.projects import ProjectProxyViewSet, _resolve_uid_to_seek_id


# ---------------------------------------------------------------------------
# Valid JSON:API response bodies
# ---------------------------------------------------------------------------
def _single_body():
    return json.dumps({
        "data": {
            "id": "2558", "type": "projects",
            "attributes": {"title": "Project 1"},
            "relationships": {"people": {"data": []}},
            "links": {"self": "/projects/2558"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }).encode()


def _list_body():
    return json.dumps({
        "data": [
            {"id": "2558", "type": "projects",
             "attributes": {"title": "Project 1"},
             "links": {"self": "/projects/2558"}}
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/projects?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://localhost:3000", "api_version": "v1"},
    }).encode()


VALID_CREATE_PAYLOAD = {
    "data": {
        "type": "projects",
        "attributes": {"title": "New Project"},
    }
}

VALID_UPDATE_PAYLOAD = {
    "data": {"type": "projects", "id": "2558",
             "attributes": {"title": "Updated"}}
}

JSON_H = {"Content-Type": "application/json"}
HTML_H = {"Content-Type": "text/html; charset=utf-8"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def rf():
    return APIRequestFactory()


@pytest.fixture
def user():
    u = MagicMock()
    u.is_authenticated = True
    return u


@pytest.fixture
def mock_client():
    with patch.object(ProjectProxyViewSet, "client") as mc:
        yield mc


def _drf(raw):
    return Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])


def _call(action, rf, user, method="get", url="/", data=None, **kwargs):
    fn = getattr(rf, method)
    raw = fn(url, data=data, format="json") if data is not None else fn(url)
    req = _drf(raw)
    req.user = user
    vs = ProjectProxyViewSet()
    return getattr(vs, action)(req, **kwargs)


# ===========================================================================
# _resolve_uid_to_seek_id
# ===========================================================================
class TestResolveUidToSeekId:
    def test_numeric(self):
        assert _resolve_uid_to_seek_id("2558") == "2558"

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_lookup_single(self, mock_dbp_cls):
        inst = MagicMock()
        inst.queryRecordsByConstraint.return_value = [{"id": 42}]
        mock_dbp_cls.return_value = inst
        assert _resolve_uid_to_seek_id("My Project") == "42"

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_lookup_multiple(self, mock_dbp_cls):
        inst = MagicMock()
        inst.queryRecordsByConstraint.return_value = [{"id": 1}, {"id": 2}]
        mock_dbp_cls.return_value = inst
        assert _resolve_uid_to_seek_id("Dupe") is None

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_lookup_empty(self, mock_dbp_cls):
        inst = MagicMock()
        inst.queryRecordsByConstraint.return_value = []
        mock_dbp_cls.return_value = inst
        assert _resolve_uid_to_seek_id("NoMatch") is None

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_lookup_none_result(self, mock_dbp_cls):
        inst = MagicMock()
        inst.queryRecordsByConstraint.return_value = None
        mock_dbp_cls.return_value = inst
        assert _resolve_uid_to_seek_id("Missing") is None

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_exception(self, mock_dbp_cls):
        mock_dbp_cls.side_effect = Exception("DB down")
        assert _resolve_uid_to_seek_id("Crash") is None

    @patch("nextseek_api.services.projects.DBtable_projects")
    def test_db_lookup_record_without_id(self, mock_dbp_cls):
        inst = MagicMock()
        inst.queryRecordsByConstraint.return_value = [{"id": None}]
        mock_dbp_cls.return_value = inst
        assert _resolve_uid_to_seek_id("NullId") is None


# ===========================================================================
# list()
# ===========================================================================
class TestProjectList:
    def test_success(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (b'', 401, {}, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 401

    def test_html_upstream_via_content_type(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_invalid_json(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_validation_failure(self, rf, user, mock_client):
        mock_client.list_projects.return_value = (b'{"data":"x"}', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# retrieve()
# ===========================================================================
class TestProjectRetrieve:
    def test_success_numeric(self, rf, user, mock_client):
        mock_client.get_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="2558")
        assert resp.status_code == 200

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value="2558")
    def test_non_numeric_resolved(self, mock_resolve, rf, user, mock_client):
        mock_client.get_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="MyProject")
        assert resp.status_code == 200

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value=None)
    def test_non_numeric_not_found(self, mock_resolve, rf, user, mock_client):
        resp = _call("retrieve", rf, user, uid="NoSuch")
        assert resp.status_code == 404
        assert b"Project not found" in resp.content

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.get_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, pk="2558")
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_project.return_value = (b'', 401, {}, MagicMock())
        resp = _call("retrieve", rf, user, uid="2558")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.get_project.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="2558")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_project.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="2558")
        assert resp.status_code == 502


# ===========================================================================
# create()
# ===========================================================================
class TestProjectCreate:
    def test_success(self, rf, user, mock_client):
        mock_client.create_project.return_value = (_single_body(), 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 201

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "projects"}})
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.create_project.return_value = (b'', 401, {}, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.create_project.return_value = (b'<html>', 201, HTML_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.create_project.return_value = (b'{}', 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502


# ===========================================================================
# partial_update()
# ===========================================================================
class TestProjectPartialUpdate:
    def test_success_numeric(self, rf, user, mock_client):
        mock_client.update_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="2558")
        assert resp.status_code == 200

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value="2558")
    def test_uid_resolved_via_db(self, mock_resolve, rf, user, mock_client):
        mock_client.update_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "projects", "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="MyProject")
        assert resp.status_code == 200
        call_args = mock_client.update_project.call_args
        assert call_args[0][1] == "2558"

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value="2558")
    def test_id_mismatch(self, mock_resolve, rf, user, mock_client):
        payload = {"data": {"type": "projects", "id": "9999",
                            "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="MyProject")
        assert resp.status_code == 422
        assert b"does not match" in resp.content

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value=None)
    def test_unresolved_uid_with_body_id(self, mock_resolve, rf, user, mock_client):
        mock_client.update_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "projects", "id": "2558",
                            "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="NoSuch")
        assert resp.status_code == 200

    @patch("nextseek_api.services.projects._resolve_uid_to_seek_id", return_value=None)
    def test_unresolved_uid_no_body_id(self, mock_resolve, rf, user, mock_client):
        payload = {"data": {"type": "projects", "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="NoSuch")
        assert resp.status_code == 404
        assert b"Project not found" in resp.content

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"nope": True}}, uid="2558")
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.update_project.return_value = (b'', 401, {}, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="2558")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.update_project.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="2558")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.update_project.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="2558")
        assert resp.status_code == 502

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.update_project.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, pk="2558")
        assert resp.status_code == 200
