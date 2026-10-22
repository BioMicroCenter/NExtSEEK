"""Contract tests pinning the orchestrator's terminal-result dict shape.

`run_batch_upload_multi`, `_error_result`, and `_cancelled_result` all return
plain dicts (for Celery JSON serialization). `BatchUploadResult` is the typed
source-of-truth for that shape. These tests assert the dict-to-model round-trip
stays valid — if anyone changes the orchestrator dict shape and breaks the
contract, this fails loudly.
"""
from __future__ import annotations

from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType
from nextseek_api.batch_upload.models import BatchUploadResult, InputRowModel, RowOutcome
from nextseek_api.batch_upload.orchestrator import _cancelled_result, _error_result

# Reuse the DB-mocking harness from the level-ordering integration tests.
from nextseek_api.batch_upload.tests.test_orchestrator_levels import (
    FakeDB,
    _make_row,
    _run_orchestrator,
)


class TestBatchUploadResultContract:
    """The orchestrator's terminal dict must validate against BatchUploadResult."""

    def test_success_path_dict_validates(self):
        """run_batch_upload_multi success path -> BatchUploadResult.model_validate passes."""
        rows = [_make_row("NHP-260101TST-1"), _make_row("NHP-260101TST-2")]
        result = _run_orchestrator(rows, FakeDB())

        model = BatchUploadResult.model_validate(result)

        assert model.job_id is not None
        assert model.summary_path is not None
        assert model.totals.processed == 2
        assert model.totals.success == 2
        assert model.totals.error is None
        assert model.totals.cancelled is False

    def test_error_result_dict_validates(self):
        """_error_result output validates; totals.error is set."""
        ec = ErrorCollector()
        ec.add(row_index=-1, uid=None, error_type=ErrorType.UNKNOWN, message="boom")
        result = _error_result("job-1", "/tmp/summary.csv", ec, "CONVERT failed: boom")

        model = BatchUploadResult.model_validate(result)

        assert model.totals.error == "CONVERT failed: boom"
        assert model.totals.processed == 0
        assert model.errors and model.errors[0].message == "boom"
        assert model.errors[0].type == ErrorType.UNKNOWN.value

    def test_cancelled_result_dict_validates(self):
        """_cancelled_result output validates; totals.cancelled is True."""
        result = _cancelled_result("job-2", "/tmp/summary.csv")

        model = BatchUploadResult.model_validate(result)

        assert model.totals.cancelled is True
        assert model.totals.error is None
        assert model.errors == []


class TestResultSeverityAndCancelCounts:
    def test_result_errors_carry_severity(self):
        """Each result error says whether it was a WARNING or an ERROR."""
        rows = [
            InputRowModel(UID="NHP-260101TST-1", SampleType="NHP_blood", json_metadata="{}", assay_ids=[]),
            InputRowModel(UID="NHP-260101TST-2", SampleType="NHP_blood", json_metadata='{"Bogus": "x"}', assay_ids=[]),
        ]
        result = _run_orchestrator(rows, FakeDB(), row_errors=[(7, ["bad cell"])])

        sev = {e["type"]: e["severity"] for e in result["errors"]}
        assert sev[ErrorType.VALIDATION_JSON.value] == "WARNING"
        assert sev[ErrorType.VALIDATION_ATTRIBUTE_NAME.value] == "ERROR"

    def test_cancelled_result_counts_committed_rows(self):
        """A cancel after some batches committed still reports those rows."""
        outcomes = {
            "A": RowOutcome(status="success", sample_id=1),
            "B": RowOutcome(status="success", sample_id=2),
            "C": RowOutcome(status="success", sample_id=3),
            "D": RowOutcome(status="failed", reason="x"),
            "E": RowOutcome(status="skipped", reason="y"),
        }
        result = _cancelled_result("job-3", "/tmp/summary.csv", outcomes=outcomes)

        totals = result["totals"]
        assert (totals["success"], totals["failed"], totals["skipped"]) == (3, 1, 1)
        assert totals["processed"] == 5
        assert totals["cancelled"] is True

    def test_cancelled_summary_csv_counts_committed_rows(self):
        from unittest.mock import patch
        outcomes = {"A": RowOutcome(status="success", sample_id=1), "B": RowOutcome(status="failed", reason="x")}
        rows = [InputRowModel(UID=u, SampleType="NHP_blood", json_metadata="{}", assay_ids=[]) for u in outcomes]
        with patch("nextseek_api.batch_upload.orchestrator.write_summary_csv") as w, \
             patch("nextseek_api.batch_upload.orchestrator.os.path.isfile", return_value=False):
            _cancelled_result("j", "/tmp/none.csv", valid_rows=rows, outcomes=outcomes)
        totals = w.call_args[0][2]
        assert (totals["processed"], totals["success"], totals["failed"]) == (2, 1, 1)
