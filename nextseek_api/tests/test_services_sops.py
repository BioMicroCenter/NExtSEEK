"""Comprehensive tests for nextseek_api/services/sops.py — SopProxyViewSet."""
import copy
import json
import re
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from nextseek_api.models import ContentBlobUploadStatus
from nextseek_api.services.sops import SopProxyViewSet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_REF = {"data": []}

factory = APIRequestFactory()


def _valid_sop_response(*, sop_id="42", title="Test SOP", content_blobs=None):
    """Build a minimal valid SopSingleResponse dict."""
    return {
        "data": {
            "id": sop_id,
            "type": "sops",
            "attributes": {
                "title": title,
                "content_blobs": content_blobs or [],
            },
            "relationships": {
                "creators": _EMPTY_REF,
                "submitter": _EMPTY_REF,
                "people": _EMPTY_REF,
                "projects": _EMPTY_REF,
                "investigations": _EMPTY_REF,
                "studies": _EMPTY_REF,
                "assays": _EMPTY_REF,
                "publications": _EMPTY_REF,
                "workflows": _EMPTY_REF,
            },
            "links": {"self": f"/sops/{sop_id}"},
            "meta": {"created": "2026-01-01", "modified": "2026-01-01", "uuid": "abc"},
        },
        "jsonapi": {"version": "1.0"},
    }


def _valid_list_response():
    return {
        "data": [
            {
                "id": "1",
                "type": "sops",
                "attributes": {"title": "SOP-1"},
                "links": {"self": "/sops/1"},
            }
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/sops?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://seek", "api_version": "v1"},
    }


def _make_viewset_and_client():
    vs = SopProxyViewSet()
    mock_client = MagicMock()
    vs.client = mock_client
    return vs, mock_client


def _make_drf_request(django_request, vs):
    req = Request(django_request, parsers=[p() for p in vs.parser_classes])
    req.user = MagicMock()
    req.auth = "token"
    return req


@pytest.fixture(autouse=True)
def _no_blob_read():
    """The list reads SEEK's content_blobs table; there is none here."""
    with patch("nextseek_api.services.sops._sop_blob_rows", return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _no_title_lookup():
    """The 409 check reads SEEK's sops table; there is none here."""
    with patch("nextseek_api.services.sops._existing_sop_ids", return_value=[]) as m:
        yield m


@pytest.fixture(autouse=True)
def _seek_login():
    """The caller has a SEEK login (the session lookup reads SEEK's database; there is none here)."""
    with patch("nextseek_api.helpers.get_auth", return_value=("user", "pw")) as m:
        yield m


def _json_bytes(d):
    return json.dumps(d).encode()


# ===========================================================================
# LIST tests
# ===========================================================================


class TestSopList:
    """GET /sops — list endpoint."""

    def test_list_success(self):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_list_response())
        client.list_sops.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 200

    def test_list_401(self):
        vs, client = _make_viewset_and_client()
        client.list_sops.return_value = (b'{}', 401, {}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 401
        assert b"Authentication required" in resp.content

    def test_list_upstream_error(self):
        vs, client = _make_viewset_and_client()
        client.list_sops.return_value = (b'{"errors":[]}', 500, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 500

    def test_list_html_response_returns_502(self):
        vs, client = _make_viewset_and_client()
        body = b"<html><body>Login</body></html>"
        client.list_sops.return_value = (body, 200, {"Content-Type": "text/html"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502
        assert b"HTML" in resp.content

    def test_list_html_in_body_returns_502(self):
        """When Content-Type is JSON but body contains <html, detect and return 502."""
        vs, client = _make_viewset_and_client()
        body = b"<html><body>oops</body></html>"
        client.list_sops.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502

    def test_list_invalid_json_returns_502(self):
        vs, client = _make_viewset_and_client()
        body = b"not json"
        client.list_sops.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502
        assert b"Invalid upstream response" in resp.content

    def test_list_validation_failure_returns_502(self):
        """Valid JSON but doesn't match SopListResponse schema."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes({"data": "wrong"})
        client.list_sops.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502


# ===========================================================================
# RETRIEVE tests
# ===========================================================================


class TestSopRetrieve:
    """GET /sops/{uid} — retrieve endpoint."""

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_success(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_sop_response())
        client.get_sop.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value=None)
    def test_retrieve_not_found(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        request = factory.get("/nextseek_api/sops/nonexistent/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="nonexistent")
        assert resp.status_code == 404
        assert b"SOP not found" in resp.content

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_401(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (b'{}', 401, {}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 401

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_upstream_error(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (b'{"errors":[]}', 422, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 422

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_html_response_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (b"<html>", 200, {"Content-Type": "text/html"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_invalid_json_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (b"garbage", 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_uses_pk_fallback(self, mock_resolve):
        """uid=None falls back to pk."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_sop_response())
        client.get_sop.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid=None, pk="42")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_retrieve_with_version_param(self, mock_resolve):
        """version query param is read but doesn't affect the call."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_sop_response())
        client.get_sop.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/sops/42/?version=2")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="42")
        assert resp.status_code == 200


# ===========================================================================
# CREATE tests
# ===========================================================================


class TestSopCreate:
    """POST /sops — create endpoint."""

    def _multipart_request(self, metadata, files=None):
        data = {"metadata": json.dumps(metadata) if isinstance(metadata, dict) else metadata}
        if files:
            data["file"] = files if isinstance(files, list) else [files]
        return factory.post("/nextseek_api/sops/", data, format="multipart")

    def test_create_json_only_success(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        body = _json_bytes(_valid_sop_response())
        client.create_sop.return_value = (body, 201, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 201
        assert resp["Content-Type"] == "application/json"
        assert json.loads(resp.content)["data"][0]["data"]["id"] == "42"
        client.create_sop.assert_called_once()

    def test_create_json_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        request = self._multipart_request({"bad": "data"})
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422
        assert b"Invalid metadata" in resp.content

    def test_create_json_401(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'{}', 401, {}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 401

    def test_create_json_upstream_error(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'{"error":"bad"}', 422, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_json_invalid_upstream_response(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'not json', 201, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_with_single_file(self, mock_upload):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "test.pdf", "content_type": "application/pdf"}
        ])
        client.create_sop.return_value = (_json_bytes(sop_resp), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="99", original_filename="test.pdf", status="uploaded")]

        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 201

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_with_multiple_files_sets_titles(self, mock_upload):
        """Multiple files: each SOP gets the filename as title."""
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "placeholder"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "a.pdf", "content_type": "application/pdf"}
        ])
        client.create_sop.return_value = (_json_bytes(sop_resp), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="99", original_filename="test.pdf", status="uploaded")]

        files = [
            SimpleUploadedFile("a.pdf", b"pdf1", content_type="application/pdf"),
            SimpleUploadedFile("b.pdf", b"pdf2", content_type="application/pdf"),
        ]
        request = self._multipart_request(metadata, files=files)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 201
        assert client.create_sop.call_count == 2

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_with_file_blob_failure_returns_207(self, mock_upload):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "test.pdf", "content_type": "application/pdf"}
        ])
        client.create_sop.return_value = (_json_bytes(sop_resp), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="99", original_filename="test.pdf", status="failed", error="upload error")]

        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 207

    def test_create_with_file_exceeding_size_limit(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        # Simulate file exceeding limit
        with patch.object(type(vs), "client", new_callable=lambda: property(lambda self: client)):
            pass  # client already set
        from django.conf import settings
        original = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', None)
        settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = 10  # 10 bytes limit

        f = SimpleUploadedFile("test.pdf", b"x" * 100, content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 413
        assert b"exceeds limit" in resp.content

        # Restore
        if original is None:
            delattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES')
        else:
            settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = original

    def test_create_with_file_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        # metadata that passes auto_populate but fails SopCreateRequest validation
        bad_metadata = {"data": {"type": "wrong_type", "attributes": {"title": "x"}}}
        request = self._multipart_request(bad_metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_with_file_401_from_seek(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'{}', 401, {}, MagicMock())

        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 401

    def test_create_with_file_upstream_error(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'{"error":"bad"}', 422, {"Content-Type": "application/json"}, MagicMock())

        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_with_file_invalid_upstream_response(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_sop.return_value = (b'not json', 201, {"Content-Type": "application/json"}, MagicMock())

        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 502

    def test_create_metadata_already_dict(self):
        """When metadata is already a dict (not a JSON string), the code handles it.

        The create method does: json.loads(metadata_str) if isinstance(metadata_str, str)
        else metadata_str — so a dict gets used directly.
        """
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }

        # Create a DRF request with data that has 'metadata' as a dict
        mock_request = MagicMock()
        mock_request.data = {"metadata": metadata}
        mock_request.FILES = {}
        mock_request.query_params = {}

        body = _json_bytes(_valid_sop_response())
        client.create_sop.return_value = (body, 201, {"Content-Type": "application/json"}, MagicMock())

        resp = vs.create(mock_request)
        assert resp.status_code == 201
        client.create_sop.assert_called_once()


# ===========================================================================
# CREATE answers JSON in every case (#108)
# ===========================================================================

_META = {
    "data": {
        "type": "sops",
        "attributes": {"title": "SOP"},
        "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
    }
}
_JSON = {"Content-Type": "application/json"}


def _post(vs, metadata=None, files=None, raw_metadata=None, json_body=None, institution=None):
    if json_body is not None:
        req = factory.post("/nextseek_api/sops/", json_body, format="json")
    else:
        data = {"metadata": raw_metadata if raw_metadata is not None else json.dumps(metadata or _META)}
        if files:
            data["file"] = files
        if institution:
            data["institution"] = institution
        req = factory.post("/nextseek_api/sops/", data, format="multipart")
    return vs.create(_make_drf_request(req, vs))


def _file(name="a.pdf"):
    return SimpleUploadedFile(name, b"x", content_type="application/pdf")


class TestSopCreateAlwaysJson:
    def test_create_json_body_as_documented(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
        resp = _post(vs, json_body=_META)
        assert resp.status_code == 201
        assert resp["Content-Type"] == "application/json"

    def test_create_bad_metadata_json_returns_422(self):
        vs, client = _make_viewset_and_client()
        resp = _post(vs, raw_metadata="{not json")
        assert resp.status_code == 422
        assert resp["Content-Type"] == "application/json"
        client.create_sop.assert_not_called()

    def test_create_with_file_metadata_without_data_returns_422(self):
        vs, client = _make_viewset_and_client()
        resp = _post(vs, metadata={"nope": 1}, files=[_file()])
        assert resp.status_code == 422
        assert resp["Content-Type"] == "application/json"
        client.create_sop.assert_not_called()

    def test_create_json_only_read_timeout_returns_202_with_title(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.side_effect = requests.ReadTimeout("slow")
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 202
        assert resp["Content-Type"] == "application/json"
        assert body["status"] == "unconfirmed" and body["title"] == "SOP"

    def test_create_with_file_read_timeout_returns_202_with_title(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.side_effect = requests.ReadTimeout("slow")
        resp = _post(vs, files=[_file("x.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 202
        assert resp["Content-Type"] == "application/json"
        assert body["status"] == "unconfirmed"
        assert body["title"].endswith("-V1_x.pdf")
        assert "not sent" in body["detail"]
        assert body["not_sent"] == ["x.pdf"]

    def test_create_connection_error_returns_502_json(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.side_effect = requests.ConnectionError("down")
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 502
        assert resp["Content-Type"] == "application/json"
        assert body["title"] == "SOP"
        assert body["errors"]

    def test_create_seek_500_html_returns_json_not_html(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (b"<html>boom</html>", 500, {"Content-Type": "text/html"}, MagicMock())
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 502
        assert resp["Content-Type"] == "application/json"
        assert body["seek_status"] == 500 and body["title"] == "SOP"

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_invalid_body_after_201_still_returns_id_and_uploads(self, mock_upload):
        vs, client = _make_viewset_and_client()
        odd = {"data": {"id": "77", "type": "sops", "attributes": {"title": "T", "content_blobs": [
            {"link": "http://seek/sops/77/content_blobs/5", "original_filename": "a.pdf"}]}}}
        client.create_sop.return_value = (_json_bytes(odd), 201, _JSON, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="5", original_filename="a.pdf", status="uploaded")]
        resp = _post(vs, files=[_file()])
        body = json.loads(resp.content)
        assert resp.status_code == 201
        assert body["data"][0]["data"]["id"] == "77"
        assert body["warnings"]
        mock_upload.assert_called_once()

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_second_file_fails_reports_first_created_and_not_sent(self, mock_upload):
        vs, client = _make_viewset_and_client()
        first = _valid_sop_response(sop_id="41", content_blobs=[
            {"link": "http://seek/sops/41/content_blobs/9", "original_filename": "a.pdf", "content_type": "application/pdf"}])
        client.create_sop.side_effect = [
            (_json_bytes(first), 201, _JSON, MagicMock()),
            requests.ReadTimeout("slow"),
        ]
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="9", original_filename="a.pdf", status="uploaded")]
        resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf"), _file("c.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 202
        assert [c["id"] for c in body["created"]] == ["41"]
        assert body["not_sent"] == ["b.pdf", "c.pdf"]

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_create_multiple_files_each_sop_gets_only_its_own_placeholder(self, mock_upload):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
        mock_upload.return_value = []
        _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        second = client.create_sop.call_args_list[1].args[1]
        blobs = second["data"]["attributes"]["content_blobs"]
        assert [b["original_filename"] for b in blobs] == ["b.pdf"]

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_later_2xx_that_is_not_json_does_not_reuse_the_earlier_sop(self, mock_upload):
        vs, client = _make_viewset_and_client()
        client.create_sop.side_effect = [
            (_json_bytes(_valid_sop_response(sop_id="41")), 201, _JSON, MagicMock()),
            (b"<html>ok</html>", 201, {"Content-Type": "text/html"}, MagicMock()),
        ]
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="9", original_filename="a.pdf", status="uploaded")]
        resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 502
        assert [c["id"] for c in body["created"]] == ["41"]
        assert body["not_sent"] == ["b.pdf"]
        assert body["title"].endswith("-V1_b.pdf")
        assert mock_upload.call_count == 1

    def test_seek_4xx_that_is_not_json_is_a_json_refusal_with_seeks_status(self):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (b"<html>Forbidden</html>", 403, {"Content-Type": "text/html"}, MagicMock())
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 403
        assert resp["Content-Type"] == "application/json"
        assert body["seek_status"] == 403 and body["title"] == "SOP"
        assert "may still" not in body["errors"][0]["detail"]

    @patch("nextseek_api.services.sops.upload_content_blobs")
    def test_207_says_which_upload_failed(self, mock_upload):
        vs, client = _make_viewset_and_client()
        client.create_sop.side_effect = [
            (_json_bytes(_valid_sop_response(sop_id="41")), 201, _JSON, MagicMock()),
            (_json_bytes(_valid_sop_response(sop_id="42")), 201, _JSON, MagicMock()),
        ]
        mock_upload.side_effect = [
            [ContentBlobUploadStatus(blob_id="9", original_filename="a.pdf", status="uploaded")],
            [ContentBlobUploadStatus(blob_id="", original_filename="b.pdf", status="failed", error="boom")],
        ]
        resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 207
        assert [(c["id"], c["blobs"][0]["status"]) for c in body["created"]] == [("41", "uploaded"), ("42", "failed")]


class TestSopCreateDuplicateTitle:
    def test_create_existing_title_returns_409_with_id_when_visible(self, _no_title_lookup):
        vs, client = _make_viewset_and_client()
        _no_title_lookup.return_value = ["9"]
        client.get_sop.return_value = (b"{}", 200, _JSON, MagicMock())
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 409
        assert resp["Content-Type"] == "application/json"
        assert body["id"] == "9"
        client.create_sop.assert_not_called()

    def test_create_existing_title_returns_409_without_id_when_not_visible(self, _no_title_lookup):
        vs, client = _make_viewset_and_client()
        _no_title_lookup.return_value = ["9"]
        client.get_sop.return_value = (b"{}", 403, _JSON, MagicMock())
        resp = _post(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 409
        assert "id" not in body
        client.create_sop.assert_not_called()

    def test_caller_without_a_seek_login_gets_401_whether_or_not_the_title_is_taken(self, _no_title_lookup, _seek_login):
        vs, client = _make_viewset_and_client()
        _no_title_lookup.return_value = ["9"]
        _seek_login.return_value = None
        resp = _post(vs)
        assert resp.status_code == 401
        assert resp["Content-Type"] == "application/json"
        # Byte for byte the create's own 401, so a taken title cannot be told from a free one by its body.
        assert resp.content == b'{"detail":"Authentication required"}'
        client.get_sop.assert_not_called()
        client.create_sop.assert_not_called()

    def test_taken_title_on_a_later_file_is_refused_before_any_create(self, _no_title_lookup):
        vs, client = _make_viewset_and_client()
        _no_title_lookup.side_effect = lambda title: ["9"] if title.endswith("_b.pdf") else []
        client.get_sop.return_value = (b"{}", 200, _JSON, MagicMock())
        resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 409
        assert body["title"].endswith("-V1_b.pdf")
        assert "Nothing was created" in body["errors"][0]["detail"]
        client.create_sop.assert_not_called()

    def test_two_files_whose_titles_differ_only_by_case_are_refused_before_any_create(self):
        # SEEK's sops.title collation ignores case, so these two titles would not resolve.
        vs, client = _make_viewset_and_client()
        resp = _post(vs, files=[_file("Protocol.pdf"), _file("protocol.pdf")])
        assert resp.status_code == 409
        assert "Nothing was created" in json.loads(resp.content)["errors"][0]["detail"]
        client.create_sop.assert_not_called()

    def test_two_files_that_would_share_a_title_are_refused_before_any_create(self):
        vs, client = _make_viewset_and_client()
        resp = _post(vs, files=[_file("a.pdf"), _file("a.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 409
        assert body["title"].endswith("-V1_a.pdf")
        assert "Nothing was created" in body["errors"][0]["detail"]
        client.create_sop.assert_not_called()


class TestSopCreateTitleRule:
    @patch("nextseek_api.services.sops.upload_content_blobs", return_value=[])
    def test_create_keeps_title_already_in_scheme(self, _u):
        for title in ("P.SHE-260807-V1_AFM.docx", "260807-V2_AFM.docx"):
            vs, client = _make_viewset_and_client()
            client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
            meta = json.loads(json.dumps(_META))
            meta["data"]["attributes"]["title"] = title
            _post(vs, metadata=meta, files=[_file("AFM.docx")])
            assert client.create_sop.call_args.args[1]["data"]["attributes"]["title"] == title

    @patch("nextseek_api.services.sops.upload_content_blobs", return_value=[])
    def test_create_keeps_file_name_already_in_scheme(self, _u):
        for attrs in ({}, {"title": "AFM protocol"}):
            vs, client = _make_viewset_and_client()
            client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
            meta = json.loads(json.dumps(_META))
            meta["data"]["attributes"] = attrs
            _post(vs, metadata=meta, files=[_file("P.SHE-260807-V1_AFM.docx")], institution="WHI")
            assert client.create_sop.call_args.args[1]["data"]["attributes"]["title"] == "P.SHE-260807-V1_AFM.docx"

    @patch("nextseek_api.services.sops.upload_content_blobs", return_value=[])
    def test_create_prefixes_plain_file_name(self, _u):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
        _post(vs, files=[_file("AFM.docx")], institution="WHI")
        title = client.create_sop.call_args.args[1]["data"]["attributes"]["title"]
        assert re.fullmatch(r"P\.WHI-\d{6}-V1_AFM\.docx", title)

    @patch("nextseek_api.services.sops.upload_content_blobs", return_value=[])
    def test_create_multiple_files_ignores_submitted_title(self, _u):
        vs, client = _make_viewset_and_client()
        client.create_sop.return_value = (_json_bytes(_valid_sop_response()), 201, _JSON, MagicMock())
        meta = json.loads(json.dumps(_META))
        meta["data"]["attributes"]["title"] = "P.SHE-260807-V1_AFM.docx"
        _post(vs, metadata=meta, files=[_file("a.docx"), _file("b.docx")])
        for call in client.create_sop.call_args_list:
            assert re.fullmatch(r"\d{6}-V1_[ab]\.docx", call.args[1]["data"]["attributes"]["title"])


class TestSopListFileNames:
    def _list(self, rows=None, exc=None):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_list_response())
        client.list_sops.return_value = (body, 200, _JSON, MagicMock())
        req = _make_drf_request(factory.get("/nextseek_api/sops/"), vs)
        with patch("nextseek_api.services.sops._sop_blob_rows", return_value=rows, side_effect=exc) as m:
            resp = vs.list(req)
        return resp, body, m

    def test_list_adds_content_blobs(self):
        rows = [{"asset_id": 1, "asset_version": 1, "original_filename": "a.docx", "content_type": "text/x"}]
        resp, _, m = self._list(rows)
        item = json.loads(resp.content)["data"][0]
        assert item["attributes"]["content_blobs"] == [{"original_filename": "a.docx", "content_type": "text/x"}]
        assert item["attributes"]["title"] == "SOP-1"
        m.assert_called_once_with(["1"])

    def test_list_keeps_only_latest_version_blobs(self):
        rows = [
            {"asset_id": 1, "asset_version": 1, "original_filename": "old.docx", "content_type": "t"},
            {"asset_id": 1, "asset_version": 2, "original_filename": "new.docx", "content_type": "t"},
        ]
        resp, _, _ = self._list(rows)
        names = [b["original_filename"] for b in json.loads(resp.content)["data"][0]["attributes"]["content_blobs"]]
        assert names == ["new.docx"]

    def test_list_blob_read_failure_returns_list_unchanged(self):
        resp, body, _ = self._list(exc=RuntimeError("mysql down"))
        assert resp.status_code == 200
        assert resp.content == body


# ===========================================================================
# PARTIAL_UPDATE tests
# ===========================================================================


class TestSopPartialUpdate:
    """PATCH /sops/{uid} — partial_update endpoint."""

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_update_json_only_success(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_sop_response())
        client.update_sop.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        data = {
            "data": {
                "type": "sops",
                "id": "42",
                "attributes": {"title": "Updated SOP"},
            }
        }
        request = factory.patch("/nextseek_api/sops/42/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value=None)
    def test_update_json_not_found_no_id(self, mock_resolve):
        """UID can't be resolved and data.id is None."""
        vs, client = _make_viewset_and_client()
        data = {
            "data": {
                "type": "sops",
                "attributes": {"title": "Updated"},
            }
        }
        request = factory.patch("/nextseek_api/sops/nonexistent/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="nonexistent")
        assert resp.status_code == 404

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value=None)
    def test_update_json_path_naming_no_sop_ignores_data_id(self, mock_resolve):
        """The path names the SOP: when it resolves to none, the answer is 404 whatever data.id says."""
        vs, client = _make_viewset_and_client()

        data = {
            "data": {
                "type": "sops",
                "id": "42",
                "attributes": {"title": "Updated"},
            }
        }
        request = factory.patch("/nextseek_api/sops/nonexistent/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="nonexistent")
        assert resp.status_code == 404
        client.update_sop.assert_not_called()

    def test_update_json_invalid_request(self):
        vs, client = _make_viewset_and_client()
        request = factory.patch("/nextseek_api/sops/42/", {"bad": "data"}, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_update_json_401(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_sop.return_value = (b'{}', 401, {}, MagicMock())

        data = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/sops/42/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 401

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_update_json_upstream_error(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_sop.return_value = (b'{"error":"bad"}', 422, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/sops/42/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 422

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_update_json_html_response_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_sop.return_value = (b"<html>", 200, {"Content-Type": "text/html"}, MagicMock())

        data = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/sops/42/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    def test_update_json_invalid_upstream(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_sop.return_value = (b"garbage", 200, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/sops/42/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sops.upload_content_blobs")
    @patch("nextseek_api.services.sops.check_unmatched_files", return_value=[])
    def test_update_with_files_success(self, mock_unmatched, mock_upload, mock_resolve):
        vs, client = _make_viewset_and_client()
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "test.pdf", "content_type": "application/pdf"}
        ])
        client.update_sop.return_value = (_json_bytes(sop_resp), 200, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="99", original_filename="test.pdf", status="uploaded")]

        metadata = json.dumps({
            "data": {
                "type": "sops",
                "id": "42",
                "attributes": {"title": "Updated"},
            }
        })
        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = factory.patch("/nextseek_api/sops/42/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sops.upload_content_blobs")
    @patch("nextseek_api.services.sops.check_unmatched_files", return_value=[])
    def test_update_with_files_blob_failure_207(self, mock_unmatched, mock_upload, mock_resolve):
        vs, client = _make_viewset_and_client()
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "test.pdf", "content_type": "application/pdf"}
        ])
        client.update_sop.return_value = (_json_bytes(sop_resp), 200, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="99", original_filename="test.pdf", status="failed", error="upload error")]

        metadata = json.dumps({
            "data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}
        })
        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = factory.patch("/nextseek_api/sops/42/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 207

    @patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sops.check_unmatched_files", return_value=["extra.txt"])
    def test_update_with_files_unmatched_returns_400(self, mock_unmatched, mock_resolve):
        vs, client = _make_viewset_and_client()
        sop_resp = _valid_sop_response(content_blobs=[
            {"link": "http://seek/sops/42/content_blobs/99",
             "original_filename": "test.pdf", "content_type": "application/pdf"}
        ])
        client.update_sop.return_value = (_json_bytes(sop_resp), 200, {"Content-Type": "application/json"}, MagicMock())

        metadata = json.dumps({
            "data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}
        })
        f = SimpleUploadedFile("extra.txt", b"text", content_type="text/plain")
        request = factory.patch("/nextseek_api/sops/42/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 400
        assert b"Unmatched" in resp.content

    def test_update_with_files_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        metadata = json.dumps({"bad": "data"})
        f = SimpleUploadedFile("test.pdf", b"pdf", content_type="application/pdf")
        request = factory.patch("/nextseek_api/sops/42/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 422
        assert b"Invalid metadata" in resp.content

    def test_update_with_files_size_exceeded(self):
        vs, client = _make_viewset_and_client()
        from django.conf import settings
        original = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', None)
        settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = 5

        metadata = json.dumps({
            "data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}
        })
        f = SimpleUploadedFile("test.pdf", b"x" * 100, content_type="application/pdf")
        request = factory.patch("/nextseek_api/sops/42/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="42")
        assert resp.status_code == 413

        if original is None:
            delattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES')
        else:
            settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = original

    def test_update_pk_fallback(self):
        """uid=None falls back to pk."""
        vs, client = _make_viewset_and_client()
        data = {"data": {"type": "sops", "id": "42", "attributes": {"title": "Updated"}}}
        body = _json_bytes(_valid_sop_response())
        client.update_sop.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        with patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42"):
            request = factory.patch("/nextseek_api/sops/42/", data, format="json")
            drf_req = _make_drf_request(request, vs)
            resp = vs.partial_update(drf_req, uid=None, pk="42")
            assert resp.status_code == 200


# ===========================================================================
# DOWNLOAD tests
# ===========================================================================


class TestSopDownload:
    """POST /sops/download — download endpoint."""

    @patch("nextseek_api.services.sops.download_single")
    def test_download_single_dict(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"file content", status=200)
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = {"uid_or_id": "42"}
        resp = vs.download(request)
        assert resp.status_code == 200
        mock_dl.assert_called_once()

    @patch("nextseek_api.services.sops.download_batch")
    def test_download_batch_list(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"zip", status=200)
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = [{"uid_or_id": "42"}, {"uid_or_id": "43"}]
        resp = vs.download(request)
        assert resp.status_code == 200
        mock_dl.assert_called_once()

    def test_download_invalid_body_type(self):
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = "invalid"
        resp = vs.download(request)
        assert resp.status_code == 422
        assert b"JSON object or array" in resp.content


def test_seek_proxy_lines_reach_django_log():
    from django.conf import settings
    loggers = settings.LOGGING["loggers"]
    assert loggers["nextseek_api.helpers"]["handlers"] == ["logfile"]


def test_services_keep_their_info_lines_and_only_warnings_reach_django_log(caplog):
    import logging
    services = logging.getLogger("nextseek_api.services")
    to_file = [h for h in services.handlers if getattr(h, "baseFilename", "").endswith("django.log")]
    assert len(to_file) == 1
    child = logging.getLogger("nextseek_api.services.zz_probe")
    with patch.object(to_file[0], "emit") as emit, caplog.at_level(logging.INFO):
        child.info("info line")
        child.warning("warning line")
    assert [r.getMessage() for r in caplog.records if r.name == child.name] == ["info line", "warning line"]
    assert [c.args[0].getMessage() for c in emit.call_args_list] == ["warning line"]


# ===========================================================================
# The SOP create's longer wait (#108)
# ===========================================================================


def _client_with_recorded_calls():
    from nextseek_api.helpers import SeekAPIClient
    client = SeekAPIClient()
    answer = MagicMock(status_code=200, content=b"{}", headers={})
    client.session = MagicMock()
    client.session.request.return_value = answer
    return client


class TestSopCreateWait:
    def test_the_sop_and_data_file_creates_wait_120_s(self):
        client = _client_with_recorded_calls()
        req = _make_drf_request(factory.post("/nextseek_api/sops/"), SopProxyViewSet())
        client.create_sop(req, {"data": {}})
        client.get_sop(req, "1")
        client.update_sop(req, "1", {"data": {}})
        client.list_sops(req)
        client.create_data_file(req, {"data": {}})
        waits = [(c.kwargs["method"], c.kwargs["url"].rsplit("/", 1)[-1], c.kwargs["timeout"])
                 for c in client.session.request.call_args_list]
        # Both creates wait 120 s for SEEK's answer; reaching SEEK keeps the uploads' 10 s connect limit.
        assert waits == [("POST", "sops", (10, 120)), ("GET", "1", 20), ("PATCH", "1", 20), ("GET", "sops", 20),
                         ("POST", "data_files", (10, 120))]

    def test_an_unanswered_create_says_it_waited_120_s(self):
        vs, client = _make_viewset_and_client()
        client.timeout_s = 20
        client.create_sop.side_effect = requests.ReadTimeout("slow")
        body = json.loads(_post(vs).content)
        assert "within 120 s" in body["detail"]


# ===========================================================================
# DELETE /sops/{uid} (#108, Task 6)
# ===========================================================================

_NOT_FOUND = b'{"errors":[{"title":"SOP not found"}]}'


def _delete(vs, uid="42"):
    return vs.destroy(_make_drf_request(factory.delete(f"/nextseek_api/sops/{uid}/"), vs), uid=uid)


@pytest.fixture
def _resolves():
    with patch("nextseek_api.services.sops._resolve_uid_to_seek_id", return_value="42") as m:
        yield m


@pytest.fixture
def _citing():
    with patch("nextseek_api.services.sops._samples_citing", return_value=0) as m:
        yield m


@pytest.fixture
def _enqueue():
    with patch("nextseek_api.services.sops.hooks.enqueue") as m:
        yield m


def _visible(client, title="P.WHI-261013-V1_a.pdf"):
    client.get_sop.return_value = (_json_bytes(_valid_sop_response(title=title)), 200, _JSON, MagicMock())


@pytest.mark.usefixtures("_resolves")
class TestSopDestroy:
    def test_delete_answers_json_and_relabels_protocols_at_once(self, _citing, _enqueue):
        vs, client = _make_viewset_and_client()
        _visible(client)
        client.delete_sop.return_value = (b'{"status":"ok"}', 200, _JSON, MagicMock())
        resp = _delete(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"
        assert body["id"] == "42" and body["title"] == "P.WHI-261013-V1_a.pdf"
        client.delete_sop.assert_called_once()
        assert client.delete_sop.call_args.args[1] == "42"
        _enqueue.assert_called_once_with("protocol_map", "*", delay_s=0)
        _citing.assert_called_once_with("42", "P.WHI-261013-V1_a.pdf")

    def test_unknown_uid_answers_404(self, _resolves, _citing):
        vs, client = _make_viewset_and_client()
        _resolves.return_value = None
        resp = _delete(vs, uid="nope")
        assert resp.status_code == 404
        assert resp.content == _NOT_FOUND
        client.delete_sop.assert_not_called()

    @pytest.mark.parametrize("seek_code", [403, 404])
    def test_a_sop_the_caller_cannot_see_answers_as_one_that_does_not_exist(self, seek_code, _citing):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (b'{"errors":[{"title":"Forbidden","detail":"sop 42"}]}', seek_code, _JSON,
                                       MagicMock())
        resp = _delete(vs)
        assert resp.status_code == 404
        assert resp.content == _NOT_FOUND
        _citing.assert_not_called()
        client.delete_sop.assert_not_called()

    @pytest.mark.parametrize("exc,status", [(requests.ReadTimeout("slow"), 504),
                                            (requests.ConnectionError("down"), 502)])
    def test_seek_not_answering_the_read_says_nothing_was_deleted(self, exc, status, _citing):
        vs, client = _make_viewset_and_client()
        client.get_sop.side_effect = exc
        resp = _delete(vs)
        assert resp.status_code == status
        assert resp["Content-Type"] == "application/json"
        assert "Nothing was deleted" in json.loads(resp.content)["errors"][0]["detail"]
        _citing.assert_not_called()
        client.delete_sop.assert_not_called()

    def test_no_seek_login_answers_401(self, _citing):
        vs, client = _make_viewset_and_client()
        client.get_sop.return_value = (None, 401, _JSON, None)
        resp = _delete(vs)
        assert resp.status_code == 401
        assert resp.content == b'{"detail":"Authentication required"}'
        client.delete_sop.assert_not_called()

    @pytest.mark.parametrize("citing", [1, 3])
    def test_a_sop_that_samples_name_as_their_protocol_is_refused(self, citing, _citing):
        vs, client = _make_viewset_and_client()
        _visible(client)
        _citing.return_value = citing
        resp = _delete(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 409
        assert resp["Content-Type"] == "application/json"
        assert body["id"] == "42"
        assert "Nothing was deleted" in body["errors"][0]["detail"]
        client.delete_sop.assert_not_called()

    def test_the_refusal_is_the_same_however_many_samples_use_it(self, _citing):
        # Some of those samples may sit in projects the caller cannot see: the answer must not tell.
        bodies = set()
        for citing in (1, 3):
            vs, client = _make_viewset_and_client()
            _visible(client)
            _citing.return_value = citing
            resp = _delete(vs)
            assert resp.status_code == 409
            bodies.add(resp.content)
        (body,) = bodies
        assert "samples" not in json.loads(body)
        assert not re.search(rb"\d+ sample", body)

    def test_the_in_use_check_failing_deletes_nothing(self, _citing):
        vs, client = _make_viewset_and_client()
        _visible(client)
        _citing.side_effect = RuntimeError("db down")
        resp = _delete(vs)
        assert resp.status_code == 503
        assert resp["Content-Type"] == "application/json"
        assert "Nothing was deleted" in json.loads(resp.content)["errors"][0]["detail"]
        client.delete_sop.assert_not_called()

    def test_seek_refusing_keeps_its_json_answer(self, _citing, _enqueue):
        vs, client = _make_viewset_and_client()
        _visible(client)
        client.delete_sop.return_value = (b'{"errors":[{"title":"Forbidden"}]}', 403, _JSON, MagicMock())
        resp = _delete(vs)
        assert resp.status_code == 403
        assert json.loads(resp.content) == {"errors": [{"title": "Forbidden"}]}
        _enqueue.assert_not_called()

    def test_seek_refusing_with_html_answers_json(self, _citing, _enqueue):
        vs, client = _make_viewset_and_client()
        _visible(client)
        client.delete_sop.return_value = (b"<html>no</html>", 403, {"Content-Type": "text/html"}, MagicMock())
        resp = _delete(vs)
        assert resp.status_code == 403
        assert resp["Content-Type"] == "application/json"
        assert json.loads(resp.content)["seek_status"] == 403

    def test_seek_5xx_answers_json_502_and_relabels_held_back(self, _citing, _enqueue):
        from nextseek_api.graph_sync import hooks
        vs, client = _make_viewset_and_client()
        _visible(client)
        client.delete_sop.return_value = (b"<html>boom</html>", 500, {"Content-Type": "text/html"}, MagicMock())
        resp = _delete(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 502
        assert body["seek_status"] == 500
        _enqueue.assert_called_once_with("protocol_map", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)

    def test_seek_not_answering_is_202_unconfirmed(self, _citing, _enqueue):
        from nextseek_api.graph_sync import hooks
        vs, client = _make_viewset_and_client()
        client.timeout_s = 20
        _visible(client)
        client.delete_sop.side_effect = requests.ReadTimeout("slow")
        resp = _delete(vs)
        body = json.loads(resp.content)
        assert resp.status_code == 202
        assert body["status"] == "unconfirmed" and body["id"] == "42"
        _enqueue.assert_called_once_with("protocol_map", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)

    def test_seek_unreachable_is_502_json(self, _citing, _enqueue):
        vs, client = _make_viewset_and_client()
        _visible(client)
        client.delete_sop.side_effect = requests.ConnectionError("down")
        resp = _delete(vs)
        assert resp.status_code == 502
        assert resp["Content-Type"] == "application/json"
        assert json.loads(resp.content)["errors"]

    def test_on_mysql_the_check_and_the_delete_run_under_a_lock_named_for_the_title(self, _citing, _enqueue):
        calls, names = [], []
        conn = MagicMock(vendor="mysql")
        cur = conn.cursor.return_value.__enter__.return_value
        cur.execute.side_effect = lambda sql, params: calls.append((re.search(r"\w+_LOCK", sql).group(0), params[0]))
        cur.fetchone.return_value = (1,)
        _citing.side_effect = lambda *a: calls.append("check") or 0
        for title in ("P.WHI-261013-V1_a.pdf", " p.whi-261013-v1_A.PDF "):  # one title key, as the graph's
            vs, client = _make_viewset_and_client()
            _visible(client, title)
            client.delete_sop.side_effect = lambda *a: calls.append("delete") or (b"{}", 200, _JSON, MagicMock())
            calls.clear()
            with patch("nextseek_api.services.sops.connections", {"seek": conn}, create=True):
                assert _delete(vs).status_code == 200
            assert [c if isinstance(c, str) else c[0] for c in calls] == ["GET_LOCK", "check", "delete", "RELEASE_LOCK"]
            assert calls[0][1] == calls[-1][1] and len(calls[0][1]) <= 64  # MySQL's limit on a lock name
            names.append(calls[0][1])
        assert names[0] == names[1]

    def test_on_mysql_a_title_lock_another_delete_holds_deletes_nothing(self, _citing):
        conn = MagicMock(vendor="mysql")
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (0,)  # GET_LOCK's answer when the wait ran out
        vs, client = _make_viewset_and_client()
        _visible(client)
        with patch("nextseek_api.services.sops.connections", {"seek": conn}, create=True):
            resp = _delete(vs)
        assert resp.status_code == 503
        assert "Nothing was deleted" in json.loads(resp.content)["errors"][0]["detail"]
        _citing.assert_not_called()
        client.delete_sop.assert_not_called()
        assert [c.args[0].split("(")[0] for c in cur.execute.call_args_list] == ["SELECT GET_LOCK"]


def test_the_delete_goes_to_seek_with_the_callers_own_login(_seek_login):
    _seek_login.return_value = ("alice", "her-pw")
    client = _client_with_recorded_calls()
    client.delete_sop(_make_drf_request(factory.delete("/nextseek_api/sops/42/"), SopProxyViewSet()), "42")
    call = client.session.request.call_args.kwargs
    assert (call["method"], call["url"].rsplit("/", 2)[-2:]) == ("DELETE", ["sops", "42"])
    from nextseek_api.helpers import basic_auth_header
    assert call["headers"]["Authorization"] == basic_auth_header(("alice", "her-pw"))["Authorization"]


def test_delete_is_routed_on_the_sop_detail_path():
    from django.urls import resolve
    match = resolve("/nextseek_api/sops/42/")
    assert match.func.actions.get("delete") == "destroy"


# ===========================================================================
# Which samples name a SOP as their Protocol (#108, Task 6)
# ===========================================================================


class _FakeSamples:
    """``Samples.objects`` over a list of stored ``json_metadata`` texts, ids 1, 2, ...: ``filter`` applies the
    query's own ``contains`` (case-sensitive, as Django's LIKE BINARY) and ``icontains`` terms, as MySQL would, and
    ``id__gt``. ``reads`` holds how many rows each fetch brought back from the database. Rows are stored in
    reverse id order, as no ORDER BY promises otherwise, so only ``order_by("id")`` makes keyset paging safe."""

    def __init__(self, metas):
        self.rows, self.reads, self.fields, self.flat = list(enumerate(metas, 1))[::-1], [], ("id", "json_metadata"), False

    def _with(self, **kw):
        out = copy.copy(self)  # shares ``reads``
        out.__dict__.update(kw)
        return out

    @staticmethod
    def _match(q, text):
        from django.db.models import Q
        hits = [_FakeSamples._match(c, text) if isinstance(c, Q)
                else (c[1] in text if c[0] == "json_metadata__contains" else c[1].lower() in text.lower())
                for c in q.children]
        hit = any(hits) if q.connector == "OR" else all(hits)
        return not hit if q.negated else hit

    def filter(self, q=None, *, id__gt=None):
        return self._with(rows=[(i, m) for i, m in self.rows
                                if (q is None or self._match(q, m)) and (id__gt is None or i > id__gt)])

    def order_by(self, field):
        assert field == "id"
        return self._with(rows=sorted(self.rows))

    def values_list(self, *fields, flat=False):
        assert set(fields) <= {"id", "json_metadata"}
        return self._with(fields=fields, flat=flat)

    def _fetch(self, rows):
        self.reads.append(len(rows))
        out = [tuple({"id": i, "json_metadata": m}[f] for f in self.fields) for i, m in rows]
        return [r[0] for r in out] if self.flat else out

    def __getitem__(self, s):
        return self._fetch(self.rows[s])

    def iterator(self):
        return iter(self._fetch(self.rows))


def _citing_over(metas, sop_id, title, fake=None, sops=None):
    """``sops``: SEEK's sops rows as (id, title); by default only the SOP being deleted."""
    from nextseek_api.services.sops import _samples_citing
    table = MagicMock()
    table.values_list.return_value = list(sops if sops is not None else [(int(sop_id), title)])
    with patch("seek.models.Samples.objects", fake or _FakeSamples(metas)), \
            patch("seek.models.Sops.objects", table):
        return _samples_citing(sop_id, title)


class TestSamplesCiting:
    def test_counts_every_way_a_sample_names_the_sop_and_nothing_else(self):
        title = "P.WHI-261013-V1_a&b.pdf"
        metas = [
            json.dumps({"Protocol": title}),
            json.dumps({"Protocol": title.upper()}),                    # SEEK's title lookup ignores case
            json.dumps({"protocol": "http://localhost:3000/sops/42"}),  # this instance's URL for it
            json.dumps({"Protocol": f"http://localhost/seek/sop/uid={title}/"}),
            '{"Protocol": "P.WHI-261013-V1_a\\u0026b.pdf"}',           # stored with & escaped, as Rails does
            json.dumps({"Protocol": title + ".old"}),                   # another SOP's title
            json.dumps({"Protocol": "http://localhost/sops/420"}),      # another SOP's id
            json.dumps({"Notes": title, "Protocol": "P.WHI-261013-V1_c.pdf"}),
            json.dumps({"Protocol": "https://elsewhere.example/sops/42"}),  # another instance's SOP
            "not json " + title,
        ]
        assert _citing_over(metas, "42", title) == 5

    def test_none_when_nothing_names_it(self):
        assert _citing_over([json.dumps({"Protocol": "P.WHI-261013-V1_c.pdf"})], "42", "P.WHI-261013-V1_a.pdf") == 0

    def test_a_title_with_no_plain_text_is_still_found(self):
        # Stored with every letter escaped, so no part of the title is in the text as typed.
        assert _citing_over([json.dumps({"Protocol": "\u00e9\u00e8"})], "42", "\u00e9\u00e8") == 1

    def test_a_title_with_spaces_around_it_matches_as_the_graph_does(self):
        # labels._title_key strips the title, so the graph links this sample to the SOP.
        meta = json.dumps({"Protocol": "P.WHI-261013-V1_a.pdf"})
        assert _citing_over([meta], "42", " P.WHI-261013-V1_a.pdf ") == 1

    def test_a_broad_title_is_read_in_bounded_pages_and_counted_once(self):
        # "Protocol" is in every row (it is the key name): the read must not bring every row back at once.
        metas = [json.dumps({"Protocol": "Protocol" if i % 2 == 0 else f"P.WHI-261013-V1_{i}.pdf"})
                 for i in range(5000)]
        fake = _FakeSamples(metas)
        assert _citing_over(metas, "42", "Protocol", fake) == 2500
        assert len(fake.reads) > 1 and max(fake.reads) < len(metas)

    def test_a_title_another_sop_keeps_no_longer_holds_the_delete(self):
        # The graph resolves a title held by two SOPs to neither, and to the one left after a delete.
        title = "P.WHI-261013-V1_a.pdf"
        metas = [json.dumps({"Protocol": title}), json.dumps({"Protocol": title.upper()}),
                 json.dumps({"Protocol": "http://localhost:3000/sops/42"})]
        assert _citing_over(metas, "42", title, sops=[(42, title), (43, " " + title.upper())]) == 1

    def test_a_title_only_this_sop_holds_still_holds_the_delete(self):
        title = "P.WHI-261013-V1_a.pdf"
        metas = [json.dumps({"Protocol": title})]
        assert _citing_over(metas, "42", title, sops=[(42, title), (43, title + ".old")]) == 1

    def test_a_sop_without_a_title_holds_no_title(self):
        title = "P.WHI-261013-V1_a.pdf"
        assert _citing_over([json.dumps({"Protocol": title})], "42", title, sops=[(43, None), (42, title)]) == 1


def test_two_deletes_at_once_of_the_sops_that_share_a_cited_title_leave_one():
    # Operator 2026-10-13: either copy may go "while the other keeps the title". Two of NExtSEEK's own deletes at
    # once must not each see the other still holding it.
    import threading
    title = "P.WHI-261013-V1_a.pdf"
    sops = {41: title, 42: title}
    table = MagicMock()
    table.values_list.side_effect = lambda *a: list(sops.items())
    reached, out = threading.Event(), {}

    def viewset(delete):
        vs, client = _make_viewset_and_client()
        _visible(client, title)
        client.delete_sop.side_effect = delete
        return vs

    def delete_42(request, sid):
        reached.set()
        sops.pop(42)
        return (b"{}", 200, _JSON, MagicMock())

    def delete_41(request, sid):
        racer.start()
        reached.wait(1)  # the other delete reaches SEEK now, unless it waits for this one to finish
        sops.pop(41)
        return (b"{}", 200, _JSON, MagicMock())

    racer = threading.Thread(target=lambda: out.__setitem__("42", _delete(viewset(delete_42), "42").status_code))
    with patch("nextseek_api.services.sops._resolve_uid_to_seek_id", side_effect=lambda uid, *a, **k: uid), \
            patch("nextseek_api.services.sops.hooks.enqueue"), \
            patch("seek.models.Samples.objects", _FakeSamples([json.dumps({"Protocol": title})])), \
            patch("seek.models.Sops.objects", table):
        out["41"] = _delete(viewset(delete_41), "41").status_code
        racer.join(15)
    assert out == {"41": 200, "42": 409}
    assert sops == {42: title}


def _clock(start=1000.0):
    """A clock for the create's request limit: ``now[0]`` is the time, moved on by the fakes."""
    now = [start]
    return now, patch("nextseek_api.services.sops.monotonic", lambda: now[0], create=True)


def _created(sop_id, name):
    return (_json_bytes(_valid_sop_response(sop_id=sop_id, content_blobs=[
        {"link": f"http://seek/sops/{sop_id}/content_blobs/9", "original_filename": name,
         "content_type": "application/pdf"}])), 201, _JSON, MagicMock())


@pytest.mark.usefixtures("_enqueue")
class TestSopCreateRequestLimit:
    """Operator 2026-10-13: the whole create is capped at 120 s, each SEEK call's own limit inside it."""

    def test_no_new_create_starts_after_120_s_and_the_rest_are_listed(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def create(request, payload, **kw):
            now[0] += 70
            return _created("4" + str(client.create_sop.call_count), payload["data"]["attributes"]["content_blobs"][0]["original_filename"])

        client.create_sop.side_effect = create
        client.upload_content_blob.return_value = (200, {}, None)
        with clock:
            resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf"), _file("c.pdf")])
        body = json.loads(resp.content)
        assert client.create_sop.call_count == 2           # c.pdf's create would start at 140 s
        assert resp.status_code == 207
        assert [c["id"] for c in body["created"]] == ["41", "42"]
        assert body["not_sent"] == ["b.pdf", "c.pdf"]      # b's SOP exists; its file upload would start at 140 s
        assert client.upload_content_blob.call_count == 1
        assert "120 s" in body["errors"][0]["detail"]

    def test_each_created_sop_names_its_file(self):
        # b.pdf's SOP was created but its file was never sent: the client must be able to pair the two.
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def create(request, payload, **kw):
            now[0] += 70
            return _created("4" + str(client.create_sop.call_count), "x.pdf")

        client.create_sop.side_effect = create
        client.upload_content_blob.return_value = (200, {}, None)
        with clock:
            body = json.loads(_post(vs, files=[_file("a.pdf"), _file("b.pdf")]).content)
        assert [(c["id"], c.get("file"), "blobs" in c) for c in body["created"]] == [("41", "a.pdf", True),
                                                                                      ("42", "b.pdf", False)]
        assert body["not_sent"] == ["b.pdf"]

    def test_a_slow_upload_keeps_the_next_create_from_starting(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()
        client.create_sop.side_effect = lambda request, payload, **kw: _created("41", "a.pdf")

        def upload(*a, **kw):
            now[0] += 130
            return (200, {}, None)

        client.upload_content_blob.side_effect = upload
        with clock:
            resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert client.create_sop.call_count == 1
        assert resp.status_code == 207
        assert [c["id"] for c in body["created"]] == ["41"] and body["not_sent"] == ["b.pdf"]
        assert body["data"][0]["data"]["id"] == "41"

    def test_each_seek_call_is_capped_at_the_time_the_request_has_left(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def create(request, payload, **kw):
            now[0] += 30
            return _created("41", "a.pdf")

        def upload(*a, **kw):
            now[0] += 20
            return (200, {}, None)

        client.create_sop.side_effect = create
        client.upload_content_blob.side_effect = upload
        with clock:
            resp = _post(vs, files=[_file("a.pdf")])
        assert resp.status_code == 201
        assert client.create_sop.call_args.kwargs["timeout_s"] == (10, 120)
        assert client.upload_content_blob.call_args.kwargs["timeout_s"] == (10, 90)

    def test_a_call_with_under_10_s_left_has_its_connect_cut_too(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def create(request, payload, **kw):
            now[0] += 115
            return _created("41", "a.pdf")

        client.create_sop.side_effect = create
        client.upload_content_blob.return_value = (200, {}, None)
        with clock:
            assert _post(vs, files=[_file("a.pdf")]).status_code == 201
        assert client.upload_content_blob.call_args.kwargs["timeout_s"] == (5, 5)

    def test_the_limit_passing_before_any_create_answers_504(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def slow_title_check(*a, **kw):
            now[0] += 130  # the title reads before the first create
            return None

        with clock, patch("nextseek_api.services.sops._duplicate_title", side_effect=slow_title_check):
            resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert resp.status_code == 504
        assert body["created"] == [] and body["not_sent"] == ["a.pdf", "b.pdf"]
        client.create_sop.assert_not_called()

    def test_a_create_cut_by_the_limit_is_unconfirmed_with_its_own_wait(self):
        vs, client = _make_viewset_and_client()
        now, clock = _clock()

        def create(request, payload, **kw):
            if client.create_sop.call_count == 1:
                now[0] += 100
                return _created("41", "a.pdf")
            now[0] += kw["timeout_s"][1]
            raise requests.ReadTimeout("slow")

        client.create_sop.side_effect = create
        client.upload_content_blob.return_value = (200, {}, None)
        with clock:
            resp = _post(vs, files=[_file("a.pdf"), _file("b.pdf")])
        body = json.loads(resp.content)
        assert client.create_sop.call_args.kwargs["timeout_s"] == (10, 20)
        assert resp.status_code == 202 and body["status"] == "unconfirmed"
        assert "within 20 s" in body["detail"]
        assert [c["id"] for c in body["created"]] == ["41"] and body["not_sent"] == ["b.pdf"]

    def test_the_upload_sends_the_limit_to_seek(self):
        client = _client_with_recorded_calls()
        req = _make_drf_request(factory.post("/nextseek_api/sops/"), SopProxyViewSet())
        client.upload_content_blob(req, path="/sops/1/content_blobs/2", file_data=b"x", content_type="a/b",
                                   timeout_s=(10, 33))
        client.upload_content_blob(req, path="/sops/1/content_blobs/2", file_data=b"x", content_type="a/b")
        assert [c.kwargs["timeout"] for c in client.session.request.call_args_list] == [(10, 33), (10, 300)]
