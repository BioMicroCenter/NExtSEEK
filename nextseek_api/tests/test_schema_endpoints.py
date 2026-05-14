"""Tests for versioned OpenAPI schema endpoints."""
import yaml
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
        """Schema does not include legacy /api/ PATH KEYS.

        Note: the v2 schema may contain `/api/` SUBSTRINGS inside OpenApiExample values
        (e.g., upstream SEEK schema URLs at https://fairdomhub.org/api/definitions/...).
        Those are user-facing documentation, not legacy paths exposed by our service.
        The test inspects schema["paths"] keys specifically.
        """
        resp = auth_client.get("/nextseek_api/schema/v2/")
        assert resp.status_code == 200
        schema = yaml.safe_load(resp.content)
        api_paths = [p for p in schema.get("paths", {}).keys() if p.startswith("/api/")]
        assert api_paths == [], f"Legacy /api/ path keys leaked into v2 schema: {api_paths}"

    def test_swagger_v2_available(self, auth_client):
        resp = auth_client.get("/nextseek_api/swagger/v2/")
        assert resp.status_code == 200
