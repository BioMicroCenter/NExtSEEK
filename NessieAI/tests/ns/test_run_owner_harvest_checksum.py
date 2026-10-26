"""run-harvest and run-checksum follow run-ls's owner rule (#64, PR 135 MF-2).

A member may harvest or checksum only a run their own chat launched (the launch record names them too), a
superuser off a turn pass any run, and nobody the runs root. SSH is a recorder; no network.
"""
from __future__ import annotations

import base64
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

import NessieAI.ns.granular as g
from NessieAI.ns.granular import OpValidationError
from nextseek_api.assistant.models_db import ChatSession, PipelineRun

LURIA = {"user": "svc", "key": "/keys/shared", "working_path": "/lab/work", "host": "cluster.example.org"}
RUN = "/lab/work/runs/rnaseq_261001_101010_1"
BODIES = {
    "run-harvest": {"run_dir": RUN},
    "run-checksum": {"run_dir": RUN, "paths": "results/a.bam"},
}


def _wizard_state(*remote_dirs):
    runs = [{"job_id": "1", "remote_dir": d, "log": f"{d}/x.out", "run_name": "r"} for d in remote_dirs]
    return {"pipeline_agent": {"active": False, "artifacts": {"luria_runs": runs}}}


class HarvestChecksumOwnerTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.owner = User.objects.create_user("run-owner", password="p")
        self.other = User.objects.create_user("other-member", password="p")
        self.admin = User.objects.create_superuser("site-admin", password="p")
        ChatSession.objects.create(user=self.owner, extra_state=_wizard_state(RUN))
        self.cmds = []

        def fake_ssh(env, cmd, *, key_path, timeout=None):
            self.cmds.append(cmd)
            return ""

        for target, kw in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", {"return_value": True}),
            ("nextseek_api.services.assistant._granular_chat_config",
             {"return_value": SimpleNamespace(LURIA_ENV=LURIA)}),
            ("nextseek_api.services.assistant._granular_outputs_dir", {"return_value": tempfile.mkdtemp()}),
            ("chat_nextseek.luria.ssh.ssh_run", {"side_effect": fake_ssh}),
            ("chat_nextseek.luria.ssh.ssh_run_bytes", {"side_effect": fake_ssh}),
            ("chat_nextseek.luria.ssh.prepare_key", {"return_value": "/nonexistent/k"}),
        ):
            p = patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _post(self, user, op, **over):
        client = APIClient()
        token = base64.b64encode(f"{user.username}:p".encode()).decode()
        client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")
        return client.post(f"/nextseek_api/assistant/{op}/", {**BODIES[op], **over}, format="json")

    def _assert_refused(self, resp, error_type):
        self.assertEqual(resp.status_code, 422, resp.content)
        self.assertEqual(resp.json()["errors"], [{"field": "run_dir", "type": error_type}])
        self.assertEqual(self.cmds, [])

    def test_another_member_may_not_harvest_or_checksum_it(self):
        for op in BODIES:
            self._assert_refused(self._post(self.other, op), "not_your_run")

    def test_the_owner_and_a_superuser_reach_staging_and_another_member_does_not(self):
        PipelineRun.objects.create(run_dir=RUN, run_name="r", pipeline="nf-core/rnaseq",
                                   launched_by=self.owner, cohort=[])
        staged = []

        def fake_stage(*a, **k):
            staged.append(1)
            raise RuntimeError("stop after staging")

        with patch("NessieAI.ns.granular._stage_run_dir", side_effect=fake_stage):
            seen = {}
            for user in (self.owner, self.admin, self.other):
                staged.clear()
                resp = self._post(user, "run-harvest")
                seen[user.username] = (bool(staged), resp.status_code)
        self.assertTrue(seen["run-owner"][0] and seen["site-admin"][0])
        self.assertEqual(seen["other-member"], (False, 422))

    def test_the_owner_may_checksum_a_run_no_chat_state_lists_any_more(self):
        # N1: a later wizard in the chat wipes the luria_runs list; the launch record still names the owner.
        ChatSession.objects.filter(user=self.owner).update(extra_state=_wizard_state())
        PipelineRun.objects.create(run_dir=RUN, run_name="r", pipeline="nf-core/rnaseq",
                                   launched_by=self.owner, cohort=[])
        self.assertNotIn(b"not_your_run", self._post(self.owner, "run-checksum").content)
        self.assertNotIn(b"not_your_run", self._post(self.owner, "run-harvest").content)
        self.cmds.clear()
        self._assert_refused(self._post(self.other, "run-checksum"), "not_your_run")

    def test_nobody_checksums_the_runs_root(self):
        for user in (self.owner, self.admin):
            self._assert_refused(self._post(user, "run-checksum", run_dir="/lab/work/runs",
                                            paths=f"{RUN.rsplit('/', 1)[1]}/results/a.bam"), "the_runs_root")

    def test_a_harvest_with_no_run_dir_still_picks_the_callers_own(self):
        resp = self._post(self.other, "run-harvest", run_dir=None)
        self.assertNotIn(b"not_your_run", resp.content)


@pytest.mark.django_db
class TestLaunchRecordOwner:
    """The engine checks who launched the run, not only that a record exists."""

    def _harvest(self, user, monkeypatch):
        monkeypatch.setattr(g, "_stage_run_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("staged")))
        monkeypatch.setattr("chat_nextseek.luria.ssh.prepare_key", lambda key: "/nonexistent/k")
        cfg = SimpleNamespace(LURIA_ENV=LURIA)
        return g.run_op("run-harvest", {"run_dir": RUN}, config=cfg, session=SimpleNamespace(user=user),
                        write_gate=None, neo4j_exec=None, outputs_dir=None)

    def _users(self):
        return (User.objects.create(username="o"), User.objects.create(username="x"),
                User.objects.create(username="su", is_superuser=True))

    def test_only_the_launcher_or_a_superuser_harvests(self, monkeypatch):
        owner, other, admin = self._users()
        PipelineRun.objects.create(run_dir=RUN, run_name="r", pipeline="nf-core/rnaseq", launched_by=owner, cohort=[])
        with pytest.raises(OpValidationError) as exc:
            self._harvest(other, monkeypatch)
        assert exc.value.error_type == "not_your_run"
        for user in (owner, admin):
            with pytest.raises(RuntimeError, match="staged"):
                self._harvest(user, monkeypatch)

    def test_a_record_with_no_launcher_is_for_a_superuser(self, monkeypatch):
        owner, _other, admin = self._users()
        PipelineRun.objects.create(run_dir=RUN, run_name="r", pipeline="nf-core/rnaseq", launched_by=None, cohort=[])
        with pytest.raises(OpValidationError):
            self._harvest(owner, monkeypatch)
        with pytest.raises(RuntimeError, match="staged"):
            self._harvest(admin, monkeypatch)
