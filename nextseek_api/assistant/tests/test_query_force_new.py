"""Tests for the `force_new` flag on POST /assistant/query/async/."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class ForceNewFlagTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher_perm = patch(
            'nextseek_api.services.assistant.UserInParticipatingProject.has_permission',
            return_value=True,
        )
        patcher_perm.start()
        self.addCleanup(patcher_perm.stop)
        # Don't actually run the pipeline.
        patcher_pipe = patch('nextseek_api.services.assistant.run_query')
        patcher_pipe.start()
        self.addCleanup(patcher_pipe.stop)
        patcher_plan = patch('nextseek_api.services.assistant.run_query_plan')
        patcher_plan.start()
        self.addCleanup(patcher_plan.stop)

    def _post(self, body):
        return self.client.post(
            "/nextseek_api/assistant/query/async/",
            body,
            format="json",
        )

    def test_force_new_creates_fresh_session_when_one_exists(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard", "force_new": True})
        self.assertEqual(resp.status_code, 202)
        new_sid = resp.json()["session_id"]
        self.assertNotEqual(new_sid, str(existing.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 2)

    def test_force_new_ignored_when_session_id_present(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self._post({
            "query": "hi", "mode": "standard",
            "session_id": str(cs.session_id), "force_new": True,
        })
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["session_id"], str(cs.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    def test_force_new_false_reuses_most_recent(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard", "force_new": False})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["session_id"], str(existing.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    def test_force_new_omitted_defaults_to_false(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard"})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["session_id"], str(existing.session_id))
