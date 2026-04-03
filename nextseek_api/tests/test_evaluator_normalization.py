"""
Tests for evaluator normalization endpoints and helper functions.

Covers:
- classify_path(): mode → (execution_mode, path_mode, path_subtype) mapping
- normalize_from_task(): QueryTask → EvaluatorRetryContextResponse
- normalize_from_bundle(): session + bundle dict → EvaluatorRetryContextResponse
- GET /evaluator/tasks/{task_id}/retry-context/ endpoint
- GET /evaluator/sessions/{session_id}/bundles/{bundle_id}/retry-context/ endpoint
- Authentication & permission enforcement (admin-only)
- Error handling (404, bad UUIDs)
"""

import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import re_path, include
from rest_framework.routers import DefaultRouter as _TestRouter
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.models_evaluator import (
    EvaluatorRetryContextResponse,
    EvaluatorRetrySignals,
    EvaluatorRouting,
)

# Stub chat_nextseek.helpers.load_prompt BEFORE chat_nextseek.agents is imported.
if "chat_nextseek.agents" not in sys.modules:
    patch("chat_nextseek.helpers.load_prompt", return_value="(test stub)").start()

from nextseek_api.services.evaluator import (
    EvaluatorViewSet,
    classify_path,
    normalize_from_task,
    normalize_from_bundle,
)

# ---------------------------------------------------------------------------
# Test URLconf — evaluator is NOT registered in the real urls.py yet (Task 05)
# ---------------------------------------------------------------------------
_test_router = _TestRouter()
_test_router.register(r"evaluator", EvaluatorViewSet, basename="evaluator")
urlpatterns = [re_path(r'^nextseek_api/', include(_test_router.urls))]

_TEST_URLCONF = "nextseek_api.tests.test_evaluator_normalization"

# ---------------------------------------------------------------------------
# Test fixtures (representative bundle dicts)
# ---------------------------------------------------------------------------
BUNDLE_NEW_SEARCH = {
    "id": 1,
    "query": "Find mice",
    "reply": "Found 5",
    "debug": {"endpoint": "/samples"},
    "mode": "new_search",
}

BUNDLE_GRAPH_QUERY = {
    "id": 2,
    "query": "Show tree",
    "reply": "Tree shown",
    "debug": {"cypher": "MATCH..."},
    "mode": "graph_query",
}

BUNDLE_REPORTER_SUMMARY = {
    "id": 3,
    "query": "Summarize",
    "reply": "Summary...",
    "debug": {"reporter_plan": {"reporter_mode": "summary"}},
    "mode": "reporter",
}

BUNDLE_REPORTER_REPORT = {
    "id": 4,
    "query": "Generate report",
    "reply": "Report done",
    "debug": {"reporter_plan": {"reporter_mode": "report_generation"}},
    "mode": "reporter",
}

BUNDLE_PLAN = {
    "id": 5,
    "query": "Plan analysis",
    "reply": "3 steps",
    "debug": {"steps": [1, 2]},
    "mode": "plan",
}

BUNDLE_EMPTY_REPLY = {
    "id": 6,
    "query": "Find elephants",
    "reply": "",
    "debug": {},
    "mode": "new_search",
}

BUNDLE_NO_REPLY = {
    "id": 7,
    "query": "Find elephants",
    "reply": None,
    "debug": {},
    "mode": "new_search",
}


# ===========================================================================
# classify_path() unit tests
# ===========================================================================

class TestClassifyPath(TestCase):
    """Test classify_path(mode, debug) → (execution_mode, path_mode, path_subtype)."""

    def test_new_search(self):
        em, pm, ps = classify_path("new_search", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "new_search")
        self.assertIsNone(ps)

    def test_refine_last_search(self):
        em, pm, ps = classify_path("refine_last_search", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "refine_last_search")
        self.assertIsNone(ps)

    def test_graph_query(self):
        em, pm, ps = classify_path("graph_query", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "graph_query")
        self.assertIsNone(ps)

    def test_system_question(self):
        em, pm, ps = classify_path("system_question", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "system_question")
        self.assertIsNone(ps)

    def test_ask_about_last_results(self):
        em, pm, ps = classify_path("ask_about_last_results", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "ask_about_last_results")
        self.assertIsNone(ps)

    def test_reporter_summary(self):
        debug = {"reporter_plan": {"reporter_mode": "summary"}}
        em, pm, ps = classify_path("reporter", debug)
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "reporter")
        self.assertEqual(ps, "reporter.summary")

    def test_reporter_report_generation(self):
        debug = {"reporter_plan": {"reporter_mode": "report_generation"}}
        em, pm, ps = classify_path("reporter", debug)
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "reporter")
        self.assertEqual(ps, "reporter.report_generation")

    def test_reporter_no_plan(self):
        em, pm, ps = classify_path("reporter", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "reporter")
        self.assertIsNone(ps)

    def test_reporter_empty_plan(self):
        em, pm, ps = classify_path("reporter", {"reporter_plan": {}})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "reporter")
        self.assertIsNone(ps)

    def test_plan_mode(self):
        em, pm, ps = classify_path("plan", {"steps": [1, 2]})
        self.assertEqual(em, "plan")
        self.assertEqual(pm, "plan")
        self.assertIsNone(ps)

    def test_none_mode(self):
        em, pm, ps = classify_path(None, {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "unsupported")
        self.assertIsNone(ps)

    def test_empty_string_mode(self):
        em, pm, ps = classify_path("", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "unsupported")
        self.assertIsNone(ps)

    def test_unknown_mode(self):
        em, pm, ps = classify_path("totally_unknown_mode", {})
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "unsupported")
        self.assertIsNone(ps)

    def test_none_debug(self):
        em, pm, ps = classify_path("new_search", None)
        self.assertEqual(em, "standard")
        self.assertEqual(pm, "new_search")
        self.assertIsNone(ps)


# ===========================================================================
# normalize_from_task() unit tests
# ===========================================================================

class TestNormalizeFromTask(TestCase):
    """Test normalize_from_task(task) → EvaluatorRetryContextResponse."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="evaluser", password="pass1234"
        )
        self.session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NEW_SEARCH],
            last_debug={"endpoint": "/samples"},
        )

    def _make_task(self, status="completed", result=None, progress=None):
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="Find mice",
            status=status,
            progress=progress or [],
            result=result,
        )
        # Ensure session is loaded via select_related pattern
        task = QueryTask.objects.select_related("session").get(pk=task.pk)
        return task

    def test_completed_task_with_bundle(self):
        result = {"reply": "Found 5", "bundle_id": 1}
        task = self._make_task(result=result)
        resp = normalize_from_task(task)

        self.assertIsInstance(resp, EvaluatorRetryContextResponse)
        self.assertEqual(resp.lookup.source, "task")
        self.assertEqual(resp.lookup.task_id, task.task_id)
        self.assertEqual(resp.lookup.bundle_id, 1)
        self.assertEqual(resp.run.status, "completed")
        self.assertEqual(resp.run.query, "Find mice")
        self.assertEqual(resp.run.reply, "Found 5")
        self.assertEqual(resp.routing.execution_mode, "standard")
        self.assertEqual(resp.routing.path_mode, "new_search")
        self.assertTrue(resp.retry_context.retryable)
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "completed")
        self.assertTrue(resp.retry_context.retry_signals.bundle_present)
        self.assertEqual(resp.retry_context.retry_signals.path_mode, "new_search")
        self.assertEqual(resp.raw.task_result, result)

    def test_completed_task_no_bundle(self):
        result = {"reply": "Done", "bundle_id": None}
        task = self._make_task(result=result)
        resp = normalize_from_task(task)

        self.assertEqual(resp.lookup.bundle_id, None)
        self.assertEqual(resp.routing.path_mode, "unsupported")
        self.assertFalse(resp.retry_context.retryable)

    def test_completed_task_bundle_not_in_history(self):
        """Task references a bundle_id not present in session history."""
        result = {"reply": "Done", "bundle_id": 999}
        task = self._make_task(result=result)
        resp = normalize_from_task(task)

        self.assertEqual(resp.lookup.bundle_id, 999)
        # No bundle found, so path_mode defaults to unsupported
        self.assertEqual(resp.routing.path_mode, "unsupported")
        self.assertFalse(resp.retry_context.retryable)

    def test_error_task(self):
        result = {"error": "Something broke"}
        task = self._make_task(status="error", result=result)
        resp = normalize_from_task(task)

        self.assertEqual(resp.run.status, "error")
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "error")
        self.assertTrue(resp.retry_context.retry_signals.query_error_present)
        self.assertFalse(resp.retry_context.retryable)

    def test_pending_task(self):
        task = self._make_task(status="pending", result=None)
        resp = normalize_from_task(task)

        self.assertEqual(resp.run.status, "pending")
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "pending")
        self.assertFalse(resp.retry_context.retry_signals.bundle_present)
        self.assertFalse(resp.retry_context.retryable)

    def test_running_task(self):
        task = self._make_task(status="running", result=None)
        resp = normalize_from_task(task)

        self.assertEqual(resp.run.status, "running")
        self.assertFalse(resp.retry_context.retryable)

    def test_task_no_result(self):
        task = self._make_task(status="completed", result=None)
        resp = normalize_from_task(task)

        self.assertEqual(resp.lookup.bundle_id, None)
        self.assertFalse(resp.retry_context.retryable)

    def test_task_with_empty_reply_signal(self):
        """A completed task with empty reply -- signals reflect bundle state."""
        result = {"reply": "", "bundle_id": 1}
        task = self._make_task(status="completed", result=result)
        resp = normalize_from_task(task)

        self.assertIsInstance(resp.retry_context.retry_signals, EvaluatorRetrySignals)
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "completed")
        self.assertTrue(resp.retry_context.retry_signals.bundle_present)

    def test_task_with_none_reply_signal(self):
        """A completed task with None reply -- signals reflect bundle state."""
        result = {"reply": None, "bundle_id": 1}
        task = self._make_task(status="completed", result=result)
        resp = normalize_from_task(task)

        self.assertIsInstance(resp.retry_context.retry_signals, EvaluatorRetrySignals)
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "completed")
        self.assertTrue(resp.retry_context.retry_signals.bundle_present)

    def test_task_progress_included(self):
        progress = [{"event": "agent_started", "data": {"agent": "entity"}}]
        task = self._make_task(
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
            progress=progress,
        )
        resp = normalize_from_task(task)

        self.assertEqual(resp.raw.task_progress, progress)

    def test_task_with_plan_bundle(self):
        """Task referencing a plan-mode bundle."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_PLAN],
        )
        task = QueryTask.objects.create(
            session=session,
            user=self.user,
            query="Plan analysis",
            status="completed",
            result={"reply": "3 steps", "bundle_id": 5},
        )
        task = QueryTask.objects.select_related("session").get(pk=task.pk)
        resp = normalize_from_task(task)

        self.assertEqual(resp.routing.execution_mode, "plan")
        self.assertEqual(resp.routing.path_mode, "plan")
        self.assertTrue(resp.retry_context.retryable)

    def test_task_with_reporter_summary_bundle(self):
        """Task referencing a reporter summary bundle."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_REPORTER_SUMMARY],
        )
        task = QueryTask.objects.create(
            session=session,
            user=self.user,
            query="Summarize",
            status="completed",
            result={"reply": "Summary...", "bundle_id": 3},
        )
        task = QueryTask.objects.select_related("session").get(pk=task.pk)
        resp = normalize_from_task(task)

        self.assertEqual(resp.routing.path_mode, "reporter")
        self.assertEqual(resp.routing.path_subtype, "reporter.summary")
        self.assertTrue(resp.retry_context.retryable)

    def test_user_id_included(self):
        task = self._make_task(
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
        )
        resp = normalize_from_task(task)
        self.assertEqual(resp.run.user_id, self.user.pk)

    def test_created_at_included(self):
        task = self._make_task(
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
        )
        resp = normalize_from_task(task)
        self.assertIsNotNone(resp.run.created_at)

    def test_last_debug_included(self):
        task = self._make_task(
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
        )
        resp = normalize_from_task(task)
        self.assertEqual(resp.raw.last_debug, {"endpoint": "/samples"})

    def test_response_round_trips_json(self):
        result = {"reply": "Found 5", "bundle_id": 1}
        task = self._make_task(result=result)
        resp = normalize_from_task(task)
        data = resp.model_dump(mode="json")
        reconstructed = EvaluatorRetryContextResponse.model_validate(data)
        self.assertEqual(reconstructed.lookup.source, "task")


# ===========================================================================
# normalize_from_bundle() unit tests
# ===========================================================================

class TestNormalizeFromBundle(TestCase):
    """Test normalize_from_bundle(session, bundle) → EvaluatorRetryContextResponse."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="evaluser", password="pass1234"
        )

    def test_new_search_bundle(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        resp = normalize_from_bundle(session, BUNDLE_NEW_SEARCH)

        self.assertIsInstance(resp, EvaluatorRetryContextResponse)
        self.assertEqual(resp.lookup.source, "bundle")
        self.assertIsNone(resp.lookup.task_id)
        self.assertEqual(resp.lookup.session_id, session.session_id)
        self.assertEqual(resp.lookup.bundle_id, 1)
        self.assertEqual(resp.run.status, "completed")
        self.assertEqual(resp.run.query, "Find mice")
        self.assertEqual(resp.run.reply, "Found 5")
        self.assertEqual(resp.routing.path_mode, "new_search")
        self.assertTrue(resp.retry_context.retryable)
        # No task info for bundle-based normalization
        self.assertIsNone(resp.raw.task_progress)
        self.assertIsNone(resp.raw.task_result)
        self.assertEqual(resp.raw.bundle, BUNDLE_NEW_SEARCH)

    def test_graph_query_bundle(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_GRAPH_QUERY],
        )
        resp = normalize_from_bundle(session, BUNDLE_GRAPH_QUERY)

        self.assertEqual(resp.routing.path_mode, "graph_query")
        self.assertTrue(resp.retry_context.retryable)

    def test_reporter_summary_bundle(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_REPORTER_SUMMARY],
        )
        resp = normalize_from_bundle(session, BUNDLE_REPORTER_SUMMARY)

        self.assertEqual(resp.routing.path_mode, "reporter")
        self.assertEqual(resp.routing.path_subtype, "reporter.summary")
        self.assertTrue(resp.retry_context.retryable)

    def test_reporter_report_bundle(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_REPORTER_REPORT],
        )
        resp = normalize_from_bundle(session, BUNDLE_REPORTER_REPORT)

        self.assertEqual(resp.routing.path_subtype, "reporter.report_generation")

    def test_plan_bundle(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_PLAN],
        )
        resp = normalize_from_bundle(session, BUNDLE_PLAN)

        self.assertEqual(resp.routing.execution_mode, "plan")
        self.assertEqual(resp.routing.path_mode, "plan")
        self.assertTrue(resp.retry_context.retryable)

    def test_empty_reply_signal_when_no_linked_task(self):
        """When bundle has empty reply and no linked task -- signals reflect bundle."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_EMPTY_REPLY],
        )
        resp = normalize_from_bundle(session, BUNDLE_EMPTY_REPLY)
        self.assertIsInstance(resp.retry_context.retry_signals, EvaluatorRetrySignals)
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "completed")
        self.assertTrue(resp.retry_context.retry_signals.bundle_present)
        self.assertEqual(resp.retry_context.retry_signals.path_mode, "new_search")

    def test_none_reply_signal_when_no_linked_task(self):
        """When bundle has no reply and no linked task -- signals reflect bundle."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NO_REPLY],
        )
        resp = normalize_from_bundle(session, BUNDLE_NO_REPLY)
        self.assertIsInstance(resp.retry_context.retry_signals, EvaluatorRetrySignals)
        self.assertEqual(resp.retry_context.retry_signals.assistant_status, "completed")
        self.assertTrue(resp.retry_context.retry_signals.bundle_present)

    def test_user_id_from_session(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        resp = normalize_from_bundle(session, BUNDLE_NEW_SEARCH)
        self.assertEqual(resp.run.user_id, self.user.pk)

    def test_last_debug_included(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NEW_SEARCH],
            last_debug={"foo": "bar"},
        )
        resp = normalize_from_bundle(session, BUNDLE_NEW_SEARCH)
        self.assertEqual(resp.raw.last_debug, {"foo": "bar"})

    def test_response_round_trips_json(self):
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        resp = normalize_from_bundle(session, BUNDLE_NEW_SEARCH)
        data = resp.model_dump(mode="json")
        reconstructed = EvaluatorRetryContextResponse.model_validate(data)
        self.assertEqual(reconstructed.lookup.source, "bundle")


# ===========================================================================
# Endpoint tests — task-based retry context
# ===========================================================================

@override_settings(ROOT_URLCONF=_TEST_URLCONF)
class TestRetryContextByTaskEndpoint(TestCase):
    """Test GET /evaluator/tasks/{task_id}/retry-context/."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="admin1234",
            is_staff=True, is_superuser=True,
        )
        self.regular = User.objects.create_user(
            username="regular", password="pass1234",
        )
        self.client = APIClient()
        self.session = ChatSession.objects.create(
            user=self.admin,
            results_history=[BUNDLE_NEW_SEARCH],
            last_debug={"endpoint": "/samples"},
        )

    def test_admin_gets_retry_context(self):
        task = QueryTask.objects.create(
            session=self.session,
            user=self.admin,
            query="Find mice",
            status="completed",
            result={"reply": "Found 5", "bundle_id": 1},
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{task.task_id}/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lookup"]["source"], "task")
        self.assertEqual(data["run"]["status"], "completed")
        self.assertEqual(data["routing"]["path_mode"], "new_search")
        self.assertTrue(data["retry_context"]["retryable"])

    def test_unauthenticated_rejected(self):
        task = QueryTask.objects.create(
            session=self.session,
            user=self.admin,
            query="test",
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
        )
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{task.task_id}/retry-context/"
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_non_admin_rejected(self):
        task = QueryTask.objects.create(
            session=self.session,
            user=self.admin,
            query="test",
            status="completed",
            result={"reply": "ok", "bundle_id": 1},
        )
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{task.task_id}/retry-context/"
        )
        self.assertEqual(resp.status_code, 403)

    def test_task_not_found(self):
        self.client.force_authenticate(user=self.admin)
        fake_id = uuid.uuid4()
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{fake_id}/retry-context/"
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    def test_error_task_context(self):
        task = QueryTask.objects.create(
            session=self.session,
            user=self.admin,
            query="broken",
            status="error",
            result={"error": "Something broke"},
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{task.task_id}/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run"]["status"], "error")
        signals = data["retry_context"]["retry_signals"]
        self.assertEqual(signals["assistant_status"], "error")
        self.assertTrue(signals["query_error_present"])

    def test_admin_can_see_other_users_task(self):
        """Admin endpoint allows access to any user's task."""
        other_session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_GRAPH_QUERY],
        )
        task = QueryTask.objects.create(
            session=other_session,
            user=self.regular,
            query="Show tree",
            status="completed",
            result={"reply": "Tree shown", "bundle_id": 2},
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/tasks/{task.task_id}/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["routing"]["path_mode"], "graph_query")


# ===========================================================================
# Endpoint tests — bundle-based retry context
# ===========================================================================

@override_settings(ROOT_URLCONF=_TEST_URLCONF)
class TestRetryContextByBundleEndpoint(TestCase):
    """Test GET /evaluator/sessions/{session_id}/bundles/{bundle_id}/retry-context/."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="admin1234",
            is_staff=True, is_superuser=True,
        )
        self.regular = User.objects.create_user(
            username="regular", password="pass1234",
        )
        self.client = APIClient()

    def test_admin_gets_bundle_context(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_NEW_SEARCH, BUNDLE_GRAPH_QUERY],
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/1/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lookup"]["source"], "bundle")
        self.assertEqual(data["lookup"]["bundle_id"], 1)
        self.assertEqual(data["routing"]["path_mode"], "new_search")

    def test_second_bundle(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_NEW_SEARCH, BUNDLE_GRAPH_QUERY],
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/2/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["routing"]["path_mode"], "graph_query")

    def test_unauthenticated_rejected(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/1/retry-context/"
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_non_admin_rejected(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/1/retry-context/"
        )
        self.assertEqual(resp.status_code, 403)

    def test_session_not_found(self):
        self.client.force_authenticate(user=self.admin)
        fake_id = uuid.uuid4()
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{fake_id}/bundles/1/retry-context/"
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    def test_bundle_not_found(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_NEW_SEARCH],
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/999/retry-context/"
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    def test_reporter_bundle_context(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_REPORTER_SUMMARY],
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/3/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["routing"]["path_subtype"], "reporter.summary")

    def test_plan_bundle_context(self):
        session = ChatSession.objects.create(
            user=self.regular,
            results_history=[BUNDLE_PLAN],
        )
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(
            f"/nextseek_api/evaluator/sessions/{session.session_id}/bundles/5/retry-context/"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["routing"]["execution_mode"], "plan")
        self.assertEqual(data["routing"]["path_mode"], "plan")


# ===========================================================================
# _build_retry_signals() unit tests
# ===========================================================================

class TestBuildRetrySignals(TestCase):
    """Test _build_retry_signals() with production-like fixtures."""

    def test_search_bundle_extracts_api_observables(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "new_search",
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 195}},
            "api_result_slim": {"ok": True, "status_code": 200, "data": {"total": 195}},
            "files": [{"key": "api_result"}],
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertTrue(signals.api_ok)
        self.assertEqual(signals.api_status_code, 200)
        self.assertEqual(signals.rows_returned, 195)
        self.assertTrue(signals.has_artifacts)

    def test_search_bundle_api_error(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "new_search",
            "api_result_full": {"ok": False, "status_code": 500, "error": "Internal Server Error"},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertFalse(signals.api_ok)
        self.assertEqual(signals.api_status_code, 500)

    def test_graph_bundle_extracts_graph_observables(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "graph_query",
            "graph_result": {"ok": True, "count": 3, "data": [{"n": 1}]},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertTrue(signals.graph_ok)
        self.assertEqual(signals.rows_returned, 3)
        self.assertIsNone(signals.api_ok)

    def test_graph_bundle_failure(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "graph_query",
            "graph_result": {"ok": False, "error": "Invalid Cypher", "count": 0},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertFalse(signals.graph_ok)
        self.assertEqual(signals.rows_returned, 0)

    def test_reporter_bundle_with_saved_files(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "reporter",
            "reporter_result": {"ok": True, "rows_returned": 42},
            "report_saved_files": {"geo_seq_workbooks": ["/path/to/file.xlsx"]},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertTrue(signals.api_ok)  # reporter_result.ok maps to api_ok
        self.assertEqual(signals.rows_returned, 42)
        self.assertTrue(signals.has_artifacts)

    def test_reporter_failure(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "reporter",
            "reporter_result": {"ok": False, "error": "No UIDs found"},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertFalse(signals.api_ok)
        self.assertIsNone(signals.rows_returned)

    def test_plan_bundle_all_steps_ok(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "plan",
            "plan": {"steps": [{"id": "s0"}, {"id": "s1"}, {"id": "s2"}]},
            "step_results": {
                "s0": {"ok": True, "count": 10},
                "s1": {"ok": True, "count": 5},
                "s2": {"ok": True, "count": 3},
            },
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertEqual(signals.plan_steps_total, 3)
        self.assertEqual(signals.plan_steps_executed, 3)
        self.assertEqual(signals.plan_steps_ok, 3)
        self.assertEqual(signals.plan_steps_failed, 0)
        self.assertIsNone(signals.plan_stop_reason)

    def test_plan_bundle_step_failed_early(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "plan",
            "plan": {"steps": [{"id": "s0"}, {"id": "s1"}, {"id": "s2"}]},
            "step_results": {
                "s0": {"ok": True, "count": 10},
                "s1": {"ok": False, "error": "API returned 404"},
            },
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertEqual(signals.plan_steps_total, 3)
        self.assertEqual(signals.plan_steps_executed, 2)
        self.assertEqual(signals.plan_steps_ok, 1)
        self.assertEqual(signals.plan_steps_failed, 1)
        self.assertEqual(signals.plan_stop_reason, "API returned 404")

    def test_plan_bundle_incomplete_no_failure(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "plan",
            "plan": {"steps": [{"id": "s0"}, {"id": "s1"}]},
            "step_results": {"s0": {"ok": True}},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertEqual(signals.plan_stop_reason, "incomplete")

    def test_error_task_extracts_error_info(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        result = {"error": "LLM timeout after 30 seconds", "agent": "router"}
        signals = _build_retry_signals("error", result, None, None)
        self.assertTrue(signals.query_error_present)
        self.assertEqual(signals.raw_error_excerpt, "LLM timeout after 30 seconds")
        self.assertFalse(signals.bundle_present)

    def test_no_bundle_no_result(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        signals = _build_retry_signals("pending", None, None, None)
        self.assertEqual(signals.assistant_status, "pending")
        self.assertFalse(signals.bundle_present)
        self.assertIsNone(signals.api_ok)
        self.assertIsNone(signals.graph_ok)

    def test_has_prior_context_true(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        user = User.objects.create_user(username="siguser", password="pass")
        session = ChatSession.objects.create(
            user=user,
            results_history=[{"id": 1}, {"id": 2}],
        )
        signals = _build_retry_signals(
            "completed", None,
            {"mode": "new_search", "api_result_full": {"ok": True, "status_code": 200}},
            session,
        )
        self.assertTrue(signals.has_prior_context)

    def test_has_prior_context_false_single_bundle(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        user = User.objects.create_user(username="siguser2", password="pass")
        session = ChatSession.objects.create(
            user=user,
            results_history=[{"id": 1}],
        )
        signals = _build_retry_signals(
            "completed", None,
            {"mode": "new_search", "api_result_full": {"ok": True, "status_code": 200}},
            session,
        )
        self.assertFalse(signals.has_prior_context)

    def test_raw_error_excerpt_truncated(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        long_error = "x" * 500
        result = {"error": long_error}
        signals = _build_retry_signals("error", result, None, None)
        self.assertEqual(len(signals.raw_error_excerpt), 200)

    def test_refine_last_search_uses_same_path_as_new_search(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "refine_last_search",
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 50}},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertTrue(signals.api_ok)
        self.assertEqual(signals.rows_returned, 50)
        self.assertEqual(signals.path_mode, "refine_last_search")

    def test_plan_mode_rows_returned_is_none(self):
        from nextseek_api.services.evaluator import _build_retry_signals
        bundle = {
            "mode": "plan",
            "plan": {"steps": [{"id": "s0"}]},
            "step_results": {"s0": {"ok": True, "count": 10}},
        }
        signals = _build_retry_signals("completed", None, bundle, None)
        self.assertIsNone(signals.rows_returned)
