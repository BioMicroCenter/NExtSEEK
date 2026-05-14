"""End-to-end tests for samples/advanced_search v2 envelope."""
import json
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

V2 = "application/vnd.nextseek.v2+json"
V1 = "application/vnd.nextseek.v1+json"


@pytest.fixture
def upstream_rows():
    """Deterministic upstream payload."""
    payload = {
        "rows": [
            {"id": 1, "title": "a", "sample_type_id": "1", "attributeValue": "x"},
            {"id": 2, "title": "b", "sample_type_id": "1", "attributeValue": "y"},
        ],
        "total": 2,
        "sampleTypes": ["TIS"],
        "noSampleTypes": 1,
        "footer": [],
    }
    with patch("nextseek_api.services.samples.DBtable_sample") as m:
        m.return_value.searchAdvanced.return_value = json.dumps(payload)
        yield m


@pytest.fixture
def upstream_rows_none():
    """Upstream returns rows=None, not missing key (TDD-08)."""
    payload = {"rows": None, "total": 0}
    with patch("nextseek_api.services.samples.DBtable_sample") as m:
        m.return_value.searchAdvanced.return_value = json.dumps(payload)
        yield m


@pytest.mark.django_db
class TestAdvancedSearchV1Preserved:
    def test_v1_keys_present_no_accept_header(self, auth_client, upstream_rows):
        resp = auth_client.post("/nextseek_api/samples/advanced_search/", {}, format="json")
        body = json.loads(resp.content)
        assert set(body.keys()) >= {"rows", "total", "sampleTypes", "noSampleTypes", "footer"}

    def test_v1_explicit_accept_preserved(self, auth_client, upstream_rows):
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json",
            HTTP_ACCEPT=V1,
        )
        body = json.loads(resp.content)
        assert "rows" in body
        assert "results" not in body


@pytest.mark.django_db
class TestAdvancedSearchV2Envelope:
    def test_v2_keys_are_results_count_next_previous(self, auth_client, upstream_rows):
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json",
            HTTP_ACCEPT=V2,
        )
        body = json.loads(resp.content)
        assert set(body.keys()) == {"results", "count", "next", "previous"}

    def test_v2_has_no_rows_or_v1_keys(self, auth_client, upstream_rows):
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json",
            HTTP_ACCEPT=V2,
        )
        body = json.loads(resp.content)
        for v1_key in ("rows", "total", "sampleTypes", "noSampleTypes", "footer"):
            assert v1_key not in body

    def test_v2_results_contains_upstream_rows(self, auth_client, upstream_rows):
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json",
            HTTP_ACCEPT=V2,
        )
        body = json.loads(resp.content)
        assert body["results"] == [
            {"id": 1, "title": "a", "sample_type_id": "1", "attributeValue": "x"},
            {"id": 2, "title": "b", "sample_type_id": "1", "attributeValue": "y"},
        ]
        assert body["count"] == 2

    def test_v2_rows_is_none_returns_empty_results_not_500(self, auth_client, upstream_rows_none):
        """Regression: upstream `{"rows": None}` must not crash; returns count=0, results=[]."""
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 200
        body = json.loads(resp.content)
        assert body["results"] == []
        assert body["count"] == 0


@pytest.mark.django_db
class TestAdvancedSearchV2PaginationError:
    def test_v2_pagination_error_returns_api_error_500(self, auth_client, upstream_rows):
        """TDD-10: patch build_v2_list_envelope, not the v1 helper."""
        with patch(
            "nextseek_api.services.samples.build_v2_list_envelope",
            side_effect=RuntimeError("boom"),
        ):
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
                HTTP_ACCEPT=V2,
            )
        assert resp.status_code == 500
        body = json.loads(resp.content)
        assert "errors" in body
        assert body["errors"][0]["status"] == "500"
        assert "Pagination" in body["errors"][0]["title"]

    def test_v1_pagination_error_still_silently_falls_back(self, auth_client, upstream_rows):
        """v1 legacy behavior preserved — silent fallback to unpaginated."""
        with patch(
            "nextseek_api.services.samples.paginate_rows_in_envelope",
            side_effect=RuntimeError("boom"),
        ):
            resp = auth_client.post(
                "/nextseek_api/samples/advanced_search/", {}, format="json",
            )
        assert resp.status_code == 200
        body = json.loads(resp.content)
        # v1 keys still emitted; rows unpaginated
        assert "rows" in body
