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


class GetSessionWithTurnsTests(SessionEndpointsTestBase):
    def _url(self, sid, include=None):
        base = f"/nextseek_api/assistant/sessions/{sid}/"
        return f"{base}?include={include}" if include else base

    def test_existing_shape_unchanged_without_include(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[{"id": 1, "user_query": "hi", "reply": "hello", "mode": "x"}],
        )
        body = self.client.get(self._url(cs.session_id)).json()
        # Pre-existing fields still present.
        self.assertEqual(body["session_id"], str(cs.session_id))
        self.assertEqual(body["query_count"], 1)
        self.assertTrue(body["has_results"])
        # New optional fields are null/absent when ?include=turns is not passed.
        self.assertIsNone(body.get("turns"))
        self.assertIsNone(body.get("title"))

    def test_include_turns_returns_title_and_turns(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[
                {"id": 1, "user_query": "hi", "terminal_reply": "hello", "mode": "new_search", "ts": "2026-05-12T00:00:00"},
                {"id": 2, "user_query": "again", "reply": "ok", "mode": "graph_query"},
            ],
        )
        body = self.client.get(self._url(cs.session_id, include="turns")).json()
        self.assertEqual(body["title"], "My chat")
        self.assertEqual(len(body["turns"]), 2)
        # terminal_reply preferred over reply when both could be set.
        self.assertEqual(body["turns"][0]["reply"], "hello")
        self.assertEqual(body["turns"][0]["bundle_id"], 1)
        self.assertEqual(body["turns"][0]["mode"], "new_search")
        self.assertEqual(body["turns"][0]["ts"], "2026-05-12T00:00:00")
        self.assertEqual(body["turns"][1]["reply"], "ok")
        self.assertIsNone(body["turns"][1]["ts"])

    def test_include_turns_skips_bundle_without_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "user_query": "hi", "reply": "hello", "mode": "x"},
                {"id": 2, "reply": "no-user-query-bundle", "mode": "wizard"},
                {"id": 3, "user_query": "third", "reply": "third-reply", "mode": "x"},
            ],
        )
        turns = self.client.get(self._url(cs.session_id, include="turns")).json()["turns"]
        self.assertEqual([t["bundle_id"] for t in turns], [1, 3])

    def test_include_turns_403_for_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.get(self._url(cs.session_id, include="turns"))
        self.assertEqual(resp.status_code, 403)

    def test_include_turns_404_when_missing(self):
        resp = self.client.get(self._url("00000000-0000-0000-0000-000000000000", include="turns"))
        self.assertEqual(resp.status_code, 404)


class PatchSessionTests(SessionEndpointsTestBase):
    def _url(self, sid):
        return f"/nextseek_api/assistant/sessions/{sid}/"

    def test_rename_happy_path(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "New name"}, format="json")
        self.assertEqual(resp.status_code, 200)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "New name")
        body = resp.json()
        self.assertEqual(body["title"], "New name")
        self.assertEqual(body["session_id"], str(cs.session_id))

    def test_rename_trims_whitespace(self):
        cs = ChatSession.objects.create(user=self.user)
        self.client.patch(self._url(cs.session_id), {"title": "  hello  "}, format="json")
        cs.refresh_from_db()
        self.assertEqual(cs.title, "hello")

    def test_reject_empty_after_trim(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "   "}, format="json")
        self.assertEqual(resp.status_code, 422)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_reject_overlong(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "x" * 201}, format="json")
        self.assertEqual(resp.status_code, 422)

    def test_403_on_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.patch(self._url(cs.session_id), {"title": "stolen"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_404_when_missing(self):
        resp = self.client.patch(
            self._url("00000000-0000-0000-0000-000000000000"),
            {"title": "ghost"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)


class DeleteSessionTests(SessionEndpointsTestBase):
    def _url(self, sid):
        return f"/nextseek_api/assistant/sessions/{sid}/"

    def test_delete_returns_204(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ChatSession.objects.filter(session_id=cs.session_id).exists())

    def test_delete_cascades_tasks(self):
        from nextseek_api.assistant.models_db import QueryTask
        cs = ChatSession.objects.create(user=self.user)
        QueryTask.objects.create(session=cs, user=self.user, query="x", status="completed")
        QueryTask.objects.create(session=cs, user=self.user, query="y", status="completed")
        self.client.delete(self._url(cs.session_id))
        self.assertFalse(QueryTask.objects.filter(session_id=cs.session_id).exists())

    def test_delete_404_when_missing(self):
        resp = self.client.delete(self._url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(resp.status_code, 404)

    def test_delete_404_after_delete(self):
        cs = ChatSession.objects.create(user=self.user)
        self.client.delete(self._url(cs.session_id))
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 404)

    def test_delete_403_on_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(ChatSession.objects.filter(session_id=cs.session_id).exists())


class AutoTitleTests(SessionEndpointsTestBase):
    def _call_helper(self, cs):
        from nextseek_api.services.assistant import _auto_title_if_unset
        _auto_title_if_unset(cs)

    def test_sets_title_from_first_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "user_query": "Find mice treated with NDMA", "reply": "...", "mode": "x"},
            ],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "Find mice treated with NDMA")

    def test_truncates_to_60_chars_after_collapsing_whitespace(self):
        long_q = "What  is\tthe\nname of the protocol used in the GBM study by Dr X"
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "user_query": long_q, "reply": "...", "mode": "x"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        expected = " ".join(long_q.split())[:60]
        self.assertEqual(cs.title, expected)
        self.assertLessEqual(len(cs.title), 60)

    def test_noop_when_title_already_set(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="Manual name",
            results_history=[{"id": 1, "user_query": "ignored", "reply": "", "mode": "x"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "Manual name")

    def test_noop_when_history_empty(self):
        cs = ChatSession.objects.create(user=self.user)
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_noop_when_first_bundle_has_no_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "reply": "no user_query", "mode": "wizard"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_finds_first_user_query_when_first_bundle_lacks_one(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "reply": "wizard intro", "mode": "wizard"},
                {"id": 2, "user_query": "real first query", "reply": "x", "mode": "x"},
            ],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "real first query")
