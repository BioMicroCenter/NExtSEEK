"""First tests for three routes CI never saw succeed (#98).

* POST /nextseek_api/assistant/api-write/: the confirmed_write gate. The agent is stubbed, the real run_op and the
  real request model run. (The older NessieAI/tests/ns tests for it are baseline reds: test settings have no
  NEXTSEEK_CHAT_CONFIG; this file stubs that one lookup.)
* POST /nextseek_api/cc-assistant/upload/ and its alias POST /nextseek_api/nessie/uploads/.
* The transcript recover route's size cap.

The api-write caller carries its own SEEK login (patched), so these tests hold whether or not the op falls back to
the shared login for a caller without one.
"""
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from NessieAI.cc.cc_transcript_store import compress
from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSession


def _plan(endpoint, method="POST"):
    p = MagicMock()
    p.endpoint, p.method, p.requestBody, p.queryParameters = endpoint, method, {}, {}
    p.model_dump.return_value = {"endpoint": endpoint, "method": method, "requestBody": {}, "queryParameters": {}, "notes": ""}
    return p


class ApiWriteTests(TestCase):
    databases = {"default"}
    URL = "/nextseek_api/assistant/api-write/"

    def setUp(self):
        self.user = User.objects.create_user("writer", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        for target, value in (
            ("nextseek_api.services.assistant.UserInParticipatingProject.has_permission", True),
            # The caller's own SEEK login, which a real sign-in leaves in the session and force_authenticate does
            # not: the op refuses a caller without one rather than act as the shared login.
            ("nextseek_api.services.assistant._request_login", ("writer", "pw")),
            ("nextseek_api.services.assistant._granular_chat_config", MagicMock()),
        ):
            p = patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_an_unconfirmed_write_answers_403_and_never_calls_the_agent(self):
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=_plan("/nextseek_api/samples/advanced_search/")) as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request", return_value={"ok": True}) as send:
            resp = self.client.post(self.URL, {"parser_plan": "{}", "confirmed_write": False}, format="json")
        build.assert_not_called()
        send.assert_not_called()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "WRITE_BLOCKED")

    def test_an_omitted_confirmed_write_is_also_blocked(self):
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=_plan("/nextseek_api/samples/advanced_search/")) as build, \
             patch("chat_nextseek.helpers.tool_nextseek_api_request", return_value={"ok": True}) as send:
            resp = self.client.post(self.URL, {"parser_plan": "{}"}, format="json")
        build.assert_not_called()
        send.assert_not_called()
        self.assertEqual(resp.status_code, 403)

    def test_the_string_true_is_refused_before_the_op_runs(self):
        with patch("nextseek_api.services.assistant.run_op") as run:
            resp = self.client.post(self.URL, {"parser_plan": "{}", "confirmed_write": "true"}, format="json")
        run.assert_not_called()
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "VALIDATION")

    def test_a_confirmed_write_executes_and_returns_the_response(self):
        with patch("chat_nextseek.portable.api_agent_build_request", return_value=_plan("/nextseek_api/samples/advanced_search/")), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request", return_value={"ok": True, "data": {}}) as send:
            resp = self.client.post(self.URL, {"parser_plan": "{}", "confirmed_write": True}, format="json")
        self.assertEqual(resp.status_code, 200)
        send.assert_called_once()
        self.assertTrue(resp.json()["result"]["response"]["ok"])

    def test_an_unauthenticated_caller_gets_no_write(self):
        with patch("nextseek_api.services.assistant.run_op") as run:
            resp = APIClient().post(self.URL, {"parser_plan": "{}", "confirmed_write": True}, format="json")
        run.assert_not_called()
        self.assertIn(resp.status_code, (401, 403))




class UploadTests(TestCase):
    databases = {"default"}
    URLS = ("/nextseek_api/cc-assistant/upload/", "/nextseek_api/nessie/uploads/")

    def setUp(self):
        self.user = User.objects.create_user("uploader", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        o = override_settings(MEDIA_ROOT=self.media)
        o.enable()
        self.addCleanup(o.disable)
        self.dirs = SimpleNamespace(input_mnt="/data/in/uploader")
        self.task = MagicMock(id="job-1")
        patches = {
            "resolve": patch("NessieAI.cc.cc_provision.resolve_user_project", return_value=SimpleNamespace(id="13", dirname="proj")),
            "dirs": patch("NessieAI.cc.cc_provision.build_user_dirs", return_value=self.dirs),
            "paths": patch("NessieAI.cc.cc_config.CCPaths.from_env", return_value=MagicMock()),
            "delay": patch("nextseek_api.cc_assistant.cc_upload_tasks.run_cc_upload_task.delay", return_value=self.task),
            "register": patch("nextseek_api.batch_upload.job_index.register_job"),
            "creds": patch("nextseek_api.services.cc_assistant.CCAssistantViewSet._resolve_credentials", return_value=("u", "p")),
        }
        self.m = {}
        for k, p in patches.items():
            self.m[k] = p.start()
            self.addCleanup(p.stop)

    def _file(self, name="reads.txt", data=b"abc"):
        return SimpleUploadedFile(name, data)

    def test_an_upload_is_queued_for_the_callers_own_input_folder_on_both_routes(self):
        for url in self.URLS:
            self.m["delay"].reset_mock()
            self.m["dirs"].reset_mock()
            resp = self.client.post(url, {"file": self._file()}, format="multipart")
            self.assertEqual(resp.status_code, 202, url)
            self.assertEqual(resp.json(), {"job_id": "job-1", "status": "queued"})
            self.assertEqual(self.m["dirs"].call_args.args[2], self.user.username, url)
            kwargs = self.m["delay"].call_args.kwargs
            self.assertEqual(kwargs["input_mnt"], "/data/in/uploader")
            self.assertEqual([f["name"] for f in kwargs["files"]], ["reads.txt"])
            self.assertEqual(self.m["register"].call_args.kwargs["user_id"], self.user.pk)

    def test_no_file_is_a_400_and_nothing_is_queued(self):
        resp = self.client.post(self.URLS[0], {}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.m["delay"].assert_not_called()

    def test_an_oversized_upload_is_a_413(self):
        with override_settings(BATCH_UPLOAD_MAX_TOTAL_BYTES=2):
            resp = self.client.post(self.URLS[0], {"file": self._file(data=b"abcd")}, format="multipart")
        self.assertEqual(resp.status_code, 413)
        self.m["delay"].assert_not_called()

    def test_a_traversal_filename_never_leaves_the_staging_folder(self):
        # Django's multipart parser keeps only the basename, so the view stages "evil.txt" under its own folder.
        resp = self.client.post(self.URLS[0], {"file": self._file(name="../../etc/evil.txt")}, format="multipart")
        self.assertEqual(resp.status_code, 202)
        (f,) = self.m["delay"].call_args.kwargs["files"]
        self.assertEqual(f["name"], "evil.txt")
        self.assertEqual(os.path.dirname(f["tmp_path"]), os.path.join(self.media, "cc_upload_staging"))

    def test_a_duplicate_name_in_one_batch_is_a_400(self):
        resp = self.client.post(self.URLS[0], {"file": [self._file(), self._file()]}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.m["delay"].assert_not_called()

    def test_an_unresolvable_project_is_a_503(self):
        from NessieAI.cc.cc_provision import ProjectResolutionError

        self.m["resolve"].side_effect = ProjectResolutionError("none")
        resp = self.client.post(self.URLS[0], {"file": self._file()}, format="multipart")
        self.assertEqual(resp.status_code, 503)
        self.m["delay"].assert_not_called()

    def test_an_unauthenticated_upload_is_refused(self):
        resp = APIClient().post(self.URLS[0], {"file": self._file()}, format="multipart")
        self.assertIn(resp.status_code, (401, 403))
        self.m["delay"].assert_not_called()


class OversizedTranscriptTests(TestCase):
    databases = {"default"}

    @pytest.mark.xfail(strict=True, reason="cc_assistant.py:397 decompress raises TranscriptTooLarge on a row over "
                       "CC_TRANSCRIPT_MAX_BYTES and nothing catches it: the route answers 500, not a 4xx")
    def test_a_row_over_the_cap_is_a_clean_4xx(self):
        user = User.objects.create_user("big", password="pw")
        cs = ChatSession.objects.create(user=user)
        raw = b"x" * 4096
        CCSessionTranscript.objects.create(chat_session=cs, cc_session_id="cc", turn_id="t1",
                                           blob=compress(raw), uncompressed_size=len(raw))
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=user)
        with override_settings(CC_TRANSCRIPT_MAX_BYTES=1024):
            resp = client.get(f"/nextseek_api/cc-assistant/transcript/{cs.session_id}/t1/")
        self.assertLess(resp.status_code, 500)
