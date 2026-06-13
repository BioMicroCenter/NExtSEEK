"""Artifact-serving coverage for every report output type.

Each real output type (merged_report, GEO, SRA metadata, SRA biosample, NFCORE
samplesheet, PRIDE px + sdrf) is written as a REAL file on disk, served via the
download-artifact endpoint, and verified byte-for-byte: HTTP 200, correct
Content-Type, non-empty body, and sha256(response) == sha256(on-disk source).
These are FREE tests (real files, real serving, no LLM).
"""
import hashlib
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from unittest.mock import patch


class SafeArtifactPathTests(SimpleTestCase):
    """Unit tests for the path-traversal containment helper (the security fix)."""

    def test_prefix_sibling_is_not_contained(self):
        # The classic prefix-match bypass: a sibling dir that shares the string
        # prefix of the allowed root (<BASE_DIR>/outputs) must be REJECTED.
        from nextseek_api.services.assistant import _safe_artifact_path
        evil = str(Path(settings.BASE_DIR) / "outputs-evil" / "secret.txt")
        self.assertIsNone(_safe_artifact_path(evil))

    def test_absolute_outside_root_rejected(self):
        from nextseek_api.services.assistant import _safe_artifact_path
        self.assertIsNone(_safe_artifact_path("/etc/passwd"))

    def test_home_is_not_allowed(self):
        from nextseek_api.services.assistant import _safe_artifact_path
        self.assertIsNone(_safe_artifact_path(str(Path.home() / ".aws" / "credentials")))

    def test_none_and_empty_rejected(self):
        from nextseek_api.services.assistant import _safe_artifact_path
        self.assertIsNone(_safe_artifact_path(None))
        self.assertIsNone(_safe_artifact_path(""))

    def test_contained_path_accepted(self):
        from nextseek_api.services.assistant import _safe_artifact_path
        ok = str(Path(settings.BASE_DIR) / "outputs" / "sub" / "f.json")
        self.assertIsNotNone(_safe_artifact_path(ok))

from nextseek_api.assistant.models_db import ChatSession


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read_streaming(resp) -> bytes:
    return b"".join(resp.streaming_content) if resp.streaming else resp.content


class ArtifactCoverageTests(TestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(settings.BASE_DIR) / "outputs" / "test_granular_artifacts"
        cls.tmp.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.user = User.objects.create_user("au", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            "nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = ChatSession.objects.create(user=self.user)
        self._files: list[str] = []

    def tearDown(self):
        for f in self._files:
            try:
                os.unlink(f)
            except OSError:
                pass

    def _write_file(self, name: str, content: bytes) -> str:
        path = str(self.tmp / f"{self.session.session_id}_{name}")
        with open(path, "wb") as fh:
            fh.write(content)
        self._files.append(path)
        return path

    def _set_bundle(self, saved_files: dict):
        self.session.results_history = [{"id": 1, "mode": "reporter", "report_saved_files": saved_files}]
        self.session.save()

    def _url(self, key: str) -> str:
        return f"/nextseek_api/assistant/sessions/{self.session.session_id}/bundles/1/artifacts/{key}/"

    def _assert_served(self, key: str, path: str, content: bytes, content_type_fragment: str):
        resp = self.client.get(self._url(key))
        self.assertEqual(resp.status_code, 200, f"{key} not served (got {resp.status_code})")
        self.assertIn(content_type_fragment, resp["Content-Type"])
        body = _read_streaming(resp)
        self.assertTrue(body, f"{key} body empty")
        self.assertEqual(_sha(body), _sha(content), f"{key} sha256 mismatch")
        with open(path, "rb") as fh:
            self.assertEqual(_sha(body), _sha(fh.read()), f"{key} != on-disk source")

    # --- the 6 real output types ---

    def test_merged_report_json_served(self):
        content = b'{"all_samples": {"report_type": "GEO", "report": {"x": 1}}}'
        path = self._write_file("merged.json", content)
        self._set_bundle({"merged_report": path})
        self._assert_served("merged_report", path, content, "application/json")

    def test_sra_submission_workbooks_served(self):
        content = b"PK\x03\x04 fake-xlsx sra metadata bytes"
        path = self._write_file("sra.xlsx", content)
        self._set_bundle({"sra_submission_workbooks": [path]})
        self._assert_served("sra_submission_workbooks", path, content, "spreadsheetml")

    def test_sra_biosample_workbooks_served(self):
        content = b"PK\x03\x04 fake-xlsx sra biosample bytes"
        path = self._write_file("sra_bio.xlsx", content)
        self._set_bundle({"sra_biosample_workbooks": [path]})
        self._assert_served("sra_biosample_workbooks", path, content, "spreadsheetml")

    def test_nfcore_samplesheet_served(self):
        content = b"sample,fastq_1,fastq_2\nS1,a.fq.gz,b.fq.gz\n"
        path = self._write_file("samplesheet.csv", content)
        self._set_bundle({"nfcore_rnaseq_samplesheet": [path]})
        self._assert_served("nfcore_rnaseq_samplesheet", path, content, "text/csv")

    def test_pride_px_served(self):
        content = b"MTD\tsubmitter_name\tJane Doe\n"
        path = self._write_file("submission.px", content)
        self._set_bundle({"pride_submission_px": [path]})
        self._assert_served("pride_submission_px", path, content, "text/plain")

    def test_pride_sdrf_served(self):
        content = b"source name\tcharacteristics\nS1\tmouse\n"
        path = self._write_file("sdrf.tsv", content)
        self._set_bundle({"pride_sdrf": [path]})
        self._assert_served("pride_sdrf", path, content, "tab-separated-values")

    def test_geo_workbook_served(self):
        content = b"PK\x03\x04 fake-xlsx geo bytes"
        path = self._write_file("geo.xlsx", content)
        self._set_bundle({"geo_seq_workbooks": [path]})
        self._assert_served("geo_seq_workbooks", path, content, "spreadsheetml")

    # --- safety: unknown key and path traversal ---

    def test_unknown_key_returns_404(self):
        self._set_bundle({"merged_report": self._write_file("m.json", b"{}")})
        resp = self.client.get(self._url("not_a_real_key"))
        self.assertEqual(resp.status_code, 404)

    def test_path_outside_allowed_dirs_blocked(self):
        # A saved_files value pointing outside BASE_DIR/home must not be served.
        self._set_bundle({"merged_report": "/etc/passwd"})
        resp = self.client.get(self._url("merged_report"))
        self.assertIn(resp.status_code, (403, 404))
