"""Coverage tests for schema_rag/service.py retrieve_endpoints — targeting uncovered lines.

Covers lines: 746-757 (auto-ingest), 786-787 (embedding failed)
"""

import numpy as np
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from nextseek_api.models import RetrieveRequest


class TestRetrieveEndpointsAutoIngest:

    @patch("nextseek_api.schema_rag.service.ingest_schema")
    @patch("nextseek_api.schema_rag.service._find_session_by_schema_url", return_value=None)
    @patch("nextseek_api.schema_rag.service.load_session_info", return_value=None)
    @patch("nextseek_api.schema_rag.service._now", return_value=datetime.now(timezone.utc))
    def test_auto_ingest_failure(self, mock_now, mock_load, mock_find, mock_ingest):
        """When auto-ingest fails, return error response (lines 752-762)."""
        from nextseek_api.schema_rag.service import retrieve_endpoints
        from nextseek_api.schema_rag.errors import SCHEMA_FETCH_FAILED

        mock_ingest.return_value = MagicMock(
            success=False,
            error=MagicMock(
                message="Fetch failed",
                error_code=SCHEMA_FETCH_FAILED,
            ),
        )

        req = RetrieveRequest(
            schema_url="http://example.com/schema.yaml",
            query="How do I create a sample?",
            mode="minimal",
        )
        response = retrieve_endpoints(req)
        assert response.message == "Fetch failed"

    @patch("nextseek_api.schema_rag.service.embed_texts", return_value=np.array([]))
    @patch("nextseek_api.schema_rag.service.is_session_expired", return_value=False)
    @patch("nextseek_api.schema_rag.service.load_session_info")
    @patch("nextseek_api.schema_rag.service._now", return_value=datetime.now(timezone.utc))
    def test_embedding_failed(self, mock_now, mock_load, mock_expired, mock_embed):
        """When embedding returns empty array (lines 786-787)."""
        from nextseek_api.schema_rag.service import retrieve_endpoints
        from nextseek_api.schema_rag.errors import EMBEDDING_FAILED

        mock_session = MagicMock()
        mock_session.session_id = "test-session"
        mock_load.return_value = mock_session

        req = RetrieveRequest(
            session_id="test-session",
            query="test",
            mode="minimal",
        )
        response = retrieve_endpoints(req)
        assert response.debug.error_code == EMBEDDING_FAILED

    @patch("nextseek_api.schema_rag.service.load_minimal_endpoints_by_ids")
    @patch("nextseek_api.schema_rag.service._pass_1", return_value=[("op1", 0.9)])
    @patch("nextseek_api.schema_rag.service.embed_texts")
    @patch("nextseek_api.schema_rag.service.is_session_expired", return_value=False)
    @patch("nextseek_api.schema_rag.service.load_session_info")
    @patch("nextseek_api.schema_rag.service.ingest_schema")
    @patch("nextseek_api.schema_rag.service._find_session_by_schema_url", return_value=None)
    @patch("nextseek_api.schema_rag.service._now", return_value=datetime.now(timezone.utc))
    @patch("nextseek_api.schema_rag.service.settings")
    def test_auto_ingest_success(
        self, mock_settings, mock_now, mock_find, mock_ingest, mock_load,
        mock_expired, mock_embed, mock_pass1, mock_load_by_ids
    ):
        """When auto-ingest succeeds, load new session (lines 746-751)."""
        from nextseek_api.schema_rag.service import retrieve_endpoints
        from nextseek_api.schema_rag.models import MinimalAPIEndpoint

        mock_settings.SCHEMA_RAG_MAX_TOP_K = 10
        mock_settings.SCHEMA_RAG_MAX_ENDPOINTS = 250

        mock_ingest.return_value = MagicMock(
            success=True,
            response=MagicMock(session_id="new-session"),
        )
        mock_session = MagicMock()
        mock_session.session_id = "new-session"
        mock_load.return_value = mock_session

        mock_embed.return_value = np.array([[0.1, 0.2]])
        ep = MinimalAPIEndpoint(
            operationId="op1", method="GET", path="/test", tags=None, description="test"
        )
        mock_load_by_ids.return_value = [ep]

        req = RetrieveRequest(
            schema_url="http://example.com/schema.yaml",
            query="test",
            mode="minimal",
        )
        response = retrieve_endpoints(req)
        assert response.session_id == "new-session"
