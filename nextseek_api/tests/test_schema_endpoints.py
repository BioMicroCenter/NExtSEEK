"""Tests for versioned OpenAPI schema endpoints."""
import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestSchemaEndpoints:
    def test_v1_schema_endpoint_returns_openapi(self, auth_client):
        resp = auth_client.get("/nextseek_api/schema/v1/")
        assert resp.status_code == 200
        # drf-spectacular emits YAML by default; accept both
        assert b"openapi" in resp.content.lower()

    def test_v2_schema_endpoint_returns_openapi(self, auth_client):
        resp = auth_client.get("/nextseek_api/schema/v2/")
        assert resp.status_code == 200
        assert b"openapi" in resp.content.lower()

    def test_v2_schema_excludes_legacy_api_app_paths(self, auth_client):
        """Schema hook excludes /api/ paths (task-07 guard from Phase 2 risk register)."""
        resp = auth_client.get("/nextseek_api/schema/v2/")
        assert resp.status_code == 200
        assert b"/api/" not in resp.content  # legacy api_app paths absent

    def test_swagger_v2_available(self, auth_client):
        resp = auth_client.get("/nextseek_api/swagger/v2/")
        assert resp.status_code == 200
