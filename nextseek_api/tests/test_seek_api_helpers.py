"""
Tests for nextseek_api/seek_api_helpers.py

Covers:
- Module-level constants (SEEK_API_BASE, PEOPLE_API_BASE, SOPS_API_BASE, HEADERS)
- convert_sample_uid_to_id()
- get_current_logged_in_user()
- call() — GET with/without query_params, JSON decode error, unexpected error
- post_call() — POST with data, JSON decode error, unexpected error
- fetch_current_user() — delegates to call(), handles exception
- list_sops() — delegates to call(), returns result, handles exception
- get_sop() — delegates to call() with id, returns result, handles exception
"""

from unittest.mock import patch, MagicMock

import pytest
import requests

from nextseek_api.seek_api_helpers import (
    convert_sample_uid_to_id,
    get_current_logged_in_user,
    call,
    post_call,
    fetch_current_user,
    list_sops,
    get_sop,
    SEEK_API_BASE,
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

    def test_people_api_base(self):
        assert PEOPLE_API_BASE == SEEK_API_BASE + "/people/"

    def test_sops_api_base(self):
        assert SOPS_API_BASE == SEEK_API_BASE + "/sops/"

    def test_headers_vnd_api_json(self):
        assert HEADERS == {"Accept": "application/vnd.api+json"}


# ===========================================================================
# convert_sample_uid_to_id()
# ===========================================================================
class TestConvertSampleUidToId:
    @patch("nextseek_api.seek_api_helpers.DBtable_sample")
    def test_returns_sample_id(self, MockDB):
        instance = MockDB.return_value
        instance.getSampleID.return_value = 42
        result = convert_sample_uid_to_id("NHP-260225MIT-1")
        assert result == 42
        MockDB.assert_called_once_with()
        instance.getSampleID.assert_called_once_with("NHP-260225MIT-1")

    @patch("nextseek_api.seek_api_helpers.DBtable_sample")
    def test_returns_none_when_not_found(self, MockDB):
        instance = MockDB.return_value
        instance.getSampleID.return_value = None
        result = convert_sample_uid_to_id("INVALID-UID")
        assert result is None


# ===========================================================================
# get_current_logged_in_user()
# ===========================================================================
class TestGetCurrentLoggedInUser:
    @patch("nextseek_api.seek_api_helpers.SeekDB")
    def test_returns_auth_tuple(self, MockSeekDB):
        instance = MockSeekDB.return_value
        instance.getSeekLogin.return_value = {
            "username": "alice",
            "password": "secret",
        }
        request = MagicMock()
        result = get_current_logged_in_user(request)
        assert result == ("alice", "secret")
        MockSeekDB.assert_called_once_with(None, None, None)
        instance.getSeekLogin.assert_called_once_with(request, False)


# ===========================================================================
# call()
# ===========================================================================
class TestCall:
    @patch("nextseek_api.seek_api_helpers.requests.get")
    def test_call_without_params(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [1, 2]}
        mock_get.return_value = mock_resp
        auth = ("u", "p")
        result = call(auth, "https://example.com/api")
        assert result == {"data": [1, 2]}
        mock_get.assert_called_once_with(
            "https://example.com/api", auth=auth, headers=HEADERS
        )

    @patch("nextseek_api.seek_api_helpers.requests.get")
    def test_call_with_params(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_get.return_value = mock_resp
        auth = ("u", "p")
        result = call(auth, "https://example.com/api", query_params={"page": 2})
        assert result == {"results": []}
        mock_get.assert_called_once_with(
            "https://example.com/api",
            auth=auth,
            params={"page": 2},
            headers=HEADERS,
        )

    @patch("nextseek_api.seek_api_helpers.requests.get")
    def test_call_json_decode_error(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = requests.exceptions.JSONDecodeError(
            "msg", "doc", 0
        )
        mock_get.return_value = mock_resp
        result = call(("u", "p"), "https://example.com/api")
        assert result is None
        captured = capsys.readouterr()
        assert "not valid JSON" in captured.out

    @patch("nextseek_api.seek_api_helpers.requests.get")
    def test_call_unexpected_error(self, mock_get, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("unexpected!")
        mock_get.return_value = mock_resp
        result = call(("u", "p"), "https://example.com/api")
        assert result is None
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# post_call()
# ===========================================================================
class TestPostCall:
    @patch("nextseek_api.seek_api_helpers.requests.post")
    def test_post_call_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 99}
        mock_post.return_value = mock_resp
        auth = ("u", "p")
        result = post_call(auth, "https://example.com/api", {"name": "test"})
        assert result == {"id": 99}
        mock_post.assert_called_once_with(
            "https://example.com/api",
            auth=auth,
            json={"name": "test"},
            headers=HEADERS,
        )

    @patch("nextseek_api.seek_api_helpers.requests.post")
    def test_post_call_json_decode_error(self, mock_post, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = requests.exceptions.JSONDecodeError(
            "msg", "doc", 0
        )
        mock_post.return_value = mock_resp
        result = post_call(("u", "p"), "https://example.com/api", {})
        assert result is None
        captured = capsys.readouterr()
        assert "not valid JSON" in captured.out

    @patch("nextseek_api.seek_api_helpers.requests.post")
    def test_post_call_unexpected_error(self, mock_post, capsys):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = RuntimeError("boom")
        mock_post.return_value = mock_resp
        result = post_call(("u", "p"), "https://example.com/api", {})
        assert result is None
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# fetch_current_user()
# ===========================================================================
class TestFetchCurrentUser:
    @patch("nextseek_api.seek_api_helpers.call")
    def test_fetch_current_user_delegates(self, mock_call):
        auth = ("u", "p")
        fetch_current_user(auth)
        mock_call.assert_called_once_with(auth, PEOPLE_API_BASE + "/current")

    @patch("nextseek_api.seek_api_helpers.call", side_effect=Exception("network"))
    def test_fetch_current_user_handles_exception(self, mock_call, capsys):
        fetch_current_user(("u", "p"))
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# list_sops()
# ===========================================================================
class TestListSops:
    @patch("nextseek_api.seek_api_helpers.call")
    def test_list_sops_delegates(self, mock_call):
        mock_call.return_value = [{"id": 1}]
        auth = ("u", "p")
        result = list_sops(auth)
        assert result == [{"id": 1}]
        mock_call.assert_called_once_with(auth, SOPS_API_BASE)

    @patch("nextseek_api.seek_api_helpers.call", side_effect=Exception("fail"))
    def test_list_sops_handles_exception(self, mock_call, capsys):
        result = list_sops(("u", "p"))
        assert result is None
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()


# ===========================================================================
# get_sop()
# ===========================================================================
class TestGetSop:
    @patch("nextseek_api.seek_api_helpers.call")
    def test_get_sop_delegates(self, mock_call):
        mock_call.return_value = {"id": 42}
        auth = ("u", "p")
        result = get_sop(auth, 42)
        assert result == {"id": 42}
        mock_call.assert_called_once_with(auth, SOPS_API_BASE + "42")

    @patch("nextseek_api.seek_api_helpers.call", side_effect=Exception("fail"))
    def test_get_sop_handles_exception(self, mock_call, capsys):
        result = get_sop(("u", "p"), 1)
        assert result is None
        captured = capsys.readouterr()
        assert "unexpected" in captured.out.lower()
