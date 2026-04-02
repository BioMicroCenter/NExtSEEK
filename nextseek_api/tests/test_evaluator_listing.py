"""
Tests for the evaluator runs listing endpoint.

Covers:
- GET /evaluator/runs/ paginated listing
- Filtering by session_id, task_id, status, has_bundle, created_after,
  created_before, user_id
- Pagination (page_size)
- Authentication & permission enforcement (admin-only)
- Default ordering (newest first)
- Computed has_bundle field
- Response field completeness
"""

import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import re_path, include
from rest_framework.routers import DefaultRouter as _TestRouter
from rest_framework.test import APIClient

# Stub chat_nextseek.helpers.load_prompt BEFORE chat_nextseek.agents is imported.
if "chat_nextseek.agents" not in sys.modules:
    patch("chat_nextseek.helpers.load_prompt", return_value="(test stub)").start()

from nextseek_api.services.evaluator import EvaluatorViewSet as _EvalVS
from nextseek_api.assistant.models_db import ChatSession, QueryTask

# ---------------------------------------------------------------------------
# Test URLconf
# ---------------------------------------------------------------------------
_test_router = _TestRouter()
_test_router.register(r"evaluator", _EvalVS, basename="evaluator")
urlpatterns = [re_path(r'^nextseek_api/', include(_test_router.urls))]
_TEST_URLCONF = "nextseek_api.tests.test_evaluator_listing"

RUNS_URL = "/nextseek_api/evaluator/runs/"


# ===========================================================================
# Listing endpoint tests
# ===========================================================================

@override_settings(ROOT_URLCONF=_TEST_URLCONF)
class TestEvaluatorRunsListing(TestCase):
    """Test GET /evaluator/runs/ endpoint."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_listing",
            password="admin1234",
            is_staff=True,
            is_superuser=True,
        )
        self.regular = User.objects.create_user(
            username="regular_listing",
            password="pass1234",
        )
        self.client = APIClient()
        self.session = ChatSession.objects.create(
            user=self.admin,
            results_history=[],
        )

    def _make_task(self, user=None, session=None, status="completed",
                   result=None, query="test query"):
        user = user or self.admin
        session = session or self.session
        return QueryTask.objects.create(
            session=session,
            user=user,
            query=query,
            status=status,
            result=result,
        )

    # --- Authentication / permissions ---

    def test_admin_can_list(self):
        """Admin user gets 200 and sees all 3 tasks."""
        self._make_task(query="q1")
        self._make_task(query="q2")
        self._make_task(query="q3")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["results"]), 3)

    def test_non_admin_forbidden(self):
        """Non-admin user gets 403."""
        self._make_task()
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_forbidden(self):
        """Unauthenticated request gets 401 or 403."""
        self._make_task()
        resp = self.client.get(RUNS_URL)
        self.assertIn(resp.status_code, (401, 403))

    def test_empty_list(self):
        """Empty DB returns count=0, results=[]."""
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])

    # --- Filters ---

    def test_filter_by_status(self):
        """Filter by status returns only matching tasks."""
        self._make_task(status="completed")
        self._make_task(status="error")
        self._make_task(status="pending")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"status": "error"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["status"], "error")

    def test_filter_by_session_id(self):
        """Filter by session_id returns only tasks in that session."""
        session2 = ChatSession.objects.create(
            user=self.admin, results_history=[],
        )
        self._make_task(session=self.session, query="s1")
        self._make_task(session=session2, query="s2")
        self._make_task(session=session2, query="s3")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"session_id": str(session2.session_id)})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)

    def test_filter_by_task_id(self):
        """Filter by task_id returns exactly that one task."""
        t1 = self._make_task(query="target")
        self._make_task(query="other")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"task_id": str(t1.task_id)})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["task_id"], str(t1.task_id))

    def test_filter_by_user_id(self):
        """Filter by user_id returns only that user's tasks."""
        other_session = ChatSession.objects.create(
            user=self.regular, results_history=[],
        )
        self._make_task(user=self.admin, query="admin task")
        self._make_task(user=self.regular, session=other_session, query="regular task")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"user_id": self.regular.pk})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["user_id"], self.regular.pk)

    def test_filter_by_has_bundle_true(self):
        """has_bundle=true returns only tasks with bundle_id in result."""
        self._make_task(result={"reply": "ok", "bundle_id": 1}, query="with bundle")
        self._make_task(result={"reply": "ok"}, query="without bundle")
        self._make_task(result=None, query="no result")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"has_bundle": "true"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertTrue(data["results"][0]["has_bundle"])

    def test_filter_by_has_bundle_false(self):
        """has_bundle=false returns tasks without bundle_id."""
        self._make_task(result={"reply": "ok", "bundle_id": 1}, query="with bundle")
        self._make_task(result={"reply": "ok"}, query="without bundle")
        self._make_task(result=None, query="no result")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"has_bundle": "false"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)
        for row in data["results"]:
            self.assertFalse(row["has_bundle"])

    def test_filter_by_created_after(self):
        """created_after filter excludes older tasks."""
        t1 = self._make_task(query="old")
        t2 = self._make_task(query="new")
        # Manually adjust t1 to be old
        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        QueryTask.objects.filter(pk=t1.pk).update(created_at=old_time)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"created_after": "2025-01-01T00:00:00Z"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)

    def test_filter_by_created_before(self):
        """created_before filter excludes newer tasks."""
        t1 = self._make_task(query="old")
        t2 = self._make_task(query="new")
        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        QueryTask.objects.filter(pk=t1.pk).update(created_at=old_time)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"created_before": "2021-01-01T00:00:00Z"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)

    # --- Ordering ---

    def test_default_ordering_newest_first(self):
        """Results are ordered by created_at descending (newest first)."""
        t1 = self._make_task(query="first")
        t2 = self._make_task(query="second")
        t3 = self._make_task(query="third")
        # Spread them out in time
        QueryTask.objects.filter(pk=t1.pk).update(
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        QueryTask.objects.filter(pk=t2.pk).update(
            created_at=datetime(2025, 6, 1, tzinfo=timezone.utc))
        QueryTask.objects.filter(pk=t3.pk).update(
            created_at=datetime(2025, 12, 1, tzinfo=timezone.utc))
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        queries = [r["query"] for r in data["results"]]
        self.assertEqual(queries, ["third", "second", "first"])

    # --- Computed fields ---

    def test_has_bundle_field_computed(self):
        """has_bundle is True when result has bundle_id, False otherwise."""
        self._make_task(result={"reply": "ok", "bundle_id": 5}, query="with")
        self._make_task(result={"reply": "ok"}, query="without")
        self._make_task(result=None, query="none")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        by_query = {r["query"]: r["has_bundle"] for r in data["results"]}
        self.assertTrue(by_query["with"])
        self.assertFalse(by_query["without"])
        self.assertFalse(by_query["none"])

    # --- Pagination ---

    def test_pagination_page_size(self):
        """page_size limits results per page, count reflects total."""
        for i in range(5):
            self._make_task(query=f"task-{i}")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL, {"page_size": 2})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 5)
        self.assertEqual(len(data["results"]), 2)
        self.assertIsNotNone(data["next"])

    # --- Field completeness ---

    def test_result_fields_present(self):
        """Each result row has all expected EvaluatorRunSummary fields."""
        self._make_task(
            result={"reply": "ok", "bundle_id": 1},
            query="check fields",
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        row = data["results"][0]
        expected_keys = {
            "task_id", "session_id", "status", "query",
            "has_bundle", "user_id", "created_at",
        }
        self.assertEqual(set(row.keys()), expected_keys)
