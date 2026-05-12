"""
Comprehensive tests for nextseek_api/services/sample_types.py

Covers:
- _resolve_uid_to_seek_id (sample type resolver)
- SampleTypeProxyViewSet: list, retrieve, create, partial_update
- SampleTypeChildrenViewSet: child_types action
- SamplesByChildTypesViewSet: parents_by_child_types action
"""

import json
import pytest
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from django.test import override_settings
from rest_framework.test import APIRequestFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(payload):
    return json.dumps(payload).encode()


def _ok(payload, code=200):
    return (_body(payload), code, {"Content-Type": "application/json"}, Mock())


def _auth_request(method="get", path="/", data=None, user=None, query=None):
    """Build a mock request that works with ViewSet methods accessing request.data / request.GET."""
    factory = APIRequestFactory()
    fn = getattr(factory, method)
    if data is not None:
        req = fn(path, data=json.dumps(data), content_type="application/json")
    else:
        req = fn(path)
    if user is None:
        user = MagicMock()
        user.is_authenticated = True
    req.user = user
    if data is not None:
        req.data = data
    if query:
        req.GET = query
    req.query_params = req.GET
    return req


# Valid JSON:API responses matching SampleTypeSingleResponse / SampleTypeListResponse models
GOOD_ST_SINGLE = {
    "data": {
        "id": "12",
        "type": "sample_types",
        "attributes": {"title": "TIS"},
        "relationships": {
            "submitter": {"data": []},
            "projects": {"data": []},
            "assays": {"data": []},
            "samples": {"data": []},
        },
        "links": {"self": "/sample_types/12"},
        "meta": {},
    },
    "jsonapi": {"version": "1.0"},
}

GOOD_ST_LIST = {
    "data": [
        {
            "id": "12",
            "type": "sample_types",
            "attributes": {"title": "TIS"},
            "links": {"self": "/sample_types/12"},
        }
    ],
    "jsonapi": {"version": "1.0"},
    "links": {"self": "/sample_types?page[number]=1&page[size]=100"},
    "meta": {"base_url": "http://localhost", "api_version": "v1"},
}

# Valid create request payload matching SampleTypeCreateRequest model
GOOD_ST_CREATE_PAYLOAD = {
    "data": {
        "type": "sample_types",
        "attributes": {
            "title": "NEW",
            "sample_attributes": [
                {
                    "title": "Title",
                    "sample_attribute_type": {"id": "1"},
                    "required": True,
                }
            ],
        },
        "relationships": {
            "projects": {"data": [{"type": "projects", "id": "1"}]},
        },
    }
}

# Valid update request payload matching SampleTypeUpdateRequest model
GOOD_ST_UPDATE_PAYLOAD = {
    "data": {
        "id": "12",
        "type": "sample_types",
        "attributes": {"title": "Revised"},
    }
}


# ============================================================================
# _resolve_uid_to_seek_id (sample type)
# ============================================================================


class TestSampleTypeResolveUid:
    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_numeric_passthrough(self, _):
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("42") == "42"

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_resolved(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = 12
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("TIS") == "12"

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_not_found(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = 0
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("UNKNOWN") is None

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_returns_none(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = None
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("UNKNOWN") is None

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_exception_returns_none(self, mock_cls):
        mock_cls.side_effect = RuntimeError("boom")
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("TIS") is None


# ============================================================================
# SampleTypeProxyViewSet.list
# ============================================================================


class TestSampleTypeList:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_list_200(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = _ok(GOOD_ST_LIST)
        resp = vs.list(_auth_request())
        assert resp.status_code == 200

    def test_list_401(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b'{}', 401, {}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 401

    def test_list_502_html_content_type(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502
        assert b"HTML" in resp.content

    def test_list_502_html_body(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"<html>login</html>", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502

    def test_list_502_invalid_json(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"not json", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502


# ============================================================================
# SampleTypeProxyViewSet.retrieve
# ============================================================================


class TestSampleTypeRetrieve:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_retrieve_numeric_200(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_retrieve_404_unresolved(self, _):
        vs = self._viewset()
        resp = vs.retrieve(_auth_request(), uid="UNKNOWN")
        assert resp.status_code == 404

    def test_retrieve_401(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b'{}', 401, {}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 401

    def test_retrieve_502_html(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 502

    def test_retrieve_502_invalid_json(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b"bad", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 502

    def test_retrieve_uses_pk_fallback(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        resp = vs.retrieve(_auth_request(), uid=None, pk="12")
        assert resp.status_code == 200


# ============================================================================
# SampleTypeProxyViewSet.create
# ============================================================================


class TestSampleTypeCreate:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_create_201(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = _ok(GOOD_ST_SINGLE, 201)
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 201

    def test_create_422_invalid_body(self):
        vs = self._viewset()
        req = _auth_request(method="post", data={"bad": "data"})
        resp = vs.create(req)
        assert resp.status_code == 422

    def test_create_401(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (b'{}', 401, {}, Mock())
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 401

    def test_create_502_bad_response(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (b"not json", 200, {"Content-Type": "application/json"}, Mock())
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 502


# ============================================================================
# SampleTypeProxyViewSet.partial_update
# ============================================================================


class TestSampleTypePartialUpdate:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def _patch_payload(self, sid="12"):
        return {
            "data": {
                "type": "sample_types",
                "id": sid,
                "attributes": {"title": "Revised"},
            }
        }

    def test_patch_numeric_200(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 200

    def test_patch_id_mismatch(self):
        vs = self._viewset()
        # Path uid=12 resolves to "12", body id="99" => mismatch
        req = _auth_request(method="patch", data=self._patch_payload("99"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 422
        assert b"does not match path id" in resp.content

    def test_patch_body_id_inferred_from_path(self):
        """Body id same as path id, inferred when body id is None."""
        vs = self._viewset()
        # SampleTypeUpdateRequest requires data.id, so we must provide it.
        # But the body_id None branch triggers when to_seek_payload() drops id.
        # Let's provide body id matching path to test the happy path.
        payload = self._patch_payload("12")
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_patch_fallback_body_id_digit(self, _):
        vs = self._viewset()
        payload = self._patch_payload("42")
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="UNKNOWN")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_patch_404_no_id(self, _):
        vs = self._viewset()
        # body id is non-digit "UNKNOWN", path also unresolvable
        payload = self._patch_payload("UNKNOWN")
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="UNKNOWN")
        assert resp.status_code == 404

    def test_patch_422_invalid_body(self):
        vs = self._viewset()
        req = _auth_request(method="patch", data={"bad": "data"})
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 422

    def test_patch_401_upstream(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b'{}', 401, {}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 401

    def test_patch_502_html(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 502

    def test_patch_502_invalid_json(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b"bad", 200, {"Content-Type": "application/json"}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 502

    def test_patch_uses_pk_fallback(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid=None, pk="12")
        assert resp.status_code == 200


# ============================================================================
# SampleTypeChildrenViewSet.child_types
# ============================================================================


class TestSampleTypeChildren:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeChildrenViewSet
        vs = SampleTypeChildrenViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_200(self, mock_st_model, mock_gd, mock_resolve, mock_auth):
        vs = self._viewset()
        # Neo4j mock
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": "CEL"}],
            MagicMock(),
            ["type"],
        )
        # ORM mock
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
            {"id": 27, "title": "CEL", "description": "Cell"},
        ]
        req = _auth_request()
        resp = vs.child_types(req, uid="42")
        assert resp.status_code == 200
        data = resp.data
        assert data["total"] == 2
        assert data["msg"] == "OK"

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(None, None))
    def test_child_types_401_unauthenticated(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(user=user)
        resp = vs.child_types(req, uid="42")
        assert resp.status_code == 401

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value=None)
    def test_child_types_404(self, _, __):
        vs = self._viewset()
        resp = vs.child_types(_auth_request(), uid="UNKNOWN")
        assert resp.status_code == 404

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_child_types_502_neo4j_error(self, mock_gd, _, __):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_child_types_502_generic_exception(self, mock_gd, _, __):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_partial_desc_lookup(self, mock_st_model, mock_gd, _, __):
        """Some types not found in DB gives warning message."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": "UNKNOWN_TYPE"}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
        ]
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert "not available for 1" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_desc_lookup_failed(self, mock_st_model, mock_gd, _, __):
        """DB exception in description lookup gives warning."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.side_effect = RuntimeError("DB fail")
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert "not available" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_empty_result(self, mock_st_model, mock_gd, _, __):
        """No child types returns total=0."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = ([], MagicMock(), ["type"])
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert resp.data["total"] == 0

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_none_type_filtered(self, mock_st_model, mock_gd, _, __):
        """None type values from Neo4j should be filtered out."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": None}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
        ]
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert resp.data["total"] == 1


# ============================================================================
# SamplesByChildTypesViewSet.parents_by_child_types
# ============================================================================


class TestSamplesByChildTypes:
    def _viewset(self):
        from nextseek_api.services.sample_types import SamplesByChildTypesViewSet
        vs = SamplesByChildTypesViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_200_titles(self, mock_st_model, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "Non Human Primate"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS", "CEL"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert resp.data["total"] == 1
        assert resp.data["msg"] == "OK"

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(None, None))
    def test_parents_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]}, user=user)
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 401

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_parents_422_invalid_body(self, _):
        vs = self._viewset()
        # The model accepts None for child_sample_types, but code requires at least one valid
        req = _auth_request(method="post", data={"child_sample_types": []})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 422

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_parent_filter(self, mock_st_model, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "Non Human Primate"},
        ]
        req = _auth_request(method="post", data={
            "child_sample_types": ["TIS"],
            "parent_sample_type_filters": ["NHP"],
        })
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_id_resolution(self, mock_st_model, mock_gd, _):
        """IDs in child_sample_types should be resolved to titles via ORM."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        # First call: resolve child_sample_types ID -> title
        # Second call: lookup descriptions
        mock_st_model.objects.filter.return_value.values_list.return_value.first.return_value = "TIS"
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["26"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_parents_502_neo4j_error(self, mock_gd, _):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_parents_502_generic_exception(self, mock_gd, _):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_desc_lookup_failed(self, mock_st_model, mock_gd, _):
        """Description lookup failure doesn't break the response."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.side_effect = RuntimeError("DB fail")
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert "not available" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_none_id_uuid_skipped(self, mock_st_model, mock_gd, _):
        """Records with None id/uuid are skipped."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"id": 1, "uuid": "NHP-1", "type": "NHP"},
                {"id": None, "uuid": None, "type": "NHP"},
            ],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert resp.data["total"] == 1

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_partial_desc(self, mock_st_model, mock_gd, _):
        """Some types not in DB gives partial warning."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"id": 1, "uuid": "NHP-1", "type": "NHP"},
                {"id": 2, "uuid": "TIS-1", "type": "UNKNOWN"},
            ],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert "not available for 1" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_parent_id_resolution(self, mock_st_model, mock_gd, _):
        """parent_sample_type_filters with numeric IDs resolved to titles."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values_list.return_value.first.return_value = "NHP"
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={
            "child_sample_types": ["TIS"],
            "parent_sample_type_filters": ["41"],
        })
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
