"""The op view's time, pass and load rules (approach 1, piece 2).

The limit comes from op_limits.py (55 s on the sidecar road, inside its 60 s wait); too little of it is TIME_UP
before any model runs; under a turn pass the throwaway-session ops take no session and the artifact ops name only the
turn's chat; a turn's third op at once is BUSY. The op handlers are stand-ins: no model, no Neo4j.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from chat_nextseek import call_scope
from NessieAI.ns import granular
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline
from nextseek_api.assistant.turn_pass_auth import PassSessionNotAccepted
from nextseek_api.services.assistant import _refuse_for_pass

BASE = "/nextseek_api/assistant"
ENTITY = {"sampletypes": [], "assays": [], "keywords": [], "projects": []}


def _live_pass(user):
    """A running Container-CC turn and its raw pass (plan 02's issuing; see plan 03 Task 0 Step 3 item 8)."""
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    turn, raw = issue_pass(task=task, chat=chat, user=user, login=(user.username, "p"))
    set_deadline(turn, time.time() + 180)
    return chat, turn, raw


def _patch_common(testcase, outputs):
    for target, kwargs in (
        ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
        ("nextseek_api.services.assistant._granular_chat_config", {"return_value": SimpleNamespace()}),
        ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": outputs}),
    ):
        patcher = patch(target, **kwargs)
        patcher.start()
        testcase.addCleanup(patcher.stop)


class LimitTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        _patch_common(self, tempfile.mkdtemp())

    def _limit_seen(self, op, body):
        seen = {}

        def handler(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
            seen["limit_s"] = op_ctx["limit_s"]
            seen["scope_s"] = call_scope.current().total_s
            return {"saved_files": {}}

        with patch.dict(granular._HANDLERS, {op: handler}):
            resp = self.client.post(f"{BASE}/{op}/", body, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return seen

    def test_the_direct_road_gives_a_report_its_150_s(self):
        with override_settings(NEXTSEEK_CC_OPS_ROAD="direct"):
            seen = self._limit_seen("report", {"mode": "samples", "project": "p"})
        self.assertEqual(seen, {"limit_s": 150.0, "scope_s": 150.0})

    def test_the_sidecar_road_keeps_every_op_at_55_s(self):
        with override_settings(NEXTSEEK_CC_OPS_ROAD="sidecar"):
            seen = self._limit_seen("report", {"mode": "samples", "project": "p"})
        self.assertEqual(seen, {"limit_s": 55.0, "scope_s": 55.0})

    def test_too_little_time_is_time_up_and_no_op_runs(self):
        handler = MagicMock(return_value=ENTITY)
        with patch("nextseek_api.services.assistant.op_limit_s", return_value=10.0), \
             patch.dict(granular._HANDLERS, {"entity": handler}):
            resp = self.client.post(f"{BASE}/entity/", {"query": "mice"}, format="json")
        self.assertEqual(resp.status_code, 408)
        self.assertEqual(resp.json()["code"], "TIME_UP")
        handler.assert_not_called()


class RefuseForPassTests(SimpleTestCase):
    CHAT = uuid.UUID("11111111-1111-4111-8111-111111111111")
    TURN = SimpleNamespace(chat_id=CHAT)

    def test_without_a_pass_nothing_is_refused(self):
        self.assertIsNone(_refuse_for_pass("parse", SimpleNamespace(session_id=uuid.uuid4()), None))

    def test_a_throwaway_session_op_takes_no_session_under_a_pass(self):
        for op in ("entity", "parse", "graph", "aggregate"):
            with self.subTest(op):
                resp = _refuse_for_pass(op, SimpleNamespace(session_id=self.CHAT), self.TURN)
                self.assertEqual(resp.status_code, 422)
                self.assertEqual(resp.data["errors"], [{"field": "session_id", "type": "not_accepted_with_turn_pass"}])
                self.assertEqual(resp.data, PassSessionNotAccepted().detail)  # one body, whichever layer refuses
        self.assertIsNone(_refuse_for_pass("graph", SimpleNamespace(session_id=None), self.TURN))
        self.assertIsNone(_refuse_for_pass("graph-schema", SimpleNamespace(), self.TURN))

    def test_an_artifact_op_may_name_only_the_turns_chat(self):
        self.assertIsNone(_refuse_for_pass("report", SimpleNamespace(session_id=self.CHAT), self.TURN))
        self.assertIsNone(_refuse_for_pass("report", SimpleNamespace(session_id=None), self.TURN))
        resp = _refuse_for_pass("generate-submission", SimpleNamespace(session_id=uuid.uuid4()), self.TURN)
        self.assertEqual((resp.status_code, resp.data["code"]), (403, "PASS_NOT_ALLOWED"))


class PassTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.chat, self.turn, self.raw = _live_pass(self.user)
        self.outputs = tempfile.mkdtemp()
        _patch_common(self, self.outputs)

    def _post(self, op, body):
        return APIClient().post(f"{BASE}/{op}/", body, format="json", HTTP_AUTHORIZATION=f"NextseekTurn {self.raw}")

    def test_a_failed_bundle_registration_is_agent_failed_not_a_500(self):
        with patch("nextseek_api.services.assistant.run_op", return_value={"saved_files": {}}), \
             patch("nextseek_api.assistant.bundle_ids.next_bundle_id_locked", side_effect=RuntimeError("lock wait")):
            resp = self._post("report", {"mode": "samples", "project": "p"})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual((resp.json()["code"], resp.json()["reason"]), ("AGENT_FAILED", "internal"))

    def test_a_busy_artifact_op_leaves_no_folder(self):
        CCTurn.objects.filter(pk=self.turn.pk).update(ops_in_flight=2)
        resp = self._post("report", {"mode": "samples", "project": "p"})
        self.assertEqual((resp.status_code, resp.json()["code"]), (429, "BUSY"))
        self.assertFalse(os.path.isdir(self.outputs))

    def test_run_ls_on_a_box_without_luria_is_agent_failed_internal(self):
        # F-LURIA: the common patch's chat config carries no LURIA_ENV.
        resp = self._post("run-ls", {"run_dir": "/runs/r"})
        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual((body["code"], body["reason"], body["message"]),
                         ("AGENT_FAILED", "internal", "The op failed inside NExtSEEK."))

    def test_a_refused_artifact_op_leaves_no_folder(self):
        handler = MagicMock(side_effect=granular.OpValidationError("x", field="mode", error_type="invalid"))
        with patch.dict(granular._HANDLERS, {"report": handler}):
            resp = self._post("report", {"mode": "samples", "project": "p"})
        self.assertEqual((resp.status_code, resp.json()["code"]), (422, "VALIDATION"))
        self.assertFalse(os.path.isdir(self.outputs))

    def test_the_turn_and_its_limit_reach_run_op(self):
        with patch("nextseek_api.services.assistant.run_op", return_value=ENTITY) as run:
            resp = self._post("entity", {"query": "mice"})
        self.assertEqual(resp.status_code, 200, resp.content)
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["turn"].pk, kwargs["limit_s"]), (self.turn.pk, 55.0))

    def test_a_parse_op_naming_a_session_never_runs(self):
        handler = MagicMock(return_value={})
        with patch.dict(granular._HANDLERS, {"parse": handler}):
            resp = self._post("parse", {"query": "q", "session_id": str(self.chat.session_id)})
        # Plan 02's allow table refuses it first, with the body the view would send.
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"], [{"field": "session_id", "type": "not_accepted_with_turn_pass"}])
        handler.assert_not_called()

    def test_a_wiped_login_is_auth_failed(self):
        # R3: _granular_chat_config cannot raise TurnPassError, so the view uses plan 02's own guard.
        handler = MagicMock(return_value=ENTITY)
        with patch("nextseek_api.services.assistant._request_login", return_value=(None, None)), \
             patch.dict(granular._HANDLERS, {"entity": handler}):
            resp = self._post("entity", {"query": "mice"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["code"], "AUTH_FAILED")
        handler.assert_not_called()

    def test_with_both_slots_taken_the_op_is_busy(self):
        CCTurn.objects.filter(pk=self.turn.pk).update(ops_in_flight=2)
        handler = MagicMock(return_value=ENTITY)
        with patch.dict(granular._HANDLERS, {"entity": handler}):
            resp = self._post("entity", {"query": "mice"})
        self.assertEqual((resp.status_code, resp.json()["code"]), (429, "BUSY"))
        handler.assert_not_called()


class ThirdOpBusyTests(TransactionTestCase):
    """Review focus 2: two ops of one turn on two threads, each with its own DB connection; the third is BUSY."""

    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.chat, self.turn, self.raw = _live_pass(self.user)
        _patch_common(self, tempfile.mkdtemp())

    def _post(self):
        return APIClient().post(f"{BASE}/entity/", {"query": "mice"}, format="json",
                                HTTP_AUTHORIZATION=f"NextseekTurn {self.raw}")

    def _in_thread(self, i, results):
        try:
            resp = self._post()
            results[i] = resp.status_code
        finally:
            connection.close()

    def test_a_third_op_of_the_turn_is_busy(self):
        inside = [threading.Event(), threading.Event()]
        go = [threading.Event(), threading.Event()]
        calls = []

        def entity(args, config, session, write_gate, neo4j_exec, outputs_dir, **op_ctx):
            n = len(calls)
            calls.append(n)
            if n < 2:
                inside[n].set()
                go[n].wait(10)
            return dict(ENTITY)

        results = {}
        with patch.dict(granular._HANDLERS, {"entity": entity}):
            first = threading.Thread(target=self._in_thread, args=(0, results))
            first.start()
            self.assertTrue(inside[0].wait(10))
            second = threading.Thread(target=self._in_thread, args=(1, results))
            second.start()
            self.assertTrue(inside[1].wait(10))

            third = self._post()

            # One at a time, so the two slot releases never write at the same moment.
            go[0].set()
            first.join(10)
            go[1].set()
            second.join(10)
            fourth = self._post()

        self.assertEqual((third.status_code, third.json()["code"]), (429, "BUSY"))
        self.assertEqual(calls, [0, 1, 2])  # the two held ops and the fourth: the third never ran
        self.assertEqual((results[0], results[1], fourth.status_code), (200, 200, 200))
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.ops_in_flight, 0)
