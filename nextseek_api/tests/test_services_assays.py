"""
Tests for nextseek_api/services/assays.py

Covers:
- _resolve_uid_to_seek_id() — numeric, non-numeric, zero
- AssayProxyViewSet.list() — success, pagination, auth failure, HTML upstream (header & body), invalid JSON, validation failure
- AssayProxyViewSet.retrieve() — success, non-numeric uid (404), pk fallback, auth failure, HTML upstream, invalid upstream
- AssayProxyViewSet.create() — success, validation error, auth failure, invalid upstream, empty body
- AssayProxyViewSet.partial_update() — success, body id injected, id mismatch, body id fallback, no body id (404), validation error, auth failure, HTML upstream, invalid upstream, pk fallback, none uid fallback
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from nextseek_api.services.assays import AssayProxyViewSet, _resolve_uid_to_seek_id


# ---------------------------------------------------------------------------
# Helpers for building valid JSON:API bodies
# ---------------------------------------------------------------------------
_EMPTY_REF = {"data": []}
_EMPTY_SINGLE_REF = {"data": {"type": "studies", "id": "1"}}


def _assay_single_body(**overrides):
    d = {
        "data": {
            "id": "351", "type": "assays",
            "attributes": {"title": "Assay 1"},
            "relationships": {
                "creators": _EMPTY_REF, "submitter": _EMPTY_REF,
                "organisms": _EMPTY_REF, "people": _EMPTY_REF,
                "projects": _EMPTY_REF, "investigation": _EMPTY_SINGLE_REF,
                "study": _EMPTY_SINGLE_REF, "data_files": _EMPTY_REF,
                "samples": _EMPTY_REF, "documents": _EMPTY_REF,
                "models": _EMPTY_REF, "sops": _EMPTY_REF,
                "publications": _EMPTY_REF, "placeholders": _EMPTY_REF,
                "human_diseases": _EMPTY_REF,
            },
            "links": {"self": "/assays/351"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }
    d.update(overrides)
    return json.dumps(d).encode()


def _assay_list_body():
    return json.dumps({
        "data": [
            {"id": "351", "type": "assays",
             "attributes": {"title": "Assay 1"},
             "links": {"self": "/assays/351"}}
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/assays?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://localhost:3000", "api_version": "v1"},
    }).encode()


VALID_CREATE_PAYLOAD = {
    "data": {
        "type": "assays",
        "attributes": {
            "title": "New Assay",
            "assay_class": {"key": "EXP"},
            "assay_type": {"uri": "http://jermontology.org/ontology/JERMOntology#Transcriptomics"},
        },
        "relationships": {
            "study": {"data": {"type": "studies", "id": "434"}},
        },
    }
}

VALID_UPDATE_PAYLOAD = {
    "data": {"type": "assays", "id": "351",
             "attributes": {"description": "Updated"}}
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
    with patch.object(AssayProxyViewSet, "client") as mc:
        yield mc


def _drf(raw):
    return Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])


def _call(action, rf, user, method="get", url="/", data=None, **kwargs):
    fn = getattr(rf, method)
    raw = fn(url, data=data, format="json") if data is not None else fn(url)
    req = _drf(raw)
    req.user = user
    vs = AssayProxyViewSet()
    return getattr(vs, action)(req, **kwargs)


# ===========================================================================
# _resolve_uid_to_seek_id
# ===========================================================================
class TestResolveUidToSeekId:
    def test_numeric(self):
        assert _resolve_uid_to_seek_id("351") == "351"

    def test_non_numeric(self):
        assert _resolve_uid_to_seek_id("NHP-260225MIT-1") is None

    def test_zero(self):
        assert _resolve_uid_to_seek_id("0") == "0"


# ===========================================================================
# list()
# ===========================================================================
class TestAssayList:
    def test_success(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (_assay_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 200
        assert b'"data"' in resp.content

    def test_pagination_params(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (_assay_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user, url="/a/?page%5Bnumber%5D=2")
        assert resp.status_code == 200
        mock_client.list_assays.assert_called_once()

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (b'', 401, {}, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_html_upstream_via_content_type(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (b'<html>login</html>', 200, HTML_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (b'<html>oops</html>', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_invalid_json(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Invalid upstream response" in resp.content

    def test_validation_failure(self, rf, user, mock_client):
        mock_client.list_assays.return_value = (b'{"data":"x"}', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# retrieve()
# ===========================================================================
class TestAssayRetrieve:
    def test_success(self, rf, user, mock_client):
        mock_client.get_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="351")
        assert resp.status_code == 200
        assert json.loads(resp.content)["data"]["id"] == "351"

    def test_non_numeric_uid(self, rf, user, mock_client):
        resp = _call("retrieve", rf, user, uid="abc")
        assert resp.status_code == 404
        assert b"Assay not found" in resp.content

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.get_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, pk="351")
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_assay.return_value = (b'', 401, {}, MagicMock())
        resp = _call("retrieve", rf, user, uid="351")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.get_assay.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="351")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_assay.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="351")
        assert resp.status_code == 502


# ===========================================================================
# create()
# ===========================================================================
class TestAssayCreate:
    def test_success(self, rf, user, mock_client):
        mock_client.create_assay.return_value = (_assay_single_body(), 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 201

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post", data={"data": {"type": "assays"}})
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.create_assay.return_value = (b'', 401, {}, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 401

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.create_assay.return_value = (b'{"bad":1}', 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502

    def test_empty_body(self, rf, user, mock_client):
        mock_client.create_assay.return_value = (None, 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502


# ===========================================================================
# partial_update()
# ===========================================================================
class TestAssayPartialUpdate:
    def test_success(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="351")
        assert resp.status_code == 200

    def test_body_id_injected(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "assays", "id": "351"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="351")
        assert resp.status_code == 200

    def test_id_mismatch(self, rf, user, mock_client):
        payload = {"data": {"type": "assays", "id": "999",
                            "attributes": {"description": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="351")
        assert resp.status_code == 422
        assert b"does not match" in resp.content

    def test_non_numeric_path_body_id_fallback(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "assays", "id": "351",
                            "attributes": {"description": "from body id"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 200

    def test_non_numeric_no_body_id(self, rf, user, mock_client):
        payload = {"data": {"type": "assays", "id": "notnum",
                            "attributes": {"description": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 404

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"nope": True}}, uid="351")
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (b'', 401, {}, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="351")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="351")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="351")
        assert resp.status_code == 502

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.update_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, pk="351")
        assert resp.status_code == 200

    def test_none_uid_body_id_fallback(self, rf, user, mock_client):
        """uid=None => path_id='None' (non-digit), body '351' => fallback."""
        mock_client.update_assay.return_value = (_assay_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD)
        assert resp.status_code == 200
