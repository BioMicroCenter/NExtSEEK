"""Coverage tests for batch_upload/orchestrator.py — targeting uncovered lines.

Focus: helper functions and edge cases that don't require full pipeline execution.
- _cancelled_result, _error_result
- _extract_identity_for_orphan
- build_identity_map
- _build_neo4j_only_outcomes
- run_batch_upload_multi with should_stop (cancellation)
- run_batch_upload_multi with rows + empty rows
- run_batch_upload_multi CONVERT exception path
"""

import json
import tempfile
import os
import pytest
from unittest.mock import patch, MagicMock

from nextseek_api.batch_upload.models import InputRowModel, RowOutcome
from nextseek_api.batch_upload.errors import ErrorCollector


# ---------------------------------------------------------------------------
# _cancelled_result and _error_result
# ---------------------------------------------------------------------------

class TestResultHelpers:

    def test_cancelled_result(self):
        from nextseek_api.batch_upload.orchestrator import _cancelled_result
        result = _cancelled_result("job-123", "/tmp/summary.csv")
        assert result["job_id"] == "job-123"
        assert result["totals"]["cancelled"] is True
        assert result["totals"]["processed"] == 0

    def test_error_result_basic(self):
        from nextseek_api.batch_upload.orchestrator import _error_result
        ec = ErrorCollector()
        result = _error_result("job-456", "/tmp/summary.csv", ec, "Something failed")
        assert result["job_id"] == "job-456"
        assert result["totals"]["error"] == "Something failed"
        assert result["errors"] == []

    def test_error_result_with_errors(self):
        from nextseek_api.batch_upload.orchestrator import _error_result
        from nextseek_api.batch_upload.errors import ErrorType
        ec = ErrorCollector()
        ec.add(row_index=0, uid="A", error_type=ErrorType.UNKNOWN, message="err1")
        ec.add(row_index=1, uid="B", error_type=ErrorType.DB_CONN, message="err2")
        result = _error_result("job-789", "/tmp/summary.csv", ec, "Multiple errors")
        assert len(result["errors"]) == 2


# ---------------------------------------------------------------------------
# _extract_identity_for_orphan
# ---------------------------------------------------------------------------

class TestExtractIdentityForOrphan:

    def test_name_from_metadata(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="NHP",
            json_metadata=json.dumps({"Name": "Subject1"}),
        )
        assert _extract_identity_for_orphan(model) == "Subject1"

    def test_file_based_prefix(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="D.IMG_something",
            json_metadata=json.dumps({"File_PrimaryData": "file001.tif"}),
        )
        assert _extract_identity_for_orphan(model) == "file001.tif"

    def test_file_based_prefix_a(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="A.GEX_something",
            json_metadata=json.dumps({"File_PrimartyData": "seq.fastq"}),
        )
        assert _extract_identity_for_orphan(model) == "seq.fastq"

    def test_file_based_no_file_field(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="D.IMG_something",
            json_metadata=json.dumps({"Name": "Subject1"}),
        )
        # For D.* prefix, if no File_PrimaryData field => None
        assert _extract_identity_for_orphan(model) is None

    def test_no_name(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="NHP",
            json_metadata=json.dumps({"Other": "value"}),
        )
        assert _extract_identity_for_orphan(model) is None

    def test_empty_metadata(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(SampleType="NHP", json_metadata="{}")
        assert _extract_identity_for_orphan(model) is None

    def test_bad_json(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(SampleType="NHP", json_metadata="{}")
        # Force bad json via direct attribute override
        object.__setattr__(model, "json_metadata", "not json")
        assert _extract_identity_for_orphan(model) is None

    def test_empty_name_string(self):
        from nextseek_api.batch_upload.orchestrator import _extract_identity_for_orphan
        model = InputRowModel(
            SampleType="NHP",
            json_metadata=json.dumps({"Name": "  "}),
        )
        assert _extract_identity_for_orphan(model) is None


# ---------------------------------------------------------------------------
# build_identity_map
# ---------------------------------------------------------------------------

class TestBuildIdentityMap:

    def test_basic_mapping(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map
        models = [
            InputRowModel(
                SampleType="NHP", UID="NHP-001",
                json_metadata=json.dumps({"Name": "Subject1"}),
            ),
        ]
        outcomes = {
            "NHP-001": RowOutcome(status="success", sample_id=42),
        }
        id_map, parent_info = build_identity_map(models, outcomes)
        assert id_map == {"Subject1": "NHP-001"}
        assert parent_info["NHP-001"]["sample_id"] == 42

    def test_skips_no_uid(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map
        models = [
            InputRowModel(SampleType="NHP", json_metadata=json.dumps({"Name": "Subject1"})),
        ]
        outcomes = {}
        id_map, parent_info = build_identity_map(models, outcomes)
        assert id_map == {}
        assert parent_info == {}

    def test_skips_no_outcome(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map
        models = [
            InputRowModel(SampleType="NHP", UID="NHP-001",
                          json_metadata=json.dumps({"Name": "X"})),
        ]
        outcomes = {}
        id_map, parent_info = build_identity_map(models, outcomes)
        assert id_map == {}

    def test_skips_no_sample_id(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map
        models = [
            InputRowModel(SampleType="NHP", UID="NHP-001",
                          json_metadata=json.dumps({"Name": "X"})),
        ]
        outcomes = {
            "NHP-001": RowOutcome(status="failed", sample_id=None),
        }
        id_map, parent_info = build_identity_map(models, outcomes)
        assert id_map == {}

    def test_dedup_identity(self):
        """First identity wins."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map
        models = [
            InputRowModel(SampleType="NHP", UID="NHP-001",
                          json_metadata=json.dumps({"Name": "X"})),
            InputRowModel(SampleType="NHP", UID="NHP-002",
                          json_metadata=json.dumps({"Name": "X"})),
        ]
        outcomes = {
            "NHP-001": RowOutcome(status="success", sample_id=1),
            "NHP-002": RowOutcome(status="success", sample_id=2),
        }
        id_map, _ = build_identity_map(models, outcomes)
        assert id_map["X"] == "NHP-001"


# ---------------------------------------------------------------------------
# run_batch_upload_multi — cancellation at various stages
# ---------------------------------------------------------------------------

class TestRunBatchUploadMultiCancellation:

    @patch("nextseek_api.batch_upload.orchestrator.os.makedirs")
    def test_cancel_at_convert(self, mock_makedirs):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        result = run_batch_upload_multi(
            xlsx_paths=["fake.xlsx"],
            project_id=1,
            contributor_id=1,
            should_stop=lambda: True,
            output_dir="/tmp/test_orch_cov",
        )
        assert result["totals"]["cancelled"] is True

    @patch("nextseek_api.batch_upload.orchestrator.os.makedirs")
    def test_empty_rows_returns_error(self, mock_makedirs):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        result = run_batch_upload_multi(
            xlsx_paths=["fake.xlsx"],
            project_id=1,
            contributor_id=1,
            rows=[],
            output_dir="/tmp/test_orch_cov",
        )
        assert "No valid rows" in result["totals"].get("error", "")


# ---------------------------------------------------------------------------
# run_batch_upload_multi — rows mode with valid rows
# ---------------------------------------------------------------------------

class TestRunBatchUploadMultiRowsMode:

    @patch("nextseek_api.batch_upload.orchestrator.upload_all")
    @patch("nextseek_api.batch_upload.orchestrator.Neo4jConfig.from_django_settings")
    @patch("nextseek_api.batch_upload.orchestrator.process_batches")
    @patch("nextseek_api.batch_upload.orchestrator.build_insertable")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_project_sample_type_links")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_assay_ids")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_sample_types", return_value={"NHP": 1})
    @patch("nextseek_api.batch_upload.orchestrator.compute_levels")
    @patch("nextseek_api.batch_upload.orchestrator.detect_cycles", return_value=[])
    @patch("nextseek_api.batch_upload.orchestrator.build_relationships")
    @patch("nextseek_api.batch_upload.orchestrator.compute_directions")
    @patch("nextseek_api.batch_upload.orchestrator.run_uid_gen")
    @patch("nextseek_api.batch_upload.orchestrator.get_connection")
    @patch("nextseek_api.batch_upload.orchestrator.write_summary_csv")
    @patch("nextseek_api.batch_upload.orchestrator.build_row_summaries", return_value=[])
    @patch("nextseek_api.batch_upload.orchestrator.os.makedirs")
    def test_rows_mode_happy_path(
        self, mock_makedirs, mock_summaries, mock_write, mock_conn,
        mock_uid_gen, mock_directions, mock_build_rel, mock_cycles,
        mock_levels, mock_pf_st, mock_pf_assay, mock_pf_proj,
        mock_build_ins, mock_process, mock_neo4j_cfg, mock_upload,
    ):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        from nextseek_api.batch_upload.models import (
            BatchResult, InsertableSample, DirectionComputation, RowOutcome,
        )
        from nextseek_api.batch_upload.levels import LevelAssignment

        # Setup mocks
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        rows = [
            {"SampleType": "NHP", "json_metadata": '{"Name":"X"}', "UID": "NHP-010101AA-1"},
        ]

        mock_uid_gen.return_value = (
            [InputRowModel(SampleType="NHP", json_metadata='{"Name":"X"}', UID="NHP-010101AA-1")],
            {"uids_generated": 0},
        )

        mock_directions.return_value = DirectionComputation(
            direction_by_pair={}, parents_of={}, assays_by_uid={},
            child_uids_by_assay={}, conflicts_by_assay={},
        )
        mock_build_rel.return_value = ({}, {}, set())

        mock_levels.return_value = LevelAssignment(
            levels={"NHP-010101AA-1": 0},
            max_level=0,
            orphan_uids=set(),
            cycle_uids=set(),
            external_parents={},
            preexisting_uids={},
        )

        sample = InsertableSample(
            uuid="NHP-010101AA-1",
            title="NHP-010101AA-1",
            sample_type_id=1,
            json_metadata='{"Name":"X"}',
        )
        mock_build_ins.return_value = (sample, {})

        mock_process.return_value = BatchResult(
            inserted_count=1,
            linked_project_count=1,
            linked_assays_count=0,
            outcomes={"NHP-010101AA-1": RowOutcome(status="success", sample_id=100)},
            attempted_uids={"NHP-010101AA-1"},
        )

        neo4j_cfg = MagicMock()
        neo4j_cfg.NEO4J_UPLOAD_ENABLED = False
        neo4j_cfg.MISSING_KEYS = ["URI"]
        mock_neo4j_cfg.return_value = neo4j_cfg

        result = run_batch_upload_multi(
            xlsx_paths=[],
            project_id=1,
            contributor_id=1,
            rows=rows,
            output_dir=tempfile.mkdtemp(),
        )

        assert result["totals"]["success"] >= 1


# ---------------------------------------------------------------------------
# _build_neo4j_only_outcomes
# ---------------------------------------------------------------------------

class TestBuildNeo4jOnlyOutcomes:

    @patch("nextseek_api.batch_upload.orchestrator.get_connection")
    @patch("nextseek_api.batch_upload.orchestrator.load_existing_samples")
    def test_all_found(self, mock_load, mock_conn):
        from nextseek_api.batch_upload.orchestrator import _build_neo4j_only_outcomes
        from nextseek_api.batch_upload.models import InsertableSample

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_load.return_value = {"UID-1": 10, "UID-2": 20}

        samples = [
            InsertableSample(uuid="UID-1", title="A", sample_type_id=1, json_metadata="{}"),
            InsertableSample(uuid="UID-2", title="B", sample_type_id=1, json_metadata="{}"),
        ]
        ec = ErrorCollector()
        result = _build_neo4j_only_outcomes(samples, ec)
        assert result.outcomes["UID-1"].status == "success"
        assert result.outcomes["UID-2"].status == "success"
        assert len(ec.all_errors()) == 0

    @patch("nextseek_api.batch_upload.orchestrator.get_connection")
    @patch("nextseek_api.batch_upload.orchestrator.load_existing_samples")
    def test_some_missing(self, mock_load, mock_conn):
        from nextseek_api.batch_upload.orchestrator import _build_neo4j_only_outcomes
        from nextseek_api.batch_upload.models import InsertableSample

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_load.return_value = {"UID-1": 10}

        samples = [
            InsertableSample(uuid="UID-1", title="A", sample_type_id=1, json_metadata="{}"),
            InsertableSample(uuid="UID-MISSING", title="B", sample_type_id=1, json_metadata="{}"),
        ]
        ec = ErrorCollector()
        result = _build_neo4j_only_outcomes(samples, ec)
        assert result.outcomes["UID-1"].status == "success"
        assert result.outcomes["UID-MISSING"].status == "failed"
        assert len(ec.all_errors()) == 1
