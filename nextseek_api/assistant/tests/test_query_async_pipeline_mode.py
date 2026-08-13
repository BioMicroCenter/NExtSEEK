"""mode='pipeline' on POST /assistant/query/async/ routes to run_pipeline_launch."""
import threading
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class PipelineModeRoutingTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("pu", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        perm = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        perm.start()
        self.addCleanup(perm.stop)

    def _post(self, body):
        return self.client.post("/nextseek_api/assistant/query/async/", body, format="json")

    def test_pipeline_mode_invokes_run_pipeline_launch(self):
        seen = {}
        done = threading.Event()

        def fake_launch(adapter, config, query, send_event, credentials=None):
            seen["query"] = query
            done.set()

        with patch("nextseek_api.services.assistant.run_pipeline_launch", fake_launch), \
             patch("nextseek_api.services.assistant.run_query") as run_query_mock:
            cs = ChatSession.objects.create(user=self.user)
            resp = self._post({
                "query": "Launch scrnaseq on D.SEQ-1",
                "mode": "pipeline", "session_id": str(cs.session_id),
            })
            self.assertEqual(resp.status_code, 202)
            self.assertEqual(resp.json()["session_id"], str(cs.session_id))
            self.assertTrue(done.wait(timeout=5), "run_pipeline_launch was not called")
            self.assertEqual(seen["query"], "Launch scrnaseq on D.SEQ-1")
            run_query_mock.assert_not_called()

    def test_standard_mode_still_routes_run_query(self):
        done = threading.Event()

        def fake_query(adapter, config, query, send_event, credentials=None):
            done.set()

        with patch("nextseek_api.services.assistant.run_query", fake_query), \
             patch("nextseek_api.services.assistant.run_pipeline_launch") as launch_mock:
            cs = ChatSession.objects.create(user=self.user)
            resp = self._post({
                "query": "Find mice", "mode": "standard", "session_id": str(cs.session_id),
            })
            self.assertEqual(resp.status_code, 202)
            self.assertTrue(done.wait(timeout=5), "run_query was not called")
            launch_mock.assert_not_called()
