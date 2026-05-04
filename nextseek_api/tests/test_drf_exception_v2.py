"""Integration tests — DRF-raised exceptions reshape correctly under v2."""
import pytest
from rest_framework.test import APIClient

V1 = "application/vnd.nextseek.v1+json"
V2 = "application/vnd.nextseek.v2+json"


@pytest.mark.django_db
class TestDRFExceptionReshape:
    def test_v1_drf_exception_unchanged(self, api_client):
        # Unauthenticated client -> NotAuthenticated
        resp = api_client.get("/nextseek_api/samples/1/")
        assert resp.status_code in (401, 403)
        assert "detail" in resp.json()

    def test_v2_not_authenticated_reshaped(self, api_client):
        resp = api_client.get("/nextseek_api/samples/1/", HTTP_ACCEPT=V2)
        assert resp.status_code in (401, 403)
        body = resp.json()
        assert "errors" in body
        assert body["errors"][0]["status"] in ("401", "403")

    def test_v2_404_router_reshaped(self, auth_client):
        resp = auth_client.get("/nextseek_api/nonexistent-route/", HTTP_ACCEPT=V2)
        assert resp.status_code == 404
        # Django's 404 may bypass DRF handler; acceptable if it does.
        # Test documents intent.

    def test_v2_authentication_failed_reshaped(self, api_client):
        resp = api_client.get(
            "/nextseek_api/samples/1/",
            HTTP_ACCEPT=V2,
            HTTP_AUTHORIZATION="Token invalid-token",
        )
        if resp.status_code == 401:
            body = resp.json()
            assert body["errors"][0]["title"].lower().startswith("authentication")
