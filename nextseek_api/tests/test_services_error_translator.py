"""Integration tests — services/*.py 4xx/5xx returns pass through translator."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

V2 = "application/vnd.nextseek.v2+json"


@pytest.fixture
def upstream_404():
    with patch("nextseek_api.services.samples.SeekAPIClient") as m:
        instance = m.return_value
        instance.get_sample.return_value = (
            b'{"errors":[{"title":"Not found"}]}', 404, {"Content-Type": "application/json"}, None,
        )
        yield instance


@pytest.fixture
def upstream_401():
    with patch("nextseek_api.services.samples.SeekAPIClient") as m:
        instance = m.return_value
        instance.get_sample.return_value = (b"", 401, {}, None)
        yield instance


@pytest.mark.django_db
class TestSampleRetrieveErrors:
    def test_v1_404_unchanged(self, auth_client, upstream_404):
        resp = auth_client.get("/nextseek_api/samples/999/")
        assert resp.status_code == 404
        body = resp.json()
        # v1 JSON:API shape preserved
        assert body["errors"][0]["title"] == "Not found" or body["errors"][0]["title"] == "Sample not found"

    def test_v2_404_reshaped(self, auth_client, upstream_404):
        resp = auth_client.get("/nextseek_api/samples/999/", HTTP_ACCEPT=V2)
        assert resp.status_code == 404
        body = resp.json()
        assert body["errors"][0]["status"] == "404"

    def test_v2_401_reshaped(self, auth_client, upstream_401):
        resp = auth_client.get("/nextseek_api/samples/1/", HTTP_ACCEPT=V2)
        assert resp.status_code == 401
        body = resp.json()
        assert body["errors"][0]["status"] == "401"
        assert body["errors"][0]["title"].lower().startswith("authentication")
