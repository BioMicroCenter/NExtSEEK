"""cc-assistant/artifacts/{session}/download/ reads the session owner's Container-CC folder (#64).

The folder is the one the session's CC turns ran in: ``extra_state['cc_project_dirname']`` plus the owner's
username (``session_export.cc_artifacts_root``), whoever asks. A superuser reading another user's session gets
that user's files, never their own. SEEK is a tripwire that names the caller's own project.
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from NessieAI.cc.cc_provision import ProjectIdentity
from nextseek_api.assistant.models_db import ChatSession

RUN = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
OWNER_PROJECT = ProjectIdentity(id="7", title="Owner Lab", slug="owner-lab")
ADMIN_PROJECT = ProjectIdentity(id="9", title="Admin Lab", slug="admin-lab")


class CCArtifactDownloadReadsTheOwnersFolder(TestCase):
    databases = {"default"}

    def setUp(self):
        self.owner = User.objects.create_user("cc-owner", password="p")
        self.admin = User.objects.create_superuser("site-admin", password="p")
        self.mount = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.mount, True)
        env = patch.dict(os.environ, {"DMAC_USER_ROOT_MOUNT": str(self.mount)})
        env.start()
        self.addCleanup(env.stop)
        seek = patch("NessieAI.cc.cc_provision.resolve_user_project",
                     side_effect=lambda user, pw, **kw: OWNER_PROJECT if user == "cc-owner" else ADMIN_PROJECT)
        seek.start()
        self.addCleanup(seek.stop)
        self._file(OWNER_PROJECT, "cc-owner", b"the owner's report")
        self._file(ADMIN_PROJECT, "site-admin", b"the superuser's own report")
        self.chat = ChatSession.objects.create(
            user=self.owner, extra_state={"cc_project_dirname": OWNER_PROJECT.dirname})

    def _file(self, project, username, content):
        path = self.mount / project.dirname / username / "output" / "artifacts" / RUN / "report.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _get(self, user, chat, **params):
        client = APIClient()
        client.force_authenticate(user=user)
        token = base64.b64encode(f"{user.username}:p".encode()).decode()
        client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")  # the caller's own login
        return client.get(f"/nextseek_api/cc-assistant/artifacts/{chat.session_id}/download/", params)

    def test_a_superuser_reads_the_owners_file(self):
        resp = self._get(self.admin, self.chat, key=f"{RUN}/report.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"the owner's report")

    def test_a_superuser_zips_the_owners_turn(self):
        resp = self._get(self.admin, self.chat, key="all", turn_id=RUN)
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(b"".join(resp.streaming_content))) as zf:
            self.assertEqual([zf.read(n) for n in zf.namelist()], [b"the owner's report"])

    def test_the_owner_reads_their_own_file(self):
        resp = self._get(self.owner, self.chat, key=f"{RUN}/report.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"the owner's report")

    def test_a_session_that_ran_no_cc_turn_has_no_files(self):
        chat = ChatSession.objects.create(user=self.owner)
        self.assertEqual(self._get(self.admin, chat, key=f"{RUN}/report.csv").status_code, 404)
