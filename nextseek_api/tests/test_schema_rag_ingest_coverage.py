"""Coverage tests for schema_rag/service.py ingest_schema — targeting uncovered error paths.

Covers lines: 300-301, 316-318, 330-332, 366-368, 381-383
- SchemaFetchError during fetch
- General exception during fetch
- Exception during flatten
- Schema too large
- Exception during session creation
- Exception during insert_endpoints
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from nextseek_api.models import IngestRequest


class TestIngestSchemaErrorPaths:

    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_schema_fetch_error(self, mock_processor_cls, mock_cleanup):
        from nextseek_api.schema_rag.service import ingest_schema
        from nextseek_api.schema_rag.errors import SchemaFetchError
        mock_processor = MagicMock()
        mock_processor.fetch_schema.side_effect = SchemaFetchError("HTTP 404")
        mock_processor_cls.return_value = mock_processor

        req = IngestRequest(schema_url="http://bad-url.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success
        assert "SCHEMA_FETCH_FAILED" in result.error.error_code

    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_general_exception_during_fetch(self, mock_processor_cls, mock_cleanup):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.side_effect = RuntimeError("unexpected")
        mock_processor_cls.return_value = mock_processor

        req = IngestRequest(schema_url="http://bad-url.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success

    @patch("nextseek_api.schema_rag.service.flatten_endpoints", side_effect=Exception("bad endpoints"))
    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_exception_during_flatten(self, mock_processor_cls, mock_cleanup, mock_flatten):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.return_value = {"paths": {}}
        mock_processor_cls.return_value = mock_processor

        req = IngestRequest(schema_url="http://example.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success
        assert "flattening" in (result.error.details or {}).get("phase", "")

    @patch("nextseek_api.schema_rag.service.settings")
    @patch("nextseek_api.schema_rag.service.flatten_endpoints")
    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_schema_too_large(self, mock_processor_cls, mock_cleanup, mock_flatten, mock_settings):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.return_value = {"paths": {}}
        mock_processor_cls.return_value = mock_processor
        # Return too many endpoints
        mock_flatten.return_value = [MagicMock() for _ in range(300)]
        mock_settings.SCHEMA_RAG_MAX_ENDPOINTS = 250

        req = IngestRequest(schema_url="http://example.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success
        assert "SCHEMA_TOO_LARGE" in result.error.error_code

    @patch("nextseek_api.schema_rag.service.create_session", side_effect=Exception("session error"))
    @patch("nextseek_api.schema_rag.service.settings")
    @patch("nextseek_api.schema_rag.service.flatten_endpoints")
    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_exception_during_session_creation(
        self, mock_processor_cls, mock_cleanup, mock_flatten, mock_settings, mock_create
    ):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.return_value = {"paths": {}}
        mock_processor_cls.return_value = mock_processor
        mock_flatten.return_value = [MagicMock()]
        mock_settings.SCHEMA_RAG_MAX_ENDPOINTS = 250
        mock_settings.SCHEMA_RAG_DEFAULT_TTL_MINUTES = 30

        req = IngestRequest(schema_url="http://example.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success
        assert "session" in result.error.message.lower()

    @patch("nextseek_api.schema_rag.service.insert_endpoints", side_effect=Exception("insert error"))
    @patch("nextseek_api.schema_rag.service.init_session_db")
    @patch("nextseek_api.schema_rag.service.create_session")
    @patch("nextseek_api.schema_rag.service.settings")
    @patch("nextseek_api.schema_rag.service.flatten_endpoints")
    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_exception_during_insert(
        self, mock_processor_cls, mock_cleanup, mock_flatten, mock_settings,
        mock_create, mock_init, mock_insert
    ):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.return_value = {"paths": {}}
        mock_processor_cls.return_value = mock_processor
        mock_flatten.return_value = [MagicMock()]
        mock_settings.SCHEMA_RAG_MAX_ENDPOINTS = 250
        mock_settings.SCHEMA_RAG_DEFAULT_TTL_MINUTES = 30
        mock_session = MagicMock()
        mock_session.session_id = "test-session"
        mock_session.created_at = datetime.now(timezone.utc)
        mock_create.return_value = mock_session

        req = IngestRequest(schema_url="http://example.com/schema.yaml")
        result = ingest_schema(req)
        assert not result.success
        assert "store" in result.error.message.lower()

    @patch("nextseek_api.schema_rag.service.insert_endpoints")
    @patch("nextseek_api.schema_rag.service.init_session_db")
    @patch("nextseek_api.schema_rag.service.create_session")
    @patch("nextseek_api.schema_rag.service.settings")
    @patch("nextseek_api.schema_rag.service.flatten_endpoints")
    @patch("nextseek_api.schema_rag.service.cleanup_expired_sessions")
    @patch("nextseek_api.schema_rag.service.OpenAPISchemaProcessor")
    def test_success_path(
        self, mock_processor_cls, mock_cleanup, mock_flatten, mock_settings,
        mock_create, mock_init, mock_insert
    ):
        from nextseek_api.schema_rag.service import ingest_schema
        mock_processor = MagicMock()
        mock_processor.fetch_schema.return_value = {"paths": {"/test": {}}}
        mock_processor_cls.return_value = mock_processor
        mock_flatten.return_value = [MagicMock()]
        mock_settings.SCHEMA_RAG_MAX_ENDPOINTS = 250
        mock_settings.SCHEMA_RAG_DEFAULT_TTL_MINUTES = 30
        mock_session = MagicMock()
        mock_session.session_id = "test-session"
        mock_session.created_at = datetime.now(timezone.utc)
        mock_create.return_value = mock_session

        req = IngestRequest(schema_url="http://example.com/schema.yaml")
        result = ingest_schema(req)
        assert result.success
        assert result.response.session_id == "test-session"
