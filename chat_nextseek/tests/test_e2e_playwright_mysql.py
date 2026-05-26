"""MySQL fetcher tests — mocked DB cursor, no real connection."""
import json
from unittest.mock import MagicMock

import pytest

from e2e.playwright.mysql import fetch_chat_session_row


def _fake_config_with_cursor(rows):
    """Return a config-like object whose _connect_db returns a mock conn with predetermined rows."""
    cur = MagicMock()
    cur.fetchone.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    cfg = MagicMock()
    cfg._connect_db.return_value = conn
    return cfg


def test_fetch_returns_chat_log_list():
    extra_state = json.dumps({"chat_log": [{"user_query": "hi", "assistant_reply": "hello"}]})
    cfg = _fake_config_with_cursor((extra_state,))
    result = fetch_chat_session_row(cfg, "abc123", env="dev")
    assert result == [{"user_query": "hi", "assistant_reply": "hello"}]


def test_fetch_returns_empty_list_when_no_chat_log_key():
    extra_state = json.dumps({"other_key": "value"})
    cfg = _fake_config_with_cursor((extra_state,))
    result = fetch_chat_session_row(cfg, "abc123", env="dev")
    assert result == []


def test_fetch_returns_empty_list_when_row_missing():
    cfg = _fake_config_with_cursor(None)
    result = fetch_chat_session_row(cfg, "missing", env="dev")
    assert result == []


def test_fetch_returns_empty_list_when_connection_none():
    cfg = MagicMock()
    cfg._connect_db.return_value = None
    result = fetch_chat_session_row(cfg, "abc123", env="dev")
    assert result == []


def test_fetch_handles_dict_extra_state():
    """Some MySQL drivers auto-decode JSONField columns to dict."""
    extra_state_dict = {"chat_log": [{"user_query": "q", "assistant_reply": "r"}]}
    cfg = _fake_config_with_cursor((extra_state_dict,))
    result = fetch_chat_session_row(cfg, "abc123", env="dev")
    assert result == [{"user_query": "q", "assistant_reply": "r"}]
