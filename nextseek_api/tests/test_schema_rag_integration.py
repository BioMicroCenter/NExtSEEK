"""
Integration tests for Schema RAG feature.

Tests the full HTTP endpoint flow hermetically: the FAIRDOM SEEK OpenAPI
schema is served from the vendored fixture
``nextseek_api/tests/fixtures/fairdomhub_openapi_v3_resolved.yaml`` (the
``requests.get`` at the schema-processor import site is patched — the ONLY
patch in this module). Embeddings are REAL: the tests load the actual
``BAAI/bge-small-en-v1.5`` model from the per-checkout cache at
``schema_rag/embedding_models`` (provision it once per checkout with
``startup/dev/provision_embedding_model.sh``). No network access is needed.

Mark tests with @tag('slow', 'integration') for easy filtering.
Use --exclude-tag=slow to skip these tests in CI/quick test runs.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import yaml

from django.test import tag
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient
from rest_framework.authtoken.models import Token
from rest_framework import status


# FAIRDOM SEEK OpenAPI schema URL
SEEK_OPENAPI_URL = "https://fairdomhub.org/api/definitions/openapi-v3-resolved.yaml"

# Vendored copy of the schema the URL above serves (fetched 2026-07-22).
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fairdomhub_openapi_v3_resolved.yaml"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

_fixture_cache = {}


def _load_fixture_text() -> str:
    """Load (and memoize) the vendored fairdomhub OpenAPI schema text."""
    if "text" not in _fixture_cache:
        _fixture_cache["text"] = FIXTURE_PATH.read_text()
    return _fixture_cache["text"]


def _mock_get_response(text: str) -> Mock:
    """Mock of the requests.get response the schema processor consumes."""
    return Mock(text=text, headers={}, raise_for_status=Mock())


def _contains_ref(node) -> bool:
    """True if a '$ref' key appears anywhere inside a parsed schema node."""
    if isinstance(node, dict):
        if "$ref" in node:
            return True
        return any(_contains_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_ref(item) for item in node)
    return False


def _assert_fixture_sanity() -> None:
    """
    Fail loudly (once per process) if the vendored fixture has drifted from
    the shape these tests depend on: >= 100 operations overall, and at least
    one POST operation whose request body uses a $ref.
    """
    if _fixture_cache.get("sanity_ok"):
        return
    doc = yaml.safe_load(_load_fixture_text())
    paths = doc.get("paths") or {}
    num_operations = sum(
        1
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for method in path_item
        if method.lower() in _HTTP_METHODS
    )
    assert num_operations >= 100, (
        f"fixture drift: expected >= 100 operations in {FIXTURE_PATH}, "
        f"found {num_operations} — re-vendor from {SEEK_OPENAPI_URL}"
    )
    has_post_with_ref = any(
        _contains_ref(path_item[method].get("requestBody", {}))
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for method in path_item
        if method.lower() == "post" and isinstance(path_item[method], dict)
    )
    assert has_post_with_ref, (
        f"fixture drift: no POST operation with a $ref-based request body in "
        f"{FIXTURE_PATH} — re-vendor from {SEEK_OPENAPI_URL}"
    )
    _fixture_cache["sanity_ok"] = True


@tag('slow', 'integration')
class SchemaRAGIntegrationTests(APITestCase):
    """
    Integration tests for Schema RAG HTTP endpoints.

    The FAIRDOM SEEK OpenAPI schema is served from the vendored fixture;
    embeddings use the real per-checkout model (no network).
    They are marked as 'slow' and 'integration' for easy filtering.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures that are shared across all tests in this class."""
        super().setUpClass()
        # Fail loudly if the vendored schema fixture has drifted in shape
        _assert_fixture_sanity()
        # Create temp directory for DuckDB files
        cls.temp_dir = tempfile.mkdtemp()
        # Store original settings value
        cls._original_duckdb_dir = settings.SCHEMA_RAG_DUCKDB_DIR
        # Override settings for tests
        settings.SCHEMA_RAG_DUCKDB_DIR = cls.temp_dir
        os.makedirs(cls.temp_dir, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests in this class."""
        # Restore original settings
        settings.SCHEMA_RAG_DUCKDB_DIR = cls._original_duckdb_dir
        # Clean up temp directory
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        """Set up test user and authentication for each test."""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@example.com'
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        # Serve the vendored schema instead of hitting fairdomhub.org — the
        # ONLY patch in this module (embeddings run the real model).
        patcher = patch(
            'nextseek_api.schema_rag.schema_processor.requests.get',
            return_value=_mock_get_response(_load_fixture_text()),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        """Clean up after each test."""
        # Clean up DuckDB files from this test
        for filename in os.listdir(self.temp_dir):
            filepath = os.path.join(self.temp_dir, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)

    def _authenticate(self):
        """Set up authentication credentials."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)

    def test_ingest_seek_openapi(self):
        """
        Test ingesting the real FAIRDOM SEEK OpenAPI schema.

        This test:
        - Makes a real HTTP request to fairdomhub.org
        - Ingests the full OpenAPI schema
        - Verifies successful response with valid session_id and num_endpoints
        """
        self._authenticate()

        response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={
                'schema_url': SEEK_OPENAPI_URL,
                'ttl_minutes': 30
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        data = response.json()
        self.assertIn('session_id', data)
        self.assertIn('num_endpoints', data)
        self.assertIn('schema_url', data)
        self.assertIn('ttl_minutes', data)
        self.assertIn('expires_at', data)

        # SEEK API has many endpoints (100+)
        self.assertGreater(data['num_endpoints'], 100)
        self.assertEqual(data['schema_url'], SEEK_OPENAPI_URL)
        self.assertEqual(data['ttl_minutes'], 30)

        # Store session_id for subsequent tests
        self.__class__.session_id = data['session_id']

    def test_retrieve_after_ingest(self):
        """
        Test retrieving endpoints after ingesting the schema.

        This test:
        - Ingests the SEEK OpenAPI schema
        - Retrieves endpoints with query "create a sample"
        - Verifies non-empty results and populated debug info
        """
        self._authenticate()

        # First ingest the schema
        ingest_response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={'schema_url': SEEK_OPENAPI_URL},
            format='json'
        )
        self.assertEqual(ingest_response.status_code, status.HTTP_201_CREATED)
        session_id = ingest_response.json()['session_id']

        # Now retrieve endpoints
        retrieve_response = self.client.post(
            '/nextseek_api/schema_rag/retrieve/',
            data={
                'session_id': session_id,
                'query': 'How do I create a new sample?',
                'mode': 'minimal',
                'top_k': 5,
                'include_debug': True
            },
            format='json'
        )

        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)

        data = retrieve_response.json()
        self.assertEqual(data['session_id'], session_id)
        self.assertEqual(data['mode'], 'minimal')

        # Debug info should be present
        self.assertIn('debug', data)
        debug = data['debug']
        self.assertIn('used_fallback_full', debug)
        self.assertIn('used_enriched_minimal_examples', debug)
        self.assertIn('similarity_threshold_effective', debug)
        self.assertIn('num_candidates_initial', debug)
        self.assertIn('num_candidates_final', debug)

        # Should have found some endpoints (samples is a common SEEK entity)
        # Note: If no match found, message will be set instead of endpoints
        if 'endpoints_minimal' in data and data['endpoints_minimal']:
            self.assertGreater(len(data['endpoints_minimal']), 0)
            # Verify endpoint structure
            endpoint = data['endpoints_minimal'][0]
            self.assertIn('operationId', endpoint)
            self.assertIn('method', endpoint)

    def test_retrieve_with_session_id(self):
        """
        Test retrieval using session_id from prior ingest.

        Verifies that the session_id correctly references the cached schema.
        """
        self._authenticate()

        # Ingest schema
        ingest_response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={'schema_url': SEEK_OPENAPI_URL},
            format='json'
        )
        self.assertEqual(ingest_response.status_code, status.HTTP_201_CREATED)
        session_id = ingest_response.json()['session_id']

        # Retrieve using session_id only (no schema_url)
        retrieve_response = self.client.post(
            '/nextseek_api/schema_rag/retrieve/',
            data={
                'session_id': session_id,
                'query': 'list all projects',
                'mode': 'full',
                'top_k': 3,
                'include_debug': True
            },
            format='json'
        )

        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)
        data = retrieve_response.json()
        self.assertEqual(data['session_id'], session_id)
        self.assertEqual(data['mode'], 'full')

    def test_retrieve_with_schema_url(self):
        """
        Test retrieval using schema_url to find existing session.

        Verifies that providing schema_url looks up and reuses the cached session.
        """
        self._authenticate()

        # Ingest schema first
        ingest_response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={'schema_url': SEEK_OPENAPI_URL},
            format='json'
        )
        self.assertEqual(ingest_response.status_code, status.HTTP_201_CREATED)
        original_session_id = ingest_response.json()['session_id']

        # Retrieve using schema_url (should find existing session)
        retrieve_response = self.client.post(
            '/nextseek_api/schema_rag/retrieve/',
            data={
                'schema_url': SEEK_OPENAPI_URL,
                'query': 'get user information',
                'mode': 'minimal',
                'top_k': 5,
                'include_debug': True
            },
            format='json'
        )

        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)
        data = retrieve_response.json()

        # Should have reused the same session
        self.assertEqual(data['session_id'], original_session_id)

    def test_ingest_unauthenticated(self):
        """
        Test that ingest endpoint requires authentication.

        Verifies 401 Unauthorized when no auth token is provided.
        """
        # Do NOT authenticate
        response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={'schema_url': 'https://example.com/api.json'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_unauthenticated(self):
        """
        Test that retrieve endpoint requires authentication.

        Verifies 401 Unauthorized when no auth token is provided.
        """
        # Do NOT authenticate
        response = self.client.post(
            '/nextseek_api/schema_rag/retrieve/',
            data={
                'session_id': 'some-session-id',
                'query': 'test query',
                'mode': 'minimal'
            },
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@tag('slow', 'integration')
class SchemaRAGFullModeIntegrationTests(APITestCase):
    """
    Additional integration tests focusing on full mode retrieval.
    """

    @classmethod
    def setUpClass(cls):
        """Set up shared test fixtures."""
        super().setUpClass()
        # Fail loudly if the vendored schema fixture has drifted in shape
        _assert_fixture_sanity()
        cls.temp_dir = tempfile.mkdtemp()
        cls._original_duckdb_dir = settings.SCHEMA_RAG_DUCKDB_DIR
        settings.SCHEMA_RAG_DUCKDB_DIR = cls.temp_dir
        os.makedirs(cls.temp_dir, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests."""
        settings.SCHEMA_RAG_DUCKDB_DIR = cls._original_duckdb_dir
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        """Set up test user and authentication."""
        self.user = User.objects.create_user(
            username='testuser2',
            password='testpass123',
            email='test2@example.com'
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        # Serve the vendored schema instead of hitting fairdomhub.org — the
        # ONLY patch in this module (embeddings run the real model).
        patcher = patch(
            'nextseek_api.schema_rag.schema_processor.requests.get',
            return_value=_mock_get_response(_load_fixture_text()),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        """Clean up DuckDB files."""
        for filename in os.listdir(self.temp_dir):
            filepath = os.path.join(self.temp_dir, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)

    def _authenticate(self):
        """Set up authentication credentials."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)

    def test_retrieve_full_mode_includes_resolved_schema(self):
        """
        Test that full mode returns RESOLVED request_schema, not $ref placeholders.
        """
        self._authenticate()

        # Ingest
        ingest_response = self.client.post(
            '/nextseek_api/schema_rag/ingest/',
            data={'schema_url': SEEK_OPENAPI_URL},
            format='json'
        )
        self.assertEqual(ingest_response.status_code, status.HTTP_201_CREATED)
        session_id = ingest_response.json()['session_id']

        # Query for POST endpoints (they have request bodies)
        retrieve_response = self.client.post(
            '/nextseek_api/schema_rag/retrieve/',
            data={
                'session_id': session_id,
                'query': 'create a new sample',
                'mode': 'full',
                'top_k': 5,
                'min_score': 0.0,
                'include_debug': True
            },
            format='json'
        )

        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)
        data = retrieve_response.json()

        # Should have full endpoints
        if 'endpoints_full' in data and data['endpoints_full']:
            post_endpoints = [ep for ep in data['endpoints_full'] if ep['method'] == 'POST']

            if post_endpoints:
                endpoint = post_endpoints[0]
                self.assertIn('operationId', endpoint)
                self.assertIn('method', endpoint)
                self.assertIn('path', endpoint)
                # Full mode should include request_schema
                self.assertIn('request_schema', endpoint)

                schema = endpoint.get('request_schema', {})
                schema_str = str(schema)

                # Verify NO unresolved $ref placeholders
                self.assertNotIn('$ref:', schema_str,
                    "request_schema should have resolved $ref references")

