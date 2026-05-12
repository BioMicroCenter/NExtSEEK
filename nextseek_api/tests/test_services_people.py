"""
Tests for nextseek_api/services/people.py

Covers:
- _validate_seek_id() — numeric, non-numeric, empty, zero
- PeopleProxyViewSet.list() — success, auth failure, HTML upstream (header & body), invalid JSON, validation failure
- PeopleProxyViewSet.retrieve() — success, non-numeric uid (422), pk fallback, auth failure, HTML upstream, invalid upstream
- PeopleProxyViewSet.current() — success, auth failure, HTML upstream (header & body), invalid upstream, empty body
- PeopleProxyViewSet.create() — success, validation error, auth failure, HTML upstream, invalid upstream
- PeopleProxyViewSet.partial_update() — success, body id injected, id mismatch, non-numeric uid (422), validation error, auth failure, HTML upstream (body), invalid upstream, pk fallback
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from nextseek_api.services.people import PeopleProxyViewSet, _validate_seek_id


# ---------------------------------------------------------------------------
# Valid JSON:API response bodies
# ---------------------------------------------------------------------------
def _single_body():
    return json.dumps({
        "data": {
            "id": "1652", "type": "people",
            "attributes": {"title": "Doe, John"},
            "relationships": {"projects": {"data": []}},
            "links": {"self": "/people/1652"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }).encode()


def _list_body():
    return json.dumps({
        "data": [
            {"id": "1652", "type": "people",
             "attributes": {"title": "Doe, John"},
             "links": {"self": "/people/1652"}}
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/people?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://localhost:3000", "api_version": "v1"},
    }).encode()


VALID_CREATE_PAYLOAD = {
    "data": {
        "type": "people",
        "attributes": {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
        },
    }
}

VALID_UPDATE_PAYLOAD = {
    "data": {"type": "people", "id": "1652",
             "attributes": {"first_name": "Jane"}}
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
    with patch.object(PeopleProxyViewSet, "client") as mc:
        yield mc


def _drf(raw):
    return Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])


def _call(action, rf, user, method="get", url="/", data=None, **kwargs):
    fn = getattr(rf, method)
    raw = fn(url, data=data, format="json") if data is not None else fn(url)
    req = _drf(raw)
    req.user = user
    vs = PeopleProxyViewSet()
    return getattr(vs, action)(req, **kwargs)


# ===========================================================================
# _validate_seek_id
# ===========================================================================
class TestValidateSeekId:
    def test_numeric(self):
        assert _validate_seek_id("1652") == "1652"

    def test_non_numeric(self):
        assert _validate_seek_id("abc") is None

    def test_empty(self):
        assert _validate_seek_id("") is None

    def test_zero(self):
        assert _validate_seek_id("0") == "0"


# ===========================================================================
# list()
# ===========================================================================
class TestPeopleList:
    def test_success(self, rf, user, mock_client):
        mock_client.list_people.return_value = (_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 200
        assert b'"data"' in resp.content

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.list_people.return_value = (b'', 401, {}, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_html_upstream_via_header(self, rf, user, mock_client):
        mock_client.list_people.return_value = (b'<html>login</html>', 200, HTML_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.list_people.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_invalid_json(self, rf, user, mock_client):
        mock_client.list_people.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_validation_failure(self, rf, user, mock_client):
        mock_client.list_people.return_value = (b'{"data":"x"}', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# retrieve()
# ===========================================================================
class TestPeopleRetrieve:
    def test_success(self, rf, user, mock_client):
        mock_client.get_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="1652")
        assert resp.status_code == 200
        assert json.loads(resp.content)["data"]["id"] == "1652"

    def test_non_numeric_uid(self, rf, user, mock_client):
        resp = _call("retrieve", rf, user, uid="abc")
        assert resp.status_code == 422
        assert b"must be numeric SEEK id" in resp.content

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.get_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, pk="1652")
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_person.return_value = (b'', 401, {}, MagicMock())
        resp = _call("retrieve", rf, user, uid="1652")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.get_person.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="1652")
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_person.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="1652")
        assert resp.status_code == 502


# ===========================================================================
# current()
# ===========================================================================
class TestPeopleCurrent:
    def test_success(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 200
        assert json.loads(resp.content)["data"]["id"] == "1652"

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (b'', 401, {}, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_html_upstream_via_header(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (b'<html>login</html>', 200, HTML_H, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 502

    def test_empty_body(self, rf, user, mock_client):
        mock_client.get_current_person.return_value = (None, 200, JSON_H, MagicMock())
        resp = _call("current", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# create()
# ===========================================================================
class TestPeopleCreate:
    def test_success(self, rf, user, mock_client):
        mock_client.create_person.return_value = (_single_body(), 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 201

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "people"}})
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.create_person.return_value = (b'', 401, {}, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.create_person.return_value = (b'<html>', 201, HTML_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.create_person.return_value = (b'{}', 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502


# ===========================================================================
# partial_update()
# ===========================================================================
class TestPeoplePartialUpdate:
    def test_success(self, rf, user, mock_client):
        mock_client.update_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="1652")
        assert resp.status_code == 200

    def test_body_id_injected(self, rf, user, mock_client):
        mock_client.update_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "people", "attributes": {"first_name": "X"}}}
        resp = _call("partial_update", rf, user, method="patch",
                      data=payload, uid="1652")
        assert resp.status_code == 200
        # Verify id was injected
        call_args = mock_client.update_person.call_args
        payload_sent = call_args[0][2]
        assert payload_sent["data"]["id"] == "1652"

    def test_id_mismatch(self, rf, user, mock_client):
        payload = {"data": {"type": "people", "id": "999",
                            "attributes": {"first_name": "X"}}}
        resp = _call("partial_update", rf, user, method="patch",
                      data=payload, uid="1652")
        assert resp.status_code == 422
        assert b"does not match" in resp.content

    def test_non_numeric_uid(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="abc")
        assert resp.status_code == 422
        assert b"must be numeric SEEK id" in resp.content

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"nope": True}}, uid="1652")
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.update_person.return_value = (b'', 401, {}, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="1652")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.update_person.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="1652")
        assert resp.status_code == 502

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.update_person.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="1652")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.update_person.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="1652")
        assert resp.status_code == 502

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.update_person.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, pk="1652")
        assert resp.status_code == 200
