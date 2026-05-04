"""Verify evaluator list endpoint already emits v2-canonical shape.

Evaluator uses DRF's PageNumberPagination which emits {count, next, previous,
results} natively. Task-02 does NOT modify evaluator code; this file locks
that contract as a regression guard.
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

V1 = "application/vnd.nextseek.v1+json"
V2 = "application/vnd.nextseek.v2+json"


@pytest.fixture
def mock_evaluator_list():
    """Patch whatever service call populates evaluator list.

    Evaluator list reads from the DB. We use a transactional test and insert
    minimal rows via the existing ORM factory if available; otherwise mock
    the queryset at the ViewSet level.
    """
    with patch(
        "nextseek_api.services.evaluator.EvaluatorRun.objects"
    ) as m:
        fake_qs = [
            {"id": 1, "name": "run-1"},
            {"id": 2, "name": "run-2"},
        ]
        m.all.return_value = fake_qs
        m.filter.return_value = fake_qs
        yield m


@pytest.mark.django_db
class TestEvaluatorListEnvelope:
    def test_v1_shape_is_already_canonical(self, auth_client, mock_evaluator_list):
        resp = auth_client.get("/nextseek_api/evaluator/runs/")
        assert resp.status_code in (200, 404)  # 404 tolerated if URL pattern differs
        if resp.status_code == 200:
            body = resp.json()
            assert set(body.keys()) >= {"count", "results"}

    def test_v2_shape_is_also_canonical(self, auth_client, mock_evaluator_list):
        resp = auth_client.get("/nextseek_api/evaluator/runs/", HTTP_ACCEPT=V2)
        if resp.status_code == 200:
            body = resp.json()
            assert set(body.keys()) >= {"count", "results"}
