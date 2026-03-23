"""Coverage tests for schema_rag/session.py — targeting uncovered lines 149, 167-169, 184, 199, 203, 212-213, 217-219.

Covers:
- load_session_info: None result, timezone-unaware created_at, duckdb.Error
- delete_session: FileNotFoundError path
- cleanup_expired_sessions: no directory, orphaned files, expired files, exception during process
"""

import os
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


class TestLoadSessionInfo:

    @patch("nextseek_api.schema_rag.session.duckdb")
    @patch("nextseek_api.schema_rag.session.get_db_path_for_session", return_value="/tmp/test.duckdb")
    @patch("nextseek_api.schema_rag.session.os.path.exists", return_value=True)
    def test_returns_none_when_no_result(self, mock_exists, mock_path, mock_duckdb):
        from nextseek_api.schema_rag.session import load_session_info
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_duckdb.connect.return_value = mock_conn
        result = load_session_info("test-session")
        assert result is None

    @patch("nextseek_api.schema_rag.session.duckdb")
    @patch("nextseek_api.schema_rag.session.get_db_path_for_session", return_value="/tmp/test.duckdb")
    @patch("nextseek_api.schema_rag.session.os.path.exists", return_value=True)
    def test_duckdb_error_returns_none(self, mock_exists, mock_path, mock_duckdb):
        from nextseek_api.schema_rag.session import load_session_info
        import duckdb
        mock_duckdb.connect.side_effect = duckdb.Error("corrupt db")
        mock_duckdb.Error = duckdb.Error
        result = load_session_info("test-session")
        assert result is None

    @patch("nextseek_api.schema_rag.session.duckdb")
    @patch("nextseek_api.schema_rag.session.get_db_path_for_session", return_value="/tmp/test.duckdb")
    @patch("nextseek_api.schema_rag.session.os.path.exists", return_value=True)
    def test_timezone_unaware_created_at(self, mock_exists, mock_path, mock_duckdb):
        from nextseek_api.schema_rag.session import load_session_info
        # Simulate timezone-unaware datetime
        naive_dt = datetime(2024, 1, 15, 12, 0, 0)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (
            "test-session", naive_dt, 30, "http://example.com/schema.yaml", 50
        )
        mock_duckdb.connect.return_value = mock_conn
        result = load_session_info("test-session")
        assert result is not None
        assert result.created_at.tzinfo is not None  # Should be timezone-aware now


class TestDeleteSession:

    @patch("nextseek_api.schema_rag.session.get_db_path_for_session", return_value="/tmp/nonexistent.duckdb")
    @patch("nextseek_api.schema_rag.session.os.remove", side_effect=FileNotFoundError)
    def test_file_not_found_silently_ignored(self, mock_remove, mock_path):
        from nextseek_api.schema_rag.session import delete_session
        # Should not raise
        delete_session("nonexistent-session")

    @patch("nextseek_api.schema_rag.session.get_db_path_for_session", return_value="/tmp/existing.duckdb")
    @patch("nextseek_api.schema_rag.session.os.remove")
    def test_file_deleted(self, mock_remove, mock_path):
        from nextseek_api.schema_rag.session import delete_session
        delete_session("existing-session")
        mock_remove.assert_called_once_with("/tmp/existing.duckdb")


class TestCleanupExpiredSessions:

    @patch("nextseek_api.schema_rag.session.os.path.isdir", return_value=False)
    @patch("nextseek_api.schema_rag.session.settings")
    def test_no_directory(self, mock_settings, mock_isdir):
        from nextseek_api.schema_rag.session import cleanup_expired_sessions
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/nonexistent"
        # Should not raise
        cleanup_expired_sessions()

    @patch("nextseek_api.schema_rag.session.delete_session")
    @patch("nextseek_api.schema_rag.session.load_session_info", return_value=None)
    @patch("nextseek_api.schema_rag.session.os.listdir", return_value=["orphan123.duckdb"])
    @patch("nextseek_api.schema_rag.session.os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.session.settings")
    def test_orphaned_file_deleted(self, mock_settings, mock_isdir, mock_listdir, mock_load, mock_delete):
        from nextseek_api.schema_rag.session import cleanup_expired_sessions
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        cleanup_expired_sessions()
        mock_delete.assert_called_once_with("orphan123")

    @patch("nextseek_api.schema_rag.session.delete_session")
    @patch("nextseek_api.schema_rag.session.is_session_expired", return_value=True)
    @patch("nextseek_api.schema_rag.session.load_session_info")
    @patch("nextseek_api.schema_rag.session.os.listdir", return_value=["expired.duckdb"])
    @patch("nextseek_api.schema_rag.session.os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.session.settings")
    def test_expired_session_deleted(self, mock_settings, mock_isdir, mock_listdir, mock_load, mock_expired, mock_delete):
        from nextseek_api.schema_rag.session import cleanup_expired_sessions
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        mock_load.return_value = MagicMock()
        cleanup_expired_sessions()
        mock_delete.assert_called_once_with("expired")

    @patch("nextseek_api.schema_rag.session.load_session_info", side_effect=Exception("corrupt"))
    @patch("nextseek_api.schema_rag.session.os.listdir", return_value=["corrupt.duckdb"])
    @patch("nextseek_api.schema_rag.session.os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.session.settings")
    def test_exception_during_process_continues(self, mock_settings, mock_isdir, mock_listdir, mock_load):
        from nextseek_api.schema_rag.session import cleanup_expired_sessions
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        # Should not raise
        cleanup_expired_sessions()

    @patch("nextseek_api.schema_rag.session.os.listdir", return_value=["readme.txt", "test.csv"])
    @patch("nextseek_api.schema_rag.session.os.path.isdir", return_value=True)
    @patch("nextseek_api.schema_rag.session.settings")
    def test_non_duckdb_files_skipped(self, mock_settings, mock_isdir, mock_listdir):
        from nextseek_api.schema_rag.session import cleanup_expired_sessions
        mock_settings.SCHEMA_RAG_DUCKDB_DIR = "/tmp/duckdb"
        # Should not raise or process anything
        cleanup_expired_sessions()
