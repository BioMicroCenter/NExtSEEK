"""
Tests for nextseek_api/helpers.py

Covers:
- get_basic_auth (Basic Authorization header parsing)
- get_auth (session-derived auth via SeekDB)
- get_token_auth (Token/Bearer header parsing)
- resolve_seek_auth (multi-source auth resolution with order parameter)
- SeekAPIClient (init, _request, all endpoint methods, stream, upload)
- StandardResultsSetPagination (page size settings)
- paginate_rows_in_envelope (pagination helper)
- resolve_sampletype_to_seek_id (sample-type title → numeric id)
"""

import base64
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from django.test import RequestFactory

from nextseek_api.helpers import (
    get_basic_auth,
    get_auth,
    get_token_auth,
    resolve_seek_auth,
    SeekAPIClient,
    StandardResultsSetPagination,
    paginate_rows_in_envelope,
    resolve_sampletype_to_seek_id,
    JSONAPI_ACCEPT,
)


# ---------------------------------------------------------------------------
# Helpers for building fake Django requests
# ---------------------------------------------------------------------------
_rf = RequestFactory()


def _make_request(auth_header=None, x_seek_header=None):
    """Build a Django request with optional auth headers."""
    req = _rf.get("/fake/")
    if auth_header is not None:
        req.META["HTTP_AUTHORIZATION"] = auth_header
    if x_seek_header is not None:
        req.META["HTTP_X_SEEK_AUTHORIZATION"] = x_seek_header
    return req


def _basic_header(user, password):
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {creds}"


# ===========================================================================
# get_basic_auth
# ===========================================================================
class TestGetBasicAuth:
    def test_valid_basic(self):
        req = _make_request(_basic_header("alice", "s3cret"))
        assert get_basic_auth(req) == ("alice", "s3cret")

    def test_password_with_colon(self):
        req = _make_request(_basic_header("bob", "pa:ss:word"))
        assert get_basic_auth(req) == ("bob", "pa:ss:word")

    def test_missing_header(self):
        req = _make_request()
        assert get_basic_auth(req) is None

    def test_non_basic_scheme(self):
        req = _make_request("Bearer abc123")
        assert get_basic_auth(req) is None

    def test_empty_header(self):
        req = _make_request("")
        assert get_basic_auth(req) is None

    def test_invalid_base64(self):
        req = _make_request("Basic %%%notbase64%%%")
        assert get_basic_auth(req) is None


# ===========================================================================
# get_auth (session-based via SeekDB)
# ===========================================================================
class TestGetAuth:
    @patch("nextseek_api.helpers.SeekDB")
    def test_authenticated_session(self, MockSeekDB):
        instance = MockSeekDB.return_value
        instance.getSeekLogin.return_value = {
            "status": True,
            "username": "alice",
            "password": "pw123",
        }
        req = _make_request()
        assert get_auth(req) == ("alice", "pw123")

    @patch("nextseek_api.helpers.SeekDB")
    def test_unauthenticated_session_no_status(self, MockSeekDB):
        instance = MockSeekDB.return_value
        instance.getSeekLogin.return_value = {"status": False, "username": "", "password": ""}
        req = _make_request()
        assert get_auth(req) is None

    @patch("nextseek_api.helpers.SeekDB")
    def test_unauthenticated_session_none(self, MockSeekDB):
        instance = MockSeekDB.return_value
        instance.getSeekLogin.return_value = None
        req = _make_request()
        assert get_auth(req) is None


# ===========================================================================
# get_token_auth
# ===========================================================================
class TestGetTokenAuth:
    def test_token_scheme(self):
        req = _make_request("Token abc123")
        assert get_token_auth(req) == "abc123"

    def test_bearer_scheme(self):
        req = _make_request("Bearer xyz789")
        assert get_token_auth(req) == "xyz789"

    def test_bearer_case_insensitive(self):
        req = _make_request("BEARER mytoken")
        assert get_token_auth(req) == "mytoken"

    def test_token_case_insensitive(self):
        req = _make_request("TOKEN mytoken")
        assert get_token_auth(req) == "mytoken"

    def test_quoted_token_double(self):
        req = _make_request('Token "abc123"')
        assert get_token_auth(req) == "abc123"

    def test_quoted_token_single(self):
        req = _make_request("Token 'abc123'")
        assert get_token_auth(req) == "abc123"

    def test_basic_scheme_returns_none(self):
        req = _make_request(_basic_header("u", "p"))
        assert get_token_auth(req) is None

    def test_no_header_returns_none(self):
        req = _make_request()
        assert get_token_auth(req) is None

    def test_x_seek_authorization_header(self):
        req = _make_request(x_seek_header="Token seektoken123")
        assert get_token_auth(req) == "seektoken123"

    def test_x_seek_takes_precedence_over_standard(self):
        req = _make_request(auth_header="Token standard", x_seek_header="Token seekspecific")
        assert get_token_auth(req) == "seekspecific"

    def test_empty_token_returns_none(self):
        req = _make_request("")
        assert get_token_auth(req) is None

    def test_exception_in_meta_returns_none(self):
        """When request.META.get raises, the except branch (line 57) is hit."""
        req = _make_request()
        req.META = MagicMock()
        req.META.get.side_effect = RuntimeError("broken")
        assert get_token_auth(req) is None


# ===========================================================================
# resolve_seek_auth
# ===========================================================================
class TestResolveSeekAuth:
    @patch("nextseek_api.helpers.get_auth")
    @patch("nextseek_api.helpers.get_basic_auth")
    def test_default_order_basic_first(self, mock_basic, mock_session):
        mock_basic.return_value = ("user", "pass")
        req = _make_request()
        result = resolve_seek_auth(req)
        assert result == (("user", "pass"), {})
        mock_session.assert_not_called()

    @patch("nextseek_api.helpers.get_token_auth")
    @patch("nextseek_api.helpers.get_auth")
    @patch("nextseek_api.helpers.get_basic_auth")
    def test_default_order_falls_to_session(self, mock_basic, mock_session, mock_token):
        mock_basic.return_value = None
        mock_session.return_value = ("sess_u", "sess_p")
        req = _make_request()
        result = resolve_seek_auth(req)
        assert result == (("sess_u", "sess_p"), {})
        mock_token.assert_not_called()

    @patch("nextseek_api.helpers.get_token_auth")
    @patch("nextseek_api.helpers.get_auth")
    @patch("nextseek_api.helpers.get_basic_auth")
    def test_default_order_falls_to_token(self, mock_basic, mock_session, mock_token):
        mock_basic.return_value = None
        mock_session.return_value = None
        mock_token.return_value = "mytoken"
        req = _make_request()
        result = resolve_seek_auth(req)
        assert result == (None, {"Authorization": "Token mytoken"})

    @patch("nextseek_api.helpers.get_token_auth")
    @patch("nextseek_api.helpers.get_auth")
    @patch("nextseek_api.helpers.get_basic_auth")
    def test_no_auth_found(self, mock_basic, mock_session, mock_token):
        mock_basic.return_value = None
        mock_session.return_value = None
        mock_token.return_value = None
        req = _make_request()
        result = resolve_seek_auth(req)
        assert result == (None, None)

    @patch("nextseek_api.helpers.get_token_auth")
    @patch("nextseek_api.helpers.get_basic_auth")
    def test_custom_order_token_only(self, mock_basic, mock_token):
        mock_token.return_value = "tok"
        req = _make_request()
        result = resolve_seek_auth(req, order=["TOKEN"])
        assert result == (None, {"Authorization": "Token tok"})
        mock_basic.assert_not_called()

    @patch("nextseek_api.helpers.get_auth")
    def test_custom_order_session_only(self, mock_session):
        mock_session.return_value = ("u", "p")
        req = _make_request()
        result = resolve_seek_auth(req, order=["SESSION"])
        assert result == (("u", "p"), {})

    @patch("nextseek_api.helpers.get_basic_auth")
    def test_basic_with_empty_username_skipped(self, mock_basic):
        """BASIC auth with empty username should be skipped."""
        mock_basic.return_value = ("", "pass")
        req = _make_request()
        result = resolve_seek_auth(req, order=["BASIC"])
        assert result == (None, None)

    @patch("nextseek_api.helpers.get_basic_auth")
    def test_basic_with_empty_password_skipped(self, mock_basic):
        """BASIC auth with empty password should be skipped."""
        mock_basic.return_value = ("user", "")
        req = _make_request()
        result = resolve_seek_auth(req, order=["BASIC"])
        assert result == (None, None)


# ===========================================================================
# SeekAPIClient
# ===========================================================================
class TestSeekAPIClient:
    """Tests for SeekAPIClient.__init__, _request, and all endpoint methods."""

    @patch("nextseek_api.helpers.settings")
    def _make_client(self, mock_settings=None):
        mock_settings.SEEK_URL = "https://seek.example.com/"
        return SeekAPIClient()

    def test_init_strips_trailing_slash(self):
        client = self._make_client()
        assert client.base_url == "https://seek.example.com"

    def test_init_sets_session_headers(self):
        client = self._make_client()
        assert client.session.headers.get("Accept") == JSONAPI_ACCEPT

    # ---- _request tests ----

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_no_auth_returns_401(self, mock_resolve):
        mock_resolve.return_value = (None, None)
        client = self._make_client()
        req = _make_request()
        content, status, headers, resp = client._request("GET", "/test", req)
        assert status == 401
        assert content is None
        assert resp is None

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_with_basic_auth(self, mock_resolve):
        mock_resolve.return_value = (("user", "pass"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"data": []}'
        mock_resp.headers = {"Content-Type": "application/json"}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        content, status, headers, resp = client._request("GET", "/samples", req)
        assert status == 200
        assert content == b'{"data": []}'
        # Verify session.request was called with correct args
        call_kwargs = client.session.request.call_args
        auth_hdr = call_kwargs.kwargs["headers"]["Authorization"]
        assert auth_hdr.startswith("Basic ")
        decoded = base64.b64decode(auth_hdr.split(" ", 1)[1]).decode("utf-8")
        assert decoded == "user:pass"
        assert "auth" not in call_kwargs.kwargs
        assert call_kwargs.kwargs["url"] == "https://seek.example.com/samples"

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_with_token_auth(self, mock_resolve):
        mock_resolve.return_value = (None, {"Authorization": "Token tok123"})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"{}"
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        content, status, headers, resp = client._request("GET", "/test", req)
        assert status == 200
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Token tok123"
        assert "auth" not in call_kwargs.kwargs

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_with_params_and_json(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.content = b'{"id":1}'
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        content, status, headers, resp = client._request(
            "POST", "/sops", req, params={"page": 1}, json={"data": {}}
        )
        assert status == 201
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["params"] == {"page": 1}
        assert call_kwargs.kwargs["json"] == {"data": {}}

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_content_none_size(self, mock_resolve):
        """When resp.content is None, size should be 0 (not crash)."""
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.content = None
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        content, status, _, _ = client._request("DELETE", "/samples/1", req)
        assert status == 204

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_request_content_len_raises(self, mock_resolve):
        """When len(resp.content) raises, size should be -1 (graceful)."""
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # content is a bytes-like whose len raises
        mock_content = MagicMock()
        mock_content.__len__ = MagicMock(side_effect=Exception("boom"))
        type(mock_resp).content = PropertyMock(return_value=mock_content)
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        # Should not raise — exception on size is caught
        content, status, _, _ = client._request("GET", "/test", req)
        assert status == 200

    # ---- Parametrized endpoint method tests ----

    @pytest.mark.parametrize(
        "method_name,args,expected_method,expected_path",
        [
            # SOPs
            ("list_sops", [None], "GET", "/sops"),
            ("get_sop", ["42"], "GET", "/sops/42"),
            ("create_sop", [{"data": {}}], "POST", "/sops"),
            ("update_sop", ["42", {"data": {}}], "PATCH", "/sops/42"),
            # DataFiles
            ("list_data_files", [None], "GET", "/data_files"),
            ("create_data_file", [{"data": {}}], "POST", "/data_files"),
            ("update_data_file", ["7", {"data": {}}], "PATCH", "/data_files/7"),
            # Projects
            ("list_projects", [None], "GET", "/projects"),
            ("get_project", ["1"], "GET", "/projects/1"),
            ("create_project", [{"data": {}}], "POST", "/projects"),
            ("update_project", ["1", {"data": {}}], "PATCH", "/projects/1"),
            # People
            ("list_people", [None], "GET", "/people"),
            ("get_person", ["5"], "GET", "/people/5"),
            ("get_current_person", [], "GET", "/people/current"),
            ("create_person", [{"data": {}}], "POST", "/people"),
            ("update_person", ["5", {"data": {}}], "PATCH", "/people/5"),
            # Investigations
            ("list_investigations", [None], "GET", "/investigations"),
            ("get_investigation", ["3"], "GET", "/investigations/3"),
            ("create_investigation", [{"data": {}}], "POST", "/investigations"),
            ("update_investigation", ["3", {"data": {}}], "PATCH", "/investigations/3"),
            # Assays
            ("list_assays", [None], "GET", "/assays"),
            ("get_assay", ["9"], "GET", "/assays/9"),
            ("create_assay", [{"data": {}}], "POST", "/assays"),
            ("update_assay", ["9", {"data": {}}], "PATCH", "/assays/9"),
            # SampleTypes
            ("list_sample_types", [None], "GET", "/sample_types"),
            ("get_sample_type", ["2"], "GET", "/sample_types/2"),
            ("create_sample_type", [{"data": {}}], "POST", "/sample_types"),
            ("update_sample_type", ["2", {"data": {}}], "PATCH", "/sample_types/2"),
            # Samples
            ("get_sample", ["10"], "GET", "/samples/10"),
            ("create_sample", [{"data": {}}], "POST", "/samples"),
            ("update_sample", ["10", {"data": {}}], "PATCH", "/samples/10"),
            ("delete_sample", ["10"], "DELETE", "/samples/10"),
        ],
    )
    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_endpoint_methods(self, mock_resolve, method_name, args, expected_method, expected_path):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"{}"
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        method = getattr(client, method_name)
        # Some methods take (request, params), others (request, id), etc.
        # Build the call arguments: request is always first
        method(req, *args)
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["method"] == expected_method
        assert call_kwargs.kwargs["url"].endswith(expected_path)

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_get_data_file_includes_version_param(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"{}"
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        client.get_data_file(req, "5", version=3)
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["params"] == {"version": 3}

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_list_sops_with_params(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"{}"
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        client.list_sops(req, params={"page": 2})
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["params"] == {"page": 2}

    # ---- stream_content_blob ----

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_stream_content_blob_no_auth(self, mock_resolve):
        mock_resolve.return_value = (None, None)
        client = self._make_client()
        req = _make_request()
        status, headers, resp = client.stream_content_blob(req, "/sops/1/content_blobs/2/download")
        assert status == 401
        assert resp is None

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_stream_content_blob_success(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/pdf"}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        status, headers, resp = client.stream_content_blob(req, "/sops/1/content_blobs/2/download")
        assert status == 200
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["stream"] is True

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_stream_content_blob_with_token_auth(self, mock_resolve):
        mock_resolve.return_value = (None, {"Authorization": "Token t"})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        status, headers, resp = client.stream_content_blob(req, "/path")
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Token t"

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_stream_content_blob_custom_accept(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        client.stream_content_blob(req, "/path", accept="application/pdf")
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["headers"]["Accept"] == "application/pdf"

    # ---- upload_content_blob ----

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_upload_content_blob_no_auth(self, mock_resolve):
        mock_resolve.return_value = (None, None)
        client = self._make_client()
        req = _make_request()
        status, headers, resp = client.upload_content_blob(req, "/sops/1/content_blobs/2", b"data", "application/pdf")
        assert status == 401
        assert resp is None

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_upload_content_blob_success(self, mock_resolve):
        mock_resolve.return_value = (("u", "p"), {})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        status, headers, resp = client.upload_content_blob(req, "/sops/1/blobs/2", b"filedata", "application/pdf")
        assert status == 200
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["method"] == "PUT"
        assert call_kwargs.kwargs["data"] == b"filedata"

    @patch("nextseek_api.helpers.resolve_seek_auth")
    def test_upload_content_blob_with_token_auth(self, mock_resolve):
        mock_resolve.return_value = (None, {"Authorization": "Token t"})
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        client.session.request = MagicMock(return_value=mock_resp)
        req = _make_request()
        status, headers, resp = client.upload_content_blob(req, "/path", b"x", "text/plain")
        call_kwargs = client.session.request.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Token t"


# ===========================================================================
# StandardResultsSetPagination
# ===========================================================================
class TestStandardResultsSetPagination:
    def test_defaults(self):
        p = StandardResultsSetPagination()
        assert p.page_size == 100
        assert p.page_size_query_param == "page_size"
        assert p.max_page_size == 1000


# ===========================================================================
# paginate_rows_in_envelope
# ===========================================================================
class TestPaginateRowsInEnvelope:
    def _drf_request(self, path="/fake/"):
        """Build a DRF Request (needed by paginator.paginate_queryset)."""
        from rest_framework.request import Request
        django_req = _rf.get(path)
        return Request(django_req)

    def test_non_list_rows_returns_unchanged(self):
        envelope = {"rows": "not_a_list", "total": 5}
        req = self._drf_request()
        result = paginate_rows_in_envelope(req, envelope)
        assert result["rows"] == "not_a_list"

    def test_missing_rows_key_returns_unchanged(self):
        envelope = {"other": [1, 2, 3]}
        req = self._drf_request()
        result = paginate_rows_in_envelope(req, envelope)
        assert "other" in result

    def test_paginated_list(self):
        envelope = {"rows": list(range(150))}
        req = self._drf_request()
        result = paginate_rows_in_envelope(req, envelope)
        # Default page_size=100, so first page has 100 items
        assert len(result["rows"]) == 100
        assert result["total"] == 150

    def test_total_not_overwritten(self):
        envelope = {"rows": [1, 2, 3], "total": 999}
        req = self._drf_request()
        result = paginate_rows_in_envelope(req, envelope)
        assert result["total"] == 999

    def test_custom_rows_key(self):
        envelope = {"items": [1, 2, 3, 4, 5]}
        req = self._drf_request()
        result = paginate_rows_in_envelope(req, envelope, rows_key="items")
        assert len(result["items"]) == 5
        assert result["total"] == 5


# ===========================================================================
# resolve_sampletype_to_seek_id
# ===========================================================================
class TestResolveSampletypeToSeekId:
    def test_numeric_string_returns_as_is(self):
        assert resolve_sampletype_to_seek_id("42") == "42"

    def test_numeric_int_returns_as_is(self):
        assert resolve_sampletype_to_seek_id(123) == "123"

    @patch("seek.dbtable_sampletype.DBtable_sampletype")
    def test_title_resolved_to_id(self, MockDBtable):
        instance = MockDBtable.return_value
        instance.getSampleTypeID.return_value = 7
        assert resolve_sampletype_to_seek_id("NHP_blood") == "7"
        MockDBtable.assert_called_once_with("DEFAULT")

    @patch("seek.dbtable_sampletype.DBtable_sampletype")
    def test_title_returns_none_on_zero(self, MockDBtable):
        instance = MockDBtable.return_value
        instance.getSampleTypeID.return_value = 0
        assert resolve_sampletype_to_seek_id("Unknown") is None

    @patch("seek.dbtable_sampletype.DBtable_sampletype")
    def test_title_returns_none_on_negative(self, MockDBtable):
        instance = MockDBtable.return_value
        instance.getSampleTypeID.return_value = -1
        assert resolve_sampletype_to_seek_id("Bad") is None

    @patch("seek.dbtable_sampletype.DBtable_sampletype")
    def test_title_returns_none_on_non_int(self, MockDBtable):
        instance = MockDBtable.return_value
        instance.getSampleTypeID.return_value = "nope"
        assert resolve_sampletype_to_seek_id("Bad") is None

    @patch.dict("sys.modules", {"seek.dbtable_sampletype": None})
    def test_import_error_returns_none(self):
        # Setting module to None in sys.modules causes ImportError on import
        assert resolve_sampletype_to_seek_id("NHP") is None

    @patch("seek.dbtable_sampletype.DBtable_sampletype")
    def test_exception_returns_none(self, MockDBtable):
        MockDBtable.side_effect = RuntimeError("boom")
        assert resolve_sampletype_to_seek_id("NHP") is None
