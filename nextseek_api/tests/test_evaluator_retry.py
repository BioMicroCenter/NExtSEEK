"""
Tests for the evaluator retry execution endpoint.

Covers:
- POST /evaluator/retry/ — submit a retry query through the assistant pipeline
- Source resolution: task_id OR session_id+bundle_id (mutually exclusive)
- Credential handling: Basic auth → "basic_auth", else → "service_account"
- Validation: missing query/mode (422), missing/conflicting identifiers (400)
- Permission enforcement: admin-only (403)
- Not found: task, session, bundle (404)
- New QueryTask ownership and session linkage
"""

import sys
import uuid
from unittest.mock import MagicMock, patch

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
_TEST_URLCONF = "nextseek_api.tests.test_evaluator_retry"

RETRY_URL = "/nextseek_api/evaluator/retry/"

# ---------------------------------------------------------------------------
# Test fixtures
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


# ===========================================================================
# Retry endpoint tests
# ===========================================================================

@override_settings(ROOT_URLCONF=_TEST_URLCONF)
class TestEvaluatorRetryExecute(TestCase):
    """Test POST /evaluator/retry/ endpoint."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_retry",
            password="admin1234",
            is_staff=True,
            is_superuser=True,
        )
        self.regular = User.objects.create_user(
            username="regular_retry",
            password="pass1234",
        )
        self.client = APIClient()
        self.session = ChatSession.objects.create(
            user=self.admin,
            results_history=[BUNDLE_NEW_SEARCH, BUNDLE_GRAPH_QUERY],
            last_debug={"endpoint": "/samples"},
        )
        self.task = QueryTask.objects.create(
            session=self.session,
            user=self.admin,
            query="Find mice",
            status="completed",
            result={"reply": "Found 5", "bundle_id": 1},
        )

    # ------------------------------------------------------------------
    # Happy path: retry by task_id with basic auth
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_retry_by_task_id_basic_auth(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Find mice treated with NDMA",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["credential_source"], "basic_auth")
        self.assertIn("task_id", data)
        self.assertEqual(data["source_task_id"], str(self.task.task_id))
        self.assertEqual(data["source_session_id"], str(self.session.session_id))
        self.assertEqual(data["source_bundle_id"], 1)
        # Thread was started
        mock_thread.return_value.start.assert_called_once()

    # ------------------------------------------------------------------
    # Happy path: retry by session_id + bundle_id
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_retry_by_session_and_bundle(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "session_id": str(self.session.session_id),
                "bundle_id": 1,
                "query": "Refined query",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["source_bundle_id"], 1)
        self.assertIsNone(data["source_task_id"])
        self.assertEqual(data["session_id"], str(self.session.session_id))

    # ------------------------------------------------------------------
    # Token auth falls back to service_account
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_retry_token_auth_falls_back(self, mock_auth, mock_thread):
        mock_auth.return_value = (None, {"Authorization": "Token abc"})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Retry query",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["credential_source"], "service_account")

    # ------------------------------------------------------------------
    # No credentials falls back to service_account
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_retry_no_credentials_falls_back(self, mock_auth, mock_thread):
        mock_auth.return_value = (None, None)
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Retry query",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["credential_source"], "service_account")

    # ------------------------------------------------------------------
    # Plan mode
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_retry_plan_mode(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Plan analysis of NDMA data",
                "mode": "plan",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)

    # ------------------------------------------------------------------
    # Non-admin forbidden
    # ------------------------------------------------------------------
    def test_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # No source identifiers → 400
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_no_source_identifiers(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {"query": "Find mice", "mode": "standard"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # Both task_id AND session_id+bundle_id → 400
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_both_task_and_session(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "session_id": str(self.session.session_id),
                "bundle_id": 1,
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # session_id without bundle_id → 400
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_session_without_bundle(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "session_id": str(self.session.session_id),
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # Task not found → 404
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_task_not_found(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        fake_id = uuid.uuid4()
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(fake_id),
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # Session not found → 404
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_session_not_found(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        fake_id = uuid.uuid4()
        resp = self.client.post(
            RETRY_URL,
            {
                "session_id": str(fake_id),
                "bundle_id": 1,
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # Bundle not found → 404
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_bundle_not_found(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "session_id": str(self.session.session_id),
                "bundle_id": 999,
                "query": "Find mice",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertIn("errors", data)

    # ------------------------------------------------------------------
    # New task owned by admin caller
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_new_task_owned_by_admin(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Find mice treated with NDMA",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        new_task_id = resp.json()["task_id"]
        new_task = QueryTask.objects.get(task_id=new_task_id)
        self.assertEqual(new_task.user_id, self.admin.pk)

    # ------------------------------------------------------------------
    # New task linked to original session
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_new_task_linked_to_session(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Find mice treated with NDMA",
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        new_task_id = resp.json()["task_id"]
        new_task = QueryTask.objects.get(task_id=new_task_id)
        self.assertEqual(new_task.session_id, self.session.pk)

    # ------------------------------------------------------------------
    # Missing query → 422
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_missing_query(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "mode": "standard",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 422)

    # ------------------------------------------------------------------
    # Missing mode → 422
    # ------------------------------------------------------------------
    @patch("nextseek_api.services.evaluator.threading.Thread")
    @patch("nextseek_api.services.evaluator.resolve_seek_auth")
    def test_missing_mode(self, mock_auth, mock_thread):
        mock_auth.return_value = (("admin_user", "admin_pass"), {})
        mock_thread.return_value = MagicMock()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            RETRY_URL,
            {
                "task_id": str(self.task.task_id),
                "query": "Find mice",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 422)
