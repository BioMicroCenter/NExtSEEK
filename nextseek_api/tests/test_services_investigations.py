"""
Tests for nextseek_api/services/investigations.py

Covers:
- _resolve_uid_to_seek_id() — numeric, non-numeric, empty
- InvestigationProxyViewSet.list() — success, auth failure, HTML upstream (header & body), invalid JSON, validation failure
- InvestigationProxyViewSet.retrieve() — success, non-numeric uid (404), pk fallback, auth failure, HTML upstream, invalid upstream
- InvestigationProxyViewSet.create() — success, validation error, auth failure, invalid upstream
- InvestigationProxyViewSet.partial_update() — success, body id injected, id mismatch, body id fallback, no body id (404), validation error, auth failure, HTML upstream, invalid upstream, pk fallback
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from nextseek_api.services.investigations import (
    InvestigationProxyViewSet,
    _resolve_uid_to_seek_id,
)


# ---------------------------------------------------------------------------
# Valid JSON:API response bodies
# ---------------------------------------------------------------------------
def _single_body():
    return json.dumps({
        "data": {
            "id": "763", "type": "investigations",
            "attributes": {"title": "Investigation 1"},
            "relationships": {"projects": {"data": []}},
            "links": {"self": "/investigations/763"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }).encode()


def _list_body():
    return json.dumps({
        "data": [
            {"id": "763", "type": "investigations",
             "attributes": {"title": "Investigation 1"},
             "links": {"self": "/investigations/763"}}
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/investigations?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://localhost:3000", "api_version": "v1"},
    }).encode()


VALID_CREATE_PAYLOAD = {
    "data": {
        "type": "investigations",
        "attributes": {"title": "New Investigation"},
        "relationships": {
            "projects": {"data": [{"type": "projects", "id": "4475"}]},
        },
    }
}

VALID_UPDATE_PAYLOAD = {
    "data": {"type": "investigations", "id": "763",
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
    with patch.object(InvestigationProxyViewSet, "client") as mc:
        yield mc


def _drf(raw):
    return Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])


def _call(action, rf, user, method="get", url="/", data=None, **kwargs):
    fn = getattr(rf, method)
    raw = fn(url, data=data, format="json") if data is not None else fn(url)
    req = _drf(raw)
    req.user = user
    vs = InvestigationProxyViewSet()
    return getattr(vs, action)(req, **kwargs)


# ===========================================================================
# _resolve_uid_to_seek_id
# ===========================================================================
class TestResolveUidToSeekId:
    def test_numeric(self):
        assert _resolve_uid_to_seek_id("763") == "763"

    def test_non_numeric(self):
        assert _resolve_uid_to_seek_id("my-investigation") is None

    def test_empty(self):
        assert _resolve_uid_to_seek_id("") is None


# ===========================================================================
# list()
# ===========================================================================
class TestInvestigationList:
    def test_success(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 200
        assert b'"data"' in resp.content

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (b'', 401, {}, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_html_upstream_via_content_type(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (b'<html>login</html>', 200, HTML_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_invalid_json(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Invalid upstream response" in resp.content

    def test_validation_failure(self, rf, user, mock_client):
        mock_client.list_investigations.return_value = (b'{"data":"x"}', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# retrieve()
# ===========================================================================
class TestInvestigationRetrieve:
    def test_success(self, rf, user, mock_client):
        mock_client.get_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="763")
        assert resp.status_code == 200
        assert json.loads(resp.content)["data"]["id"] == "763"

    def test_non_numeric_uid(self, rf, user, mock_client):
        resp = _call("retrieve", rf, user, uid="abc")
        assert resp.status_code == 404
        assert b"Investigation not found" in resp.content

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.get_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, pk="763")
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_investigation.return_value = (b'', 401, {}, MagicMock())
        resp = _call("retrieve", rf, user, uid="763")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.get_investigation.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="763")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_investigation.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="763")
        assert resp.status_code == 502


# ===========================================================================
# create()
# ===========================================================================
class TestInvestigationCreate:
    def test_success(self, rf, user, mock_client):
        mock_client.create_investigation.return_value = (_single_body(), 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 201

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "investigations"}})
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.create_investigation.return_value = (b'', 401, {}, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 401

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.create_investigation.return_value = (b'{}', 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502

    def test_empty_body(self, rf, user, mock_client):
        mock_client.create_investigation.return_value = (None, 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502


# ===========================================================================
# partial_update()
# ===========================================================================
class TestInvestigationPartialUpdate:
    def test_success_numeric_path(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="763")
        assert resp.status_code == 200

    def test_body_id_injected(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "investigations", "id": "763"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="763")
        assert resp.status_code == 200

    def test_id_mismatch(self, rf, user, mock_client):
        payload = {"data": {"type": "investigations", "id": "999",
                            "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="763")
        assert resp.status_code == 422
        assert b"does not match" in resp.content

    def test_non_numeric_path_body_id_fallback(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "investigations", "id": "763"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 200

    def test_non_numeric_no_body_id(self, rf, user, mock_client):
        payload = {"data": {"type": "investigations", "id": "abc"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 404

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"nope": True}}, uid="763")
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (b'', 401, {}, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="763")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="763")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="763")
        assert resp.status_code == 502

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.update_investigation.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, pk="763")
        assert resp.status_code == 200
