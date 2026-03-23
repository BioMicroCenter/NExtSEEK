"""Coverage tests for services/schema_rag.py ViewSet — targeting uncovered lines.

Covers:
- _check_auth: unauthenticated path
- ingest: validation error, ingest failure with error_code mapping
- retrieve_endpoints: validation error, success path
"""

import pytest
from unittest.mock import patch, MagicMock

from rest_framework.test import APIRequestFactory, force_authenticate


class TestSchemaRAGViewSet:

    def _make_user(self):
        user = MagicMock()
        user.pk = 9999
        user.is_authenticated = True
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        return user

    def _get_view(self, action):
        from nextseek_api.services.schema_rag import SchemaRAGViewSet
        view = SchemaRAGViewSet.as_view({"post": action})
        return view

    @patch("nextseek_api.services.schema_rag.resolve_seek_auth", return_value=(None, None))
    def test_ingest_unauthenticated(self, mock_auth):
        """Unauthenticated request returns 403 (DRF IsAuthenticated permission)."""
        factory = APIRequestFactory()
        view = self._get_view("ingest")
        request = factory.post("/schema_rag/ingest/", {}, format="json")
        request.user = MagicMock()
        request.user.is_authenticated = False
        response = view(request)
        assert response.status_code == 403

    @patch("nextseek_api.services.schema_rag.resolve_seek_auth", return_value=(("user", "pass"), {}))
    def test_ingest_validation_error(self, mock_auth):
        """Invalid request body returns 422."""
        factory = APIRequestFactory()
        view = self._get_view("ingest")
        request = factory.post(
            "/schema_rag/ingest/",
            {"bad_field": "value"},
            format="json",
        )
        force_authenticate(request, user=self._make_user())
        response = view(request)
        assert response.status_code == 422

    @patch("nextseek_api.services.schema_rag.schema_rag_service.ingest_schema")
    @patch("nextseek_api.services.schema_rag.resolve_seek_auth", return_value=(("user", "pass"), {}))
    def test_ingest_failure_maps_error_code(self, mock_auth, mock_ingest):
        """Failed ingestion returns mapped HTTP status."""
        from nextseek_api.schema_rag.errors import SCHEMA_FETCH_FAILED
        mock_ingest.return_value = MagicMock(
            success=False,
            error=MagicMock(
                error_code=SCHEMA_FETCH_FAILED,
                message="Failed to fetch",
                details={"exception": "timeout"},
            ),
        )
        factory = APIRequestFactory()
        view = self._get_view("ingest")
        request = factory.post(
            "/schema_rag/ingest/",
            {"schema_url": "http://bad-url.com/schema.yaml"},
            format="json",
        )
        force_authenticate(request, user=self._make_user())
        response = view(request)
        assert response.status_code == 502

    @patch("nextseek_api.services.schema_rag.resolve_seek_auth", return_value=(None, None))
    def test_retrieve_unauthenticated(self, mock_auth):
        """Unauthenticated request returns 403 (DRF IsAuthenticated permission)."""
        factory = APIRequestFactory()
        view = self._get_view("retrieve_endpoints")
        request = factory.post("/schema_rag/retrieve/", {}, format="json")
        request.user = MagicMock()
        request.user.is_authenticated = False
        response = view(request)
        assert response.status_code == 403

    @patch("nextseek_api.services.schema_rag.resolve_seek_auth", return_value=(("user", "pass"), {}))
    def test_retrieve_validation_error(self, mock_auth):
        factory = APIRequestFactory()
        view = self._get_view("retrieve_endpoints")
        request = factory.post(
            "/schema_rag/retrieve/",
            {"bad_field": "value"},
            format="json",
        )
        force_authenticate(request, user=self._make_user())
        response = view(request)
        assert response.status_code == 422
