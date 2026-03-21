"""
Comprehensive tests for nextseek_api/services/assistant.py.

Covers gaps NOT addressed by test_assistant_unit.py:
- _check_auth with various resolve_seek_auth outcomes
- _error_response helper
- UserInParticipatingProject permission class (has_permission, has_object_permission)
- CsrfExemptSessionAuthentication.enforce_csrf
- SSE query pipeline error handling
- Async query pipeline error handling + ownership validation
- download_artifact endpoint (all branches)
- get_session with populated results_history
- task_progress with error status
- me() with superuser-only (not staff)
- test_cases with superuser-only (not staff)
- download_bundle with non-existent session
"""

import io
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory

from nextseek_api.assistant.models_db import ChatSession, QueryTask

# Stub chat_nextseek.helpers.load_prompt BEFORE chat_nextseek.agents is imported.
if "chat_nextseek.agents" not in sys.modules:
    patch("chat_nextseek.helpers.load_prompt", return_value="(test stub)").start()


# ============================================================================
# _error_response helper
# ============================================================================

class ErrorResponseHelperTests(TestCase):
    """Test the _error_response standalone helper."""

    def test_error_response_structure(self):
        from nextseek_api.services.assistant import _error_response
        resp = _error_response("Bad Request", "Something went wrong", 400)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("errors", resp.data)
        self.assertEqual(len(resp.data["errors"]), 1)
        self.assertEqual(resp.data["errors"][0]["title"], "Bad Request")
        self.assertEqual(resp.data["errors"][0]["detail"], "Something went wrong")

    def test_error_response_various_status_codes(self):
        from nextseek_api.services.assistant import _error_response
        for code in (401, 403, 404, 422, 500):
            resp = _error_response("Title", "Detail", code)
            self.assertEqual(resp.status_code, code)


# ============================================================================
# CsrfExemptSessionAuthentication
# ============================================================================

class CsrfExemptSessionAuthTests(TestCase):
    """Test CsrfExemptSessionAuthentication.enforce_csrf returns None."""

    def test_enforce_csrf_returns_none(self):
        from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
        auth = CsrfExemptSessionAuthentication()
        request = MagicMock()
        result = auth.enforce_csrf(request)
        self.assertIsNone(result)


# ============================================================================
# UserInParticipatingProject permission
# ============================================================================

class UserInParticipatingProjectTests(TestCase):
    """Test UserInParticipatingProject permission class."""

    def setUp(self):
        self.user = User.objects.create_user(username="permuser", password="pass1234")

    @patch("nextseek_api.services.assistant.SeekAPIClient")
    def test_has_permission_user_in_project(self, mock_client_cls):
        """Returns True when user is in a participating project."""
        from nextseek_api.services.assistant import UserInParticipatingProject

        mock_client = mock_client_cls.return_value
        mock_client.get_current_person.return_value = (
            json.dumps({
                "data": {
                    "relationships": {
                        "projects": {
                            "data": [{"id": "1"}, {"id": "5"}]
                        }
                    }
                }
            }),
        )

        with override_settings(ASSISTANT_PARTICIPATING_PROJECTS={"5"}):
            # Need to re-import or patch the module-level constant
            with patch("nextseek_api.services.assistant.ASSISTANT_PARTICIPATING_PROJECTS", {"5"}):
                perm = UserInParticipatingProject()
                request = MagicMock()
                view = MagicMock()
                result = perm.has_permission(request, view)
                self.assertTrue(result)

    @patch("nextseek_api.services.assistant.SeekAPIClient")
    def test_has_permission_user_not_in_project(self, mock_client_cls):
        """Returns False when user's projects don't overlap."""
        from nextseek_api.services.assistant import UserInParticipatingProject

        mock_client = mock_client_cls.return_value
        mock_client.get_current_person.return_value = (
            json.dumps({
                "data": {
                    "relationships": {
                        "projects": {
                            "data": [{"id": "99"}]
                        }
                    }
                }
            }),
        )

        with patch("nextseek_api.services.assistant.ASSISTANT_PARTICIPATING_PROJECTS", {"5", "10"}):
            perm = UserInParticipatingProject()
            request = MagicMock()
            view = MagicMock()
            result = perm.has_permission(request, view)
            self.assertFalse(result)

    @patch("nextseek_api.services.assistant.SeekAPIClient")
    def test_has_permission_no_projects(self, mock_client_cls):
        """Returns False when user has no projects."""
        from nextseek_api.services.assistant import UserInParticipatingProject

        mock_client = mock_client_cls.return_value
        mock_client.get_current_person.return_value = (
            json.dumps({
                "data": {
                    "relationships": {
                        "projects": {
                            "data": []
                        }
                    }
                }
            }),
        )

        with patch("nextseek_api.services.assistant.ASSISTANT_PARTICIPATING_PROJECTS", {"5"}):
            perm = UserInParticipatingProject()
            request = MagicMock()
            view = MagicMock()
            result = perm.has_permission(request, view)
            self.assertFalse(result)

    @patch("nextseek_api.services.assistant.SeekAPIClient")
    def test_has_permission_exception_returns_false(self, mock_client_cls):
        """Returns False when SeekAPIClient raises an exception."""
        from nextseek_api.services.assistant import UserInParticipatingProject

        mock_client = mock_client_cls.return_value
        mock_client.get_current_person.side_effect = Exception("connection error")

        perm = UserInParticipatingProject()
        request = MagicMock()
        view = MagicMock()
        result = perm.has_permission(request, view)
        self.assertFalse(result)

    def test_message_attribute(self):
        """Permission has a descriptive message."""
        from nextseek_api.services.assistant import UserInParticipatingProject
        perm = UserInParticipatingProject()
        self.assertIn("participating project", perm.message)

    def test_has_object_permission_delegates(self):
        """has_object_permission calls has_permissions (note: typo in source)."""
        from nextseek_api.services.assistant import UserInParticipatingProject
        perm = UserInParticipatingProject()
        request = MagicMock()
        view = MagicMock()
        # has_object_permission calls self.has_permissions which doesn't exist
        # (typo: should be has_permission). This should raise AttributeError.
        with self.assertRaises(AttributeError):
            perm.has_object_permission(request, view)


# ============================================================================
# _check_auth method
# ============================================================================

class CheckAuthTests(TestCase):
    """Test AssistantViewSet._check_auth with various auth combos."""

    def setUp(self):
        self.user = User.objects.create_user(username="authuser", password="pass1234")
        self.client = APIClient()
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_check_auth_basic_auth_succeeds(self, mock_resolve):
        """When resolve_seek_auth returns basic_tuple, _check_auth passes."""
        mock_resolve.return_value = (("user", "pass"), {})
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_check_auth_token_auth_succeeds(self, mock_resolve):
        """When resolve_seek_auth returns extra_headers, _check_auth passes."""
        mock_resolve.return_value = (None, {"Authorization": "Token abc123"})
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_check_auth_authenticated_user_no_seek_auth(self, mock_resolve):
        """When resolve_seek_auth returns nothing but user.is_authenticated, passes."""
        mock_resolve.return_value = (None, None)
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_check_auth_no_auth_at_all_returns_401(self, mock_resolve):
        """When resolve_seek_auth returns nothing and user not authenticated, 401."""
        mock_resolve.return_value = (None, None)
        # Create a request directly to test _check_auth returning 401
        from nextseek_api.services.assistant import AssistantViewSet
        factory = APIRequestFactory()
        request = factory.get("/nextseek_api/assistant/me/")
        request.user = MagicMock()
        request.user.is_authenticated = False
        view = AssistantViewSet()
        authed, err = view._check_auth(request)
        self.assertFalse(authed)
        self.assertEqual(err.status_code, 401)
        self.assertIn("Authentication required", err.data["errors"][0]["title"])


# ============================================================================
# GET /assistant/me/ — additional cases
# ============================================================================

class MeEndpointExtraTests(TestCase):
    """Additional me() endpoint tests."""

    def setUp(self):
        self.client = APIClient()
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_me_superuser_not_staff(self, mock_resolve):
        """Superuser (not staff) returns is_admin=True."""
        mock_resolve.return_value = (("u", "p"), {})
        user = User.objects.create_user(
            username="superonly", password="pass", is_superuser=True, is_staff=False
        )
        self.client.force_authenticate(user=user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["is_admin"])

    @patch("nextseek_api.services.assistant.resolve_seek_auth")
    def test_me_regular_user_not_admin(self, mock_resolve):
        """Regular user (not staff, not superuser) returns is_admin=False."""
        mock_resolve.return_value = (("u", "p"), {})
        user = User.objects.create_user(username="regular", password="pass")
        self.client.force_authenticate(user=user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["is_admin"])


# ============================================================================
# GET /assistant/sessions/{id}/ — additional cases
# ============================================================================

class GetSessionExtraTests(TestCase):
    """Additional get_session tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="sessuser", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_session_with_results(self):
        """Session with results_history returns correct query_count and has_results."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "query": "q1"},
                {"id": 2, "query": "q2"},
                {"id": 3, "query": "q3"},
            ],
        )
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["query_count"], 3)
        self.assertTrue(resp.data["has_results"])

    def test_get_session_empty_results(self):
        """Session with no results returns query_count=0 and has_results=False."""
        session = ChatSession.objects.create(user=self.user, results_history=[])
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["query_count"], 0)
        self.assertFalse(resp.data["has_results"])

    def test_get_session_returns_correct_session_id(self):
        """Session detail response contains the correct session_id."""
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["session_id"], str(session.session_id))


# ============================================================================
# POST /assistant/query/ — pipeline error handling
# ============================================================================

class QuerySSEPipelineErrorTests(TestCase):
    """Test SSE query pipeline error handling."""

    def setUp(self):
        self.user = User.objects.create_user(username="sseuser", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_pipeline_exception_sends_error_event(self, mock_run_query, mock_adapter_cls):
        """When run_query raises an exception, a query_error SSE event is sent."""
        mock_run_query.side_effect = RuntimeError("LLM timeout")

        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "Find mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])

        content = b"".join(resp.streaming_content).decode()
        self.assertIn("event: query_error", content)
        self.assertIn("Internal pipeline error", content)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_pipeline_error_includes_session_id(self, mock_run_query, mock_adapter_cls):
        """query_error event includes session_id."""
        mock_run_query.side_effect = ValueError("bad input")

        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        content = b"".join(resp.streaming_content).decode()
        self.assertIn(str(session.session_id), content)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_sse_cache_control_headers(self, mock_run_query, mock_adapter_cls):
        """SSE response has Cache-Control: no-cache and X-Accel-Buffering: no."""
        def fake_run(adapter, config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp["Cache-Control"], "no-cache")
        self.assertEqual(resp["X-Accel-Buffering"], "no")

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_multiple_events_stream(self, mock_run_query, mock_adapter_cls):
        """Multiple SSE events are streamed in order."""
        def fake_run(adapter, config, query, send_event):
            send_event("agent_started", {"agent": "parser", "mode": "search"})
            send_event("agent_complete", {"agent": "parser", "summary": {}})
            send_event("agent_started", {"agent": "api_caller", "mode": ""})
            send_event("query_complete", {"reply": "found 3", "debug": {}, "bundle_id": 1})

        mock_run_query.side_effect = fake_run
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "find mice"},
            format="json",
        )
        content = b"".join(resp.streaming_content).decode()
        events = [line for line in content.split("\n") if line.startswith("event:")]
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], "event: agent_started")
        self.assertEqual(events[1], "event: agent_complete")
        self.assertEqual(events[2], "event: agent_started")
        self.assertEqual(events[3], "event: query_complete")

    def test_query_with_other_users_session(self):
        """Query with another user's session_id returns 404."""
        other = User.objects.create_user(username="other_sse", password="pass1234")
        session = ChatSession.objects.create(user=other)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_query_extra_fields_rejected(self):
        """QueryRequest with extra='forbid' rejects unknown fields."""
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test", "extra_field": "bad"},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_query_empty_string_rejected(self):
        """QueryRequest with empty query string is rejected (min_length=1)."""
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)


# ============================================================================
# POST /assistant/query/async/ — additional cases
# ============================================================================

class QueryAsyncExtraTests(TestCase):
    """Additional async query endpoint tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="asyncuser", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_query_async_with_other_users_session(self):
        """Async query with another user's session returns 404."""
        other = User.objects.create_user(username="other_async", password="pass1234")
        session = ChatSession.objects.create(user=other)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_creates_query_task_with_running_status(self, mock_run_query, mock_adapter_cls):
        """Async query creates QueryTask with status='running'."""
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(session.session_id), "query": "Find mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        task = QueryTask.objects.get(user=self.user)
        self.assertEqual(task.status, "running")
        self.assertEqual(task.query, "Find mice")

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_task_linked_to_session(self, mock_run_query, mock_adapter_cls):
        """QueryTask is linked to the correct session."""
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        task = QueryTask.objects.get(user=self.user)
        self.assertEqual(task.session_id, session.session_id)

    def test_query_async_extra_fields_rejected(self):
        """Async query with extra fields rejected (extra='forbid')."""
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "test", "unknown_field": "value"},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_query_async_empty_query_rejected(self):
        """Async query with empty string query rejected."""
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)


# ============================================================================
# GET /assistant/tasks/{id}/progress/ — additional cases
# ============================================================================

class TaskProgressExtraTests(TestCase):
    """Additional task progress tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="taskuser", password="pass1234")
        self.session = ChatSession.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_task_progress_error_status_includes_result(self):
        """Task with error status includes result in response."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="error",
            result={"error": "LLM timeout", "agent": "parser"},
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "error")
        self.assertIsNotNone(resp.data["result"])
        self.assertEqual(resp.data["result"]["error"], "LLM timeout")

    def test_task_progress_running_no_result(self):
        """Running task does not expose result even if one exists."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="running",
            result={"partial": "data"},  # should not be exposed
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "running")
        self.assertIsNone(resp.data["result"])

    def test_task_progress_empty_progress_list(self):
        """Task with empty progress list returns empty progress array."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="running",
            progress=[],
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["progress"], [])

    def test_task_progress_preserves_event_data(self):
        """Progress events retain their original event and data fields."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="running",
            progress=[
                {"event": "agent_started", "data": {"agent": "parser", "mode": "search"}},
                {"event": "thinking", "data": {"step": "analyzing"}},
            ],
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["progress"]), 2)
        self.assertEqual(resp.data["progress"][0]["event"], "agent_started")
        self.assertEqual(resp.data["progress"][0]["data"]["agent"], "parser")
        self.assertEqual(resp.data["progress"][1]["event"], "thinking")

    def test_task_progress_missing_event_key_defaults_empty(self):
        """Progress entry without 'event' key defaults to empty string."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="running",
            progress=[
                {"data": {"step": "no event key here"}},
            ],
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["progress"][0]["event"], "")

    def test_task_progress_missing_data_key_defaults_empty(self):
        """Progress entry without 'data' key defaults to empty dict."""
        task = QueryTask.objects.create(
            session=self.session,
            user=self.user,
            query="test",
            status="running",
            progress=[
                {"event": "thinking"},
            ],
        )
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["progress"][0]["data"], {})


# ============================================================================
# GET /assistant/sessions/{sid}/bundles/{bid}/ — additional cases
# ============================================================================

class DownloadBundleExtraTests(TestCase):
    """Additional download_bundle tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="bundleuser", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_download_bundle_nonexistent_session(self):
        """Bundle request for non-existent session returns 404."""
        fake_id = uuid.uuid4()
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{fake_id}/bundles/1/")
        self.assertEqual(resp.status_code, 404)

    def test_download_bundle_returns_full_bundle_data(self):
        """Bundle response includes all bundle fields."""
        bundle = {"id": 42, "query": "test", "mode": "new_search", "api_result_full": {"data": []}}
        session = ChatSession.objects.create(user=self.user, results_history=[bundle])
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/42/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], 42)
        self.assertEqual(resp.data["mode"], "new_search")


# ============================================================================
# GET /assistant/sessions/{sid}/bundles/{bid}/artifacts/{key}/ — download_artifact
# ============================================================================

class DownloadArtifactTests(TestCase):
    """Test download_artifact endpoint — all code branches."""

    def setUp(self):
        self.user = User.objects.create_user(username="artifactuser", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_artifact_nonexistent_session(self):
        """Artifact request for non-existent session returns 404."""
        fake_id = uuid.uuid4()
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{fake_id}/bundles/1/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_wrong_user(self):
        """Artifact request for another user's session returns 403."""
        other = User.objects.create_user(username="other_art", password="pass1234")
        session = ChatSession.objects.create(
            user=other,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 403)

    def test_artifact_bundle_not_found(self):
        """Artifact request for non-existent bundle returns 404."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/999/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_unknown_key_not_found(self):
        """Artifact request for unknown key returns 404."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "mode": "new_search"}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/nonexistent/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_geo_seq_workbooks_no_files(self):
        """geo_seq_workbooks with no workbooks returns 404."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "report_saved_files": {}}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/geo_seq_workbooks/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_geo_seq_workbooks_file_missing_on_disk(self):
        """geo_seq_workbooks with file path that doesn't exist returns 404."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{
                "id": 1,
                "report_saved_files": {
                    "geo_seq_workbooks": ["/opt/NExtSEEK/nonexistent_workbook.xlsx"],
                },
            }],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/geo_seq_workbooks/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_geo_seq_workbooks_path_traversal_forbidden(self):
        """geo_seq_workbooks with path outside allowed dirs returns 403."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{
                "id": 1,
                "report_saved_files": {
                    "geo_seq_workbooks": ["/etc/passwd"],
                },
            }],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/geo_seq_workbooks/"
        )
        # Should be 403 (path traversal protection) or 404 (not a file)
        self.assertIn(resp.status_code, (403, 404))

    def test_artifact_geo_seq_workbooks_success(self):
        """geo_seq_workbooks with valid file on disk returns FileResponse."""
        import tempfile
        import os
        # Create a temp file within the project directory
        tmp = tempfile.NamedTemporaryFile(
            suffix=".xlsx", dir="/opt/NExtSEEK", delete=False
        )
        tmp.write(b"PK\x03\x04fake xlsx")
        tmp.close()
        try:
            session = ChatSession.objects.create(
                user=self.user,
                results_history=[{
                    "id": 1,
                    "report_saved_files": {
                        "geo_seq_workbooks": [tmp.name],
                    },
                }],
            )
            resp = self.client.get(
                f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/geo_seq_workbooks/"
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn("spreadsheetml", resp["Content-Type"])
        finally:
            os.unlink(tmp.name)

    @patch("nextseek_api.assistant.excel_export.generate_search_xlsx")
    def test_artifact_search_results_success(self, mock_gen_xlsx):
        """search_results artifact generates xlsx for search bundles."""
        mock_gen_xlsx.return_value = b"PK\x03\x04fake_xlsx_content"
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "mode": "new_search", "api_result_full": {"data": []}}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/search_results/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        mock_gen_xlsx.assert_called_once()

    def test_artifact_search_results_non_search_bundle(self):
        """search_results artifact on non-search bundle returns 404."""
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "mode": "report"}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/search_results/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_search_results_refine_mode(self):
        """search_results works for refine_last_search mode too."""
        with patch("nextseek_api.assistant.excel_export.generate_search_xlsx") as mock_gen:
            mock_gen.return_value = b"PK\x03\x04xlsx_bytes"
            session = ChatSession.objects.create(
                user=self.user,
                results_history=[{"id": 1, "mode": "refine_last_search"}],
            )
            resp = self.client.get(
                f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/search_results/"
            )
            self.assertEqual(resp.status_code, 200)

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    @patch("nextseek_api.assistant.excel_export.generate_table_xlsx")
    def test_artifact_all_tables_success(self, mock_gen_xlsx, mock_build):
        """all_tables artifact generates combined xlsx."""
        mock_build.return_value = [
            {"key": "samples", "label": "Samples", "columns": ["id"], "data": [{"id": 1}]},
        ]
        mock_gen_xlsx.return_value = b"PK\x03\x04xlsx"
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/all_tables/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    def test_artifact_all_tables_no_data(self, mock_build):
        """all_tables with no report data returns 404."""
        mock_build.return_value = []
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/all_tables/"
        )
        self.assertEqual(resp.status_code, 404)

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    @patch("nextseek_api.assistant.excel_export.generate_table_xlsx")
    def test_artifact_all_tables_generation_error(self, mock_gen_xlsx, mock_build):
        """all_tables xlsx generation error returns 500."""
        mock_build.return_value = [{"key": "t1", "label": "T1", "columns": ["c"], "data": []}]
        mock_gen_xlsx.side_effect = Exception("openpyxl error")
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/all_tables/"
        )
        self.assertEqual(resp.status_code, 500)

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    @patch("nextseek_api.assistant.excel_export.generate_table_xlsx")
    def test_artifact_single_table_by_key(self, mock_gen_xlsx, mock_build):
        """Artifact with a specific table key generates xlsx for that table."""
        mock_build.return_value = [
            {"key": "samples", "label": "Samples", "columns": ["id"], "data": [{"id": 1}]},
            {"key": "protocols", "label": "Protocols", "columns": ["name"], "data": [{"name": "p1"}]},
        ]
        mock_gen_xlsx.return_value = b"PK\x03\x04xlsx"
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
        # Should only pass the matching table
        mock_gen_xlsx.assert_called_once()
        args = mock_gen_xlsx.call_args[0][0]
        self.assertEqual(len(args), 1)
        self.assertEqual(args[0]["key"], "samples")

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    @patch("nextseek_api.assistant.excel_export.generate_table_xlsx")
    def test_artifact_single_table_generation_error(self, mock_gen_xlsx, mock_build):
        """Single table xlsx generation error returns 500."""
        mock_build.return_value = [
            {"key": "samples", "label": "Samples", "columns": ["id"], "data": [{"id": 1}]},
        ]
        mock_gen_xlsx.side_effect = Exception("write error")
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 500)

    @patch("nextseek_api.assistant.excel_export.build_tables_from_bundle")
    def test_artifact_key_not_in_tables(self, mock_build):
        """Artifact with key not matching any table returns 404."""
        mock_build.return_value = [
            {"key": "samples", "label": "Samples", "columns": ["id"], "data": [{"id": 1}]},
        ]
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/no_such_table/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_artifact_empty_results_history(self):
        """Artifact with empty results_history returns 404."""
        session = ChatSession.objects.create(user=self.user, results_history=[])
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/artifacts/samples/"
        )
        self.assertEqual(resp.status_code, 404)


# ============================================================================
# GET /assistant/test-cases/ — additional cases
# ============================================================================

class TestCasesExtraTests(TestCase):
    """Additional test_cases endpoint tests."""

    def setUp(self):
        self.client = APIClient()
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_test_cases_superuser_not_staff(self):
        """Superuser (not staff) can access test cases."""
        user = User.objects.create_user(
            username="super_tc", password="pass", is_superuser=True, is_staff=False
        )
        self.client.force_authenticate(user=user)
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total", resp.data)

    def test_test_cases_response_structure(self):
        """Test cases response has total and test_cases list with id and prompt."""
        admin = User.objects.create_user(
            username="tc_admin", password="pass", is_staff=True
        )
        self.client.force_authenticate(user=admin)
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data["total"], int)
        self.assertIsInstance(resp.data["test_cases"], list)
        if resp.data["total"] > 0:
            tc = resp.data["test_cases"][0]
            self.assertIn("id", tc)
            self.assertIn("prompt", tc)


# ============================================================================
# ViewSet authentication_classes and permission_classes
# ============================================================================

class ViewSetConfigTests(TestCase):
    """Verify ViewSet class-level configuration."""

    def test_authentication_classes(self):
        from nextseek_api.services.assistant import (
            AssistantViewSet,
            CsrfExemptSessionAuthentication,
        )
        from rest_framework.authentication import TokenAuthentication, BasicAuthentication

        auth_classes = AssistantViewSet.authentication_classes
        # Should have 3 authentication classes
        self.assertEqual(len(auth_classes), 3)
        self.assertIn(TokenAuthentication, auth_classes)
        self.assertIn(CsrfExemptSessionAuthentication, auth_classes)
        self.assertIn(BasicAuthentication, auth_classes)

    def test_permission_classes(self):
        from nextseek_api.services.assistant import (
            AssistantViewSet,
            UserInParticipatingProject,
        )
        from rest_framework.permissions import IsAuthenticated

        perm_classes = AssistantViewSet.permission_classes
        self.assertEqual(len(perm_classes), 2)
        self.assertIn(IsAuthenticated, perm_classes)
        self.assertIn(UserInParticipatingProject, perm_classes)


# ============================================================================
# Unauthenticated access across all endpoints
# ============================================================================

class UnauthenticatedAccessTests(TestCase):
    """Verify all endpoints reject unauthenticated requests."""

    def setUp(self):
        self.client = APIClient()
        # Do NOT mock permission — let real permission deny

    def test_me_unauthenticated(self):
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertIn(resp.status_code, (401, 403))

    def test_create_session_unauthenticated(self):
        resp = self.client.post("/nextseek_api/assistant/sessions/")
        self.assertIn(resp.status_code, (401, 403))

    def test_get_session_unauthenticated(self):
        fake_id = uuid.uuid4()
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{fake_id}/")
        self.assertIn(resp.status_code, (401, 403))

    def test_query_unauthenticated(self):
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"query": "test"},
            format="json",
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_query_async_unauthenticated(self):
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "test"},
            format="json",
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_task_progress_unauthenticated(self):
        fake_id = uuid.uuid4()
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{fake_id}/progress/")
        self.assertIn(resp.status_code, (401, 403))

    def test_download_bundle_unauthenticated(self):
        fake_id = uuid.uuid4()
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{fake_id}/bundles/1/")
        self.assertIn(resp.status_code, (401, 403))

    def test_test_cases_unauthenticated(self):
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertIn(resp.status_code, (401, 403))

    def test_download_artifact_unauthenticated(self):
        fake_id = uuid.uuid4()
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{fake_id}/bundles/1/artifacts/samples/"
        )
        self.assertIn(resp.status_code, (401, 403))


# ============================================================================
# Permission denied (not in participating project)
# ============================================================================

class PermissionDeniedTests(TestCase):
    """Test that UserInParticipatingProject=False blocks access."""

    def setUp(self):
        self.user = User.objects.create_user(username="blocked", password="pass1234")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        # Permission returns False — user NOT in participating project
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_me_permission_denied(self):
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 403)

    def test_create_session_permission_denied(self):
        resp = self.client.post("/nextseek_api/assistant/sessions/")
        self.assertEqual(resp.status_code, 403)

    def test_query_permission_denied(self):
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_query_async_permission_denied(self):
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_test_cases_permission_denied(self):
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertEqual(resp.status_code, 403)
