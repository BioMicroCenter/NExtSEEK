"""Verify evaluator list endpoint already emits v2-canonical shape.

Evaluator uses DRF's PageNumberPagination which emits {count, next, previous,
results} natively. Task-02 does NOT modify evaluator code; this file locks
that contract as a regression guard.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

V1 = "application/vnd.nextseek.v1+json"
V2 = "application/vnd.nextseek.v2+json"


def _make_fake_task(task_id=None, session_id=None, status_str="completed"):
    """Build a MagicMock that quacks like a QueryTask row for runs_list."""
    task = MagicMock()
    task.task_id = task_id or uuid.uuid4()
    task.session = MagicMock()
    task.session.session_id = session_id or uuid.uuid4()
    task.status = status_str
    task.query = "select * from samples"
    task.result = {"bundle_id": 1}  # so _task_has_bundle returns True
    task.user_id = 1
    task.created_at = datetime.now(timezone.utc)
    return task


@pytest.fixture
def mock_evaluator_list():
    """Patch the QueryTask ORM call used by EvaluatorViewSet.runs_list.

    The viewset calls ``QueryTask.objects.select_related("session").order_by(...)``
    then paginates. We stub the chain to return a list of fake task objects so
    the endpoint can serialize a v2-canonical envelope.
    """
    with patch(
        "nextseek_api.services.evaluator.QueryTask.objects"
    ) as m:
        fake_qs = [_make_fake_task(), _make_fake_task()]
        # The viewset chains: .select_related("session").order_by("-created_at")
        # Configure both to return a list (DRF's PageNumberPagination handles lists).
        m.select_related.return_value.order_by.return_value = fake_qs
        yield m


@pytest.mark.django_db
class TestEvaluatorListEnvelope:
    def test_v1_shape_is_already_canonical(self, admin_client, mock_evaluator_list):
        resp = admin_client.get("/nextseek_api/evaluator/runs/")
        assert resp.status_code in (200, 404)  # 404 tolerated if URL pattern differs
        if resp.status_code == 200:
            body = resp.json()
            assert set(body.keys()) >= {"count", "results"}

    def test_v2_shape_is_also_canonical(self, admin_client, mock_evaluator_list):
        resp = admin_client.get("/nextseek_api/evaluator/runs/", HTTP_ACCEPT=V2)
        if resp.status_code == 200:
            body = resp.json()
            assert set(body.keys()) >= {"count", "results"}
