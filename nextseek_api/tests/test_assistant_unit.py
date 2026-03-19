"""
Unit tests for the Assistant integration (chat_nextseek → Django).

Tests cover:
- ChatSession ORM model (creation, UUID, JSONField, ownership)
- QueryTask ORM model (creation, progress, status transitions)
- DictSessionAdapter (get, set, save)
- DB event callback (make_db_event_callback for async pipeline)
- ViewSet endpoints (me, sessions, query SSE, query async, task progress,
  bundles, test-cases)

All chat_nextseek agents are mocked — no real LLM or HTTP calls.
"""

import json
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.models_api import (
    QueryRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    AssistantUserResponse,
    TestCaseListResponse,
)
from nextseek_api.assistant.session_adapter import DictSessionAdapter

# Stub chat_nextseek.helpers.load_prompt BEFORE chat_nextseek.agents is imported.
# The agents module calls load_prompt() at module level to read prompt text files
# that may not be present in the installed package (they are externalized to
# XDG dirs in the new chat_nextseek architecture).  All agent functions are
# mocked in the pipeline tests, so the actual prompt content is never needed.
if "chat_nextseek.agents" not in sys.modules:
    patch("chat_nextseek.helpers.load_prompt", return_value="(test stub)").start()


# ---------------------------------------------------------------------------
# SessionModelTests
# ---------------------------------------------------------------------------

class SessionModelTests(TestCase):
    """Test ChatSession Django ORM model."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")

    def test_create_session(self):
        session = ChatSession.objects.create(user=self.user)
        self.assertIsNotNone(session.session_id)
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.results_history, [])
        self.assertEqual(session.last_debug, {})

    def test_uuid_auto_generation(self):
        s1 = ChatSession.objects.create(user=self.user)
        s2 = ChatSession.objects.create(user=self.user)
        self.assertNotEqual(s1.session_id, s2.session_id)
        self.assertIsInstance(s1.session_id, uuid.UUID)

    def test_results_history_json(self):
        session = ChatSession.objects.create(user=self.user)
        session.results_history = [{"id": 1, "query": "test"}]
        session.save()
        session.refresh_from_db()
        self.assertEqual(len(session.results_history), 1)
        self.assertEqual(session.results_history[0]["query"], "test")

    def test_ownership_filter(self):
        other = User.objects.create_user(username="other", password="pass1234")
        ChatSession.objects.create(user=self.user)
        ChatSession.objects.create(user=other)
        mine = ChatSession.objects.filter(user=self.user)
        self.assertEqual(mine.count(), 1)

    def test_db_table_name(self):
        self.assertEqual(ChatSession._meta.db_table, "assistant_chat_session")


# ---------------------------------------------------------------------------
# SessionAdapterTests
# ---------------------------------------------------------------------------

class SessionAdapterTests(TestCase):
    """Test DictSessionAdapter wrapping a ChatSession."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.chat_session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "query": "old query"}],
            last_debug={"key": "val"},
        )

    def test_get_returns_correct_values(self):
        adapter = DictSessionAdapter(self.chat_session)
        self.assertEqual(len(adapter.get("results_history")), 1)
        self.assertEqual(adapter.get("last_debug"), {"key": "val"})
        self.assertIsNone(adapter.get("nonexistent"))
        self.assertEqual(adapter.get("nonexistent", "default"), "default")

    def test_setitem_updates_cache(self):
        adapter = DictSessionAdapter(self.chat_session)
        adapter["results_history"] = [{"id": 2}]
        self.assertEqual(adapter["results_history"], [{"id": 2}])

    def test_contains(self):
        adapter = DictSessionAdapter(self.chat_session)
        self.assertIn("results_history", adapter)
        self.assertNotIn("nonexistent", adapter)

    def test_save_persists_to_model(self):
        adapter = DictSessionAdapter(self.chat_session)
        adapter["results_history"] = [{"id": 99}]
        adapter["last_debug"] = {"updated": True}
        adapter.save()

        self.chat_session.refresh_from_db()
        self.assertEqual(self.chat_session.results_history, [{"id": 99}])
        self.assertEqual(self.chat_session.last_debug, {"updated": True})


# ---------------------------------------------------------------------------
# MakeDbEventCallbackTests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class MakeDbEventCallbackTests(TestCase):
    """Test make_db_event_callback — comprehensive coverage of pipeline_adapter.py."""

    def setUp(self):
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback
        self.make_cb = make_db_event_callback
        self.user = User.objects.create_user(username="cbuser", password="pass")
        self.session = ChatSession.objects.create(user=self.user)
        self.task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test query",
        )

    def test_callback_appends_progress(self):
        cb = self.make_cb(str(self.task.task_id), str(self.session.session_id))
        cb("thinking", {"step": "parsing"})
        self.task.refresh_from_db()
        self.assertEqual(len(self.task.progress), 1)
        self.assertEqual(self.task.progress[0]["event"], "thinking")

    def test_query_complete_sets_status_and_result(self):
        cb = self.make_cb(str(self.task.task_id), str(self.session.session_id))
        cb("query_complete", {"answer": "found 5 samples"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
        self.assertEqual(self.task.result["answer"], "found 5 samples")

    def test_query_error_sets_status_and_result(self):
        cb = self.make_cb(str(self.task.task_id), str(self.session.session_id))
        cb("query_error", {"error": "timeout"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "error")

    def test_terminal_events_inject_session_id(self):
        cb = self.make_cb(str(self.task.task_id), str(self.session.session_id))
        cb("query_complete", {"answer": "done"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.result["session_id"], str(self.session.session_id))

    def test_missing_task_logs_error(self):
        cb = self.make_cb("00000000-0000-0000-0000-000000000000", str(self.session.session_id))
        with self.assertLogs("nextseek_api.assistant.pipeline_adapter", level="ERROR"):
            cb("thinking", {"step": "test"})

    def test_multiple_events_accumulate(self):
        cb = self.make_cb(str(self.task.task_id), str(self.session.session_id))
        cb("thinking", {"step": 1})
        cb("searching", {"step": 2})
        cb("query_complete", {"answer": "done"})
        self.task.refresh_from_db()
        self.assertEqual(len(self.task.progress), 3)


# ---------------------------------------------------------------------------
# EmitCompleteTests
# ---------------------------------------------------------------------------

class EmitCompleteTests(TestCase):
    """Test _emit_complete helper from pipeline_adapter."""

    def test_emit_complete_without_artifacts(self):
        from nextseek_api.assistant.pipeline_adapter import _emit_complete
        events = []
        _emit_complete(lambda t, d: events.append((t, d)), "answer", {"key": "val"}, 42)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "query_complete")
        self.assertEqual(events[0][1]["reply"], "answer")
        self.assertEqual(events[0][1]["debug"], {"key": "val"})
        self.assertEqual(events[0][1]["bundle_id"], 42)
        self.assertNotIn("artifacts", events[0][1])

    def test_emit_complete_with_artifacts(self):
        from nextseek_api.assistant.pipeline_adapter import _emit_complete
        events = []
        artifacts = [{"type": "table", "data": []}]
        _emit_complete(lambda t, d: events.append((t, d)), "reply", {}, None, artifacts=artifacts)
        self.assertEqual(events[0][1]["artifacts"], artifacts)


# ---------------------------------------------------------------------------
# ViewSetTests
# ---------------------------------------------------------------------------

class ViewSetTests(TestCase):
    """Test AssistantViewSet endpoints using DRF APIClient."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.admin = User.objects.create_user(
            username="admin", password="admin1234", is_staff=True
        )
        self.client = APIClient()
        patcher = patch('nextseek_api.services.assistant.UserInParticipatingProject.has_permission', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_me_returns_username(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["username"], "testuser")
        self.assertFalse(resp.data["is_admin"])

    def test_me_admin(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["is_admin"])

    def test_me_unauthenticated(self):
        resp = self.client.get("/nextseek_api/assistant/me/")
        self.assertIn(resp.status_code, (401, 403))

    def test_create_session(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post("/nextseek_api/assistant/sessions/")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("session_id", resp.data)
        self.assertIn("created_at", resp.data)
        self.assertEqual(ChatSession.objects.count(), 1)

    def test_get_session(self):
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["query_count"], 0)
        self.assertFalse(resp.data["has_results"])

    def test_get_session_wrong_user(self):
        other = User.objects.create_user(username="other", password="pass1234")
        session = ChatSession.objects.create(user=other)
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 403)

    def test_get_session_not_found(self):
        self.client.force_authenticate(user=self.user)
        fake_id = uuid.uuid4()
        resp = self.client.get(f"/nextseek_api/assistant/sessions/{fake_id}/")
        self.assertEqual(resp.status_code, 404)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_returns_sse_content_type(self, mock_run_query, mock_adapter_cls):
        """Verify POST /assistant/query/ returns text/event-stream."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run_query

        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])

    def test_query_invalid_session(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(uuid.uuid4()), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_query_missing_fields(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_download_bundle(self):
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "query": "test", "mode": "new_search"}],
        )
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], 1)

    def test_download_bundle_not_found(self):
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/999/"
        )
        self.assertEqual(resp.status_code, 404)

    def test_download_bundle_wrong_user(self):
        other = User.objects.create_user(username="other", password="pass1234")
        session = ChatSession.objects.create(
            user=other,
            results_history=[{"id": 1}],
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            f"/nextseek_api/assistant/sessions/{session.session_id}/bundles/1/"
        )
        self.assertEqual(resp.status_code, 403)

    def test_test_cases_admin(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["total"], 0)
        self.assertEqual(len(resp.data["test_cases"]), resp.data["total"])

    def test_test_cases_non_admin_forbidden(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/nextseek_api/assistant/test-cases/")
        self.assertEqual(resp.status_code, 403)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_sse_event_format(self, mock_run_query, mock_adapter_cls):
        """Verify SSE events are correctly formatted and include session_id."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("agent_started", {"agent": "entity", "mode": ""})
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run_query

        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )

        content = b"".join(resp.streaming_content).decode()
        self.assertIn("event: agent_started", content)
        self.assertIn("event: query_complete", content)
        self.assertIn('"agent": "entity"', content)
        # session_id should be injected into query_complete
        self.assertIn(str(session.session_id), content)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_auto_session_reuses_recent(self, mock_run_query, mock_adapter_cls):
        """POST /query/ without session_id reuses the most recent session."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run_query

        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])

        content = b"".join(resp.streaming_content).decode()
        self.assertIn(str(session.session_id), content)
        # No new session should have been created
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_auto_session_creates_when_none(self, mock_run_query, mock_adapter_cls):
        """POST /query/ without session_id auto-creates when user has no sessions."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run_query

        self.client.force_authenticate(user=self.user)
        # No session created yet
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 0)

        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])

        # A session should have been auto-created
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)
        auto_session = ChatSession.objects.get(user=self.user)

        content = b"".join(resp.streaming_content).decode()
        self.assertIn(str(auto_session.session_id), content)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_explicit_session_still_works(self, mock_run_query, mock_adapter_cls):
        """POST /query/ with explicit session_id still validates ownership."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})

        mock_run_query.side_effect = fake_run_query

        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        content = b"".join(resp.streaming_content).decode()
        self.assertIn(str(session.session_id), content)


# ---------------------------------------------------------------------------
# QueryTaskModelTests
# ---------------------------------------------------------------------------

class QueryTaskModelTests(TestCase):
    """Test QueryTask Django ORM model."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.session = ChatSession.objects.create(user=self.user)

    def test_create_query_task(self):
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="Find me mice", status="running",
        )
        self.assertIsNotNone(task.task_id)
        self.assertEqual(task.status, "running")
        self.assertEqual(task.progress, [])
        self.assertIsNone(task.result)

    def test_uuid_auto_generation(self):
        t1 = QueryTask.objects.create(session=self.session, user=self.user, query="q1")
        t2 = QueryTask.objects.create(session=self.session, user=self.user, query="q2")
        self.assertNotEqual(t1.task_id, t2.task_id)
        self.assertIsInstance(t1.task_id, uuid.UUID)

    def test_progress_append(self):
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="running",
        )
        task.progress = [{"event": "agent_started", "data": {"agent": "entity"}}]
        task.save(update_fields=["progress"])
        task.refresh_from_db()
        self.assertEqual(len(task.progress), 1)
        self.assertEqual(task.progress[0]["event"], "agent_started")

    def test_status_transitions(self):
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="pending",
        )
        task.status = "running"
        task.save(update_fields=["status"])
        task.refresh_from_db()
        self.assertEqual(task.status, "running")

        task.status = "completed"
        task.result = {"reply": "done"}
        task.save(update_fields=["status", "result"])
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result, {"reply": "done"})

    def test_db_table_name(self):
        self.assertEqual(QueryTask._meta.db_table, "assistant_query_task")


# ---------------------------------------------------------------------------
# DBEventCallbackTests
# ---------------------------------------------------------------------------

class DBEventCallbackTests(TestCase):
    """Test make_db_event_callback from pipeline_adapter."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.session = ChatSession.objects.create(user=self.user)
        self.task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="running",
        )

    def test_appends_progress_events(self):
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

        cb = make_db_event_callback(str(self.task.task_id), str(self.session.session_id))
        cb("agent_started", {"agent": "entity"})
        cb("agent_complete", {"agent": "entity", "summary": {}})

        self.task.refresh_from_db()
        self.assertEqual(len(self.task.progress), 2)
        self.assertEqual(self.task.progress[0]["event"], "agent_started")
        self.assertEqual(self.task.progress[1]["event"], "agent_complete")
        self.assertEqual(self.task.status, "running")  # not terminal yet

    def test_query_complete_sets_completed(self):
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

        cb = make_db_event_callback(str(self.task.task_id), str(self.session.session_id))
        cb("query_complete", {"reply": "Found 5 samples.", "debug": {}})

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
        self.assertEqual(self.task.result["reply"], "Found 5 samples.")
        # session_id should be injected via setdefault
        self.assertEqual(self.task.result["session_id"], str(self.session.session_id))

    def test_query_error_sets_error(self):
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

        cb = make_db_event_callback(str(self.task.task_id), str(self.session.session_id))
        cb("query_error", {"error": "Something went wrong", "agent": "entity"})

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "error")
        self.assertEqual(self.task.result["error"], "Something went wrong")
        self.assertEqual(self.task.result["session_id"], str(self.session.session_id))

    def test_nonexistent_task_does_not_crash(self):
        from nextseek_api.assistant.pipeline_adapter import make_db_event_callback

        cb = make_db_event_callback(str(uuid.uuid4()), str(self.session.session_id))
        # Should not raise
        cb("agent_started", {"agent": "entity"})


# ---------------------------------------------------------------------------
# AsyncQueryViewSetTests
# ---------------------------------------------------------------------------

class AsyncQueryViewSetTests(TestCase):
    """Test POST /assistant/query/async/ endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.client = APIClient()
        patcher = patch('nextseek_api.services.assistant.UserInParticipatingProject.has_permission', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_returns_202_with_task_id(self, mock_run_query, mock_adapter_cls):
        """POST /query/async/ returns 202 with task_id and session_id."""
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(session.session_id), "query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertIn("task_id", resp.data)
        self.assertIn("session_id", resp.data)
        self.assertEqual(resp.data["session_id"], str(session.session_id))
        # Verify QueryTask was created
        self.assertEqual(QueryTask.objects.filter(user=self.user).count(), 1)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_auto_session_creates(self, mock_run_query, mock_adapter_cls):
        """POST /query/async/ without session_id auto-creates a session."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        # Session should have been auto-created
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)
        auto_session = ChatSession.objects.get(user=self.user)
        self.assertEqual(resp.data["session_id"], str(auto_session.session_id))

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_reuses_recent_session(self, mock_run_query, mock_adapter_cls):
        """POST /query/async/ without session_id reuses most recent session."""
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "test query"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["session_id"], str(session.session_id))
        # No new session should have been created
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_query_async_explicit_session(self, mock_run_query, mock_adapter_cls):
        """POST /query/async/ with explicit session_id validates ownership."""
        self.client.force_authenticate(user=self.user)
        session = ChatSession.objects.create(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(session.session_id), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["session_id"], str(session.session_id))

    def test_query_async_missing_query_returns_422(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 422)

    def test_query_async_invalid_session_returns_404(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"session_id": str(uuid.uuid4()), "query": "test"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# TaskProgressViewSetTests
# ---------------------------------------------------------------------------

class TaskProgressViewSetTests(TestCase):
    """Test GET /assistant/tasks/{task_id}/progress/ endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass1234")
        self.session = ChatSession.objects.create(user=self.user)
        self.client = APIClient()
        patcher = patch('nextseek_api.services.assistant.UserInParticipatingProject.has_permission', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_task_progress_running(self):
        """GET /tasks/{id}/progress/ returns current progress for a running task."""
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="running",
            progress=[
                {"event": "agent_started", "data": {"agent": "entity"}},
                {"event": "agent_complete", "data": {"agent": "entity", "summary": {}}},
            ],
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["task_id"], str(task.task_id))
        self.assertEqual(resp.data["session_id"], str(self.session.session_id))
        self.assertEqual(resp.data["status"], "running")
        self.assertEqual(len(resp.data["progress"]), 2)
        self.assertIsNone(resp.data["result"])

    def test_task_progress_not_found(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{uuid.uuid4()}/progress/")
        self.assertEqual(resp.status_code, 404)

    def test_task_progress_wrong_user(self):
        other = User.objects.create_user(username="other", password="pass1234")
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="running",
        )
        self.client.force_authenticate(user=other)
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 404)

    def test_task_progress_completed_includes_result(self):
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="completed",
            progress=[
                {"event": "agent_started", "data": {"agent": "entity"}},
                {"event": "query_complete", "data": {"reply": "Found 5."}},
            ],
            result={"reply": "Found 5.", "session_id": str(self.session.session_id)},
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "completed")
        self.assertIsNotNone(resp.data["result"])
        self.assertEqual(resp.data["result"]["reply"], "Found 5.")

    def test_task_progress_pending_no_result(self):
        task = QueryTask.objects.create(
            session=self.session, user=self.user, query="test", status="pending",
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(f"/nextseek_api/assistant/tasks/{task.task_id}/progress/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "pending")
        self.assertIsNone(resp.data["result"])
        self.assertEqual(len(resp.data["progress"]), 0)


# ---------------------------------------------------------------------------
# CsrfExemptionTests — regression test for the 403 CSRF bug
# ---------------------------------------------------------------------------

class CsrfExemptionTests(TestCase):
    """
    Verify session-authenticated users can POST to assistant endpoints
    without a CSRF token.

    NOTE: force_authenticate() bypasses DRF auth entirely, so it cannot
    catch CSRF issues.  These tests use client.login() to exercise the
    real SessionAuthentication path.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="csrfuser", password="pass1234")
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.login(username="csrfuser", password="pass1234")
        patcher = patch('nextseek_api.services.assistant.UserInParticipatingProject.has_permission', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_post_query_async_no_csrf_token(self, mock_run_query, mock_adapter_cls):
        """POST /assistant/query/async/ must return 202, not 403, for a
        session-authenticated user who sends no X-CSRFToken header."""
        resp = self.client.post(
            "/nextseek_api/assistant/query/async/",
            {"query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertIn("task_id", resp.data)

    @patch("nextseek_api.services.assistant.DictSessionAdapter")
    @patch("nextseek_api.services.assistant.run_query")
    def test_post_query_sse_no_csrf_token(self, mock_run_query, mock_adapter_cls):
        """POST /assistant/query/ must return 200 (SSE), not 403."""
        def fake_run_query(adapter, chat_config, query, send_event):
            send_event("query_complete", {"reply": "done", "debug": {}, "bundle_id": None})
        mock_run_query.side_effect = fake_run_query

        resp = self.client.post(
            "/nextseek_api/assistant/query/",
            {"query": "Find me mice"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])

    def test_post_create_session_no_csrf_token(self):
        """POST /assistant/sessions/ must return 201, not 403."""
        resp = self.client.post("/nextseek_api/assistant/sessions/")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("session_id", resp.data)
