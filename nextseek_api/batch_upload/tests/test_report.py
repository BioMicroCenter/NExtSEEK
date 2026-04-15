"""Tests for report.py — ProgressReporter, CSV output, build_row_summaries."""
from __future__ import annotations

import csv
import io
import os
import tempfile
import threading
import time

import pytest

from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType
from nextseek_api.batch_upload.models import InputRowModel, Metrics, RowOutcome
from nextseek_api.batch_upload.report import (
    ProgressReporter,
    RowSummary,
    _ThreadSafeProgressReporter,
    build_row_summaries,
    write_summary_csv,
)


# ── ProgressReporter ─────────────────────────────────────────────────────


class TestProgressReporter:
    def test_initial_state(self):
        r = ProgressReporter(total_rows=100)
        assert r.processed == 0
        assert r.elapsed == 0.0

    def test_begin_batch_resets_counts(self):
        r = ProgressReporter()
        r.update_counts(success=5)
        r.begin_batch(50)
        assert r.processed == 0
        assert r.total_rows == 50

    def test_update_counts(self):
        r = ProgressReporter(total_rows=100)
        r.begin_batch(100)
        r.update_counts(success=10, skipped=5, failed=2)
        assert r.processed == 17
        assert r._success == 10
        assert r._skipped == 5
        assert r._failed == 2

    def test_update_counts_accumulates(self):
        r = ProgressReporter(total_rows=100)
        r.begin_batch(100)
        r.update_counts(success=10)
        r.update_counts(success=5, failed=1)
        assert r.processed == 16
        assert r._success == 15
        assert r._failed == 1

    def test_elapsed_nonzero_after_begin(self):
        r = ProgressReporter()
        r.begin_batch(10)
        time.sleep(0.01)
        assert r.elapsed > 0

    def test_elapsed_zero_without_begin(self):
        r = ProgressReporter()
        assert r.elapsed == 0.0


# ── _ThreadSafeProgressReporter ──────────────────────────────────────────


class TestThreadSafeProgressReporter:
    def test_begin_batch_delegates(self):
        inner = ProgressReporter()
        safe = _ThreadSafeProgressReporter(inner)
        safe.begin_batch(50)
        assert inner.total_rows == 50

    def test_update_counts_delegates(self):
        inner = ProgressReporter()
        safe = _ThreadSafeProgressReporter(inner)
        safe.begin_batch(50)
        safe.update_counts(success=10)
        assert safe.processed == 10

    def test_processed_property(self):
        inner = ProgressReporter()
        safe = _ThreadSafeProgressReporter(inner)
        safe.begin_batch(50)
        safe.update_counts(success=3, skipped=2, failed=1)
        assert safe.processed == 6

    def test_elapsed_property(self):
        inner = ProgressReporter()
        safe = _ThreadSafeProgressReporter(inner)
        safe.begin_batch(10)
        time.sleep(0.01)
        assert safe.elapsed > 0

    def test_custom_lock(self):
        lock = threading.Lock()
        inner = ProgressReporter()
        safe = _ThreadSafeProgressReporter(inner, lock=lock)
        safe.begin_batch(10)
        assert safe.processed == 0


# ── RowSummary ──────────────────────────────────────────────────────────


class TestRowSummary:
    def test_defaults(self):
        rs = RowSummary(
            row_index=0,
            uid="UID-001",
            status="success",
            reason="",
            sample_type="NHP",
            sample_id=123,
            assays_linked_count=2,
            access_type=4,
        )
        assert rs.uid_generated is False
        assert rs.topo_level is None

    def test_new_field_defaults(self):
        rs = RowSummary(
            row_index=0, uid="UID-001", status="success", reason="",
            sample_type="NHP", sample_id=123, assays_linked_count=2, access_type=4,
        )
        assert rs.json_metadata == ""
        assert rs.assay_ids == ""
        assert rs.original_row_index is None

    def test_new_fields_populated(self):
        rs = RowSummary(
            row_index=0, uid="UID-001", status="success", reason="",
            sample_type="NHP", sample_id=123, assays_linked_count=2, access_type=4,
            json_metadata='{"Name":"s1"}', assay_ids="1; 2; 3",
            original_row_index=5,
        )
        assert rs.json_metadata == '{"Name":"s1"}'
        assert rs.assay_ids == "1; 2; 3"
        assert rs.original_row_index == 5


# ── write_summary_csv ────────────────────────────────────────────────────


class TestWriteSummaryCsv:
    def test_basic_csv_output(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        summaries = [
            RowSummary(
                row_index=0, uid="UID-001", status="success", reason="",
                sample_type="NHP", sample_id=100, assays_linked_count=2,
                access_type=4, uid_generated=False, topo_level=0,
            ),
            RowSummary(
                row_index=1, uid="UID-002", status="failed", reason="DB error",
                sample_type="NHP", sample_id=None, assays_linked_count=None,
                access_type=None, uid_generated=True, topo_level=1,
            ),
        ]
        totals = {
            "processed": 2, "success": 1, "skipped": 0, "failed": 1,
            "elapsed_s": 1.5, "throughput_rps": 1.3,
        }
        write_summary_csv(path, summaries, totals)

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # 2 data rows + 1 separator + 1 totals = 4 rows
        assert len(rows) >= 3
        assert rows[0]["uid"] == "UID-001"
        assert rows[0]["status"] == "success"
        assert rows[1]["uid"] == "UID-002"
        assert rows[1]["status"] == "failed"

    def test_csv_with_warnings(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        summaries = []
        totals = {"processed": 0, "success": 0, "skipped": 0, "failed": 0,
                  "elapsed_s": 0.0, "throughput_rps": 0.0}
        warnings = {"unknown_columns": ["col_a", "col_b"]}
        write_summary_csv(path, summaries, totals, warnings=warnings)

        with open(path) as f:
            content = f.read()
        assert "WARNINGS" in content
        assert "col_a" in content
        assert "col_b" in content

    def test_csv_with_permissions(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        totals = {"processed": 5, "success": 5, "skipped": 0, "failed": 0,
                  "elapsed_s": 1.0, "throughput_rps": 5.0,
                  "permissions_inserted": 10}
        write_summary_csv(path, [], totals)

        with open(path) as f:
            content = f.read()
        assert "PERMISSIONS" in content
        assert "inserted=10" in content

    def test_csv_with_neo4j_metrics(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        metrics = Metrics(
            nodes_created=5, nodes_matched=3,
            derived_from_rels_created=2, of_type_rels_created=5,
            sample_type_nodes_created=1, elapsed_ms_total=150.0,
        )
        totals = {"processed": 5, "success": 5, "skipped": 0, "failed": 0,
                  "elapsed_s": 1.0, "throughput_rps": 5.0}
        write_summary_csv(path, [], totals, neo4j_metrics=metrics)

        with open(path) as f:
            content = f.read()
        assert "NEO4J" in content
        assert "nodes_created=5" in content

    def test_csv_includes_new_columns(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        summaries = [
            RowSummary(
                row_index=0, uid="UID-001", status="success", reason="",
                sample_type="NHP", sample_id=100, assays_linked_count=2,
                access_type=4, json_metadata='{"Name":"s1"}',
                assay_ids="1; 2", original_row_index=3,
            ),
        ]
        totals = {"processed": 1, "success": 1, "skipped": 0, "failed": 0,
                  "elapsed_s": 0.5, "throughput_rps": 2.0}
        write_summary_csv(path, summaries, totals)
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert "json_metadata" in rows[0]
        assert "assay_ids" in rows[0]
        assert "original_row_index" in rows[0]
        assert rows[0]["json_metadata"] == '{"Name":"s1"}'
        assert rows[0]["assay_ids"] == "1; 2"
        assert rows[0]["original_row_index"] == "3"

    def test_csv_no_neo4j_when_zero_elapsed(self, tmp_path):
        path = str(tmp_path / "summary.csv")
        metrics = Metrics(elapsed_ms_total=0.0)
        totals = {"processed": 0, "success": 0, "skipped": 0, "failed": 0,
                  "elapsed_s": 0.0, "throughput_rps": 0.0}
        write_summary_csv(path, [], totals, neo4j_metrics=metrics)

        with open(path) as f:
            content = f.read()
        assert "NEO4J" not in content


# ── build_row_summaries ──────────────────────────────────────────────────


class TestBuildRowSummaries:
    def test_basic_build(self):
        outcomes = {
            "UID-001": RowOutcome(status="success", sample_id=100),
            "UID-002": RowOutcome(status="failed", reason="DB error"),
        }
        input_models = [
            InputRowModel(UID="UID-001", SampleType="NHP_blood", json_metadata="{}"),
            InputRowModel(UID="UID-002", SampleType="D.IMG_scan", json_metadata="{}"),
        ]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert len(summaries) == 2
        assert summaries[0].uid == "UID-001"
        assert summaries[0].status == "success"
        assert summaries[1].uid == "UID-002"
        assert summaries[1].reason == "DB error"

    def test_error_collector_fills_reason(self):
        outcomes = {
            "UID-001": RowOutcome(status="failed"),
        }
        input_models = [
            InputRowModel(UID="UID-001", SampleType="NHP_blood", json_metadata="{}"),
        ]
        ec = ErrorCollector()
        ec.add(0, "UID-001", ErrorType.DB_CONSTRAINT, "Duplicate entry")
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert "Duplicate entry" in summaries[0].reason

    def test_outcome_reason_takes_precedence(self):
        outcomes = {
            "UID-001": RowOutcome(status="failed", reason="explicit reason"),
        }
        input_models = [
            InputRowModel(UID="UID-001", SampleType="NHP_blood", json_metadata="{}"),
        ]
        ec = ErrorCollector()
        ec.add(0, "UID-001", ErrorType.DB_CONSTRAINT, "Duplicate entry")
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert summaries[0].reason == "explicit reason"

    def test_missing_input_model(self):
        outcomes = {
            "UID-ORPHAN": RowOutcome(status="success", sample_id=1),
        }
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, [], ec)
        assert len(summaries) == 1
        assert summaries[0].sample_type == ""

    def test_populates_json_metadata(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{"Name":"s1"}')]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert summaries[0].json_metadata == '{"Name":"s1"}'

    def test_populates_assay_ids(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}', assay_ids=[1, 2, 3])]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert summaries[0].assay_ids == "1; 2; 3"

    def test_empty_assay_ids(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}')]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert summaries[0].assay_ids == ""

    def test_includes_rows_without_outcomes(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        input_models = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
            InputRowModel(UID="UID-002", SampleType="NHP", json_metadata='{}'),
            InputRowModel(UID="UID-003", SampleType="NHP", json_metadata='{}'),
        ]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert len(summaries) == 3
        assert summaries[0].status == "success"
        assert summaries[1].status == "not_processed"
        assert summaries[2].status == "not_processed"

    def test_includes_outcomes_without_input_models_compat(self):
        outcomes = {"UID-ORPHAN": RowOutcome(status="success", sample_id=1)}
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, [], ec)
        assert len(summaries) == 1
        assert summaries[0].uid == "UID-ORPHAN"
        assert summaries[0].sample_type == ""

    def test_uses_original_row_index(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        model = InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}')
        model.original_row_index = 5
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, [model], ec)
        assert summaries[0].row_index == 5
        assert summaries[0].original_row_index == 5

    def test_fallback_row_index_when_no_original(self):
        outcomes = {"UID-001": RowOutcome(status="success", sample_id=100)}
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}')]
        ec = ErrorCollector()
        summaries = build_row_summaries(outcomes, input_models, ec)
        assert summaries[0].row_index == 0
        assert summaries[0].original_row_index is None

    def test_empty_outcomes_all_rows_appear(self):
        input_models = [
            InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}'),
            InputRowModel(UID="UID-002", SampleType="NHP", json_metadata='{}'),
        ]
        ec = ErrorCollector()
        summaries = build_row_summaries({}, input_models, ec)
        assert len(summaries) == 2
        assert all(s.status == "not_processed" for s in summaries)

    def test_not_processed_reason_from_error_collector(self):
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}')]
        ec = ErrorCollector()
        ec.add(0, "UID-001", ErrorType.VALIDATION_JSON, "bad metadata")
        summaries = build_row_summaries({}, input_models, ec)
        assert summaries[0].status == "not_processed"
        assert "bad metadata" in summaries[0].reason

    def test_summary_csv_preserves_new_validation_reason(self):
        input_models = [InputRowModel(UID="UID-001", SampleType="NHP", json_metadata='{}')]
        ec = ErrorCollector()
        reason = "json_metadata.UID is 'NHP-260225MIT-1' but row UID column is empty; provide the UID explicitly"
        ec.add(0, "UID-001", ErrorType.VALIDATION_UID_MISMATCH, reason)
        summaries = build_row_summaries({}, input_models, ec)

        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as handle:
            path = handle.name
        try:
            write_summary_csv(
                path,
                summaries,
                totals={"processed": 1, "success": 0, "skipped": 0, "failed": 1, "elapsed_s": 0, "throughput_rps": 0},
            )
            with open(path, newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))
            assert rows[0]["reason"] == reason
        finally:
            os.unlink(path)

    def test_summary_csv_preserves_ambiguous_identity_reason(self):
        input_models = [InputRowModel(UID="UID-002", SampleType="NHP", json_metadata='{}')]
        ec = ErrorCollector()
        reason = (
            "ambiguous identity match: 2 existing samples with name_identity='Mouse-A' - "
            "duplicates must be resolved before batch can proceed (conflicting sample ids: [10, 20])"
        )
        ec.add(0, "UID-002", ErrorType.AMBIGUOUS_IDENTITY, reason)
        summaries = build_row_summaries(
            {"UID-002": RowOutcome(status="failed", reason=reason)},
            input_models,
            ec,
        )

        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as handle:
            path = handle.name
        try:
            write_summary_csv(
                path,
                summaries,
                totals={"processed": 1, "success": 0, "skipped": 0, "failed": 1, "elapsed_s": 0, "throughput_rps": 0},
            )
            with open(path, newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))
            assert rows[0]["reason"] == reason
        finally:
            os.unlink(path)


class TestInputRowModelOriginalRowIndex:
    def test_field_defaults_to_none(self):
        m = InputRowModel(SampleType="NHP", json_metadata='{}')
        assert m.original_row_index is None

    def test_field_set_explicitly(self):
        m = InputRowModel(SampleType="NHP", json_metadata='{}', original_row_index=7)
        assert m.original_row_index == 7

    def test_field_set_after_creation(self):
        m = InputRowModel(SampleType="NHP", json_metadata='{}')
        m.original_row_index = 3
        assert m.original_row_index == 3
