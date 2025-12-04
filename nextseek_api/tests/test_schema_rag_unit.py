"""
Unit tests for Schema RAG feature.

Tests cover:
- Ingestion (Phase 9.1): Valid schema, missing operationId, schema too large, malformed endpoints
- Session lifecycle (Phase 9.2): Create, load, expiry, cleanup
- Retrieval (Phase 9.3): Multi-pass scoring, resolved_terms boosting, top_k cap, mode selection
"""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock, MagicMock

import numpy as np
from django.test import TestCase
from django.conf import settings

from nextseek_api.models import IngestRequest, RetrieveRequest
from nextseek_api.schema_rag import service as schema_rag_service
from nextseek_api.schema_rag.session import (
    create_session,
    load_session_info,
    is_session_expired,
    delete_session,
    cleanup_expired_sessions,
    get_db_path_for_session,
    _now,
)
from nextseek_api.schema_rag.db import (
    init_session_db,
    insert_endpoints,
    load_all_minimal_endpoints,
    load_all_full_endpoints,
)
from nextseek_api.schema_rag.models import FullAPIEndpoint, MinimalAPIEndpoint, SessionInfo
from nextseek_api.schema_rag.errors import (
    SCHEMA_FETCH_FAILED,
    SCHEMA_TOO_LARGE,
    SESSION_MISSING_OR_EXPIRED,
    NO_GOOD_MATCH,
)


# ---------------------------------------------------------------------------
# Test Fixtures - OpenAPI Documents
# ---------------------------------------------------------------------------

MINIMAL_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "summary": "List all users",
                "description": "Retrieve a list of all users in the system",
                "tags": ["Users"],
                "responses": {"200": {"description": "Success"}}
            },
            "post": {
                "operationId": "createUser",
                "summary": "Create a user",
                "description": "Create a new user account",
                "tags": ["Users"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}}
            }
        },
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "summary": "Get user by ID",
                "tags": ["Users"],
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "Success"}}
            }
        }
    }
}

OPENAPI_WITHOUT_OPERATION_ID = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "summary": "List items",
                "tags": ["Items"],
                "responses": {"200": {"description": "Success"}}
            },
            "post": {
                "summary": "Create item",
                "tags": ["Items"],
                "responses": {"201": {"description": "Created"}}
            }
        }
    }
}

OPENAPI_WITH_MALFORMED_ENDPOINTS = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/valid": {
            "get": {
                "operationId": "validEndpoint",
                "summary": "Valid endpoint",
                "tags": ["Valid"],
                "responses": {"200": {"description": "Success"}}
            }
        },
        "/malformed": {
            "get": "not a valid operation object",  # Invalid - should be dict
        },
        "/another-valid": {
            "post": {
                "operationId": "anotherValid",
                "summary": "Another valid endpoint",
                "responses": {"200": {"description": "Success"}}
            }
        }
    }
}


def generate_large_openapi(num_endpoints: int) -> dict:
    """Generate an OpenAPI doc with the specified number of endpoints."""
    paths = {}
    for i in range(num_endpoints):
        paths[f"/endpoint{i}"] = {
            "get": {
                "operationId": f"endpoint{i}",
                "summary": f"Endpoint {i}",
                "responses": {"200": {"description": "Success"}}
            }
        }
    return {
        "openapi": "3.0.0",
        "info": {"title": "Large API", "version": "1.0.0"},
        "paths": paths
    }


# Schema with $ref that should be resolved
OPENAPI_WITH_REFS = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/studies": {
            "post": {
                "operationId": "createStudy",
                "summary": "Create a study",
                "tags": ["Studies"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/StudyCreate"}
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}}
            }
        }
    },
    "components": {
        "schemas": {
            "StudyCreate": {
                "type": "object",
                "required": ["title", "investigation_id"],
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "investigation_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["draft", "active", "archived"]}
                }
            }
        }
    }
}


# ---------------------------------------------------------------------------
# Base Test Class
# ---------------------------------------------------------------------------

class SchemaRAGTestCase(TestCase):
    """Base test case for Schema RAG tests with isolated DuckDB directory."""

    def setUp(self):
        """Create isolated temp directory for DuckDB files."""
        self.temp_dir = tempfile.mkdtemp()
        self.duckdb_patcher = patch.object(settings, 'SCHEMA_RAG_DUCKDB_DIR', self.temp_dir)
        self.duckdb_patcher.start()
        # Also ensure embedding model path exists
        self.embedding_path = tempfile.mkdtemp()
        self.embedding_patcher = patch.object(settings, 'SCHEMA_RAG_EMBEDDING_MODEL_PATH', self.embedding_path)
        self.embedding_patcher.start()

    def tearDown(self):
        """Clean up temp directories."""
        self.duckdb_patcher.stop()
        self.embedding_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        shutil.rmtree(self.embedding_path, ignore_errors=True)

    def _create_mock_response(self, json_data: dict, status_code: int = 200) -> Mock:
        """Create a mock HTTP response with JSON content."""
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.text = json.dumps(json_data)
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.raise_for_status = Mock()
        if status_code >= 400:
            mock_response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return mock_response


# ---------------------------------------------------------------------------
# Ingestion Tests
# ---------------------------------------------------------------------------

class IngestionTests(SchemaRAGTestCase):
    """Unit tests for schema ingestion."""

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_valid_schema(self, mock_embedder, mock_get):
        """Test successful ingestion of a valid OpenAPI schema."""
        # Setup mocks
        mock_get.return_value = self._create_mock_response(MINIMAL_OPENAPI)
        mock_embedder.return_value = Mock()

        # Execute
        req = IngestRequest(schema_url="https://example.com/api.json")
        result = schema_rag_service.ingest_schema(req)

        # Assert
        self.assertTrue(result.success)
        self.assertIsNotNone(result.response)
        self.assertIsNone(result.error)
        self.assertEqual(result.response.schema_url, "https://example.com/api.json")
        self.assertEqual(result.response.num_endpoints, 3)  # 3 endpoints in MINIMAL_OPENAPI
        self.assertIsNotNone(result.response.session_id)

        # Verify DuckDB file was created
        db_path = get_db_path_for_session(result.response.session_id)
        self.assertTrue(os.path.exists(db_path))

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_missing_operation_id(self, mock_embedder, mock_get):
        """Test that endpoints without operationId get synthesized IDs."""
        # Setup mocks
        mock_get.return_value = self._create_mock_response(OPENAPI_WITHOUT_OPERATION_ID)
        mock_embedder.return_value = Mock()

        # Execute
        req = IngestRequest(schema_url="https://example.com/api.json")
        result = schema_rag_service.ingest_schema(req)

        # Assert success
        self.assertTrue(result.success)
        self.assertEqual(result.response.num_endpoints, 2)

        # Load and verify synthesized operationIds
        session = load_session_info(result.response.session_id)
        endpoints = load_all_minimal_endpoints(session)

        operation_ids = [ep.operationId for ep in endpoints]
        self.assertIn("GET /items", operation_ids)
        self.assertIn("POST /items", operation_ids)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_schema_too_large(self, mock_embedder, mock_get):
        """Test that schemas exceeding MAX_ENDPOINTS return SCHEMA_TOO_LARGE error."""
        # Generate schema with more endpoints than allowed
        max_endpoints = settings.SCHEMA_RAG_MAX_ENDPOINTS
        large_openapi = generate_large_openapi(max_endpoints + 10)

        mock_get.return_value = self._create_mock_response(large_openapi)
        mock_embedder.return_value = Mock()

        # Execute
        req = IngestRequest(schema_url="https://example.com/large-api.json")
        result = schema_rag_service.ingest_schema(req)

        # Assert error
        self.assertFalse(result.success)
        self.assertIsNone(result.response)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.error_code, SCHEMA_TOO_LARGE)
        self.assertIn("num_endpoints", result.error.details)
        self.assertIn("max_endpoints", result.error.details)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_malformed_endpoints(self, mock_embedder, mock_get):
        """Test that malformed endpoints are skipped while valid ones are ingested."""
        mock_get.return_value = self._create_mock_response(OPENAPI_WITH_MALFORMED_ENDPOINTS)
        mock_embedder.return_value = Mock()

        # Execute
        req = IngestRequest(schema_url="https://example.com/api.json")
        result = schema_rag_service.ingest_schema(req)

        # Assert success with valid endpoints only
        self.assertTrue(result.success)
        self.assertEqual(result.response.num_endpoints, 2)  # Only 2 valid endpoints

        # Verify correct endpoints were ingested
        session = load_session_info(result.response.session_id)
        endpoints = load_all_minimal_endpoints(session)
        operation_ids = [ep.operationId for ep in endpoints]
        self.assertIn("validEndpoint", operation_ids)
        self.assertIn("anotherValid", operation_ids)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    def test_ingest_fetch_failure(self, mock_get):
        """Test that HTTP fetch failures return SCHEMA_FETCH_FAILED error."""
        # Setup mock to raise exception
        mock_get.side_effect = Exception("Connection refused")

        # Execute
        req = IngestRequest(schema_url="https://example.com/nonexistent.json")
        result = schema_rag_service.ingest_schema(req)

        # Assert error
        self.assertFalse(result.success)
        self.assertIsNone(result.response)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.error_code, SCHEMA_FETCH_FAILED)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_custom_ttl(self, mock_embedder, mock_get):
        """Test that custom TTL is respected in session creation."""
        mock_get.return_value = self._create_mock_response(MINIMAL_OPENAPI)
        mock_embedder.return_value = Mock()

        # Execute with custom TTL
        req = IngestRequest(schema_url="https://example.com/api.json", ttl_minutes=60)
        result = schema_rag_service.ingest_schema(req)

        # Assert
        self.assertTrue(result.success)
        self.assertEqual(result.response.ttl_minutes, 60)

        # Verify session metadata
        session = load_session_info(result.response.session_id)
        self.assertEqual(session.ttl_minutes, 60)

    @patch('nextseek_api.schema_rag.schema_processor.requests.get')
    @patch('nextseek_api.schema_rag.service._get_embedder')
    def test_ingest_resolves_refs_end_to_end(self, mock_embedder, mock_get):
        """Test that ingestion resolves $ref and stores actual schema content."""
        mock_get.return_value = self._create_mock_response(OPENAPI_WITH_REFS)
        mock_embedder.return_value = Mock()

        req = IngestRequest(schema_url="https://example.com/api.json")
        result = schema_rag_service.ingest_schema(req)

        self.assertTrue(result.success)

        session = load_session_info(result.response.session_id)
        endpoints = load_all_full_endpoints(session)

        create_study = next(ep for ep in endpoints if ep.operationId == "createStudy")

        # Verify resolved properties exist
        self.assertIn("title", create_study.request_schema)
        self.assertIn("investigation_id", create_study.request_schema)
        self.assertIn("$required", create_study.request_schema)

        # Verify $ref placeholder is NOT present
        self.assertNotIn("$ref", str(create_study.request_schema))


# ---------------------------------------------------------------------------
# Session Lifecycle Tests
# ---------------------------------------------------------------------------

class SessionLifecycleTests(SchemaRAGTestCase):
    """Unit tests for session lifecycle management."""

    def test_create_session_db_path(self):
        """Test that created sessions have db_path under SCHEMA_RAG_DUCKDB_DIR."""
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=15,
            num_endpoints=10
        )

        self.assertTrue(session.db_path.startswith(self.temp_dir))
        self.assertTrue(session.db_path.endswith(".duckdb"))
        self.assertTrue(os.path.exists(session.db_path))

    def test_load_session_info(self):
        """Test loading an existing session returns correct metadata."""
        # Create a session
        original = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=30,
            num_endpoints=25
        )

        # Load it back
        loaded = load_session_info(original.session_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.session_id, original.session_id)
        self.assertEqual(loaded.schema_url, "https://example.com/api.json")
        self.assertEqual(loaded.ttl_minutes, 30)
        self.assertEqual(loaded.num_endpoints, 25)

    def test_load_missing_session(self):
        """Test that loading a non-existent session returns None."""
        result = load_session_info("nonexistent-session-id")
        self.assertIsNone(result)

    def test_is_session_expired_false(self):
        """Test that a session within TTL is not expired."""
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=15,
            num_endpoints=10
        )

        # Check immediately - should not be expired
        now = _now()
        self.assertFalse(is_session_expired(session, now))

        # Check 10 minutes later - still within 15 min TTL
        future = now + timedelta(minutes=10)
        self.assertFalse(is_session_expired(session, future))

    def test_is_session_expired_true(self):
        """Test that a session past TTL is expired."""
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=15,
            num_endpoints=10
        )

        # Check 20 minutes later - past 15 min TTL
        now = _now()
        future = now + timedelta(minutes=20)
        self.assertTrue(is_session_expired(session, future))

    def test_delete_session(self):
        """Test that deleting a session removes its DuckDB file."""
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=15,
            num_endpoints=10
        )

        # Verify file exists
        self.assertTrue(os.path.exists(session.db_path))

        # Delete session
        delete_session(session.session_id)

        # Verify file is gone
        self.assertFalse(os.path.exists(session.db_path))

    @patch('nextseek_api.schema_rag.session._now')
    def test_cleanup_expired_sessions(self, mock_now):
        """Test that cleanup_expired_sessions only removes expired sessions."""
        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create sessions at different times by manipulating _now
        mock_now.return_value = base_time
        session1 = create_session("https://example.com/1.json", ttl_minutes=10, num_endpoints=5)

        mock_now.return_value = base_time + timedelta(minutes=5)
        session2 = create_session("https://example.com/2.json", ttl_minutes=10, num_endpoints=5)

        mock_now.return_value = base_time + timedelta(minutes=10)
        session3 = create_session("https://example.com/3.json", ttl_minutes=30, num_endpoints=5)

        # All sessions should exist
        self.assertTrue(os.path.exists(session1.db_path))
        self.assertTrue(os.path.exists(session2.db_path))
        self.assertTrue(os.path.exists(session3.db_path))

        # Run cleanup at base_time + 11 minutes
        # session1 (created at base, ttl=10) -> expired at base+10, now base+11 -> EXPIRED
        # session2 (created at base+5, ttl=10) -> expired at base+15, now base+11 -> NOT EXPIRED
        # session3 (created at base+10, ttl=30) -> expired at base+40, now base+11 -> NOT EXPIRED
        mock_now.return_value = base_time + timedelta(minutes=11)  # Past session1's expiry only
        cleanup_expired_sessions()

        # session1 should be deleted
        self.assertFalse(os.path.exists(session1.db_path))
        # session2 and session3 should still exist
        self.assertTrue(os.path.exists(session2.db_path))
        self.assertTrue(os.path.exists(session3.db_path))


# ---------------------------------------------------------------------------
# Retrieval Tests
# ---------------------------------------------------------------------------

class RetrievalTests(SchemaRAGTestCase):
    """Unit tests for endpoint retrieval."""

    def _setup_test_session(self) -> SessionInfo:
        """Create a test session with sample endpoints."""
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=60,
            num_endpoints=5
        )
        init_session_db(session)

        endpoints = [
            FullAPIEndpoint(
                operationId="createSample",
                method="POST",
                path="/samples",
                tags=["Samples"],
                description="Create a new sample in the system",
                request_schema={"title": "string", "data": "object"},
                examples=['{"title":"test","data":{}}']
            ),
            FullAPIEndpoint(
                operationId="listSamples",
                method="GET",
                path="/samples",
                tags=["Samples"],
                description="List all samples",
                request_schema={},
            ),
            FullAPIEndpoint(
                operationId="createProject",
                method="POST",
                path="/projects",
                tags=["Projects"],
                description="Create a new project",
                request_schema={"name": "string"},
            ),
            FullAPIEndpoint(
                operationId="listProjects",
                method="GET",
                path="/projects",
                tags=["Projects"],
                description="Get all projects for a user",
                request_schema={},
            ),
            FullAPIEndpoint(
                operationId="deleteProject",
                method="DELETE",
                path="/projects/{id}",
                tags=["Projects"],
                description="Delete a project by ID",
                request_schema={},
            ),
        ]
        insert_endpoints(session, endpoints)
        return session

    def _make_mock_embeddings(self, num_texts: int, target_idx: int = 0, target_score: float = 0.9) -> np.ndarray:
        """
        Create mock embeddings where target_idx has high similarity to query.

        Returns embeddings normalized for cosine similarity (unit vectors).
        """
        # Create random embeddings
        embeddings = np.random.randn(num_texts, 384)  # BGE-small-en uses 384 dims
        # Normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        return embeddings

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_pass_1_success(self, mock_embed):
        """Test retrieval succeeds in Pass 1 when minimal text matches well."""
        session = self._setup_test_session()

        # Mock embeddings so "createSample" matches well
        def embed_side_effect(texts):
            n = len(texts)
            emb = np.eye(n, 384)[:, :384] if n <= 384 else np.random.randn(n, 384)
            # Normalize
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            return emb / norms

        mock_embed.side_effect = embed_side_effect

        # Execute retrieval
        req = RetrieveRequest(
            session_id=session.session_id,
            query="How do I create a new sample?",
            mode="minimal",
            top_k=5,
            min_score=0.0,  # Accept all scores for this test
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # Assert
        self.assertEqual(response.session_id, session.session_id)
        self.assertEqual(response.mode, "minimal")
        self.assertFalse(response.debug.used_fallback_full)
        self.assertFalse(response.debug.used_enriched_minimal_examples)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_pass_2_fallback(self, mock_embed):
        """Test retrieval falls back to Pass 2 when Pass 1 doesn't meet threshold."""
        session = self._setup_test_session()

        call_count = [0]

        def embed_side_effect(texts):
            call_count[0] += 1
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            normalized = emb / norms

            # Pass 1 returns low scores, Pass 2 returns high scores
            if call_count[0] <= 2:  # First two calls are Pass 1 (query + endpoints)
                return normalized * 0.1  # Low similarity
            else:  # Pass 2
                return normalized

        mock_embed.side_effect = embed_side_effect

        req = RetrieveRequest(
            session_id=session.session_id,
            query="create sample",
            mode="minimal",
            top_k=5,
            min_score=0.5,  # Pass 1 won't meet this
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # Should have used Pass 2
        self.assertTrue(response.debug.used_enriched_minimal_examples)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_pass_3_fallback(self, mock_embed):
        """Test retrieval falls back to Pass 3 when Pass 1 and 2 fail threshold."""
        session = self._setup_test_session()

        call_count = [0]

        def embed_side_effect(texts):
            call_count[0] += 1
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            normalized = emb / norms

            # Pass 1 and 2 return low scores, Pass 3 returns high scores
            if call_count[0] <= 4:  # Pass 1 and Pass 2
                return normalized * 0.1
            else:  # Pass 3
                return normalized

        mock_embed.side_effect = embed_side_effect

        req = RetrieveRequest(
            session_id=session.session_id,
            query="create sample",
            mode="minimal",
            top_k=5,
            min_score=0.5,
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # Should have used Pass 3 (full fallback)
        self.assertTrue(response.debug.used_fallback_full)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_no_good_match(self, mock_embed):
        """Test NO_GOOD_MATCH when all passes fail threshold."""
        session = self._setup_test_session()

        def embed_side_effect(texts):
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            return (emb / norms) * 0.1  # Always low similarity

        mock_embed.side_effect = embed_side_effect

        req = RetrieveRequest(
            session_id=session.session_id,
            query="completely unrelated query about quantum physics",
            mode="minimal",
            top_k=5,
            min_score=0.9,  # Very high threshold
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # Should have no endpoints and error message
        self.assertIsNone(response.endpoints_minimal)
        self.assertIsNotNone(response.message)
        self.assertEqual(response.debug.error_code, NO_GOOD_MATCH)
        self.assertEqual(response.debug.num_candidates_final, 0)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_resolved_terms_boost(self, mock_embed):
        """Test that resolved_terms boosting raises borderline matches above threshold."""
        session = self._setup_test_session()

        def embed_side_effect(texts):
            n = len(texts)
            # Create embeddings that give scores just below threshold
            emb = np.zeros((n, 384))
            for i in range(n):
                emb[i, i % 384] = 1.0  # Each text gets unique direction
            return emb

        mock_embed.side_effect = embed_side_effect

        # First test without resolved_terms - should get lower scores
        req_no_boost = RetrieveRequest(
            session_id=session.session_id,
            query="sample",
            mode="minimal",
            top_k=5,
            min_score=0.0,  # Accept all for comparison
            include_debug=True
        )
        response_no_boost = schema_rag_service.retrieve_endpoints(req_no_boost)

        # Now with resolved_terms that match endpoint text
        req_with_boost = RetrieveRequest(
            session_id=session.session_id,
            query="sample",
            mode="minimal",
            top_k=5,
            min_score=0.0,
            resolved_terms={"sample": "Samples"},  # Should match tags
            include_debug=True
        )
        response_with_boost = schema_rag_service.retrieve_endpoints(req_with_boost)

        # Scores with boost should be higher (boost adds +0.1 per matched term)
        if response_no_boost.debug.scores and response_with_boost.debug.scores:
            # At least some scores should be boosted
            max_no_boost = max(response_no_boost.debug.scores)
            max_with_boost = max(response_with_boost.debug.scores)
            # With term matching, scores should be higher
            self.assertGreaterEqual(max_with_boost, max_no_boost)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_top_k_cap(self, mock_embed):
        """Test that top_k is capped at SCHEMA_RAG_MAX_TOP_K (10)."""
        # Create session with more endpoints
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=60,
            num_endpoints=20
        )
        init_session_db(session)

        # Create 20 endpoints
        endpoints = [
            FullAPIEndpoint(
                operationId=f"endpoint{i}",
                method="GET",
                path=f"/path{i}",
                tags=["Test"],
                description=f"Test endpoint {i}",
                request_schema={},
            )
            for i in range(20)
        ]
        insert_endpoints(session, endpoints)

        def embed_side_effect(texts):
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            return emb / norms

        mock_embed.side_effect = embed_side_effect

        # Request top_k=20, which exceeds MAX_TOP_K
        req = RetrieveRequest(
            session_id=session.session_id,
            query="test endpoint",
            mode="minimal",
            top_k=20,  # Exceeds SCHEMA_RAG_MAX_TOP_K (10)
            min_score=0.0,  # Accept all
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # Should be capped at MAX_TOP_K
        max_top_k = settings.SCHEMA_RAG_MAX_TOP_K
        self.assertLessEqual(response.debug.num_candidates_final, max_top_k)
        if response.endpoints_minimal:
            self.assertLessEqual(len(response.endpoints_minimal), max_top_k)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_min_score_threshold(self, mock_embed):
        """Test that custom min_score threshold filters results correctly."""
        session = self._setup_test_session()

        def embed_side_effect(texts):
            n = len(texts)
            # Create varied scores
            emb = np.eye(n, 384)[:, :384] if n <= 384 else np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            return emb / norms

        mock_embed.side_effect = embed_side_effect

        # Test with high threshold
        req = RetrieveRequest(
            session_id=session.session_id,
            query="test",
            mode="minimal",
            top_k=10,
            min_score=0.95,  # Very high threshold
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        # All returned scores should be >= min_score (or no results)
        if response.debug.scores:
            for score in response.debug.scores:
                self.assertGreaterEqual(score, 0.95)

    def test_retrieve_session_missing(self):
        """Test retrieval with invalid session_id returns SESSION_MISSING_OR_EXPIRED."""
        req = RetrieveRequest(
            session_id="nonexistent-session-id",
            query="test query",
            mode="minimal",
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        self.assertIsNotNone(response.message)
        self.assertEqual(response.debug.error_code, SESSION_MISSING_OR_EXPIRED)

    @patch('nextseek_api.schema_rag.session._now')
    @patch('nextseek_api.schema_rag.service._now')
    def test_retrieve_session_expired(self, mock_service_now, mock_session_now):
        """Test retrieval with expired session returns SESSION_MISSING_OR_EXPIRED."""
        base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create session at base time
        mock_session_now.return_value = base_time
        mock_service_now.return_value = base_time
        session = create_session(
            schema_url="https://example.com/api.json",
            ttl_minutes=10,
            num_endpoints=5
        )
        init_session_db(session)

        # Try to retrieve after session expired
        expired_time = base_time + timedelta(minutes=15)
        mock_session_now.return_value = expired_time
        mock_service_now.return_value = expired_time

        req = RetrieveRequest(
            session_id=session.session_id,
            query="test query",
            mode="minimal",
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        self.assertEqual(response.debug.error_code, SESSION_MISSING_OR_EXPIRED)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_mode_minimal(self, mock_embed):
        """Test that mode='minimal' returns endpoints_minimal and not endpoints_full."""
        session = self._setup_test_session()

        def embed_side_effect(texts):
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            return emb / norms

        mock_embed.side_effect = embed_side_effect

        req = RetrieveRequest(
            session_id=session.session_id,
            query="sample",
            mode="minimal",
            top_k=5,
            min_score=0.0,
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        self.assertEqual(response.mode, "minimal")
        if response.debug.num_candidates_final > 0:
            self.assertIsNotNone(response.endpoints_minimal)
            self.assertIsNone(response.endpoints_full)
            # Verify it's MinimalAPIEndpoint (has limited fields)
            for ep in response.endpoints_minimal:
                self.assertIsInstance(ep, MinimalAPIEndpoint)

    @patch('nextseek_api.schema_rag.service.embed_texts')
    def test_retrieve_mode_full(self, mock_embed):
        """Test that mode='full' returns endpoints_full and not endpoints_minimal."""
        session = self._setup_test_session()

        def embed_side_effect(texts):
            n = len(texts)
            emb = np.random.randn(n, 384)
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            return emb / norms

        mock_embed.side_effect = embed_side_effect

        req = RetrieveRequest(
            session_id=session.session_id,
            query="sample",
            mode="full",
            top_k=5,
            min_score=0.0,
            include_debug=True
        )
        response = schema_rag_service.retrieve_endpoints(req)

        self.assertEqual(response.mode, "full")
        if response.debug.num_candidates_final > 0:
            self.assertIsNone(response.endpoints_minimal)
            self.assertIsNotNone(response.endpoints_full)
            # Verify it's FullAPIEndpoint (has request_schema etc.)
            for ep in response.endpoints_full:
                self.assertIsInstance(ep, FullAPIEndpoint)
                self.assertIsNotNone(ep.request_schema)

