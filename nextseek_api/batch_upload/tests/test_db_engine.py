"""Tests for db_engine.py — singleton engine, capabilities detection, connection context."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from nextseek_api.batch_upload import db_engine as db_engine_module
from nextseek_api.batch_upload.db_engine import (
    CAPABILITIES,
    _MARIADB_VERSION_RE,
    _detect_capabilities,
    dispose_engine,
    get_connection,
    get_engine,
)


@pytest.fixture(autouse=True)
def _reset_engine():
    """Reset the engine singleton before and after each test."""
    db_engine_module._engine = None
    CAPABILITIES["insert_returning"] = False
    yield
    db_engine_module._engine = None
    CAPABILITIES["insert_returning"] = False


# ── _MARIADB_VERSION_RE ──────────────────────────────────────────────────


class TestMariaDBVersionRegex:
    def test_matches_mariadb_10_5(self):
        m = _MARIADB_VERSION_RE.search("10.5.23-MariaDB")
        assert m is not None
        assert int(m.group(1)) == 10
        assert int(m.group(2)) == 5

    def test_matches_mariadb_11_0(self):
        m = _MARIADB_VERSION_RE.search("11.0.1-MariaDB")
        assert m is not None
        assert int(m.group(1)) == 11

    def test_no_match_mysql(self):
        m = _MARIADB_VERSION_RE.search("8.0.35")
        assert m is None


# ── _detect_capabilities ─────────────────────────────────────────────────


class TestDetectCapabilities:
    def test_mariadb_10_5_enables_returning(self):
        engine = MagicMock()
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = ("10.5.23-MariaDB",)
        conn.execute.return_value = result
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = conn

        CAPABILITIES["insert_returning"] = False
        _detect_capabilities(engine)
        assert CAPABILITIES["insert_returning"] is True

    def test_mariadb_10_3_disables_returning(self):
        engine = MagicMock()
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = ("10.3.39-MariaDB",)
        conn.execute.return_value = result
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = conn

        _detect_capabilities(engine)
        assert CAPABILITIES["insert_returning"] is False

    def test_non_mariadb_disables_returning(self):
        engine = MagicMock()
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = ("8.0.35",)
        conn.execute.return_value = result
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = conn

        _detect_capabilities(engine)
        assert CAPABILITIES["insert_returning"] is False

    def test_exception_handled_gracefully(self):
        engine = MagicMock()
        engine.connect.side_effect = Exception("Connection failed")

        # Should not raise
        _detect_capabilities(engine)
        assert CAPABILITIES["insert_returning"] is False

    def test_none_version_row(self):
        engine = MagicMock()
        conn = MagicMock()
        result = MagicMock()
        result.fetchone.return_value = None
        conn.execute.return_value = result
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        engine.connect.return_value = conn

        _detect_capabilities(engine)
        assert CAPABILITIES["insert_returning"] is False


# ── get_engine ───────────────────────────────────────────────────────────


class TestGetEngine:
    @patch("nextseek_api.batch_upload.db_engine._detect_capabilities")
    @patch("nextseek_api.batch_upload.db_engine._sa_create_engine")
    @patch("nextseek_api.batch_upload.db_engine.get_sqlalchemy_url")
    def test_creates_engine_once(self, mock_url, mock_create, mock_detect):
        mock_url.return_value = "mysql+mysqldb://user:pass@host/db"
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine

        engine1 = get_engine()
        engine2 = get_engine()
        assert engine1 is engine2
        mock_create.assert_called_once()

    @patch("nextseek_api.batch_upload.db_engine._detect_capabilities")
    @patch("nextseek_api.batch_upload.db_engine._sa_create_engine")
    @patch("nextseek_api.batch_upload.db_engine.get_sqlalchemy_url")
    def test_engine_parameters(self, mock_url, mock_create, mock_detect):
        mock_url.return_value = "mysql+mysqldb://user:pass@host/db"
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine

        get_engine()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_size"] == 20
        assert call_kwargs["max_overflow"] == 10
        assert call_kwargs["pool_pre_ping"] is True


# ── dispose_engine ───────────────────────────────────────────────────────


class TestDisposeEngine:
    @patch("nextseek_api.batch_upload.db_engine._detect_capabilities")
    @patch("nextseek_api.batch_upload.db_engine._sa_create_engine")
    @patch("nextseek_api.batch_upload.db_engine.get_sqlalchemy_url")
    def test_dispose_resets_singleton(self, mock_url, mock_create, mock_detect):
        mock_url.return_value = "mysql+mysqldb://user:pass@host/db"
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine

        get_engine()
        assert db_engine_module._engine is not None

        dispose_engine()
        assert db_engine_module._engine is None
        mock_engine.dispose.assert_called_once()

    def test_dispose_noop_when_no_engine(self):
        # Should not raise
        dispose_engine()
        assert db_engine_module._engine is None


# ── get_connection ───────────────────────────────────────────────────────


class TestGetConnection:
    @patch("nextseek_api.batch_upload.db_engine.get_engine")
    def test_commits_on_success(self, mock_get_engine):
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_trans = MagicMock()
        mock_conn.begin.return_value = mock_trans
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn
        mock_get_engine.return_value = mock_engine

        with get_connection() as conn:
            conn.execute("SELECT 1")

        mock_trans.commit.assert_called_once()
        mock_trans.rollback.assert_not_called()

    @patch("nextseek_api.batch_upload.db_engine.get_engine")
    def test_rollback_on_exception(self, mock_get_engine):
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_trans = MagicMock()
        mock_conn.begin.return_value = mock_trans
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn
        mock_get_engine.return_value = mock_engine

        with pytest.raises(ValueError):
            with get_connection() as conn:
                raise ValueError("test error")

        mock_trans.rollback.assert_called_once()
        mock_trans.commit.assert_not_called()
