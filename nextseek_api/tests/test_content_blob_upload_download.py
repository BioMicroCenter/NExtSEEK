"""Unit tests for content blob upload/download shared logic."""
import json
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import TestCase

from nextseek_api.helpers import SeekAPIClient

_EMPTY_REF = {"data": []}


def _valid_sop_response(content_blobs=None):
    """Build a minimal valid SopSingleResponse dict for test mocks."""
    return {
        "data": {
            "id": "42", "type": "sops",
            "attributes": {
                "title": "Test SOP",
                "content_blobs": content_blobs or [],
            },
            "relationships": {
                "creators": _EMPTY_REF, "submitter": _EMPTY_REF,
                "people": _EMPTY_REF, "projects": _EMPTY_REF,
                "investigations": _EMPTY_REF, "studies": _EMPTY_REF,
                "assays": _EMPTY_REF, "publications": _EMPTY_REF,
                "workflows": _EMPTY_REF,
            },
            "links": {"self": "/sops/42"},
            "meta": {"created": "2026-01-01", "modified": "2026-01-01", "uuid": "abc"},
        },
        "jsonapi": {"version": "1.0"},
    }


class SeekAPIClientUploadTest(TestCase):
    """Test SeekAPIClient.upload_content_blob method."""

    def setUp(self):
        self.client = SeekAPIClient()
        self.mock_request = MagicMock()

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_upload_content_blob_success(self, mock_auth):
        mock_auth.return_value = (("user", "pass"), {})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.content = b'{"ok": true}'

        with patch.object(self.client.session, "request", return_value=mock_resp) as mock_req:
            status, headers, resp = self.client.upload_content_blob(
                self.mock_request,
                path="/sops/42/content_blobs/99",
                file_data=b"fake pdf content",
                content_type="application/pdf",
            )
            self.assertEqual(status, 200)
            call_kwargs = mock_req.call_args
            self.assertEqual(call_kwargs.kwargs["method"], "PUT")
            self.assertIn("/sops/42/content_blobs/99", call_kwargs.kwargs["url"])
            self.assertEqual(call_kwargs.kwargs["data"], b"fake pdf content")
            self.assertEqual(
                call_kwargs.kwargs["headers"]["Content-Type"], "application/octet-stream"
            )

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_upload_content_blob_no_auth(self, mock_auth):
        mock_auth.return_value = (None, {})
        status, headers, resp = self.client.upload_content_blob(
            self.mock_request,
            path="/sops/42/content_blobs/99",
            file_data=b"data",
            content_type="application/pdf",
        )
        self.assertEqual(status, 401)
        self.assertIsNone(resp)


from nextseek_api.services.content_blobs import (
    resolve_asset_and_blobs,
    download_single,
    download_batch,
    upload_content_blobs,
    deduplicate_filename,
)


class ResolveAssetAndBlobsTest(TestCase):
    """Test resolve_asset_and_blobs for both asset types."""

    @patch("nextseek_api.services.content_blobs._resolve_uid_to_seek_id")
    def test_resolve_returns_404_when_not_found(self, mock_resolve):
        mock_resolve.return_value = None
        mock_req = MagicMock()
        mock_req.seek_id = None
        mock_req.uid_or_id = "nonexistent"
        client = SeekAPIClient()
        result = resolve_asset_and_blobs(client, MagicMock(), "sops", mock_req)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], 404)

    @patch("nextseek_api.services.content_blobs._resolve_uid_to_seek_id")
    def test_resolve_single_blob_success(self, mock_resolve):
        mock_resolve.return_value = "42"
        client = SeekAPIClient()
        sop_response = json.dumps({
            "data": {
                "attributes": {
                    "version": 1,
                    "latest_version": 1,
                    "content_blobs": [
                        {"link": "http://seek/sops/42/content_blobs/99",
                         "original_filename": "test.pdf",
                         "content_type": "application/pdf"}
                    ],
                }
            }
        }).encode()
        with patch.object(client, "get_sop", return_value=(sop_response, 200, {}, MagicMock())):
            mock_req = MagicMock()
            mock_req.seek_id = None
            mock_req.uid_or_id = "42"
            mock_req.blob_id = None
            mock_req.asset_types = None
            mock_req.output_format = None
            result = resolve_asset_and_blobs(client, MagicMock(), "sops", mock_req)
            self.assertTrue(result["success"])
            self.assertEqual(result["seek_id"], "42")


class DeduplicateFilenameTest(TestCase):

    def test_unique_filename_returned_as_is(self):
        used = set()
        self.assertEqual(deduplicate_filename("file.pdf", "1", used), "file.pdf")
        self.assertIn("file.pdf", used)

    def test_duplicate_gets_seek_id_suffix(self):
        used = {"file.pdf"}
        self.assertEqual(deduplicate_filename("file.pdf", "42", used), "file_42.pdf")


class SopDownloadDelegationTest(TestCase):
    """Verify SOP download delegates to shared module."""

    @patch("nextseek_api.services.sops.download_single")
    def test_single_download_delegates(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"file content", status=200)
        from nextseek_api.services.sops import SopProxyViewSet
        vs = SopProxyViewSet()
        vs.client = SeekAPIClient()
        mock_request = MagicMock()
        mock_request.data = {"uid_or_id": "42"}
        resp = vs.download(mock_request)
        mock_dl.assert_called_once()
        self.assertEqual(mock_dl.call_args[0][2], "sops")

    @patch("nextseek_api.services.sops.download_batch")
    def test_batch_download_delegates(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"zip", status=200)
        from nextseek_api.services.sops import SopProxyViewSet
        vs = SopProxyViewSet()
        vs.client = SeekAPIClient()
        mock_request = MagicMock()
        mock_request.data = [{"uid_or_id": "42"}]
        resp = vs.download(mock_request)
        mock_dl.assert_called_once()
        self.assertEqual(mock_dl.call_args[0][2], "sops")


class DataFileDownloadTest(TestCase):
    """Test DataFile download action exists and delegates to shared module."""

    @patch("nextseek_api.services.data_files.download_single")
    def test_single_download(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"csv data", status=200)
        from nextseek_api.services.data_files import DataFileProxyViewSet
        vs = DataFileProxyViewSet()
        mock_request = MagicMock()
        mock_request.data = {"uid_or_id": "560"}
        resp = vs.download(mock_request)
        mock_dl.assert_called_once()
        self.assertEqual(mock_dl.call_args[0][2], "data_files")

    @patch("nextseek_api.services.data_files.download_batch")
    def test_batch_download(self, mock_dl):
        mock_dl.return_value = HttpResponse(b"zip", status=200)
        from nextseek_api.services.data_files import DataFileProxyViewSet
        vs = DataFileProxyViewSet()
        mock_request = MagicMock()
        mock_request.data = [{"uid_or_id": "560"}, {"uid_or_id": "561"}]
        resp = vs.download(mock_request)
        mock_dl.assert_called_once()
        self.assertEqual(mock_dl.call_args[0][2], "data_files")


class SopCreateWithUploadTest(TestCase):
    """Test SOP create handles multipart upload."""

    def _make_drf_request(self, django_request, vs):
        """Wrap a Django WSGIRequest in DRF's Request with the viewset's parsers."""
        from rest_framework.request import Request
        return Request(django_request, parsers=[p() for p in vs.parser_classes])

    @patch("nextseek_api.services.content_blobs.SeekAPIClient.upload_content_blob")
    def test_create_with_file_uploads_to_seek(self, mock_upload):
        """When files are present, create should POST metadata then PUT each file."""
        seek_response = json.dumps(_valid_sop_response(
            content_blobs=[{"link": "http://seek/sops/42/content_blobs/99",
                            "original_filename": "test.pdf",
                            "content_type": "application/pdf"}]
        )).encode()

        mock_upload.return_value = (200, {}, MagicMock())

        from rest_framework.test import APIRequestFactory
        from nextseek_api.services.sops import SopProxyViewSet

        factory = APIRequestFactory()
        metadata = json.dumps({
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "Test SOP",
                    "content_blobs": [
                        {"original_filename": "test.pdf", "content_type": "application/pdf"}
                    ]
                },
                "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}}
            }
        })
        django_request = factory.post('/nextseek_api/sops/', {
            'metadata': metadata,
            'file': SimpleUploadedFile("test.pdf", b"fake pdf content", content_type="application/pdf"),
        }, format='multipart')

        vs = SopProxyViewSet()
        drf_request = self._make_drf_request(django_request, vs)
        drf_request.user = MagicMock()
        drf_request.auth = "token"

        mock_client = MagicMock()
        mock_client.create_sop.return_value = (seek_response, 201, {"Content-Type": "application/json"}, MagicMock())
        vs.client = mock_client
        response = vs.create(drf_request)
        mock_client.create_sop.assert_called_once()
        self.assertIn(response.status_code, (201, 207))

    def test_create_without_files_backward_compat(self):
        """Pure JSON create still works (no multipart)."""
        seek_response = json.dumps(_valid_sop_response(content_blobs=[])).encode()

        from rest_framework.test import APIRequestFactory
        from nextseek_api.services.sops import SopProxyViewSet

        factory = APIRequestFactory()
        django_request = factory.post('/nextseek_api/sops/', {
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "Test SOP",
                    "content_blobs": [{"original_filename": "test.pdf", "content_type": "application/pdf"}]
                },
                "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}}
            }
        }, format='json')

        vs = SopProxyViewSet()
        drf_request = self._make_drf_request(django_request, vs)
        drf_request.user = MagicMock()
        drf_request.auth = "token"

        mock_client = MagicMock()
        mock_client.create_sop.return_value = (seek_response, 201, {"Content-Type": "application/json"}, MagicMock())
        vs.client = mock_client
        response = vs.create(drf_request)
        self.assertEqual(response.status_code, 201)
        mock_client.create_sop.assert_called_once()
