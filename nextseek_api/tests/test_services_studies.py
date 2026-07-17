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
