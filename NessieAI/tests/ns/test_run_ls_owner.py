"""run-ls lists a run its caller launched, or any run for a superuser off a turn pass, and never the runs root (#64).

A run is the caller's when one of the caller's own chats records it as launched: the pipeline wizard's
``luria_runs[].remote_dir`` (``chat_nextseek/pipeline/agent_tools.py`` ``tool_submit_to_luria``), which only the
wizard writes. Text a user typed into a chat never counts. SSH is a recorder; no network.
"""
from __future__ import annotations

import base64
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.turn_pass import issue_pass, set_deadline

URL = "/nextseek_api/assistant/run-ls/"
LURIA = {"user": "svc", "key": "/keys/shared", "working_path": "/lab/work", "host": "cluster.example.org"}
RUN = "/lab/work/runs/rnaseq_261001_101010_1"


def _wizard_state(*remote_dirs):
    runs = [{"job_id": "1", "remote_dir": d, "log": f"{d}/x.out", "run_name": "r"} for d in remote_dirs]
    return {"pipeline_agent": {"active": False, "artifacts": {"luria_runs": runs}}}


class RunLsOwnerTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.owner = User.objects.create_user("run-owner", password="p")
        self.other = User.objects.create_user("other-member", password="p")
        self.admin = User.objects.create_superuser("site-admin", password="p")
        ChatSession.objects.create(user=self.owner, extra_state=_wizard_state(RUN))
        self.cmds = []

        def fake_ssh(env, cmd, *, key_path, timeout=None):
            self.cmds.append(cmd)
            return "total 0\n"

        for target, kw in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            ("nextseek_api.services.assistant._granular_chat_config",
             {"return_value": SimpleNamespace(LURIA_ENV=LURIA)}),
            ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": tempfile.mkdtemp()}),
            ("chat_nextseek.luria.ssh.ssh_run", {"side_effect": fake_ssh}),
            ("chat_nextseek.luria.ssh.prepare_key", {"return_value": "/nonexistent/k"}),
        ):
            p = patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _ls(self, user, run_dir):
        client = APIClient()
        token = base64.b64encode(f"{user.username}:p".encode()).decode()
        client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
        return client.post(URL, {"run_dir": run_dir}, format="json")

    def _assert_refused(self, resp, error_type):
        self.assertEqual(resp.status_code, 422, resp.content)
        self.assertEqual(resp.json()["errors"], [{"field": "run_dir", "type": error_type}])
        self.assertEqual(self.cmds, [])

    def test_the_owner_lists_their_run_and_its_folders(self):
        for run_dir in (RUN, RUN + "/results/star_salmon"):
            self.assertEqual(self._ls(self.owner, run_dir).status_code, 200)
        self.assertEqual(self.cmds, [f"ls -laR {RUN}", f"ls -laR {RUN}/results/star_salmon"])

    def test_another_member_may_not_list_it(self):
        self._assert_refused(self._ls(self.other, RUN), "not_your_run")

    def test_a_path_typed_into_a_chat_does_not_make_a_run_yours(self):
        ChatSession.objects.create(user=self.other, extra_state={
            "chat_log": [{"user_query": f"list {RUN}", "assistant_reply": f"Listing `{RUN}/`"}],
            "pipeline_agent": {"artifacts": {"note": RUN}},
        })
        self._assert_refused(self._ls(self.other, RUN), "not_your_run")

    def test_a_run_whose_name_extends_yours_is_not_yours(self):
        self._assert_refused(self._ls(self.owner, RUN + "0"), "not_your_run")

    def test_a_superuser_lists_any_run(self):
        self.assertEqual(self._ls(self.admin, RUN).status_code, 200)
        self.assertEqual(self.cmds, [f"ls -laR {RUN}"])

    def test_nobody_lists_the_runs_root_itself(self):
        for user in (self.owner, self.admin):
            for run_dir in ("/lab/work/runs", "/lab/work/runs/", "/lab/work/runs/.", f"{RUN}/.."):
                self._assert_refused(self._ls(user, run_dir), "the_runs_root")

    def test_a_turn_pass_lists_only_its_own_users_runs(self):
        chat = ChatSession.objects.create(user=self.admin)
        task = QueryTask.objects.create(session=chat, user=self.admin, query="q", status="running")
        turn, raw = issue_pass(task=task, chat=chat, user=self.admin, login=(self.admin.username, "p"))
        set_deadline(turn, time.time() + 180)
        resp = APIClient().post(URL, {"run_dir": RUN}, format="json", HTTP_AUTHORIZATION=f"NextseekTurn {raw}")
        self._assert_refused(resp, "not_your_run")
