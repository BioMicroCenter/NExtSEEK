"""Unit tests for content blob upload/download shared logic."""
import json
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import TestCase

from nextseek_api.helpers import SeekAPIClient


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
