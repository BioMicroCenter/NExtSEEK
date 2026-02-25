"""Tests for the EXTRACT stage."""
import json
import os
import tempfile

import openpyxl
import pytest
from pydantic import ValidationError as PydanticValidationError

from nextseek_api.batch_upload.extract import (
    _coerce_json_cell,
    _detect_unknown_columns,
    _extract_error_messages,
    _extract_failed_indices,
    _prepare_row_dicts,
    stream_rows,
)


class TestCoerceJsonCell:
    def test_dict(self):
        result = _coerce_json_cell({"a": 1})
        assert json.loads(result) == {"a": 1}

    def test_list(self):
        result = _coerce_json_cell([1, 2])
        assert json.loads(result) == [1, 2]

    def test_none(self):
        assert _coerce_json_cell(None) == "{}"

    def test_string_passthrough(self):
        assert _coerce_json_cell('{"x":1}') == '{"x":1}'


class TestPrepareRowDicts:
    def test_column_mapping(self):
        rows = [
            {"uid": "A", "sampletype": "B", "json_metadata": "{}", "assay_ids": "1,2"},
        ]
        result = _prepare_row_dicts(rows, ["uid", "sampletype", "json_metadata", "assay_ids"])
        assert result[0]["UID"] == "A"
        assert result[0]["SampleType"] == "B"
        assert result[0]["json_metadata"] == "{}"

    def test_json_cell_coercion(self):
        rows = [
            {"uid": "A", "sampletype": "B", "json_metadata": {"k": "v"}, "assay_ids": ""},
        ]
        result = _prepare_row_dicts(rows, ["uid", "sampletype", "json_metadata", "assay_ids"])
        assert result[0]["json_metadata"] == '{"k":"v"}'


class TestDropLogicNullUid:
    """Test that rows with null uid but valid sampletype+json_metadata are kept."""

    def _make_xlsx(self, rows: list[dict]) -> str:
        wb = openpyxl.Workbook()
        ws = wb.active
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
        path = os.path.join(tempfile.mkdtemp(), "test.xlsx")
        wb.save(path)
        return path

    def test_null_uid_row_kept_when_sampletype_present(self):
        """Row with null uid but valid SampleType and json_metadata should NOT be dropped."""
        xlsx = self._make_xlsx([
            {"uid": "TEST-000001AA-1", "SampleType": "TypeA",
             "json_metadata": '{"Name":"has_uid"}', "assay_ids": "1"},
            {"uid": None, "SampleType": "TypeB",
             "json_metadata": '{"Name":"no_uid"}', "assay_ids": "2"},
        ])
        try:
            _, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            assert len(results) == 2, f"Expected 2 rows, got {len(results)}"
            # Both should be valid
            assert results[0].data is not None
            assert results[1].data is not None
            assert results[1].data.UID is None
        finally:
            os.unlink(xlsx)

    def test_fully_blank_row_dropped(self):
        """Row where uid, sampletype, AND json_metadata are all null should be dropped."""
        xlsx = self._make_xlsx([
            {"uid": "TEST-000001AA-1", "SampleType": "TypeA",
             "json_metadata": '{"Name":"valid"}', "assay_ids": "1"},
            {"uid": None, "SampleType": None,
             "json_metadata": None, "assay_ids": None},
        ])
        try:
            _, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            assert len(results) == 1, f"Expected 1 row (blank dropped), got {len(results)}"
        finally:
            os.unlink(xlsx)


class TestDetectUnknownColumns:
    def test_unknown_columns_detected(self):
        cols = {"uid", "sampletype", "json_metadata", "assay_ids", "totally_random_col"}
        unknown = _detect_unknown_columns(cols)
        assert "totally_random_col" in unknown

    def test_mapped_columns_are_known(self):
        cols = {"uid", "sampletype", "json_metadata", "assay_ids", "mapped_assay_ids", "mapped_study_id"}
        unknown = _detect_unknown_columns(cols)
        assert "mapped_assay_ids" not in unknown
        assert "mapped_study_id" not in unknown

class TestExtractFailedIndices:
    def test_parses_loc(self):
        """Test extraction of failed indices from ValidationError."""
        from pydantic import TypeAdapter
        from nextseek_api.batch_upload.models import InputRowModel

        adapter = TypeAdapter(list[InputRowModel])
        rows = [
            {"UID": "ok", "SampleType": "T", "json_metadata": "{}", "assay_ids": []},
            {"UID": "", "SampleType": "T", "json_metadata": "{}", "assay_ids": []},  # bad: empty UID? Actually UID="" is valid string
        ]
        # Force a validation error with a missing required field (SampleType)
        bad_rows = [
            {"UID": "ok", "SampleType": "T", "json_metadata": "{}", "assay_ids": []},
            {"UID": "ok", "json_metadata": "{}", "assay_ids": []},  # missing SampleType
        ]
        try:
            adapter.validate_python(bad_rows)
            assert False, "Should have raised ValidationError"
        except PydanticValidationError as e:
            failed = _extract_failed_indices(e)
            assert 1 in failed

    def test_error_messages_grouped(self):
        from pydantic import TypeAdapter
        from nextseek_api.batch_upload.models import InputRowModel

        adapter = TypeAdapter(list[InputRowModel])
        bad_rows = [
            {"UID": "ok", "SampleType": "T", "json_metadata": "{}", "assay_ids": []},
            {"json_metadata": "{}", "assay_ids": []},  # missing UID and SampleType
        ]
        try:
            adapter.validate_python(bad_rows)
        except PydanticValidationError as e:
            messages = _extract_error_messages(e)
            assert 1 in messages
            assert len(messages[1]) >= 1  # at least one error for row 1


class TestStreamRowsErrorRecovery:
    """Regression tests for stream_rows error recovery path.

    Covers:
    - UnboundLocalError crash (Python 3 deletes `as e` after except block)
    - model_construct producing raw untransformed data for valid rows
    - User's exact spreadsheet columns/data
    """

    def _make_xlsx(self, rows: list[dict]) -> str:
        """Write rows to a temporary .xlsx file and return the path."""
        wb = openpyxl.Workbook()
        ws = wb.active
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row[h] for h in headers])
        path = os.path.join(tempfile.mkdtemp(), "test.xlsx")
        wb.save(path)
        return path

    def test_mixed_valid_and_invalid_rows_no_crash(self):
        """stream_rows must not crash with UnboundLocalError when bulk
        validation fails. Uses UID mismatch to trigger ValidationError."""
        xlsx = self._make_xlsx([
            # Valid row
            {"UID": "TEST-000001AA-1", "SampleType": "TypeA",
             "json_metadata": '{"UID":"TEST-000001AA-1"}', "assay_ids": "1"},
            # Invalid row: UID mismatch triggers model_validator error
            {"UID": "MISMATCH-000001AA-1", "SampleType": "TypeB",
             "json_metadata": '{"UID":"DIFFERENT-000001AA-1"}', "assay_ids": "2"},
        ])
        try:
            unknown_cols, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            assert len(results) == 2
            # Row 0: valid
            assert results[0].data is not None
            assert results[0].errors == []
            # Row 1: invalid (UID mismatch)
            assert results[1].data is None
            assert len(results[1].errors) > 0
        finally:
            os.unlink(xlsx)

    def test_valid_rows_in_recovery_have_coerced_fields(self):
        """When error recovery runs, valid rows must have properly coerced
        fields (assay_ids as List[int], json_metadata minified), NOT raw
        untransformed strings from model_construct."""
        xlsx = self._make_xlsx([
            # Valid row with assay_ids as string (needs coercion)
            {"UID": "TEST-000001AA-1", "SampleType": "TypeA",
             "json_metadata": '{"UID":"TEST-000001AA-1"}', "assay_ids": "171,172"},
            # Invalid row to trigger error recovery path
            {"UID": "MISMATCH-000001AA-1", "SampleType": "TypeB",
             "json_metadata": '{"UID":"DIFFERENT-000001AA-1"}', "assay_ids": "3"},
        ])
        try:
            unknown_cols, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            valid_result = results[0]
            assert valid_result.data is not None
            # assay_ids must be List[int], not raw string "171,172"
            assert valid_result.data.assay_ids == [171, 172]
            assert isinstance(valid_result.data.assay_ids, list)
        finally:
            os.unlink(xlsx)

    def test_all_valid_rows_fast_path(self):
        """When all rows are valid, stream_rows uses the fast path (no recovery)."""
        xlsx = self._make_xlsx([
            {"UID": "TEST-000001AA-1", "SampleType": "TypeA",
             "json_metadata": '{"UID":"TEST-000001AA-1"}', "assay_ids": "1"},
        ])
        try:
            unknown_cols, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            assert len(results) == 1
            assert results[0].data is not None
            assert results[0].data.UID == "TEST-000001AA-1"
        finally:
            os.unlink(xlsx)

    def test_user_spreadsheet_columns_accepted(self):
        """Reproduce the exact columns and data from the user's spreadsheet."""
        xlsx = self._make_xlsx([{
            "UID": "A.ADCD-250312ALT-1-TEST",
            "json_metadata": '{"UID": "A.ADCD-250312ALT-1-TEST", "File_PrimaryData": "test.xlsx"}',
            "study_title": "Test Study",
            "assay_titles": "['ADCD Analysis - Data Linked']",
            "mapped_assay_ids": "['171']",
            "mapped_study_id": 20,
            "SampleType": "A.ADCD",
            "assay_ids": "['171']",
            "study_id": 20,
            "sop_id": 30,
        }])
        try:
            unknown_cols, row_iter = stream_rows(xlsx)
            results = list(row_iter)
            assert len(results) == 1
            result = results[0]
            assert result.data is not None, f"Expected valid data, got errors: {result.errors}"
            assert result.data.UID == "A.ADCD-250312ALT-1-TEST"
            assert result.data.SampleType == "A.ADCD"
            assert result.data.assay_ids == [171]
        finally:
            os.unlink(xlsx)
