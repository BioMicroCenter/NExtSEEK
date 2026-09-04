"""Every route registers its output files; every route should offer them.

`chat_nextseek.artifacts.build_file_manifest_entry` writes a normalized entry
(key/label/path/filename/mime/kind) for each file a run produces, and every
orchestrator route stores that list on the bundle as ``files``.  Only the
reporter route was ever wired to the download UI, so on production 211 bundles
carried files the download endpoint could not even address — including a
510 KB "Full API result JSON" written on all 125 searches.

These tests pin the manifest, not ``report_saved_files``, as the source of
truth for what the user can download.
"""

from django.test import SimpleTestCase

from nextseek_api.assistant.excel_export import build_artifacts, build_file_artifacts
from nextseek_api.assistant.models_api import ArtifactFile


def _entry(key, label, filename, kind, path=None):
    """A file-manifest entry in the shape build_file_manifest_entry emits."""
    return {
        "key": key,
        "label": label,
        "path": path or f"/app/outputs/run/files/{filename}",
        "filename": filename,
        "mime": "application/json",
        "kind": kind,
    }


class SearchFilesAreOfferedTests(SimpleTestCase):
    """The biggest gap: new_search wrote the full result set 125 times, silently."""

    def test_search_bundle_offers_its_registered_result_file(self):
        bundle = {
            "mode": "new_search",
            "files": [_entry("api_result", "Full API result JSON",
                             "api_result_20260904.json", "api")],
        }

        artifacts = build_artifacts(bundle)

        self.assertEqual(
            [(a["artifact_type"], a["key"], a["label"]) for a in artifacts],
            [("file", "api_result", "Full API result JSON")],
        )

    def test_graph_query_bundle_offers_nothing_but_does_not_error(self):
        """graph_debug is an internal trace, not a user output."""
        bundle = {
            "mode": "graph_query",
            "files": [_entry("graph_debug", "Graph query debug JSON",
                             "graph_20260904.json", "graph")],
        }

        self.assertEqual(build_artifacts(bundle), [])


class InternalTraceKindsTests(SimpleTestCase):

    def test_internal_kinds_are_withheld_from_the_user(self):
        bundle = {
            "mode": "new_search",
            "files": [
                _entry("api_result", "Full API result JSON", "api.json", "api"),
                _entry("graph_debug", "Graph query debug JSON", "g.json", "graph"),
                _entry("memory_coder_result_20260904T122014Z",
                       "Memory coder execution result", "m.json", "memory"),
            ],
        }

        self.assertEqual([a["key"] for a in build_file_artifacts(bundle)], ["api_result"])

    def test_an_unrecognised_kind_is_shown_rather_than_hidden(self):
        """The bug being fixed is files going unseen, so new kinds must default
        to visible.  A future output kind that nobody adds to a list here is a
        button the user does not need; the reverse is a file they never learn
        about."""
        bundle = {
            "mode": "new_search",
            "files": [_entry("novel_export", "Some Future Export", "x.json", "brand_new")],
        }

        self.assertEqual([a["key"] for a in build_file_artifacts(bundle)], ["novel_export"])


class WireContractTests(SimpleTestCase):
    """ArtifactFile is extra="forbid" in models_api.py AND in the Container-CC
    shim (_assistant_models.py).  An artifact carrying `mime` or `path` fails
    the CC client with exit 4 — a documented past regression (2026-07-05)."""

    def test_emitted_file_artifact_validates_against_the_strict_model(self):
        bundle = {
            "mode": "new_search",
            "files": [_entry("api_result", "Full API result JSON", "api.json", "api")],
        }

        artifact = build_file_artifacts(bundle)[0]

        ArtifactFile(**artifact)  # raises on any extra key
        self.assertEqual(
            set(artifact), {"artifact_type", "key", "label", "file_format"}
        )

    def test_file_format_is_taken_from_the_registered_filename(self):
        bundle = {
            "mode": "reporter",
            "files": [_entry("geo_seq_workbooks_0", "GEO submission workbook 1",
                             "geo_submission.xlsx", "export")],
        }

        self.assertEqual(build_file_artifacts(bundle)[0]["file_format"], "xlsx")


class ReporterKeepsItsTablesTests(SimpleTestCase):

    def test_reporter_bundle_offers_both_its_tables_and_its_files(self):
        bundle = {
            "mode": "reporter",
            "reporter_result": {"sampletypes_table": {"TIS": 3}, "rows_returned": 3},
            "files": [_entry("samples_report", "Samples report JSON",
                             "project_2.uuids.json", "report")],
        }

        artifacts = build_artifacts(bundle)

        self.assertEqual(
            [a["artifact_type"] for a in artifacts], ["table", "file"]
        )
        self.assertEqual(artifacts[0]["key"], "sampletypes_table")
        self.assertEqual(artifacts[1]["key"], "samples_report")


class LegacyBundleTests(SimpleTestCase):
    """generate-submission bundles carry report_saved_files and no manifest.
    They must not lose the downloads they already had."""

    def test_bundle_without_a_manifest_falls_back_to_report_saved_files(self):
        bundle = {
            "mode": "generate-submission",
            "report_saved_files": {
                "geo_seq_workbooks": ["/app/outputs/run/geo.xlsx"],
                "pride_sdrf": ["/app/outputs/run/design.tsv"],
            },
        }

        keys = [a["key"] for a in build_file_artifacts(bundle)]

        self.assertIn("geo_seq_workbooks", keys)
        self.assertIn("pride_sdrf", keys)

    def test_a_manifest_suppresses_the_fallback_so_files_are_not_listed_twice(self):
        bundle = {
            "mode": "reporter",
            "files": [_entry("geo_seq_workbooks_0", "GEO submission workbook 1",
                             "geo.xlsx", "export", path="/app/outputs/run/geo.xlsx")],
            "report_saved_files": {"geo_seq_workbooks": ["/app/outputs/run/geo.xlsx"]},
        }

        self.assertEqual(len(build_file_artifacts(bundle)), 1)
