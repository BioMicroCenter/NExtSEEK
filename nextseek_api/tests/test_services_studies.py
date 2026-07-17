"""
Tests for the Study proxy: Pydantic models, SeekAPIClient study methods,
StudyProxyViewSet actions, routing, and OpenAPI schema generation.

Mirrors nextseek_api/tests/test_services_investigations.py with study payloads,
plus the study-specific cases from the 2026-07-17 spec:
- experimentalists accepted (POST and PATCH) and forwarded
- POST missing relationships.investigation -> 422
- investigation given as a list where SingleReference is expected -> 422
- top-level resource type "investigations" on a study request -> 422
- forwarding to /studies and /studies/{id} via the client seam
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from pydantic import ValidationError
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser

from nextseek_api.models import (
    StudyListResponse,
    StudySingleResponse,
    StudyCreateRequest,
    StudyUpdateRequest,
)


# ---------------------------------------------------------------------------
# Valid JSON:API bodies / payloads
# ---------------------------------------------------------------------------
def _single_body():
    return json.dumps({
        "data": {
            "id": "746", "type": "studies",
            "attributes": {"title": "Vaccine Dose Response"},
            "relationships": {"investigation": {"data": {"id": "763", "type": "investigations"}}},
            "links": {"self": "/studies/746"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    }).encode()


def _list_body():
    return json.dumps({
        "data": [
            {"id": "746", "type": "studies",
             "attributes": {"title": "Vaccine Dose Response"},
             "links": {"self": "/studies/746"}}
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/studies?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://seek:3000", "api_version": "v1"},
    }).encode()


VALID_CREATE_PAYLOAD = {
    "data": {
        "type": "studies",
        "attributes": {
            "title": "Vaccine Dose Response",
            "description": "Comparison of immune response across doses",
            "experimentalists": "Wet lab team",
        },
        "relationships": {
            "investigation": {"data": {"id": "763", "type": "investigations"}},
        },
    }
}

VALID_UPDATE_PAYLOAD = {
    "data": {"type": "studies", "id": "746",
             "attributes": {"title": "Revised Vaccine Dose Response"}}
}

JSON_H = {"Content-Type": "application/json"}
HTML_H = {"Content-Type": "text/html; charset=utf-8"}


# ===========================================================================
# Study Pydantic models
# ===========================================================================
class TestStudyModels:
    def test_create_request_valid_and_payload_shape(self):
        req = StudyCreateRequest.model_validate(VALID_CREATE_PAYLOAD)
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "studies"
        assert payload["data"]["attributes"]["experimentalists"] == "Wet lab team"
        assert payload["data"]["relationships"]["investigation"]["data"]["id"] == "763"
        # exclude_none: optional attrs not provided must be absent
        assert "policy" not in payload["data"]["attributes"]

    def test_create_missing_investigation_rejected(self):
        bad = {"data": {"type": "studies",
                        "attributes": {"title": "T"},
                        "relationships": {}}}
        with pytest.raises(ValidationError):
            StudyCreateRequest.model_validate(bad)

    def test_create_investigation_as_list_rejected(self):
        bad = {"data": {"type": "studies",
                        "attributes": {"title": "T"},
                        "relationships": {"investigation": {"data": [
                            {"id": "763", "type": "investigations"}]}}}}
        with pytest.raises(ValidationError):
            StudyCreateRequest.model_validate(bad)

    def test_create_wrong_top_level_type_rejected(self):
        bad = {"data": {"type": "investigations",
                        "attributes": {"title": "T"},
                        "relationships": {"investigation": {"data": {
                            "id": "763", "type": "investigations"}}}}}
        with pytest.raises(ValidationError):
            StudyCreateRequest.model_validate(bad)

    def test_create_extra_attribute_rejected(self):
        bad = json.loads(json.dumps(VALID_CREATE_PAYLOAD))
        bad["data"]["attributes"]["nonexistent_field"] = "x"
        with pytest.raises(ValidationError):
            StudyCreateRequest.model_validate(bad)

    def test_patch_requires_id(self):
        bad = {"data": {"type": "studies", "attributes": {"title": "T"}}}
        with pytest.raises(ValidationError):
            StudyUpdateRequest.model_validate(bad)

    def test_patch_experimentalists_and_investigation_single(self):
        payload = {"data": {"id": "746", "type": "studies",
                            "attributes": {"experimentalists": "New team"},
                            "relationships": {"investigation": {"data": {
                                "id": "764", "type": "investigations"}}}}}
        req = StudyUpdateRequest.model_validate(payload)
        out = req.to_seek_payload()
        assert out["data"]["id"] == "746"
        assert out["data"]["type"] == "studies"
        assert out["data"]["attributes"] == {"experimentalists": "New team"}
        assert out["data"]["relationships"]["investigation"]["data"]["id"] == "764"

    def test_patch_investigation_as_list_rejected(self):
        bad = {"data": {"id": "746", "type": "studies",
                        "relationships": {"investigation": {"data": [
                            {"id": "764", "type": "investigations"}]}}}}
        with pytest.raises(ValidationError):
            StudyUpdateRequest.model_validate(bad)

    def test_list_response_validates(self):
        StudyListResponse.model_validate(json.loads(_list_body()))

    def test_list_response_wrong_item_type_rejected(self):
        body = json.loads(_list_body())
        body["data"][0]["type"] = "investigations"
        with pytest.raises(ValidationError):
            StudyListResponse.model_validate(body)

    def test_single_response_validates(self):
        StudySingleResponse.model_validate(json.loads(_single_body()))


# ===========================================================================
# SeekAPIClient study methods
# ===========================================================================
class TestSeekClientStudyMethods:
    def _client(self):
        from nextseek_api.helpers import SeekAPIClient
        return SeekAPIClient()

    def test_list_studies_path(self):
        c = self._client()
        with patch.object(c, "_request") as m:
            req = MagicMock()
            c.list_studies(req, params={"page[number]": "1"})
            m.assert_called_once_with('GET', '/studies', req, params={"page[number]": "1"})

    def test_get_study_path(self):
        c = self._client()
        with patch.object(c, "_request") as m:
            req = MagicMock()
            c.get_study(req, "746")
            m.assert_called_once_with('GET', '/studies/746', req)

    def test_create_study_path_and_content_type(self):
        from nextseek_api.helpers import JSONAPI_ACCEPT
        c = self._client()
        with patch.object(c, "_request") as m:
            req = MagicMock()
            c.create_study(req, {"data": {}})
            m.assert_called_once_with('POST', '/studies', req, json={"data": {}})
        assert c.session.headers['Content-Type'] == JSONAPI_ACCEPT

    def test_update_study_path_and_content_type(self):
        from nextseek_api.helpers import JSONAPI_ACCEPT
        c = self._client()
        with patch.object(c, "_request") as m:
            req = MagicMock()
            c.update_study(req, "746", {"data": {}})
            m.assert_called_once_with('PATCH', '/studies/746', req, json={"data": {}})
        assert c.session.headers['Content-Type'] == JSONAPI_ACCEPT


# ===========================================================================
# ViewSet fixtures/helpers (mirror of the investigation test module)
# ===========================================================================
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
    from nextseek_api.services.studies import StudyProxyViewSet
    with patch.object(StudyProxyViewSet, "client") as mc:
        yield mc


def _drf(raw):
    return Request(raw, parsers=[JSONParser(), FormParser(), MultiPartParser()])


def _call(action, rf, user, method="get", url="/", data=None, **kwargs):
    from nextseek_api.services.studies import StudyProxyViewSet
    fn = getattr(rf, method)
    raw = fn(url, data=data, format="json") if data is not None else fn(url)
    req = _drf(raw)
    req.user = user
    vs = StudyProxyViewSet()
    return getattr(vs, action)(req, **kwargs)


# ===========================================================================
# _resolve_uid_to_seek_id
# ===========================================================================
class TestResolveUidToSeekId:
    def test_numeric(self):
        from nextseek_api.services.studies import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("746") == "746"

    def test_non_numeric(self):
        from nextseek_api.services.studies import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("my-study") is None

    def test_empty(self):
        from nextseek_api.services.studies import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("") is None


# ===========================================================================
# list()
# ===========================================================================
class TestStudyList:
    def test_success(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (_list_body(), 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 200
        assert b'"data"' in resp.content
        mock_client.list_studies.assert_called_once()

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (b'', 401, {}, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_html_upstream_via_content_type(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (b'<html>login</html>', 200, HTML_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Upstream returned HTML" in resp.content

    def test_html_in_body(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (b'<html>x</html>', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502

    def test_invalid_json(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502
        assert b"Invalid upstream response" in resp.content

    def test_validation_failure(self, rf, user, mock_client):
        mock_client.list_studies.return_value = (b'{"data":"x"}', 200, JSON_H, MagicMock())
        resp = _call("list", rf, user)
        assert resp.status_code == 502


# ===========================================================================
# retrieve()
# ===========================================================================
class TestStudyRetrieve:
    def test_success(self, rf, user, mock_client):
        mock_client.get_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="746")
        assert resp.status_code == 200
        assert json.loads(resp.content)["data"]["id"] == "746"
        mock_client.get_study.assert_called_once()
        assert mock_client.get_study.call_args.args[1] == "746"

    def test_non_numeric_uid(self, rf, user, mock_client):
        resp = _call("retrieve", rf, user, uid="abc")
        assert resp.status_code == 404
        assert b"Study not found" in resp.content

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.get_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, pk="746")
        assert resp.status_code == 200

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.get_study.return_value = (b'', 401, {}, MagicMock())
        resp = _call("retrieve", rf, user, uid="746")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.get_study.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="746")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.get_study.return_value = (b'{"bad":1}', 200, JSON_H, MagicMock())
        resp = _call("retrieve", rf, user, uid="746")
        assert resp.status_code == 502


# ===========================================================================
# create()
# ===========================================================================
class TestStudyCreate:
    def test_success(self, rf, user, mock_client):
        mock_client.create_study.return_value = (_single_body(), 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 201
        sent = mock_client.create_study.call_args.args[1]
        assert sent["data"]["type"] == "studies"
        assert sent["data"]["attributes"]["experimentalists"] == "Wet lab team"

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "studies"}})
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content
        mock_client.create_study.assert_not_called()

    def test_missing_investigation_422(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "studies",
                                     "attributes": {"title": "T"},
                                     "relationships": {}}})
        assert resp.status_code == 422

    def test_investigation_list_422(self, rf, user, mock_client):
        resp = _call("create", rf, user, method="post",
                      data={"data": {"type": "studies",
                                     "attributes": {"title": "T"},
                                     "relationships": {"investigation": {"data": [
                                         {"id": "763", "type": "investigations"}]}}}})
        assert resp.status_code == 422

    def test_wrong_type_422(self, rf, user, mock_client):
        bad = json.loads(json.dumps(VALID_CREATE_PAYLOAD))
        bad["data"]["type"] = "investigations"
        resp = _call("create", rf, user, method="post", data=bad)
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.create_study.return_value = (b'', 401, {}, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 401

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.create_study.return_value = (b'{}', 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502

    def test_empty_body(self, rf, user, mock_client):
        mock_client.create_study.return_value = (None, 201, JSON_H, MagicMock())
        resp = _call("create", rf, user, method="post", data=VALID_CREATE_PAYLOAD)
        assert resp.status_code == 502


# ===========================================================================
# partial_update()
# ===========================================================================
class TestStudyPartialUpdate:
    def test_success_numeric_path(self, rf, user, mock_client):
        mock_client.update_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="746")
        assert resp.status_code == 200
        assert mock_client.update_study.call_args.args[1] == "746"

    def test_body_id_present_matching(self, rf, user, mock_client):
        mock_client.update_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "studies", "id": "746"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="746")
        assert resp.status_code == 200

    def test_id_mismatch(self, rf, user, mock_client):
        payload = {"data": {"type": "studies", "id": "999",
                            "attributes": {"title": "x"}}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="746")
        assert resp.status_code == 422
        assert b"does not match" in resp.content
        mock_client.update_study.assert_not_called()

    def test_non_numeric_path_body_id_fallback(self, rf, user, mock_client):
        mock_client.update_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        payload = {"data": {"type": "studies", "id": "746"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 200

    def test_non_numeric_no_usable_id(self, rf, user, mock_client):
        payload = {"data": {"type": "studies", "id": "abc"}}
        resp = _call("partial_update", rf, user, method="patch", data=payload, uid="abc")
        assert resp.status_code == 404
        assert b"Study not found" in resp.content

    def test_validation_error(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"nope": True}}, uid="746")
        assert resp.status_code == 422

    def test_missing_id_422(self, rf, user, mock_client):
        resp = _call("partial_update", rf, user, method="patch",
                      data={"data": {"type": "studies",
                                     "attributes": {"title": "x"}}}, uid="746")
        assert resp.status_code == 422

    def test_auth_failure(self, rf, user, mock_client):
        mock_client.update_study.return_value = (b'', 401, {}, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="746")
        assert resp.status_code == 401

    def test_html_upstream(self, rf, user, mock_client):
        mock_client.update_study.return_value = (b'<html>', 200, HTML_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="746")
        assert resp.status_code == 502

    def test_invalid_upstream(self, rf, user, mock_client):
        mock_client.update_study.return_value = (b'not-json', 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, uid="746")
        assert resp.status_code == 502

    def test_pk_fallback(self, rf, user, mock_client):
        mock_client.update_study.return_value = (_single_body(), 200, JSON_H, MagicMock())
        resp = _call("partial_update", rf, user, method="patch",
                      data=VALID_UPDATE_PAYLOAD, pk="746")
        assert resp.status_code == 200


# ===========================================================================
# Routing + OpenAPI schema generation
# ===========================================================================
class TestStudyRoutingAndSchema:
    def test_routes_registered(self):
        from django.urls import reverse
        assert reverse("studies-list").endswith("/studies/")
        assert reverse("studies-detail", kwargs={"uid": "746"}).endswith("/studies/746/")

    def test_schema_generation_includes_studies(self):
        from drf_spectacular.generators import SchemaGenerator
        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = list(schema["paths"].keys())
        assert any(p.endswith("/studies/") for p in paths)
        assert any(p.endswith("/studies/{uid}/") for p in paths)
        list_path = next(p for p in paths if p.endswith("/studies/"))
        detail_path = next(p for p in paths if p.endswith("/studies/{uid}/"))
        assert set(schema["paths"][list_path].keys()) >= {"get", "post"}
        assert set(schema["paths"][detail_path].keys()) >= {"get", "patch"}
        assert "put" not in schema["paths"][detail_path]
        assert "delete" not in schema["paths"][detail_path]
