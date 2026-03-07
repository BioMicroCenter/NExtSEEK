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
