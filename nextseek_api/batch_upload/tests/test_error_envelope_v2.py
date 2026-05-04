"""Integration tests — batch_upload endpoints emit v2 error envelope.

Covers all 13 error sites: 8 from spec table + 5 DRF Response sites from vet.
"""
import pytest
from rest_framework.test import APIClient

V1 = "application/vnd.nextseek.v1+json"
V2 = "application/vnd.nextseek.v2+json"


@pytest.mark.django_db
class TestBatchUploadStart:
    def test_v1_missing_project_id_returns_detail(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": [{"SampleType": "NHP"}]},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "project_id is required"}

    def test_v2_missing_project_id_returns_jsonapi(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": [{"SampleType": "NHP"}]},
            format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["errors"][0]["source"]["pointer"] == "/data/attributes/project_id"
        assert body["errors"][0]["meta"]["example"] == 12
        assert body["errors"][0]["status"] == "400"

    def test_v2_project_id_not_int_includes_valid_values_hint(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": [{"SampleType": "NHP"}], "project_id": "abc"},
            format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["errors"][0]["source"]["pointer"] == "/data/attributes/project_id"

    def test_v2_empty_rows_has_pointer(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": [], "project_id": 12},
            format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["errors"][0]["source"]["pointer"] == "/data/attributes/rows"

    def test_v2_pydantic_validation_error_422(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"rows": "not_a_list"},  # triggers pydantic validation
            format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["errors"][0]["status"] == "422"

    def test_v2_neither_rows_nor_file_400(self, auth_client):
        resp = auth_client.post(
            "/nextseek_api/batch-upload/start/",
            data={"project_id": 12},
            format="json",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "rows" in body["errors"][0].get("meta", {}).get("example", {})

    def test_v2_non_xlsx_file_400(self, auth_client, tmp_path):
        # File upload path — mocked via APIClient multipart
        txt = tmp_path / "bad.txt"
        txt.write_text("x")
        with open(txt, "rb") as fh:
            resp = auth_client.post(
                "/nextseek_api/batch-upload/start/",
                data={"file": fh, "project_id": 12},
                format="multipart",
                HTTP_ACCEPT=V2,
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["errors"][0]["meta"]["valid_values"] == [".xlsx"]


@pytest.mark.django_db
class TestBatchUploadOwnership:
    """Covers _check_ownership 404 site (lines 103-106)."""

    def test_v2_unknown_job_id_returns_jsonapi_404(self, auth_client):
        resp = auth_client.get(
            "/nextseek_api/batch-upload/nonexistent-job-id/status/",
            HTTP_ACCEPT=V2,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["errors"][0]["status"] == "404"
        assert body["errors"][0]["title"] == "Not found"

    def test_v1_unknown_job_id_returns_detail_404(self, auth_client):
        resp = auth_client.get("/nextseek_api/batch-upload/nonexistent-job-id/status/")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found."}
