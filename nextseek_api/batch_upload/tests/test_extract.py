"""Tests for the EXTRACT stage."""
import json

import pytest
from pydantic import ValidationError as PydanticValidationError

from nextseek_api.batch_upload.extract import (
    _coerce_json_cell,
    _detect_unknown_columns,
    _extract_error_messages,
    _extract_failed_indices,
    _prepare_row_dicts,
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


class TestDetectUnknownColumns:
    def test_unknown_columns_detected(self):
        cols = {"uid", "sampletype", "json_metadata", "assay_ids", "mapped_assay_ids"}
        unknown = _detect_unknown_columns(cols)
        assert "mapped_assay_ids" in unknown

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
        # Force a validation error with a missing field
        bad_rows = [
            {"UID": "ok", "SampleType": "T", "json_metadata": "{}", "assay_ids": []},
            {"SampleType": "T", "json_metadata": "{}", "assay_ids": []},  # missing UID
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
