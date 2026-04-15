"""Coverage tests for batch_upload/errors.py — targeting uncovered lines 132-149.

Covers:
- _ThreadSafeErrorCollector: all proxy methods
- _classify_validation_error: all branches
"""

import threading
import pytest

from nextseek_api.batch_upload.errors import (
    ErrorCollector,
    ErrorType,
    Severity,
    _ThreadSafeErrorCollector,
    _classify_validation_error,
    classify_severity,
    RowError,
)


class TestThreadSafeErrorCollector:

    def test_add_and_all_errors(self):
        collector = ErrorCollector()
        ts = _ThreadSafeErrorCollector(collector)
        ts.add(row_index=0, uid="A", error_type=ErrorType.UNKNOWN, message="test error")
        errors = ts.all_errors()
        assert len(errors) == 1
        assert errors[0].message == "test error"

    def test_errors_for_uid(self):
        collector = ErrorCollector()
        ts = _ThreadSafeErrorCollector(collector)
        ts.add(row_index=0, uid="A", error_type=ErrorType.DB_CONN, message="err1")
        ts.add(row_index=1, uid="B", error_type=ErrorType.UNKNOWN, message="err2")
        assert len(ts.errors_for_uid("A")) == 1
        assert len(ts.errors_for_uid("B")) == 1
        assert len(ts.errors_for_uid("C")) == 0

    def test_count_by_type(self):
        collector = ErrorCollector()
        ts = _ThreadSafeErrorCollector(collector)
        ts.add(row_index=0, uid="A", error_type=ErrorType.DB_CONN, message="err1")
        ts.add(row_index=1, uid="B", error_type=ErrorType.DB_CONN, message="err2")
        ts.add(row_index=2, uid="C", error_type=ErrorType.UNKNOWN, message="err3")
        counts = ts.count_by_type()
        assert counts[ErrorType.DB_CONN] == 2
        assert counts[ErrorType.UNKNOWN] == 1

    def test_has_critical(self):
        collector = ErrorCollector()
        ts = _ThreadSafeErrorCollector(collector)
        assert ts.has_critical() is False
        ts.add(row_index=0, uid="A", error_type=ErrorType.DB_CONN, message="critical")
        assert ts.has_critical() is True

    def test_custom_lock(self):
        collector = ErrorCollector()
        lock = threading.Lock()
        ts = _ThreadSafeErrorCollector(collector, lock=lock)
        ts.add(row_index=0, uid="A", error_type=ErrorType.UNKNOWN, message="test")
        assert len(ts.all_errors()) == 1

    def test_severity_override(self):
        collector = ErrorCollector()
        ts = _ThreadSafeErrorCollector(collector)
        ts.add(row_index=0, uid="A", error_type=ErrorType.UNKNOWN, message="test",
               severity=Severity.INFO)
        assert ts.all_errors()[0].severity == Severity.INFO


class TestClassifyValidationError:

    def test_json_keyword(self):
        assert _classify_validation_error("Invalid JSON format") == ErrorType.VALIDATION_JSON

    def test_uid_mismatch_keyword(self):
        assert _classify_validation_error(
            "json_metadata.UID is 'NHP-1' but row UID column is empty; provide the UID explicitly"
        ) == ErrorType.VALIDATION_UID_MISMATCH

    def test_metadata_shape_keyword(self):
        assert _classify_validation_error(
            "json_metadata must be a JSON object, not a JSON array/scalar"
        ) == ErrorType.VALIDATION_METADATA_SHAPE

    def test_assay_keyword(self):
        assert _classify_validation_error("Assay not found") == ErrorType.VALIDATION_ASSAY

    def test_sampletype_keyword(self):
        assert _classify_validation_error("SampleType not found") == ErrorType.VALIDATION_SAMPLE_TYPE

    def test_sample_type_keyword(self):
        assert _classify_validation_error("sample_type mismatch") == ErrorType.VALIDATION_SAMPLE_TYPE

    def test_sample_type_with_space(self):
        assert _classify_validation_error("sample type invalid") == ErrorType.VALIDATION_SAMPLE_TYPE

    def test_unknown_fallback(self):
        assert _classify_validation_error("something else entirely") == ErrorType.UNKNOWN


class TestClassifySeverity:

    def test_known_type(self):
        assert classify_severity(ErrorType.DB_CONN) == Severity.CRITICAL
        assert classify_severity(ErrorType.DUPLICATE) == Severity.INFO
        assert classify_severity(ErrorType.VALIDATION_UID_MISMATCH) == Severity.ERROR
        assert classify_severity(ErrorType.VALIDATION_METADATA_SHAPE) == Severity.ERROR

    def test_all_error_types_mapped(self):
        for et in ErrorType:
            sev = classify_severity(et)
            assert isinstance(sev, Severity)
