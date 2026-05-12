"""Coverage tests for batch_upload/orchestrator.py — targeting uncovered lines.

Focus: helper functions and edge cases that don't require full pipeline execution.
- _cancelled_result, _error_result
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
from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType


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


# ---------------------------------------------------------------------------
# _ensure_summary_csv
# ---------------------------------------------------------------------------

class TestEnsureSummaryCsv:

    def test_writes_csv_when_not_exists(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        ec = ErrorCollector()
        _ensure_summary_csv(path, valid_rows=valid_rows, error_collector=ec)
        assert os.path.isfile(path)
        with open(path) as f:
            content = f.read()
        assert "UID-001" in content
        assert "TOTALS" in content

    def test_skips_when_file_exists(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        with open(path, "w") as f:
            f.write("existing content")
        _ensure_summary_csv(path, valid_rows=[])
        with open(path) as f:
            assert f.read() == "existing content"

    def test_skips_when_valid_rows_is_none(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        _ensure_summary_csv(path, valid_rows=None)
        assert not os.path.isfile(path)

    def test_empty_valid_rows_writes_headers_and_totals(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        _ensure_summary_csv(path, valid_rows=[], error_collector=ErrorCollector())
        assert os.path.isfile(path)
        with open(path) as f:
            content = f.read()
        assert "TOTALS" in content
        assert "row_index" in content

    def test_extra_totals_merged(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        _ensure_summary_csv(
            path, valid_rows=[], error_collector=ErrorCollector(),
            extra_totals={"error": "CONVERT failed"},
        )
        with open(path) as f:
            content = f.read()
        assert "TOTALS" in content

    def test_with_outcomes(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _ensure_summary_csv
        path = str(tmp_path / "summary.csv")
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        _ensure_summary_csv(
            path, valid_rows=valid_rows, outcomes=outcomes,
            error_collector=ErrorCollector(),
        )
        with open(path) as f:
            content = f.read()
        assert "success" in content


# ---------------------------------------------------------------------------
# _error_result writes CSV
# ---------------------------------------------------------------------------

class TestErrorResultWritesCsv:

    def test_error_result_with_valid_rows_writes_csv(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _error_result
        path = str(tmp_path / "summary.csv")
        ec = ErrorCollector()
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        result = _error_result("job-1", path, ec, "some error", valid_rows=valid_rows)
        assert os.path.isfile(path)
        assert result["totals"]["error"] == "some error"

    def test_error_result_without_valid_rows_no_csv(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _error_result
        path = str(tmp_path / "summary.csv")
        ec = ErrorCollector()
        result = _error_result("job-1", path, ec, "some error")
        assert not os.path.isfile(path)
        assert result["totals"]["error"] == "some error"

    def test_error_result_csv_contains_error_info(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _error_result
        path = str(tmp_path / "summary.csv")
        ec = ErrorCollector()
        ec.add(0, "UID-001", ErrorType.UNKNOWN, "bad thing happened")
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        _error_result("job-1", path, ec, "pipeline error", valid_rows=valid_rows)
        with open(path) as f:
            content = f.read()
        assert "bad thing happened" in content


# ---------------------------------------------------------------------------
# _cancelled_result writes CSV
# ---------------------------------------------------------------------------

class TestCancelledResultWritesCsv:

    def test_cancelled_result_with_valid_rows_writes_csv(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _cancelled_result
        path = str(tmp_path / "summary.csv")
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        result = _cancelled_result("job-1", path, valid_rows=valid_rows)
        assert os.path.isfile(path)
        assert result["totals"]["cancelled"] is True

    def test_cancelled_result_without_valid_rows_no_csv(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _cancelled_result
        path = str(tmp_path / "summary.csv")
        result = _cancelled_result("job-1", path)
        assert not os.path.isfile(path)
        assert result["totals"]["cancelled"] is True

    def test_cancelled_result_with_outcomes(self, tmp_path):
        from nextseek_api.batch_upload.orchestrator import _cancelled_result
        path = str(tmp_path / "summary.csv")
        valid_rows = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
        ]
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        _cancelled_result("job-1", path, valid_rows=valid_rows, outcomes=outcomes)
        with open(path) as f:
            content = f.read()
        assert "success" in content


# ---------------------------------------------------------------------------
# Pipeline error paths produce summary CSV
# ---------------------------------------------------------------------------

class TestPipelineErrorPathsSummary:

    @patch("nextseek_api.batch_upload.orchestrator.merge_files")
    def test_convert_exception_produces_summary(self, mock_merge):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        mock_merge.side_effect = RuntimeError("bad file")
        with tempfile.TemporaryDirectory() as td:
            result = run_batch_upload_multi(
                xlsx_paths=["fake.xlsx"],
                project_id=1,
                contributor_id=1,
                output_dir=td,
            )
            summary_path = result.get("summary_path", "")
            assert os.path.isfile(summary_path)


class TestAmbiguousIdentityHandling:

    @patch("nextseek_api.batch_upload.orchestrator.Neo4jConfig.from_django_settings")
    @patch("nextseek_api.batch_upload.orchestrator.process_batches")
    @patch("nextseek_api.batch_upload.orchestrator.build_insertable")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_project_sample_type_links")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_assay_ids")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_sample_types", return_value={"NHP": 1})
    @patch("nextseek_api.batch_upload.orchestrator.compute_levels")
    @patch("nextseek_api.batch_upload.orchestrator.detect_cycles", return_value=[])
    @patch("nextseek_api.batch_upload.orchestrator.build_relationships", return_value=({}, {}, []))
    @patch("nextseek_api.batch_upload.orchestrator.compute_directions")
    @patch("nextseek_api.batch_upload.orchestrator.get_connection")
    @patch("nextseek_api.batch_upload.orchestrator.os.makedirs")
    def test_parent_resolution_ambiguity_fails_one_row_and_continues(
        self,
        mock_makedirs,
        mock_conn,
        mock_directions,
        mock_build_rel,
        mock_cycles,
        mock_levels,
        mock_pf_st,
        mock_pf_assay,
        mock_pf_proj,
        mock_build_insertable,
        mock_process_batches,
        mock_neo4j_cfg,
    ):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        from nextseek_api.batch_upload.models import BatchResult, InsertableSample

        good_row = InputRowModel(UID="UID-GOOD", SampleType="NHP", json_metadata='{"Name":"Good"}')
        bad_row = InputRowModel(UID="UID-BAD", SampleType="NHP", json_metadata='{"Name":"Bad"}')

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_directions.return_value = MagicMock()
        mock_levels.return_value = MagicMock(
            max_level=0,
            orphan_uids=set(),
            cycle_uids=set(),
            external_parents={},
            preexisting_uids={},
            levels={"UID-GOOD": 0},
        )
        mock_build_insertable.return_value = (
            InsertableSample(uuid="UID-GOOD", sample_type_id=1, json_metadata="{}", title="Good"),
            {},
        )
        mock_process_batches.return_value = BatchResult(
            inserted_count=1,
            linked_project_count=0,
            linked_assays_count=0,
            outcomes={"UID-GOOD": RowOutcome(status="success", sample_id=101)},
            attempted_uids={"UID-GOOD"},
            stopped_early=False,
            permissions_inserted_count=0,
            updated_count=0,
        )
        mock_neo4j_cfg.return_value = MagicMock(NEO4J_UPLOAD_ENABLED=False, MISSING_KEYS=["disabled"])

        def _run_uid_gen(rows, lababbv, conn, error_collector):
            reason = (
                "ambiguous identity match: 2 existing samples with name_identity='Parent-X' - "
                "duplicates must be resolved before batch can proceed (conflicting sample ids: [10, 20])"
            )
            error_collector.add(1, "UID-BAD", ErrorType.AMBIGUOUS_IDENTITY, reason)
            return [good_row], {
                "uids_generated": 0,
                "duplicates_removed": 0,
                "parents_resolved": 0,
                "parents_unresolved": 0,
                "failed_rows": [{"uid": "UID-BAD", "row_index": 1, "reason": reason, "error_type": "ambiguous_identity"}],
                "warnings": [],
            }

        with patch("nextseek_api.batch_upload.orchestrator.run_uid_gen", side_effect=_run_uid_gen):
            with tempfile.TemporaryDirectory() as td:
                result = run_batch_upload_multi(
                    xlsx_paths=[],
                    project_id=1,
                    contributor_id=1,
                    rows=[good_row.model_dump(), bad_row.model_dump()],
                    output_dir=td,
                )

        assert result["totals"]["success"] == 1
        assert result["totals"]["failed"] == 1
        assert result["totals"]["processed"] == (
            result["totals"]["success"]
            + result["totals"]["skipped"]
            + result["totals"]["failed"]
        )
        assert any(err["type"] == ErrorType.AMBIGUOUS_IDENTITY.value for err in result["errors"])

    @patch("nextseek_api.batch_upload.orchestrator.Neo4jConfig.from_django_settings")
    @patch("nextseek_api.batch_upload.orchestrator.process_batches")
    @patch("nextseek_api.batch_upload.orchestrator.build_insertable")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_project_sample_type_links")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_assay_ids")
    @patch("nextseek_api.batch_upload.orchestrator.prefetch_sample_types", return_value={"NHP": 1})
    @patch("nextseek_api.batch_upload.orchestrator.compute_levels")
    @patch("nextseek_api.batch_upload.orchestrator.detect_cycles", return_value=[])
    @patch("nextseek_api.batch_upload.orchestrator.build_relationships", return_value=({}, {}, []))
    @patch("nextseek_api.batch_upload.orchestrator.compute_directions")
    @patch("nextseek_api.batch_upload.orchestrator.get_connection")
    @patch("nextseek_api.batch_upload.orchestrator.os.makedirs")
    def test_name_check_ambiguity_records_error_and_continues(
        self,
        mock_makedirs,
        mock_conn,
        mock_directions,
        mock_build_rel,
        mock_cycles,
        mock_levels,
        mock_pf_st,
        mock_pf_assay,
        mock_pf_proj,
        mock_build_insertable,
        mock_process_batches,
        mock_neo4j_cfg,
    ):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        from nextseek_api.batch_upload.models import BatchResult, InsertableSample
        from nextseek_api.batch_upload.uid_gen import AmbiguousIdentityError

        good_row = InputRowModel(UID="UID-GOOD", SampleType="NHP", json_metadata='{"Name":"Good"}')
        ambiguous_row = InputRowModel(SampleType="NHP", json_metadata='{"Name":"Mouse-A"}')

        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_directions.return_value = MagicMock()
        mock_levels.return_value = MagicMock(
            max_level=0,
            orphan_uids=set(),
            cycle_uids=set(),
            external_parents={},
            preexisting_uids={},
            levels={"UID-GOOD": 0},
        )
        mock_build_insertable.return_value = (
            InsertableSample(uuid="UID-GOOD", sample_type_id=1, json_metadata="{}", title="Good"),
            {},
        )
        mock_process_batches.return_value = BatchResult(
            inserted_count=1,
            linked_project_count=0,
            linked_assays_count=0,
            outcomes={"UID-GOOD": RowOutcome(status="success", sample_id=101)},
            attempted_uids={"UID-GOOD"},
            stopped_early=False,
            permissions_inserted_count=0,
            updated_count=0,
        )
        mock_neo4j_cfg.return_value = MagicMock(NEO4J_UPLOAD_ENABLED=False, MISSING_KEYS=["disabled"])

        with patch(
            "nextseek_api.batch_upload.orchestrator.check_name_exists_in_db",
            return_value=(
                [good_row],
                {},
                [],
                [(
                    ambiguous_row,
                    AmbiguousIdentityError(
                        row_index=0,
                        identity="Mouse-A",
                        conflicting_sample_ids=[10, 20],
                    ),
                )],
            ),
        ), patch(
            "nextseek_api.batch_upload.orchestrator.run_uid_gen",
            return_value=(
                [good_row],
                {
                    "uids_generated": 0,
                    "duplicates_removed": 0,
                    "parents_resolved": 0,
                    "parents_unresolved": 0,
                    "failed_rows": [],
                    "warnings": [],
                },
            ),
        ):
            with tempfile.TemporaryDirectory() as td:
                result = run_batch_upload_multi(
                    xlsx_paths=[],
                    project_id=1,
                    contributor_id=1,
                    rows=[ambiguous_row.model_dump(), good_row.model_dump()],
                    output_dir=td,
                )

        assert result["totals"]["success"] == 1
        assert result["totals"]["failed"] == 0
        assert any(err["type"] == ErrorType.AMBIGUOUS_IDENTITY.value for err in result["errors"])

    def test_empty_rows_produces_summary(self):
        from nextseek_api.batch_upload.orchestrator import run_batch_upload_multi
        with tempfile.TemporaryDirectory() as td:
            result = run_batch_upload_multi(
                xlsx_paths=[],
                project_id=1,
                contributor_id=1,
                rows=[],
                output_dir=td,
            )
            summary_path = result.get("summary_path", "")
            assert os.path.isfile(summary_path)
