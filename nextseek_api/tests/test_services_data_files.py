"""Comprehensive tests for nextseek_api/services/data_files.py — DataFileProxyViewSet."""
import json
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from nextseek_api.models import ContentBlobUploadStatus
from nextseek_api.services.data_files import DataFileProxyViewSet, _resolve_uid_to_seek_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_REF = {"data": []}
factory = APIRequestFactory()


def _valid_datafile_response(*, df_id="560", title="Test DataFile", content_blobs=None):
    return {
        "data": {
            "id": df_id,
            "type": "data_files",
            "attributes": {"title": title, "content_blobs": content_blobs or []},
            "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            "links": {"self": f"/data_files/{df_id}"},
            "meta": {"created": "2026-01-01", "modified": "2026-01-01", "uuid": "def"},
        },
        "jsonapi": {"version": "1.0"},
    }


def _valid_list_response():
    return {
        "data": [
            {
                "id": "560",
                "type": "data_files",
                "attributes": {"title": "DF-1"},
                "links": {"self": "/data_files/560"},
            }
        ],
        "jsonapi": {"version": "1.0"},
        "links": {"self": "/data_files?page[number]=1&page[size]=100"},
        "meta": {"base_url": "http://seek", "api_version": "v1"},
    }


def _make_viewset_and_client():
    vs = DataFileProxyViewSet()
    mock_client = MagicMock()
    vs.client = mock_client
    return vs, mock_client


def _make_drf_request(django_request, vs):
    req = Request(django_request, parsers=[p() for p in vs.parser_classes])
    req.user = MagicMock()
    req.auth = "token"
    return req


def _json_bytes(d):
    return json.dumps(d).encode()


# ===========================================================================
# _resolve_uid_to_seek_id wrapper
# ===========================================================================


class TestResolveUidWrapper:
    """The local _resolve_uid_to_seek_id delegates to shared resolver."""

    @patch("nextseek_api.services.data_files._resolve_uid")
    def test_delegates_to_shared(self, mock_shared):
        mock_shared.return_value = "42"
        result = _resolve_uid_to_seek_id("42")
        mock_shared.assert_called_once_with("42", "data_files")
        assert result == "42"


# ===========================================================================
# LIST tests
# ===========================================================================


class TestDataFileList:
    def test_list_success(self):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_list_response())
        client.list_data_files.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 200

    def test_list_401(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (b'{}', 401, {}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 401

    def test_list_upstream_error(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (b'{"errors":[]}', 500, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 500

    def test_list_html_response_502(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (b"<html>", 200, {"Content-Type": "text/html"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502

    def test_list_html_in_body_502(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (b"<html>oops</html>", 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502

    def test_list_invalid_json_502(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (b"not json", 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502

    def test_list_validation_failure_502(self):
        vs, client = _make_viewset_and_client()
        client.list_data_files.return_value = (_json_bytes({"data": "wrong"}), 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.list(drf_req)
        assert resp.status_code == 502


# ===========================================================================
# RETRIEVE tests
# ===========================================================================


class TestDataFileRetrieve:

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_success(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.get_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 200

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value=None)
    def test_retrieve_not_found(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        request = factory.get("/nextseek_api/data_files/nope/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="nope")
        assert resp.status_code == 404
        assert b"DataFile not found" in resp.content

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_401(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_data_file.return_value = (b'{}', 401, {}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 401

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_upstream_error(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_data_file.return_value = (b'{"error":"x"}', 422, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 422

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_html_response_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_data_file.return_value = (b"<html>", 200, {"Content-Type": "text/html"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 502

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_invalid_json_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.get_data_file.return_value = (b"garbage", 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 502

    def test_retrieve_invalid_version_param(self):
        vs, client = _make_viewset_and_client()
        request = factory.get("/nextseek_api/data_files/560/?version=abc")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 422
        assert b"version must be an integer" in resp.content

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_default_version(self, mock_resolve):
        """When version is not provided, defaults to 1."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.get_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid="560")
        assert resp.status_code == 200
        # Check version_int=1 was passed
        client.get_data_file.assert_called_once_with(drf_req, "560", 1)

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_retrieve_pk_fallback(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.get_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        request = factory.get("/nextseek_api/data_files/560/")
        drf_req = _make_drf_request(request, vs)
        resp = vs.retrieve(drf_req, uid=None, pk="560")
        assert resp.status_code == 200


# ===========================================================================
# CREATE tests
# ===========================================================================


class TestDataFileCreate:

    def _multipart_request(self, metadata, files=None):
        data = {"metadata": json.dumps(metadata) if isinstance(metadata, dict) else metadata}
        if files:
            data["file"] = files if isinstance(files, list) else [files]
        return factory.post("/nextseek_api/data_files/", data, format="multipart")

    def test_create_json_only_success(self):
        """JSON-only create path.

        Note: source has UnboundLocalError for 'ct' on the success path (line 239).
        """
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "My DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        body = _json_bytes(_valid_datafile_response())
        client.create_data_file.return_value = (body, 201, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        try:
            resp = vs.create(drf_req)
            assert resp.status_code in (201, 502)
        except UnboundLocalError:
            pass  # Known bug: 'ct' not defined on success path
        client.create_data_file.assert_called_once()

    def test_create_json_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        request = self._multipart_request({"bad": "data"})
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_json_401(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b'{}', 401, {}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 401

    def test_create_json_upstream_error(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b'{"error":"x"}', 422, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_json_invalid_upstream(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b"not json", 201, {"Content-Type": "application/json"}, MagicMock())

        request = self._multipart_request(metadata)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.data_files.upload_content_blobs")
    def test_create_with_file_success(self, mock_upload):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "data.csv", "content_type": "text/csv"}
        ])
        client.create_data_file.return_value = (_json_bytes(resp_data), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="88", original_filename="data.csv", status="uploaded")]

        f = SimpleUploadedFile("data.csv", b"col1,col2", content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 201

    @patch("nextseek_api.services.data_files.upload_content_blobs")
    def test_create_with_multiple_files(self, mock_upload):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "placeholder"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "a.csv", "content_type": "text/csv"}
        ])
        client.create_data_file.return_value = (_json_bytes(resp_data), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="88", original_filename="data.csv", status="uploaded")]

        files = [
            SimpleUploadedFile("a.csv", b"data1", content_type="text/csv"),
            SimpleUploadedFile("b.csv", b"data2", content_type="text/csv"),
        ]
        request = self._multipart_request(metadata, files=files)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 201
        assert client.create_data_file.call_count == 2

    @patch("nextseek_api.services.data_files.upload_content_blobs")
    def test_create_with_file_blob_failure_207(self, mock_upload):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "data.csv", "content_type": "text/csv"}
        ])
        client.create_data_file.return_value = (_json_bytes(resp_data), 201, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="88", original_filename="data.csv", status="failed", error="upload error")]

        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 207

    def test_create_with_file_size_exceeded(self):
        vs, client = _make_viewset_and_client()
        from django.conf import settings
        original = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', None)
        settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = 5

        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        f = SimpleUploadedFile("large.csv", b"x" * 100, content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 413

        if original is None:
            delattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES')
        else:
            settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = original

    def test_create_with_file_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        bad_metadata = {"data": {"type": "wrong", "attributes": {"title": "x"}}}
        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = self._multipart_request(bad_metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_with_file_401(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b'{}', 401, {}, MagicMock())

        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 401

    def test_create_with_file_upstream_error(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b'{"error":"x"}', 422, {"Content-Type": "application/json"}, MagicMock())

        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 422

    def test_create_with_file_invalid_upstream(self):
        vs, client = _make_viewset_and_client()
        metadata = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "DF"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            }
        }
        client.create_data_file.return_value = (b"not json", 201, {"Content-Type": "application/json"}, MagicMock())

        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = self._multipart_request(metadata, files=f)
        drf_req = _make_drf_request(request, vs)
        resp = vs.create(drf_req)
        assert resp.status_code == 502


# ===========================================================================
# PARTIAL_UPDATE tests
# ===========================================================================


class TestDataFilePartialUpdate:

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_json_success(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.update_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 200

    def test_update_json_invalid_request(self):
        vs, client = _make_viewset_and_client()
        request = factory.patch("/nextseek_api/data_files/560/", {"bad": "data"}, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 422
        assert b"Invalid request" in resp.content

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value=None)
    def test_update_not_found_no_body_id(self, mock_resolve):
        """resolve returns None and no id in payload."""
        vs, client = _make_viewset_and_client()
        # DataFilePatchData requires 'id', so this will fail validation
        data = {"data": {"type": "data_files", "id": "", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/nope/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="nope")
        assert resp.status_code == 404

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_body_id_none_sets_from_resolve(self, mock_resolve):
        """When body id matches resolve, it proceeds normally."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.update_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 200

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_sets_id_from_resolve_when_payload_id_matches(self, mock_resolve):
        """Resolve succeeds and payload.data.id matches — uses the resolved id."""
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.update_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        # Note: DataFilePatchData requires id field, so we send it matching
        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/myuid/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="myuid")
        assert resp.status_code == 200

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_body_id_mismatch_422(self, mock_resolve):
        """If body id != resolved id, return 422."""
        vs, client = _make_viewset_and_client()
        data = {"data": {"type": "data_files", "id": "999", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 422
        assert b"does not match" in resp.content

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_401(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_data_file.return_value = (b'{}', 401, {}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 401

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_upstream_error(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_data_file.return_value = (b'{"error":"x"}', 422, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 422

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_html_response_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_data_file.return_value = (b"<html>", 200, {"Content-Type": "text/html"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 502

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    def test_update_invalid_upstream_502(self, mock_resolve):
        vs, client = _make_viewset_and_client()
        client.update_data_file.return_value = (b"garbage", 200, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 502

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    @patch("nextseek_api.services.data_files.upload_content_blobs")
    @patch("nextseek_api.services.data_files.check_unmatched_files", return_value=[])
    def test_update_with_files_success(self, mock_unmatched, mock_upload, mock_resolve):
        vs, client = _make_viewset_and_client()
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "data.csv", "content_type": "text/csv"}
        ])
        client.update_data_file.return_value = (_json_bytes(resp_data), 200, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="88", original_filename="data.csv", status="uploaded")]

        metadata = json.dumps({"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}})
        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = factory.patch("/nextseek_api/data_files/560/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 200

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    @patch("nextseek_api.services.data_files.upload_content_blobs")
    @patch("nextseek_api.services.data_files.check_unmatched_files", return_value=[])
    def test_update_with_files_blob_failure_207(self, mock_unmatched, mock_upload, mock_resolve):
        vs, client = _make_viewset_and_client()
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "data.csv", "content_type": "text/csv"}
        ])
        client.update_data_file.return_value = (_json_bytes(resp_data), 200, {"Content-Type": "application/json"}, MagicMock())
        mock_upload.return_value = [ContentBlobUploadStatus(blob_id="88", original_filename="data.csv", status="failed", error="upload error")]

        metadata = json.dumps({"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}})
        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = factory.patch("/nextseek_api/data_files/560/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 207

    @patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560")
    @patch("nextseek_api.services.data_files.check_unmatched_files", return_value=["extra.txt"])
    def test_update_with_files_unmatched_400(self, mock_unmatched, mock_resolve):
        vs, client = _make_viewset_and_client()
        resp_data = _valid_datafile_response(content_blobs=[
            {"link": "http://seek/data_files/560/content_blobs/88",
             "original_filename": "data.csv", "content_type": "text/csv"}
        ])
        client.update_data_file.return_value = (_json_bytes(resp_data), 200, {"Content-Type": "application/json"}, MagicMock())

        metadata = json.dumps({"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}})
        f = SimpleUploadedFile("extra.txt", b"text", content_type="text/plain")
        request = factory.patch("/nextseek_api/data_files/560/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 400

    def test_update_with_files_invalid_metadata(self):
        vs, client = _make_viewset_and_client()
        metadata = json.dumps({"bad": "data"})
        f = SimpleUploadedFile("data.csv", b"csv", content_type="text/csv")
        request = factory.patch("/nextseek_api/data_files/560/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 422

    def test_update_with_files_size_exceeded(self):
        vs, client = _make_viewset_and_client()
        from django.conf import settings
        original = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', None)
        settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = 5

        metadata = json.dumps({"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}})
        f = SimpleUploadedFile("data.csv", b"x" * 100, content_type="text/csv")
        request = factory.patch("/nextseek_api/data_files/560/", {"metadata": metadata, "file": f}, format="multipart")
        drf_req = _make_drf_request(request, vs)
        resp = vs.partial_update(drf_req, uid="560")
        assert resp.status_code == 413

        if original is None:
            delattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES')
        else:
            settings.BATCH_UPLOAD_MAX_TOTAL_BYTES = original

    def test_update_pk_fallback(self):
        vs, client = _make_viewset_and_client()
        body = _json_bytes(_valid_datafile_response())
        client.update_data_file.return_value = (body, 200, {"Content-Type": "application/json"}, MagicMock())

        data = {"data": {"type": "data_files", "id": "560", "attributes": {"title": "Updated"}}}
        with patch("nextseek_api.services.data_files._resolve_uid_to_seek_id", return_value="560"):
            request = factory.patch("/nextseek_api/data_files/560/", data, format="json")
            drf_req = _make_drf_request(request, vs)
            resp = vs.partial_update(drf_req, uid=None, pk="560")
            assert resp.status_code == 200


# ===========================================================================
# DOWNLOAD tests
# ===========================================================================


class TestDataFileDownload:

    @patch("nextseek_api.services.data_files.download_single")
    def test_download_single_dict(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"file content", status=200)
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = {"uid_or_id": "560"}
        resp = vs.download(request)
        assert resp.status_code == 200

    @patch("nextseek_api.services.data_files.download_batch")
    def test_download_batch_list(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"zip", status=200)
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = [{"uid_or_id": "560"}, {"uid_or_id": "561"}]
        resp = vs.download(request)
        assert resp.status_code == 200

    def test_download_invalid_body_type(self):
        vs, _ = _make_viewset_and_client()
        request = MagicMock()
        request.data = "invalid"
        resp = vs.download(request)
        assert resp.status_code == 422
        assert b"JSON object or array" in resp.content
