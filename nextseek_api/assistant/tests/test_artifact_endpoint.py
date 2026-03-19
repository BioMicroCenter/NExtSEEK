"""Integration tests for the artifact download endpoint."""
import io
import os
import tempfile
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class DownloadArtifactEndpointTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpass")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(user=self.user)
        patcher = patch('nextseek_api.services.assistant.UserInParticipatingProject.has_permission', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_bundle(self, bundle):
        self.session.results_history = [bundle]
        self.session.save()

    def _url(self, artifact_key):
        return f"/nextseek_api/assistant/sessions/{self.session.session_id}/bundles/1/artifacts/{artifact_key}/"

    def test_geo_workbook_serves_file_from_disk(self):
        from pathlib import Path
        wb = openpyxl.Workbook()
        wb.active.cell(1, 1, value="test")
        # Create temp file under home dir (path traversal check requires this)
        home_tmp = Path.home() / ".cache" / "test_artifacts"
        home_tmp.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False, dir=str(home_tmp))
        wb.save(tmp.name)
        tmp.close()
        try:
            self._set_bundle({
                "id": 1,
                "mode": "reporter",
                "report_saved_files": {"geo_seq_workbooks": [tmp.name]},
                "report_writer_output": {"report_type": "GEO", "report": {}, "narrative": "", "notes": ""},
            })
            resp = self.client.get(self._url("geo_seq_workbooks"))
            self.assertEqual(resp.status_code, 200)
            self.assertIn("spreadsheetml", resp["Content-Type"])
        finally:
            os.unlink(tmp.name)

    def test_table_artifact_generates_xlsx(self):
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_writer_output": {
                "report_type": "GEO",
                "report": {"samples": [{"uid": "X-1", "title": "S1"}]},
                "narrative": "",
                "notes": "",
            },
            "report_saved_files": {},
        })
        resp = self.client.get(self._url("samples"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    def test_search_results_generates_xlsx(self):
        self._set_bundle({
            "id": 1,
            "mode": "new_search",
            "api_result_full": {
                "data": [{"id": "1", "type": "samples", "attributes": {"title": "S1"}}],
            },
        })
        resp = self.client.get(self._url("search_results"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])

    def test_all_tables_generates_combined_workbook(self):
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_writer_output": {
                "report_type": "GEO",
                "report": {
                    "samples": [{"uid": "X-1", "title": "S1"}],
                    "study": {"title": "My Study"},
                },
                "narrative": "",
                "notes": "",
            },
            "report_saved_files": {},
        })
        resp = self.client.get(self._url("all_tables"))
        self.assertEqual(resp.status_code, 200)

    def test_invalid_artifact_key_returns_404(self):
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_writer_output": {"report": {}},
            "report_saved_files": {},
        })
        resp = self.client.get(self._url("nonexistent"))
        self.assertEqual(resp.status_code, 404)

    def test_missing_file_returns_404(self):
        from pathlib import Path
        missing = str(Path.home() / ".cache" / "nonexistent_workbook.xlsx")
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_saved_files": {"geo_seq_workbooks": [missing]},
            "report_writer_output": {"report": {}, "report_type": "GEO", "narrative": "", "notes": ""},
        })
        resp = self.client.get(self._url("geo_seq_workbooks"))
        self.assertEqual(resp.status_code, 404)

    def test_path_traversal_blocked(self):
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_saved_files": {"geo_seq_workbooks": ["/etc/passwd"]},
            "report_writer_output": {"report": {}, "report_type": "GEO", "narrative": "", "notes": ""},
        })
        resp = self.client.get(self._url("geo_seq_workbooks"))
        self.assertEqual(resp.status_code, 403)

    def test_other_user_cannot_access(self):
        other = User.objects.create_user("other", password="pass")
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        self._set_bundle({
            "id": 1,
            "mode": "reporter",
            "report_writer_output": {"report": {}},
            "report_saved_files": {},
        })
        resp = other_client.get(self._url("samples"))
        self.assertEqual(resp.status_code, 403)
