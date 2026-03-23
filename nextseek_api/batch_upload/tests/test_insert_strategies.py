"""Tests for insert_strategies.py — INSERT RETURNING vs fallback, compute_first_letter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.insert_strategies import (
    compute_first_letter,
    insert_samples,
    insert_samples_fallback_select,
    insert_samples_returning,
)


def _make_payload(n=1, prefix="UID"):
    """Helper to create n row payloads."""
    return [
        {
            "title": f"Sample {i}",
            "sample_type_id": 1,
            "json_metadata": "{}",
            "uuid": f"{prefix}-{i:06d}",
            "contributor_id": 1,
            "first_letter": prefix[0] if prefix else "",
            "policy_id": 1,
        }
        for i in range(n)
    ]


# ── compute_first_letter ─────────────────────────────────────────────────


class TestComputeFirstLetter:
    def test_normal_string(self):
        assert compute_first_letter("hello") == "H"

    def test_uppercase(self):
        assert compute_first_letter("ABC") == "A"

    def test_lowercase(self):
        assert compute_first_letter("abc") == "A"

    def test_empty_string(self):
        assert compute_first_letter("") == ""

    def test_whitespace_only(self):
        assert compute_first_letter("   ") == ""

    def test_numeric_string(self):
        assert compute_first_letter("123") == "1"

    def test_leading_whitespace_stripped(self):
        assert compute_first_letter("  xyz") == "X"

    def test_none_like_empty(self):
        # Passing empty string
        assert compute_first_letter("") == ""


# ── insert_samples (dispatch) ────────────────────────────────────────────


class TestInsertSamplesDispatch:
    def test_empty_payload_returns_empty(self):
        conn = MagicMock()
        result = insert_samples([], conn)
        assert result == []
        conn.execute.assert_not_called()

    @patch("nextseek_api.batch_upload.insert_strategies.CAPABILITIES", {"insert_returning": True})
    @patch("nextseek_api.batch_upload.insert_strategies.insert_samples_returning")
    def test_dispatches_to_returning(self, mock_returning):
        mock_returning.return_value = [(1, "UID-001")]
        conn = MagicMock()
        payload = _make_payload(1)
        result = insert_samples(payload, conn)
        mock_returning.assert_called_once_with(payload, conn)
        assert result == [(1, "UID-001")]

    @patch("nextseek_api.batch_upload.insert_strategies.CAPABILITIES", {"insert_returning": False})
    @patch("nextseek_api.batch_upload.insert_strategies.insert_samples_fallback_select")
    def test_dispatches_to_fallback(self, mock_fallback):
        mock_fallback.return_value = [(1, "UID-001")]
        conn = MagicMock()
        payload = _make_payload(1)
        result = insert_samples(payload, conn)
        mock_fallback.assert_called_once_with(payload, conn)
        assert result == [(1, "UID-001")]


# ── insert_samples_returning ─────────────────────────────────────────────


class TestInsertSamplesReturning:
    def test_single_row(self):
        conn = MagicMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = [(100, "UID-000000")]
        conn.execute.return_value = result_mock

        payload = _make_payload(1)
        result = insert_samples_returning(payload, conn)
        assert result == [(100, "UID-000000")]
        conn.execute.assert_called_once()

    def test_multi_row(self):
        conn = MagicMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = [(1, "UID-000000"), (2, "UID-000001"), (3, "UID-000002")]
        conn.execute.return_value = result_mock

        payload = _make_payload(3)
        result = insert_samples_returning(payload, conn)
        assert len(result) == 3

    def test_sql_contains_returning(self):
        conn = MagicMock()
        result_mock = MagicMock()
        result_mock.fetchall.return_value = [(1, "UID-000000")]
        conn.execute.return_value = result_mock

        insert_samples_returning(_make_payload(1), conn)
        call_args = conn.execute.call_args
        sql_str = str(call_args[0][0])
        assert "RETURNING" in sql_str


# ── insert_samples_fallback_select ───────────────────────────────────────


class TestInsertSamplesFallbackSelect:
    def test_single_row(self):
        conn = MagicMock()
        insert_result = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = [(200, "UID-000000")]
        conn.execute.side_effect = [insert_result, select_result]

        payload = _make_payload(1)
        result = insert_samples_fallback_select(payload, conn)
        assert result == [(200, "UID-000000")]
        assert conn.execute.call_count == 2  # INSERT + SELECT

    def test_multi_row(self):
        conn = MagicMock()
        insert_result = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = [
            (1, "UID-000000"), (2, "UID-000001"),
        ]
        conn.execute.side_effect = [insert_result, select_result]

        payload = _make_payload(2)
        result = insert_samples_fallback_select(payload, conn)
        assert len(result) == 2

    def test_select_query_uses_uuids(self):
        conn = MagicMock()
        insert_result = MagicMock()
        select_result = MagicMock()
        select_result.fetchall.return_value = [(1, "UID-000000")]
        conn.execute.side_effect = [insert_result, select_result]

        insert_samples_fallback_select(_make_payload(1), conn)
        # Second call should be the SELECT with uuid params
        select_call = conn.execute.call_args_list[1]
        params = select_call[0][1]
        assert "u_0" in params
        assert params["u_0"] == "UID-000000"
