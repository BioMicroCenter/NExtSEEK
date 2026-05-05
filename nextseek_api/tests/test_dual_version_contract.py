"""Dual-version contract tests — lock v1 and v2 shapes side-by-side.

Covers every endpoint migrated in task-02 or retrofitted in task-03. Each test
is parametrized so both versions' shapes are asserted explicitly.
"""
import json
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

V1_LIT = "application/vnd.nextseek.v1+json"
V2_LIT = "application/vnd.nextseek.v2+json"


@pytest.fixture
def advanced_search_upstream():
    payload = {
        "rows": [{"id": 1, "title": "a"}],
        "total": 1,
        "sampleTypes": ["TIS"],
        "noSampleTypes": 1,
        "footer": [],
    }
    with patch("nextseek_api.services.samples.DBtable_samples") as m:
        m.return_value.searchAdvanced.return_value = json.dumps(payload)
        yield m


@pytest.mark.django_db
class TestAdvancedSearchDualVersion:
    @pytest.mark.parametrize("accept,expected_keys,forbidden_keys", [
        ("",     {"rows", "total", "sampleTypes", "noSampleTypes", "footer"}, {"results", "count"}),
        (V1_LIT, {"rows", "total", "sampleTypes", "noSampleTypes", "footer"}, {"results", "count"}),
        (V2_LIT, {"results", "count", "next", "previous"}, {"rows", "total", "sampleTypes"}),
    ])
    def test_envelope_keys_match_version(
        self, auth_client, advanced_search_upstream,
        accept, expected_keys, forbidden_keys,
    ):
        # Empty string → no kwarg → no HTTP_ACCEPT header → DEFAULT_VERSION=v1
        kwargs = {"HTTP_ACCEPT": accept} if accept else {}
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json", **kwargs,
        )
        assert resp.status_code == 200
        body = json.loads(resp.content)
        assert expected_keys <= set(body.keys()), (
            f"Accept={accept!r}: missing keys {expected_keys - set(body.keys())}"
        )
        for k in forbidden_keys:
            assert k not in body, f"Accept={accept!r}: forbidden key {k!r} present"


@pytest.mark.django_db
class TestBatchUploadErrorDualVersion:
    @pytest.mark.parametrize("accept,expected_shape", [
        ("",     "detail"),   # v1
        (V1_LIT, "detail"),
        (V2_LIT, "errors"),
    ])
    def test_project_id_missing_error_shape(self, auth_client, accept, expected_shape):
        kwargs = {"HTTP_ACCEPT": accept} if accept else {}
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": [{"SampleType": "NHP"}]},
            format="json",
            **kwargs,
        )
        assert resp.status_code == 400
        body = resp.json()
        if expected_shape == "detail":
            assert "detail" in body
            assert "errors" not in body
        else:
            assert "errors" in body
            assert "detail" not in body
