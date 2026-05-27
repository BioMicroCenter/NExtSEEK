"""Contract tests pinning the orchestrator's terminal-result dict shape.

`run_batch_upload_multi`, `_error_result`, and `_cancelled_result` all return
plain dicts (for Celery JSON serialization). `BatchUploadResult` is the typed
source-of-truth for that shape. These tests assert the dict-to-model round-trip
stays valid — if anyone changes the orchestrator dict shape and breaks the
contract, this fails loudly.
"""
from __future__ import annotations

from nextseek_api.batch_upload.errors import ErrorCollector, ErrorType
from nextseek_api.batch_upload.models import BatchUploadResult
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
