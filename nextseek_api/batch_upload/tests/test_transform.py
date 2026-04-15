"""Tests for the TRANSFORM stage (transform.py)."""
from __future__ import annotations

import json
import unicodedata
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.errors import JsonNormalizationError
from nextseek_api.batch_upload.models import InputRowModel, InsertableSample, SampleMetadata
from nextseek_api.batch_upload.transform import (
    _normalize_text,
    build_insertable,
    parse_and_minify_json,
    title_from_metadata,
)


# ── parse_and_minify_json ──────────────────────────────────────────────────


class TestParseAndMinifyJson:
    def test_valid_json_object(self):
        result = parse_and_minify_json('{"key": "value"}')
        assert result == '{"key":"value"}'

    def test_empty_string_returns_empty_object(self):
        result = parse_and_minify_json("")
        assert result == "{}"

    def test_whitespace_only_returns_empty_object(self):
        result = parse_and_minify_json("   ")
        assert result == "{}"

    def test_strips_bom(self):
        raw = "\ufeff" + '{"name": "test"}'
        result = parse_and_minify_json(raw)
        assert result == '{"name":"test"}'

    def test_nfc_normalization(self):
        # Use NFD e-acute (two code points) and verify NFC output (single code point)
        nfd_str = '{"name": "' + unicodedata.normalize("NFD", "\u00e9") + '"}'
        result = parse_and_minify_json(nfd_str)
        assert "\u00e9" in result  # NFC single code point

    def test_malformed_json_raises_error(self):
        with pytest.raises(JsonNormalizationError, match="Cannot parse JSON"):
            parse_and_minify_json("{bad json!!")

    def test_nested_json_minified(self):
        raw = '{"a": {"b": [1, 2, 3]}}'
        result = parse_and_minify_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": {"b": [1, 2, 3]}}
        # Verify minified (no extra spaces)
        assert "  " not in result

    def test_non_string_input_converted(self):
        result = parse_and_minify_json(123)
        # str(123) = "123" which is valid JSON
        assert result == "123"


# ── title_from_metadata ───────────────────────────────────────────────────


class TestTitleFromMetadata:
    def test_name_takes_priority(self):
        meta = SampleMetadata(Name="Sample Name", File_PrimartyData="file.csv")
        assert title_from_metadata(meta, "UID-123") == "Sample Name"

    def test_file_primary_data_fallback(self):
        meta = SampleMetadata(Name=None, File_PrimartyData="data_file.csv")
        assert title_from_metadata(meta, "UID-123") == "data_file.csv"

    def test_uid_fallback(self):
        meta = SampleMetadata()
        assert title_from_metadata(meta, "UID-123") == "UID-123"

    def test_empty_name_falls_through(self):
        meta = SampleMetadata(Name="   ", File_PrimartyData="file.txt")
        assert title_from_metadata(meta, "UID-123") == "file.txt"

    def test_empty_file_primary_data_falls_through(self):
        meta = SampleMetadata(Name="", File_PrimartyData="  ")
        assert title_from_metadata(meta, "UID-123") == "UID-123"

    def test_integer_name(self):
        meta = SampleMetadata(Name=42)
        assert title_from_metadata(meta, "UID-123") == "42"

    def test_d_prefix_canonical_file_primary_data_wins(self):
        meta = SampleMetadata(Name="ignored", File_PrimaryData="canonical.csv")
        assert title_from_metadata(meta, "D.SEQ-260413NA-1") == "canonical.csv"

    def test_malformed_uid_defaults_to_name_based_precedence(self):
        meta = SampleMetadata(Name="sample-A", File_PrimaryData="other.csv")
        assert title_from_metadata(meta, "MALFORMED_UID") == "sample-A"

    def test_missing_uid_and_no_identity_returns_none(self):
        assert title_from_metadata(SampleMetadata(), None) is None


# ── _normalize_text ───────────────────────────────────────────────────────


class TestNormalizeText:
    def test_strips_whitespace(self):
        assert _normalize_text("  hello  ") == "hello"

    def test_collapses_internal_whitespace(self):
        assert _normalize_text("hello   world") == "hello world"

    def test_nfc_normalization(self):
        nfd = unicodedata.normalize("NFD", "\u00e9")
        result = _normalize_text(nfd)
        assert result == "\u00e9"  # NFC form


# ── build_insertable ─────────────────────────────────────────────────────


class TestBuildInsertable:
    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_basic_build(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = ({1, 2}, set())

        row = InputRowModel(
            UID="TEST-260101MIT-1",
            SampleType="NHP_blood",
            json_metadata='{"Name": "Test Sample"}',
            assay_ids=[1, 2],
        )
        conn = MagicMock()
        sample, warnings = build_insertable(row, project_id=10, conn=conn)

        assert isinstance(sample, InsertableSample)
        assert sample.uuid == "TEST-260101MIT-1"
        assert sample.sample_type_id == 42
        assert sample.assay_ids == [1, 2]
        assert sample.title == "Test Sample"
        assert warnings == {}

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_missing_assays_generates_warning(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = ({1}, {99})

        row = InputRowModel(
            UID="TEST-260101MIT-2",
            SampleType="NHP_blood",
            json_metadata="{}",
            assay_ids=[1, 99],
        )
        conn = MagicMock()
        sample, warnings = build_insertable(row, project_id=10, conn=conn)

        assert "missing_assays" in warnings
        assert "99" in warnings["missing_assays"][0]

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_per_row_project_id_override(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())

        row = InputRowModel(
            UID="TEST-260101MIT-3",
            SampleType="NHP_blood",
            json_metadata="{}",
            project_id=99,
        )
        conn = MagicMock()
        build_insertable(row, project_id=10, conn=conn)
        # Should use per-row project_id=99
        mock_resolve.assert_called_once_with("NHP_blood", 99, conn)

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_malformed_json_raises(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())

        row = InputRowModel(
            UID="TEST-260101MIT-4",
            SampleType="NHP_blood",
            json_metadata="{}",  # Valid for model creation
        )
        # Patch parse_and_minify_json to raise
        conn = MagicMock()
        with patch(
            "nextseek_api.batch_upload.transform.parse_and_minify_json",
            side_effect=JsonNormalizationError("bad json"),
        ):
            with pytest.raises(JsonNormalizationError):
                build_insertable(row, project_id=10, conn=conn)

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_canonicalizes_typo_spelling_on_write(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())
        row = InputRowModel(
            UID="D.SEQ-260413NA-1",
            SampleType="D.SEQ_files",
            json_metadata='{"File_PrimartyData":"x.csv"}',
        )
        conn = MagicMock()
        sample, _ = build_insertable(row, project_id=10, conn=conn)
        parsed = json.loads(sample.json_metadata)
        assert parsed["File_PrimaryData"] == "x.csv"
        assert "File_PrimartyData" not in parsed

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_non_dict_metadata_falls_back_to_empty_dict(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())
        row = InputRowModel(
            UID="TEST-260101MIT-5",
            SampleType="NHP_blood",
            json_metadata="{}",
        )
        conn = MagicMock()
        with patch("nextseek_api.batch_upload.transform.parse_and_minify_json", return_value='["x"]'):
            sample, _ = build_insertable(row, project_id=10, conn=conn)
        assert sample.json_metadata == "{}"
        assert sample.title == "TEST-260101MIT-5"

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_unparseable_post_minify_metadata_uses_empty_model(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())
        row = InputRowModel(
            UID="TEST-260101MIT-6",
            SampleType="NHP_blood",
            json_metadata='{"Name":"x"}',
        )
        conn = MagicMock()
        with patch("nextseek_api.batch_upload.transform.parse_and_minify_json", return_value='{"Name":"x"}'), \
             patch("nextseek_api.batch_upload.transform._json_loads", side_effect=ValueError("boom")):
            sample, _ = build_insertable(row, project_id=10, conn=conn)
        assert sample.json_metadata == "{}"
        assert sample.title == "TEST-260101MIT-6"

    @patch("nextseek_api.batch_upload.transform.validate_assay_ids")
    @patch("nextseek_api.batch_upload.transform.resolve_sample_type_id")
    def test_model_validation_failure_uses_empty_sample_metadata(self, mock_resolve, mock_validate):
        mock_resolve.return_value = 42
        mock_validate.return_value = (set(), set())
        row = InputRowModel(
            UID="TEST-260101MIT-7",
            SampleType="NHP_blood",
            json_metadata='{"Name":"x"}',
        )
        conn = MagicMock()
        with patch("nextseek_api.batch_upload.transform.SampleMetadata.model_validate", side_effect=ValueError("bad")):
            sample, _ = build_insertable(row, project_id=10, conn=conn)
        assert sample.title == "TEST-260101MIT-7"
