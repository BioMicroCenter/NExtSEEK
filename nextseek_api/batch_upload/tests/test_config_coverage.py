"""Coverage tests for batch_upload/config.py — targeting uncovered lines.

Covers lines: 93, 95, 97, 105-106, 120
- Neo4jConfig.from_django_settings: missing NAME, URI, AUTH
"""

import pytest
from unittest.mock import patch
from django.test import override_settings

from nextseek_api.batch_upload.config import Neo4jConfig


class TestNeo4jConfigFromDjangoSettings:

    def setup_method(self):
        """Clear the lru_cache before each test."""
        Neo4jConfig.from_django_settings.cache_clear()

    @override_settings(NEO4J_DATABASE={"NAME": "", "URI": "bolt://localhost:7687", "AUTH": ("neo4j", "password")})
    def test_missing_name(self):
        cfg = Neo4jConfig.from_django_settings()
        assert not cfg.NEO4J_UPLOAD_ENABLED
        assert "NAME" in cfg.MISSING_KEYS

    @override_settings(NEO4J_DATABASE={"NAME": "mydb", "URI": "", "AUTH": ("neo4j", "password")})
    def test_missing_uri(self):
        cfg = Neo4jConfig.from_django_settings()
        assert not cfg.NEO4J_UPLOAD_ENABLED
        assert "URI" in cfg.MISSING_KEYS

    @override_settings(NEO4J_DATABASE={"NAME": "mydb", "URI": "bolt://localhost:7687", "AUTH": ("", "password")})
    def test_missing_auth_user(self):
        cfg = Neo4jConfig.from_django_settings()
        assert not cfg.NEO4J_UPLOAD_ENABLED
        assert any("user" in k.lower() for k in cfg.MISSING_KEYS)

    @override_settings(NEO4J_DATABASE={"NAME": "mydb", "URI": "bolt://localhost:7687", "AUTH": ("neo4j", "")})
    def test_missing_auth_password(self):
        cfg = Neo4jConfig.from_django_settings()
        assert not cfg.NEO4J_UPLOAD_ENABLED
        assert any("password" in k.lower() for k in cfg.MISSING_KEYS)

    def teardown_method(self):
        """Restore clean cache state."""
        Neo4jConfig.from_django_settings.cache_clear()
