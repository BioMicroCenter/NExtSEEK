"""Tests that pipeline emits artifacts in query_complete events."""
from django.test import SimpleTestCase

from nextseek_api.assistant.pipeline_adapter import _emit_complete
from nextseek_api.assistant.excel_export import extract_table_artifacts


class EmitCompleteArtifactTests(SimpleTestCase):

    def test_emit_complete_includes_artifacts(self):
        events = []
        def fake_send(event_type, data):
            events.append((event_type, data))

        bundle = {
            "id": 1,
            "mode": "reporter",
            "report_writer_output": {
                "report_type": "GEO",
                "report": {"samples": [{"uid": "X-1", "title": "S1"}]},
                "narrative": "",
                "notes": "",
            },
            "report_saved_files": {},
        }
        artifacts = extract_table_artifacts(bundle)
        _emit_complete(fake_send, "Report done.", {"debug": True}, 1, artifacts=artifacts)

        self.assertEqual(len(events), 1)
        event_type, data = events[0]
        self.assertEqual(event_type, "query_complete")
        self.assertIn("artifacts", data)
        self.assertGreaterEqual(len(data["artifacts"]), 1)
        self.assertEqual(data["artifacts"][0]["artifact_type"], "table")

    def test_emit_complete_no_artifacts_for_search(self):
        events = []
        def fake_send(event_type, data):
            events.append((event_type, data))

        _emit_complete(fake_send, "Results.", {}, 1)

        event_type, data = events[0]
        self.assertNotIn("artifacts", data)
