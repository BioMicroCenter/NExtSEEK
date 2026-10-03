"""One bundle-id allocator for a chat (approach 1, piece 2; issue 13).

The artifact ops now write their bundles into the live chat, where a nested NS turn may be numbering its own bundle
at the same moment from a snapshot it loaded earlier. Both take the next id from one allocator, under a row lock, and
the chat's counter never moves back, so the adapter's merge keeps both bundles.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import TestCase
from rest_framework.test import APIClient

from chat_nextseek.orchestrator import BUNDLE_SEQ_KEY as ORCHESTRATOR_SEQ_KEY
from chat_nextseek.orchestrator import _next_bundle_id
from NessieAI.ns import granular
from nextseek_api.assistant import bundle_ids
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline

BASE = "/nextseek_api/assistant"


def _report_handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
    path = Path(outputs_dir) / "summary.json"
    path.write_text("{}")
    return {"summary": {}, "saved_files": {"summary_json": str(path)}, "rows": {}}


class _Base(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.outputs = tempfile.mkdtemp()
        for target, kwargs in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            ("nextseek_api.services.assistant._granular_chat_config", {"return_value": SimpleNamespace()}),
            ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": self.outputs}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _report(self, client, **body):
        with patch.dict(granular._HANDLERS, {"report": _report_handler}):
            resp = client.post(f"{BASE}/report/", {"mode": "samples", "project": "p", **body}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["download"]


class OneAllocatorTests(_Base):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.chat = ChatSession.objects.create(user=self.user)

    def test_the_counter_key_is_the_orchestrators(self):
        self.assertEqual(bundle_ids.BUNDLE_SEQ_KEY, ORCHESTRATOR_SEQ_KEY)

    def test_a_nested_ns_turn_and_a_report_op_keep_both_bundles(self):
        """Review focus 3. The NS turn loads the chat, numbers its bundle, the report op registers one, then the NS
        turn saves: with two numbering schemes both took id 1 and the merge dropped the report's bundle."""
        adapter = DictSessionAdapter(ChatSession.objects.get(pk=self.chat.pk))
        ns_id = _next_bundle_id(adapter)
        op_id = self._report(self.client, session_id=str(self.chat.session_id))["bundle_id"]
        adapter["results_history"] = [*adapter.get("results_history", []), {"id": ns_id, "mode": "new_search"}]
        adapter.save()

        self.chat.refresh_from_db()
        ids = sorted(b["id"] for b in self.chat.results_history)
        self.assertNotEqual(ns_id, op_id)
        self.assertEqual(ids, sorted([ns_id, op_id]))
        self.assertEqual({b["mode"] for b in self.chat.results_history}, {"new_search", "reporter"})
        self.assertEqual(self.chat.extra_state[bundle_ids.BUNDLE_SEQ_KEY], max(ids))

    def test_two_report_ops_get_two_ids(self):
        first = self._report(self.client, session_id=str(self.chat.session_id))
        second = self._report(self.client, session_id=str(self.chat.session_id))
        self.assertEqual((first["bundle_id"], second["bundle_id"]), (1, 2))
        self.chat.refresh_from_db()
        self.assertEqual([b["id"] for b in self.chat.results_history], [1, 2])

    def test_a_stale_session_save_cannot_wind_the_counter_back(self):
        """An NS turn took an id it has not appended yet; another turn, loaded earlier, saves; then a report op asks.
        Written back, the older count would hand the report op the NS turn's id."""
        stale = DictSessionAdapter(ChatSession.objects.get(pk=self.chat.pk))  # loaded before any id was taken
        ns = DictSessionAdapter(ChatSession.objects.get(pk=self.chat.pk))
        ns_id = _next_bundle_id(ns)  # taken, not yet in results_history
        stale["last_debug"] = {"turn": "other"}
        stale.save()
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.extra_state[bundle_ids.BUNDLE_SEQ_KEY], ns_id)
        op_id = self._report(self.client, session_id=str(self.chat.session_id))["bundle_id"]
        self.assertNotEqual(op_id, ns_id)

    def test_a_throwaway_session_numbers_in_memory_and_writes_nothing(self):
        adapter = DictSessionAdapter(ChatSession(user=self.user))
        self.assertIsNone(adapter.allocate_bundle_id())
        self.assertEqual(_next_bundle_id(adapter), 1)
        self.assertFalse(ChatSession.objects.filter(pk=adapter._session.pk).exists())

    def test_a_failed_allocation_numbers_in_memory_and_never_ends_the_turn(self):
        """W1-9: the allocator's DB write can fail mid-turn (a dropped connection); the NS turn keeps going."""
        adapter = DictSessionAdapter(ChatSession.objects.get(pk=self.chat.pk))
        with patch("nextseek_api.assistant.bundle_ids.allocate_bundle_id",
                   side_effect=OperationalError(2006, "Server has gone away")), \
             self.assertLogs("nextseek_api.assistant.session_adapter", level="WARNING"):
            self.assertIsNone(adapter.allocate_bundle_id())
            self.assertGreater(_next_bundle_id(adapter), 0)

    def test_without_a_session_id_a_browser_caller_gets_a_new_chat(self):
        download = self._report(self.client)
        self.assertNotEqual(download["session_id"], str(self.chat.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 2)


class PassBundleTests(_Base):
    def setUp(self):
        super().setUp()
        self.chat = ChatSession.objects.create(user=self.user)
        task = QueryTask.objects.create(session=self.chat, user=self.user, query="q", status="running")
        self.turn, raw = issue_pass(task=task, chat=self.chat, user=self.user, login=("u1", "p"))
        set_deadline(self.turn, time.time() + 180)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"NextseekTurn {raw}")

    def test_under_a_pass_the_bundle_lands_in_the_turns_chat(self):
        """The sidecar road sends no session id; the bundle still lands in the asking chat, so the artifact GET, which
        plan 02 allows only for the turn's chat, can fetch it."""
        download = self._report(self.client)
        self.assertEqual(download["session_id"], str(self.chat.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)
        self.chat.refresh_from_db()
        self.assertEqual([b["id"] for b in self.chat.results_history], [download["bundle_id"]])
