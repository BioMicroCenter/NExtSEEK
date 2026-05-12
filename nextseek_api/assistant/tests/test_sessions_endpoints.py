"""Tests for the saved-chats session endpoints on AssistantViewSet.

list / detail-with-turns / patch / delete / auto-title.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class SessionEndpointsTestBase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.other = User.objects.create_user("u2", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            'nextseek_api.services.assistant.UserInParticipatingProject.has_permission',
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ListSessionsTests(SessionEndpointsTestBase):
    URL = "/nextseek_api/assistant/sessions/"

    def test_returns_only_own_sessions(self):
        own = ChatSession.objects.create(user=self.user)
        ChatSession.objects.create(user=self.other)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["sessions"]), 1)
        self.assertEqual(body["sessions"][0]["session_id"], str(own.session_id))

    def test_projection_shape(self):
        ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[
                {"id": 1, "user_query": "hello world", "reply": "hi", "mode": "new_search"},
                {"id": 2, "user_query": "second", "reply": "ok", "mode": "new_search"},
            ],
        )
        resp = self.client.get(self.URL)
        item = resp.json()["sessions"][0]
        self.assertEqual(
            set(item.keys()),
            {"session_id", "title", "created_at", "updated_at", "query_count", "preview"},
        )
        self.assertEqual(item["title"], "My chat")
        self.assertEqual(item["query_count"], 2)
        self.assertEqual(item["preview"], "hello world")

    def test_title_fallback_when_null(self):
        ChatSession.objects.create(user=self.user)
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(item["title"], "New chat")

    def test_preview_truncated_to_80(self):
        long_q = "a" * 200
        ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "user_query": long_q, "reply": "r", "mode": "x"}],
        )
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(len(item["preview"]), 80)
        self.assertEqual(item["preview"], "a" * 80)

    def test_preview_empty_when_no_bundles(self):
        ChatSession.objects.create(user=self.user)
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(item["preview"], "")

    def test_ordered_by_updated_at_desc(self):
        import time
        s1 = ChatSession.objects.create(user=self.user, title="first")
        time.sleep(0.01)
        s2 = ChatSession.objects.create(user=self.user, title="second")
        time.sleep(0.01)
        s1.title = "first-updated"
        s1.save(update_fields=["title", "updated_at"])
        ids = [s["session_id"] for s in self.client.get(self.URL).json()["sessions"]]
        self.assertEqual(ids, [str(s1.session_id), str(s2.session_id)])

    def test_cap_at_50(self):
        for i in range(55):
            ChatSession.objects.create(user=self.user, title=f"s{i}")
        body = self.client.get(self.URL).json()
        self.assertEqual(len(body["sessions"]), 50)
        self.assertEqual(body["total"], 50)

    def test_401_without_auth(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get(self.URL)
        self.assertIn(resp.status_code, (401, 403))
