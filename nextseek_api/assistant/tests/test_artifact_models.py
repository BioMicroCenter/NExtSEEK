"""Unit tests for artifact Pydantic models."""
from django.test import SimpleTestCase

from nextseek_api.assistant.models_api import (
    ArtifactTable,
    ArtifactFile,
    QueryCompleteEvent,
)


class ArtifactModelTests(SimpleTestCase):

    def test_artifact_table_valid(self):
        a = ArtifactTable(
            key="samples_table",
            label="Samples",
            columns=["UID", "Title", "SampleType"],
            data=[{"UID": "NHP-260225MIT-1", "Title": "Blood sample", "SampleType": "NHP_blood"}],
        )
        self.assertEqual(a.artifact_type, "table")
        self.assertEqual(len(a.data), 1)
        self.assertEqual(a.columns, ["UID", "Title", "SampleType"])

    def test_artifact_file_valid(self):
        a = ArtifactFile(
            key="geo_seq_workbooks",
            label="GEO Submission Workbook",
            file_format="xlsx",
        )
        self.assertEqual(a.artifact_type, "file")
        self.assertEqual(a.file_format, "xlsx")

    def test_query_complete_event_with_artifacts(self):
        evt = QueryCompleteEvent(
            reply="Here is your report.",
            debug={"some": "data"},
            bundle_id=1,
            session_id="abc-123",
            artifacts=[
                {
                    "artifact_type": "table",
                    "key": "samples_table",
                    "label": "Samples",
                    "columns": ["UID"],
                    "data": [{"UID": "X-1"}],
                },
                {
                    "artifact_type": "file",
                    "key": "geo_workbook",
                    "label": "GEO Workbook",
                    "file_format": "xlsx",
                },
            ],
        )
        self.assertEqual(len(evt.artifacts), 2)
        self.assertEqual(evt.artifacts[0]["key"], "samples_table")

    def test_query_complete_event_without_artifacts(self):
        evt = QueryCompleteEvent(
            reply="Search results.",
            bundle_id=2,
        )
        self.assertIsNone(evt.artifacts)
