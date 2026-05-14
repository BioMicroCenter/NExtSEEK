"""Integration tests — services/*.py 4xx/5xx returns pass through translator."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from nextseek_api.services.samples import SampleProxyViewSet

V2 = "application/vnd.nextseek.v2+json"


@pytest.fixture
def upstream_404():
    """Mock SampleProxyViewSet.client (class attribute) so self.client.get_sample
    returns a 404 envelope without making an outbound SEEK call. Patches the
    class attribute directly because `client = SeekAPIClient()` is instantiated
    at module-load time — patching the class name in the samples namespace has
    no effect on the existing instance."""
    mock_client = MagicMock()
    mock_client.get_sample.return_value = (
        b'{"errors":[{"title":"Not found"}]}', 404, {"Content-Type": "application/json"}, None,
    )
    with patch.object(SampleProxyViewSet, "client", mock_client):
        yield mock_client


@pytest.fixture
def upstream_401():
    """Same pattern as upstream_404 but configured for 401."""
    mock_client = MagicMock()
    mock_client.get_sample.return_value = (b"", 401, {}, None)
    with patch.object(SampleProxyViewSet, "client", mock_client):
        yield mock_client


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

    def test_mock_is_effective_for_404(self, auth_client, upstream_404):
        """Sentinel — proves the mocked client.get_sample is invoked AND that the
        view returns the expected 404 envelope (not 502 from validation rejection).
        Catches both T13-C1 (mock not effective) and the iter-1 regression where
        the mock is effective but SampleSingleResponse.model_validate reshapes the
        4xx body to a 502. Strengthened per Phase 4 iter-1 finding T13-I1."""
        resp = auth_client.get("/nextseek_api/samples/999/")
        upstream_404.get_sample.assert_called_once()
        call_args = upstream_404.get_sample.call_args
        assert call_args.args[1] == "999"
        # Status assertions — catch the 502-instead-of-404 failure mode
        assert resp.status_code == 404
        body = resp.json()
        assert body["errors"][0]["title"] in ("Not found", "Sample not found")
