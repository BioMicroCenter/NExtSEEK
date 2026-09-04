"""The download endpoint resolves keys against the bundle's file manifest.

It used to resolve only ``report_saved_files``, a dict that just the reporter
and generate-submission routes populate.  On production that left 211 bundles
carrying files the endpoint could not address at all, so a search's
"Full API result JSON" was written to disk and then unreachable.

``bundle["files"]`` is written by every route and already carries the path,
filename and mime, so it is the resolution source; ``report_saved_files``
stays as the fallback for bundles predating the manifest.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class ManifestDownloadTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("manifestuser", password="testpass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.out_dir = Path(settings.BASE_DIR) / "outputs" / "test_manifest_artifacts"
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _write(self, suffix, payload):
        tmp = tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, dir=str(self.out_dir), mode="w"
        )
        tmp.write(payload)
        tmp.close()
        self.addCleanup(lambda p=tmp.name: os.path.exists(p) and os.unlink(p))
        return tmp.name

    def _set_bundle(self, bundle):
        self.session.results_history = [bundle]
        self.session.save()

    def _url(self, artifact_key):
        return (
            f"/nextseek_api/assistant/sessions/{self.session.session_id}"
            f"/bundles/1/artifacts/{artifact_key}/"
        )

    def test_search_result_file_is_served_from_the_manifest(self):
        path = self._write(".json", json.dumps({"rows": [{"uid": "TIS-1"}]}))
        self._set_bundle({
            "id": 1,
            "mode": "new_search",
            "files": [{
                "key": "api_result",
                "label": "Full API result JSON",
                "path": path,
                "filename": "api_result_20260904.json",
                "mime": "application/json",
                "kind": "api",
            }],
        })

        resp = self.client.get(self._url("api_result"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertIn("api_result_20260904.json", resp["Content-Disposition"])
        self.assertEqual(
            json.loads(b"".join(resp.streaming_content)), {"rows": [{"uid": "TIS-1"}]}
        )

    def test_a_manifest_path_outside_the_artifact_root_is_refused(self):
        self._set_bundle({
            "id": 1,
            "mode": "new_search",
            "files": [{
                "key": "api_result",
                "label": "Full API result JSON",
                "path": "/etc/passwd",
                "filename": "passwd",
                "mime": "text/plain",
                "kind": "api",
            }],
        })

        self.assertEqual(self.client.get(self._url("api_result")).status_code, 403)

    def test_a_manifest_entry_whose_file_is_gone_reports_not_found(self):
        path = self._write(".json", "{}")
        os.unlink(path)
        self._set_bundle({
            "id": 1,
            "mode": "new_search",
            "files": [{
                "key": "api_result", "label": "Full API result JSON", "path": path,
                "filename": "api_result.json", "mime": "application/json", "kind": "api",
            }],
        })

        self.assertEqual(self.client.get(self._url("api_result")).status_code, 404)

    def test_report_saved_files_still_resolves_when_a_bundle_has_no_manifest(self):
        """generate-submission bundles carry no manifest and must keep working."""
        path = self._write(".tsv", "col\tval\n")
        self._set_bundle({
            "id": 1,
            "mode": "generate-submission",
            "report_saved_files": {"pride_sdrf": [path]},
        })

        resp = self.client.get(self._url("pride_sdrf"))

        self.assertEqual(resp.status_code, 200)
        self.assertIn("tab-separated", resp["Content-Type"])


class ReloadKeepsDownloadsTests(TestCase):
    """A reloaded chat rebuilds artifacts from the stored bundle. If that
    rebuild ignores the manifest, every download button vanishes on refresh
    even though the file is still on disk and still addressable."""

    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("reloaduser", password="testpass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_reloaded_search_turn_still_offers_its_result_file(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="Search chat",
            results_history=[{
                "id": 1,
                "user_query": "all tissue samples",
                "reply": "Found 5,613.",
                "mode": "new_search",
                "files": [{
                    "key": "api_result", "label": "Full API result JSON",
                    "path": "/app/outputs/run/files/api_result.json",
                    "filename": "api_result.json",
                    "mime": "application/json", "kind": "api",
                }],
            }],
        )

        body = self.client.get(
            f"/nextseek_api/assistant/sessions/{cs.session_id}/?include=turns"
        ).json()

        artifacts = body["turns"][0]["artifacts"] or []
        self.assertEqual(
            [(a["artifact_type"], a["key"]) for a in artifacts],
            [("file", "api_result")],
        )
