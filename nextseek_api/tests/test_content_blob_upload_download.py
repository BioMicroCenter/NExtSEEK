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
    auto_populate_content_blobs,
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

    def test_create_rejects_unmatched_files(self):
        """Files that don't match any content_blob placeholder get 400."""
        seek_response = json.dumps(_valid_sop_response(
            content_blobs=[{"link": "http://seek/sops/42/content_blobs/99",
                            "original_filename": "expected.pdf",
                            "content_type": "application/pdf"}]
        )).encode()

        from rest_framework.test import APIRequestFactory
        from nextseek_api.services.sops import SopProxyViewSet

        factory = APIRequestFactory()
        metadata = json.dumps({
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "Test SOP",
                    "content_blobs": [
                        {"original_filename": "expected.pdf", "content_type": "application/pdf"}
                    ]
                },
                "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}}
            }
        })
        django_request = factory.post('/nextseek_api/sops/', {
            'metadata': metadata,
            'file': SimpleUploadedFile("wrong_name.pdf", b"content", content_type="application/pdf"),
        }, format='multipart')

        vs = SopProxyViewSet()
        drf_request = self._make_drf_request(django_request, vs)
        drf_request.user = MagicMock()
        drf_request.auth = "token"

        mock_client = MagicMock()
        mock_client.create_sop.return_value = (seek_response, 201, {"Content-Type": "application/json"}, MagicMock())
        vs.client = mock_client
        response = vs.create(drf_request)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.content)
        self.assertIn("wrong_name.pdf", body["errors"][0]["detail"])

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


def _valid_datafile_response(content_blobs=None):
    """Build a minimal valid DataFileSingleResponse dict for test mocks."""
    return {
        "data": {
            "id": "560", "type": "data_files",
            "attributes": {
                "title": "Test DataFile",
                "content_blobs": content_blobs or [],
            },
            "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
            "links": {"self": "/data_files/560"},
            "meta": {"created": "2026-01-01", "modified": "2026-01-01", "uuid": "def"},
        },
        "jsonapi": {"version": "1.0"},
    }


class DataFileCreateWithUploadTest(TestCase):
    """Test DataFile create handles multipart upload."""

    def _make_drf_request(self, django_request, vs):
        from rest_framework.request import Request
        return Request(django_request, parsers=[p() for p in vs.parser_classes])

    @patch("nextseek_api.services.content_blobs.SeekAPIClient.upload_content_blob")
    def test_create_with_file_uploads_to_seek(self, mock_upload):
        seek_response = json.dumps(_valid_datafile_response(
            content_blobs=[{"link": "http://seek/data_files/560/content_blobs/88",
                            "original_filename": "data.csv",
                            "content_type": "text/csv"}]
        )).encode()

        mock_upload.return_value = (200, {}, MagicMock())

        from rest_framework.test import APIRequestFactory
        from nextseek_api.services.data_files import DataFileProxyViewSet

        factory = APIRequestFactory()
        metadata = json.dumps({
            "data": {
                "type": "data_files",
                "attributes": {
                    "title": "Test DataFile",
                    "content_blobs": [
                        {"original_filename": "data.csv", "content_type": "text/csv"}
                    ]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        })
        django_request = factory.post('/nextseek_api/data_files/', {
            'metadata': metadata,
            'file': SimpleUploadedFile("data.csv", b"col1,col2\n1,2", content_type="text/csv"),
        }, format='multipart')

        vs = DataFileProxyViewSet()
        drf_request = self._make_drf_request(django_request, vs)
        drf_request.user = MagicMock()
        drf_request.auth = "token"

        mock_client = MagicMock()
        mock_client.create_data_file.return_value = (seek_response, 201, {"Content-Type": "application/json"}, MagicMock())
        vs.client = mock_client
        response = vs.create(drf_request)
        mock_client.create_data_file.assert_called_once()
        self.assertIn(response.status_code, (201, 207))

    def test_create_without_files_backward_compat(self):
        seek_response = json.dumps(_valid_datafile_response()).encode()

        from rest_framework.test import APIRequestFactory
        from nextseek_api.services.data_files import DataFileProxyViewSet

        factory = APIRequestFactory()
        django_request = factory.post('/nextseek_api/data_files/', {
            "data": {
                "type": "data_files",
                "attributes": {
                    "title": "Test DataFile",
                    "content_blobs": [{"original_filename": "data.csv", "content_type": "text/csv"}]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }, format='json')

        vs = DataFileProxyViewSet()
        drf_request = self._make_drf_request(django_request, vs)
        drf_request.user = MagicMock()
        drf_request.auth = "token"

        mock_client = MagicMock()
        mock_client.create_data_file.return_value = (seek_response, 201, {"Content-Type": "application/json"}, MagicMock())
        vs.client = mock_client
        response = vs.create(drf_request)
        self.assertEqual(response.status_code, 201)
        mock_client.create_data_file.assert_called_once()


class UploadFlowIntegrationTest(TestCase):
    """Test the full upload_content_blobs flow with mocked SEEK."""

    def test_upload_matches_files_to_blobs_by_filename(self):
        """Files are matched to blob placeholders by original_filename."""
        client = SeekAPIClient()
        blobs = [
            {"link": "http://seek/sops/1/content_blobs/10", "original_filename": "a.pdf", "content_type": "application/pdf"},
            {"link": "http://seek/sops/1/content_blobs/11", "original_filename": "b.csv", "content_type": "text/csv"},
        ]
        files = [
            SimpleUploadedFile("a.pdf", b"pdf content", content_type="application/pdf"),
            SimpleUploadedFile("b.csv", b"csv content", content_type="text/csv"),
        ]
        with patch.object(client, "upload_content_blob", return_value=(200, {}, MagicMock())) as mock_upload:
            results = upload_content_blobs(client, MagicMock(), "sops", "1", blobs, files)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(r.status == "uploaded" for r in results))
            self.assertEqual(mock_upload.call_count, 2)

    def test_upload_unmatched_file_is_skipped_at_low_level(self):
        """At the upload_content_blobs level, unmatched files are silently skipped."""
        client = SeekAPIClient()
        blobs = [
            {"link": "http://seek/sops/1/content_blobs/10", "original_filename": "a.pdf", "content_type": "application/pdf"},
        ]
        files = [
            SimpleUploadedFile("unmatched.txt", b"text", content_type="text/plain"),
        ]
        with patch.object(client, "upload_content_blob") as mock_upload:
            results = upload_content_blobs(client, MagicMock(), "sops", "1", blobs, files)
            self.assertEqual(len(results), 0)
            mock_upload.assert_not_called()

    def test_check_unmatched_files_detects_extras(self):
        """check_unmatched_files returns filenames that don't match any blob."""
        from nextseek_api.services.content_blobs import check_unmatched_files
        blobs = [{"original_filename": "a.pdf"}, {"original_filename": "b.csv"}]
        files = [
            SimpleUploadedFile("a.pdf", b"pdf", content_type="application/pdf"),
            SimpleUploadedFile("extra.txt", b"text", content_type="text/plain"),
        ]
        unmatched = check_unmatched_files(blobs, files)
        self.assertEqual(unmatched, ["extra.txt"])

    def test_check_unmatched_files_all_match(self):
        """check_unmatched_files returns empty list when all files match."""
        from nextseek_api.services.content_blobs import check_unmatched_files
        blobs = [{"original_filename": "a.pdf"}]
        files = [SimpleUploadedFile("a.pdf", b"pdf", content_type="application/pdf")]
        self.assertEqual(check_unmatched_files(blobs, files), [])

    def test_upload_partial_failure_returns_mixed_status(self):
        """If one blob upload fails, results reflect mixed status."""
        client = SeekAPIClient()
        blobs = [
            {"link": "http://seek/sops/1/content_blobs/10", "original_filename": "a.pdf", "content_type": "application/pdf"},
            {"link": "http://seek/sops/1/content_blobs/11", "original_filename": "b.csv", "content_type": "text/csv"},
        ]
        files = [
            SimpleUploadedFile("a.pdf", b"pdf content", content_type="application/pdf"),
            SimpleUploadedFile("b.csv", b"csv content", content_type="text/csv"),
        ]
        with patch.object(client, "upload_content_blob", side_effect=[(200, {}, MagicMock()), (500, {}, MagicMock())]):
            results = upload_content_blobs(client, MagicMock(), "sops", "1", blobs, files)
            self.assertEqual(results[0].status, "uploaded")
            self.assertEqual(results[1].status, "failed")


from nextseek_api.models import SopCreateRequest, DataFileCreateRequest


class OptionalContentBlobsTest(TestCase):
    """content_blobs should be optional when files provide them."""

    def test_sop_create_without_content_blobs(self):
        data = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        req = SopCreateRequest.model_validate(data)
        self.assertEqual(req.data.attributes.title, "My SOP")
        self.assertIsNone(req.data.attributes.content_blobs)

    def test_sop_create_with_content_blobs_still_works(self):
        data = {
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "My SOP",
                    "content_blobs": [{"original_filename": "a.pdf", "content_type": "application/pdf"}]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        req = SopCreateRequest.model_validate(data)
        self.assertEqual(len(req.data.attributes.content_blobs), 1)

    def test_datafile_create_without_content_blobs(self):
        data = {
            "data": {
                "type": "data_files",
                "attributes": {"title": "My DataFile"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        req = DataFileCreateRequest.model_validate(data)
        self.assertEqual(req.data.attributes.title, "My DataFile")
        self.assertIsNone(req.data.attributes.content_blobs)

    def test_datafile_create_with_content_blobs_still_works(self):
        data = {
            "data": {
                "type": "data_files",
                "attributes": {
                    "title": "My DataFile",
                    "content_blobs": [{"original_filename": "data.csv", "content_type": "text/csv"}]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        req = DataFileCreateRequest.model_validate(data)
        self.assertEqual(len(req.data.attributes.content_blobs), 1)


class AutoPopulateContentBlobsTest(TestCase):
    """Test auto_populate_content_blobs merges files into metadata."""

    def setUp(self):
        from nextseek_api.services.content_blobs import auto_populate_content_blobs
        self.auto_populate = auto_populate_content_blobs

    def test_no_blobs_generates_from_files(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        files = [
            SimpleUploadedFile("report.pdf", b"pdf", content_type="application/pdf"),
            SimpleUploadedFile("data.csv", b"csv", content_type="text/csv"),
        ]
        result = self.auto_populate(metadata, files)
        blobs = result["data"]["attributes"]["content_blobs"]
        self.assertEqual(len(blobs), 2)
        self.assertEqual(blobs[0]["original_filename"], "report.pdf")
        self.assertEqual(blobs[0]["content_type"], "application/pdf")
        self.assertEqual(blobs[1]["original_filename"], "data.csv")
        self.assertEqual(blobs[1]["content_type"], "text/csv")

    def test_empty_blobs_list_generates_from_files(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP", "content_blobs": []},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        files = [SimpleUploadedFile("report.pdf", b"pdf", content_type="application/pdf")]
        result = self.auto_populate(metadata, files)
        blobs = result["data"]["attributes"]["content_blobs"]
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0]["original_filename"], "report.pdf")

    def test_explicit_blobs_preserved(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "My SOP",
                    "content_blobs": [
                        {"original_filename": "report.pdf", "content_type": "application/pdf"}
                    ]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        files = [SimpleUploadedFile("report.pdf", b"pdf", content_type="application/pdf")]
        result = self.auto_populate(metadata, files)
        blobs = result["data"]["attributes"]["content_blobs"]
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0]["original_filename"], "report.pdf")
        self.assertEqual(blobs[0]["content_type"], "application/pdf")

    def test_merge_explicit_and_auto(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {
                    "title": "My SOP",
                    "content_blobs": [
                        {"original_filename": "report.pdf", "content_type": "application/pdf"}
                    ]
                },
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        files = [
            SimpleUploadedFile("report.pdf", b"pdf", content_type="application/pdf"),
            SimpleUploadedFile("extra.csv", b"csv", content_type="text/csv"),
        ]
        result = self.auto_populate(metadata, files)
        blobs = result["data"]["attributes"]["content_blobs"]
        self.assertEqual(len(blobs), 2)
        filenames = {b["original_filename"] for b in blobs}
        self.assertEqual(filenames, {"report.pdf", "extra.csv"})

    def test_no_files_returns_unchanged(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP", "content_blobs": []},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        result = self.auto_populate(metadata, [])
        self.assertEqual(result, metadata)

    def test_does_not_mutate_original(self):
        metadata = {
            "data": {
                "type": "sops",
                "attributes": {"title": "My SOP"},
                "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}}
            }
        }
        files = [SimpleUploadedFile("file.pdf", b"pdf", content_type="application/pdf")]
        result = self.auto_populate(metadata, files)
        self.assertNotIn("content_blobs", metadata["data"]["attributes"])
        self.assertIn("content_blobs", result["data"]["attributes"])
