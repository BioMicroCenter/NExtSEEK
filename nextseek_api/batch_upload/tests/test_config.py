"""Tests for config.py — BatchUploadConfig, Neo4jConfig, helpers."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.config import (
    BatchUploadConfig,
    Neo4jConfig,
    _env_bool,
    _env_int,
    get_sqlalchemy_url,
)


# ── BatchUploadConfig ────────────────────────────────────────────────────


class TestBatchUploadConfig:
    def test_defaults(self):
        cfg = BatchUploadConfig()
        assert cfg.max_rows_per_batch == 2000
        assert cfg.min_batch_size == 500
        assert cfg.max_batch_size == 5000
        assert cfg.max_payload_bytes == 8_388_608
        assert cfg.target_rows_per_sec == 100.0
        assert cfg.adaptive_batching is True
        assert cfg.parallel_threshold == 5000
        assert cfg.gc_every_n_batches == 10
        assert cfg.clear_caches_every_n_batches == 20
        assert cfg.mem_limit_mb is None
        assert cfg.enable_auto_permissions is False
        assert cfg.permissions_default_access_type == 4
        assert cfg.permissions_default_contributor_type == "Project"
        assert cfg.assay_cache_max == 10_000
        assert cfg.title_to_id_cache_max == 5000
        assert cfg.limit is None
        assert cfg.update_existing is False

    def test_overrides(self):
        cfg = BatchUploadConfig(
            max_rows_per_batch=500,
            min_batch_size=100,
            max_batch_size=1000,
            target_rows_per_sec=50.0,
            adaptive_batching=False,
            limit=1000,
            update_existing=True,
        )
        assert cfg.max_rows_per_batch == 500
        assert cfg.min_batch_size == 100
        assert cfg.max_batch_size == 1000
        assert cfg.target_rows_per_sec == 50.0
        assert cfg.adaptive_batching is False
        assert cfg.limit == 1000
        assert cfg.update_existing is True

    def test_env_var_override_max_rows(self, monkeypatch):
        monkeypatch.setenv("BATCH_UPLOAD_MAX_ROWS", "9999")
        cfg = BatchUploadConfig()
        assert cfg.max_rows_per_batch == 9999

    def test_env_var_override_mem_limit(self, monkeypatch):
        monkeypatch.setenv("BATCH_UPLOAD_MEM_LIMIT_MB", "256")
        cfg = BatchUploadConfig()
        assert cfg.mem_limit_mb == 256

    def test_env_var_override_enable_auto_permissions(self, monkeypatch):
        monkeypatch.setenv("ENABLE_AUTO_PERMISSIONS", "true")
        cfg = BatchUploadConfig()
        assert cfg.enable_auto_permissions is True

    def test_env_var_override_update_existing(self, monkeypatch):
        monkeypatch.setenv("BATCH_UPLOAD_UPDATE_EXISTING", "1")
        cfg = BatchUploadConfig()
        assert cfg.update_existing is True

    def test_to_dict(self):
        cfg = BatchUploadConfig()
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert "max_rows_per_batch" in d
        assert "min_batch_size" in d
        assert d["max_rows_per_batch"] == 2000

    def test_to_dict_excludes_private(self):
        cfg = BatchUploadConfig()
        d = cfg.to_dict()
        for key in d:
            assert not key.startswith("_")


# ── Neo4jConfig ──────────────────────────────────────────────────────────


class TestNeo4jConfig:
    def setup_method(self):
        # Clear lru_cache between tests
        Neo4jConfig.from_django_settings.cache_clear()

    def test_from_django_settings_fully_configured(self):
        Neo4jConfig.from_django_settings.cache_clear()
        mock_settings = MagicMock()
        mock_settings.NEO4J_DATABASE = {
            "NAME": "testdb",
            "URI": "bolt://localhost:7687",
            "AUTH": ("neo4j", "password123"),
        }
        with patch("django.conf.settings", mock_settings):
            cfg = Neo4jConfig.from_django_settings()
            assert cfg.NEO4J_DB == "testdb"
            assert cfg.URI == "bolt://localhost:7687"
            assert cfg.NEO4J_USER == "neo4j"
            assert cfg.PASSWORD == "password123"
            assert cfg.DRIVER_AVAILABLE is True

    def test_frozen_model(self):
        cfg = Neo4jConfig(NEO4J_DB="test", URI="bolt://localhost")
        with pytest.raises(Exception):
            cfg.NEO4J_DB = "changed"

    def test_missing_keys_tracked(self):
        cfg = Neo4jConfig(
            NEO4J_DB="",
            URI="",
            NEO4J_USER="",
            PASSWORD="",
            MISSING_KEYS=["NAME", "URI"],
        )
        assert "NAME" in cfg.MISSING_KEYS
        assert cfg.NEO4J_UPLOAD_ENABLED is False

    def test_default_chunk_sizes(self):
        """Neo4jConfig class-level defaults use os.environ at class definition time."""
        cfg = Neo4jConfig()
        # Default values from class definition
        assert isinstance(cfg.NEO4J_NODE_CHUNK, int)
        assert isinstance(cfg.NEO4J_REL_CHUNK, int)

    def test_explicit_chunk_sizes(self):
        cfg = Neo4jConfig(NEO4J_NODE_CHUNK=500, NEO4J_REL_CHUNK=1000)
        assert cfg.NEO4J_NODE_CHUNK == 500
        assert cfg.NEO4J_REL_CHUNK == 1000


# ── get_sqlalchemy_url ───────────────────────────────────────────────────


class TestGetSqlalchemyUrl:
    def test_builds_url_from_settings(self):
        mock_settings = MagicMock()
        mock_settings.DATABASES = {
            "seek": {
                "HOST": "db.example.com",
                "PORT": "3307",
                "USER": "admin",
                "PASSWORD": "secret",
                "NAME": "seekdb",
            }
        }
        with patch("nextseek_api.batch_upload.config.settings", mock_settings, create=True):
            with patch.dict("sys.modules", {"django.conf": MagicMock(settings=mock_settings)}):
                url = get_sqlalchemy_url()
                assert url == "mysql+mysqldb://admin:secret@db.example.com:3307/seekdb"

    def test_defaults_host_and_port(self):
        mock_settings = MagicMock()
        mock_settings.DATABASES = {
            "seek": {
                "HOST": "",
                "PORT": "",
                "USER": "user",
                "PASSWORD": "pass",
                "NAME": "db",
            }
        }
        with patch("nextseek_api.batch_upload.config.settings", mock_settings, create=True):
            with patch.dict("sys.modules", {"django.conf": MagicMock(settings=mock_settings)}):
                url = get_sqlalchemy_url()
                assert "127.0.0.1" in url
                assert "3306" in url


# ── _env_int / _env_bool ─────────────────────────────────────────────────


class TestEnvHelpers:
    def test_env_int_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "42")
        assert _env_int("TEST_INT_VAR") == 42

    def test_env_int_default_when_unset(self):
        assert _env_int("NONEXISTENT_VAR_1234", 99) == 99

    def test_env_int_none_default(self):
        assert _env_int("NONEXISTENT_VAR_1234") is None

    def test_env_bool_true_values(self, monkeypatch):
        for val in ("1", "true", "True", "TRUE", "yes", "YES"):
            monkeypatch.setenv("TEST_BOOL_VAR", val)
            assert _env_bool("TEST_BOOL_VAR") is True

    def test_env_bool_false_values(self, monkeypatch):
        for val in ("0", "false", "no", "anything_else"):
            monkeypatch.setenv("TEST_BOOL_VAR", val)
            assert _env_bool("TEST_BOOL_VAR") is False

    def test_env_bool_default_when_unset(self):
        assert _env_bool("NONEXISTENT_VAR_1234") is False
        assert _env_bool("NONEXISTENT_VAR_1234", True) is True
