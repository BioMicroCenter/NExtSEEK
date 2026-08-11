"""
Tests for nextseek_api/seek_api.py

Covers:
- Module-level constants (SEEK_API_BASE, SAMPLES_API_BASE, PEOPLE_API_BASE, SOPS_API_BASE)
- call() — GET with/without query_params, JSON decode error, unexpected error
- post_call() — POST with data, JSON decode error, unexpected error
- fetch_current_user() — delegates to call(), handles exception
- list_sops() — delegates to call(), handles exception
- get_sop() — delegates to call() with id, handles exception
"""

from unittest.mock import patch, MagicMock

import pytest
import requests


# We need to patch settings.SEEK_URL before importing the module, since it reads
# settings at module level.
@pytest.fixture(autouse=True)
def _patch_seek_url():
    """Ensure SEEK_URL is set before seek_api module constants are evaluated."""
    # The module is already imported with the real settings value, so we just
    # verify it doesn't crash. The actual URL value doesn't matter for tests.
    pass


# Import after ensuring settings are available
from nextseek_api.seek_api import (
    call,
    post_call,
    fetch_current_user,
    list_sops,
    get_sop,
    SEEK_API_BASE,
    SAMPLES_API_BASE,
    PEOPLE_API_BASE,
    SOPS_API_BASE,
    HEADERS,
)


# ===========================================================================
# Module constants
# ===========================================================================
class TestModuleConstants:
    def test_seek_api_base_is_string(self):
        assert isinstance(SEEK_API_BASE, str)

    def test_samples_api_base(self):
        assert SAMPLES_API_BASE == SEEK_API_BASE + "/samples/"

    def test_people_api_base(self):
        assert PEOPLE_API_BASE == SEEK_API_BASE + "/people/"

    def test_sops_api_base(self):
        assert SOPS_API_BASE == SEEK_API_BASE + "/sops/"

    def test_headers(self):
        assert HEADERS == {"Accept": "application/json"}


# ===========================================================================
# call()
# ===========================================================================
class TestCall:
    @patch("nextseek_api.seek_api.requests.get")
    def test_call_without_params(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [1, 2, 3]}
        mock_get.return_value = mock_resp
        auth = ("user", "pass")
        call(auth, "https://example.com/api")
        # Credentials travel as a pre-encoded UTF-8 header, never as requests'
        # Latin-1 auth= (#52).
        mock_get.assert_called_once_with(
            "https://example.com/api",
            headers={**HEADERS, "Authorization": _expected_header(*auth)},
        )

    @patch("nextseek_api.seek_api.requests.get")
    def test_call_with_params(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp
        auth = ("u", "p")
        call(auth, "https://example.com/api", query_params={"page": 2})
        mock_get.assert_called_once_with(
            "https://example.com/api",
            params={"page": 2},
            headers={**HEADERS, "Authorization": _expected_header(*auth)},
        )

    @patch("nextseek_api.seek_api.requests.get")
    def test_call_prints_json(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"key": "value"}
        mock_get.return_value = mock_resp
        call(("u", "p"), "https://example.com/api")
        captured = capsys.readouterr()
        assert "key" in captured.out
        assert "value" in captured.out

    @patch("nextseek_api.seek_api.requests.get")
    def test_call_json_decode_error(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = requests.exceptions.JSONDecodeError(
            "msg", "doc", 0
        )
        mock_get.return_value = mock_resp
        call(("u", "p"), "https://example.com/api")
        captured = capsys.readouterr()
        assert "not valid JSON" in captured.out

    @patch("nextseek_api.seek_api.requests.get")
    def test_call_unexpected_error(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("unexpected!")
        mock_get.return_value = mock_resp
        call(("u", "p"), "https://example.com/api")
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# post_call()
# ===========================================================================
class TestPostCall:
    @patch("nextseek_api.seek_api.requests.post")
    def test_post_call_success(self, mock_post, capsys):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1}
        mock_post.return_value = mock_resp
        auth = ("u", "p")
        post_call(auth, "https://example.com/api", {"name": "test"})
        mock_post.assert_called_once_with(
            "https://example.com/api",
            json={"name": "test"},
            headers={**HEADERS, "Authorization": _expected_header(*auth)},
        )
        captured = capsys.readouterr()
        assert "id" in captured.out

    @patch("nextseek_api.seek_api.requests.post")
    def test_post_call_json_decode_error(self, mock_post, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = requests.exceptions.JSONDecodeError(
            "msg", "doc", 0
        )
        mock_post.return_value = mock_resp
        post_call(("u", "p"), "https://example.com/api", {})
        captured = capsys.readouterr()
        assert "not valid JSON" in captured.out

    @patch("nextseek_api.seek_api.requests.post")
    def test_post_call_unexpected_error(self, mock_post, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = RuntimeError("boom")
        mock_post.return_value = mock_resp
        post_call(("u", "p"), "https://example.com/api", {})
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# fetch_current_user()
# ===========================================================================
class TestFetchCurrentUser:
    @patch("nextseek_api.seek_api.call")
    def test_fetch_current_user_delegates(self, mock_call):
        auth = ("u", "p")
        fetch_current_user(auth)
        mock_call.assert_called_once_with(auth, PEOPLE_API_BASE + "/current")

    @patch("nextseek_api.seek_api.call", side_effect=Exception("network"))
    def test_fetch_current_user_handles_exception(self, mock_call, capsys):
        fetch_current_user(("u", "p"))
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# list_sops()
# ===========================================================================
class TestListSops:
    @patch("nextseek_api.seek_api.call")
    def test_list_sops_delegates(self, mock_call):
        auth = ("u", "p")
        list_sops(auth)
        mock_call.assert_called_once_with(auth, SOPS_API_BASE)

    @patch("nextseek_api.seek_api.call", side_effect=Exception("fail"))
    def test_list_sops_handles_exception(self, mock_call, capsys):
        list_sops(("u", "p"))
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# get_sop()
# ===========================================================================
class TestGetSop:
    @patch("nextseek_api.seek_api.call")
    def test_get_sop_delegates(self, mock_call):
        auth = ("u", "p")
        get_sop(auth, 42)
        mock_call.assert_called_once_with(auth, SOPS_API_BASE + "42")

    @patch("nextseek_api.seek_api.call", side_effect=Exception("fail"))
    def test_get_sop_handles_exception(self, mock_call, capsys):
        get_sop(("u", "p"), 1)
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# Basic auth must be UTF-8 safe (#52)
# ===========================================================================

import base64
from contextlib import contextmanager

NON_LATIN1_AUTH = ("jörg", "pa55wörd-✓-Ω")


@contextmanager
def _captured_send():
    """Real request preparation (where Latin-1 auth encoding happens), no socket."""
    sent = {}

    def fake_send(self, request, **kwargs):
        sent["request"] = request
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'{"ok": true}'
        resp.headers["Content-Type"] = "application/json"
        resp.url = request.url
        return resp

    with patch("requests.adapters.HTTPAdapter.send", fake_send):
        yield sent


def _expected_header(user, password):
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


class TestUtf8BasicAuth:
    """requests' auth= encodes credentials as Latin-1 and raises
    UnicodeEncodeError on anything outside it; send a UTF-8 header instead."""

    def test_call_sends_utf8_authorization(self):
        with _captured_send() as sent:
            call(NON_LATIN1_AUTH, "http://seek.example/sops/")
        assert sent["request"].headers["Authorization"] == _expected_header(*NON_LATIN1_AUTH)

    def test_call_with_query_params_sends_utf8_authorization(self):
        with _captured_send() as sent:
            call(NON_LATIN1_AUTH, "http://seek.example/sops/", {"page": "1"})
        assert sent["request"].headers["Authorization"] == _expected_header(*NON_LATIN1_AUTH)

    def test_post_call_sends_utf8_authorization(self):
        with _captured_send() as sent:
            post_call(NON_LATIN1_AUTH, "http://seek.example/sops/", {"a": 1})
        assert sent["request"].headers["Authorization"] == _expected_header(*NON_LATIN1_AUTH)

    def test_accept_header_is_preserved_alongside_auth(self):
        with _captured_send() as sent:
            call(NON_LATIN1_AUTH, "http://seek.example/sops/")
        assert sent["request"].headers["Accept"] == HEADERS["Accept"]

    def test_no_auth_still_works_and_sends_no_authorization(self):
        with _captured_send() as sent:
            call(None, "http://seek.example/sops/")
        assert "Authorization" not in sent["request"].headers
