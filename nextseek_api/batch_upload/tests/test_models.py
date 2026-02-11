"""Tests for Pydantic models, validators, and error system."""
import json
import pytest
from pydantic import ValidationError

from nextseek_api.batch_upload.models import (
    BatchResult,
    ConstraintStatus,
    DerivedFromRelRow,
    InputRowModel,
    InsertableSample,
    Metrics,
    Neo4jRuntimeConfig,
    NodeRow,
    OfTypeRelRow,
    PayloadStats,
    PermissionCreate,
    RelRow,
    RowOutcome,
    SampleMetadata,
    SampleTypeDescription,
    SampleTypeNodeRow,
    StreamResult,
    _minify_json_string,
)
from nextseek_api.batch_upload.errors import (
    ErrorCollector,
    ErrorType,
    Severity,
    _classify_validation_error,
    classify_severity,
)


# ── _minify_json_string ───────────────────────────────────────────────────


class TestMinifyJsonString:
    def test_dict_input(self):
        assert _minify_json_string({"a": 1, "b": "x"}) in ['{"a":1,"b":"x"}', '{"b":"x","a":1}']

    def test_string_input(self):
        result = _minify_json_string('  { "a" : 1 }  ')
        assert result == '{"a":1}'

    def test_bom_stripped(self):
        result = _minify_json_string('\ufeff{"key":"val"}')
        assert result == '{"key":"val"}'

    def test_unicode_preserved(self):
        result = _minify_json_string('{"name": "\u00e9l\u00e8ve"}')
        assert "\u00e9" in result

    def test_nested(self):
        result = _minify_json_string('{"a": {"b": [1, 2, 3]}}')
        assert result == '{"a":{"b":[1,2,3]}}'

    def test_empty_string(self):
        assert _minify_json_string("") == "{}"

    def test_empty_whitespace(self):
        assert _minify_json_string("   ") == "{}"


# ── InputRowModel ─────────────────────────────────────────────────────────


class TestInputRowModel:
    def test_basic_valid(self):
        row = InputRowModel(
            UID="NHP-220630FLY-1-PUB",
            SampleType="Fly",
            json_metadata='{"Name":"test"}',
            assay_ids=[1, 2, 3],
        )
        assert row.UID == "NHP-220630FLY-1-PUB"
        assert row.assay_ids == [1, 2, 3]

    def test_coerce_assay_ids_from_string(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata="{}", assay_ids="1,2,3"
        )
        assert row.assay_ids == [1, 2, 3]

    def test_coerce_assay_ids_from_int(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata="{}", assay_ids=42
        )
        assert row.assay_ids == [42]

    def test_coerce_assay_ids_empty_string(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata="{}", assay_ids=""
        )
        assert row.assay_ids == []

    def test_coerce_assay_ids_none(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata="{}", assay_ids=None
        )
        assert row.assay_ids == []

    def test_normalize_json_metadata_from_dict(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata={"a": 1}, assay_ids=[]
        )
        assert row.json_metadata == '{"a":1}'

    def test_normalize_json_metadata_minifies(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata='  { "a" : 1 }  ', assay_ids=[]
        )
        assert row.json_metadata == '{"a":1}'

    def test_project_id_optional(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata="{}", assay_ids=[]
        )
        assert row.project_id is None

    def test_extra_columns_allowed(self):
        row = InputRowModel(
            UID="X",
            SampleType="Y",
            json_metadata="{}",
            assay_ids=[],
            mapped_assay_ids="[1]",
        )
        # extra fields should not error
        assert row.UID == "X"

    def test_assay_titles_bracketed_string(self):
        row = InputRowModel(
            UID="X",
            SampleType="Y",
            json_metadata="{}",
            assay_ids="[171]",
            assay_titles="[ADCD Analysis - Data Linked]",
        )
        assert row.assay_ids == [171]
        assert row.assay_titles == ["ADCD Analysis - Data Linked"]

    def test_assay_titles_length_mismatch_rejected(self):
        with pytest.raises(ValidationError):
            InputRowModel(
                UID="X",
                SampleType="Y",
                json_metadata="{}",
                assay_ids="1,2",
                assay_titles="[Only one]",
            )

    def test_uid_mismatch_vs_json_metadata_rejected(self):
        with pytest.raises(ValidationError):
            InputRowModel(
                UID="COL-UID",
                SampleType="Y",
                json_metadata='{\"UID\":\"META-UID\"}',
                assay_ids=[],
            )


# ── InsertableSample ──────────────────────────────────────────────────────


class TestInsertableSample:
    def test_validators_match_input_row(self):
        sample = InsertableSample(
            uuid="ABC", title="Test", sample_type_id=1,
            json_metadata='{"x":1}', assay_ids="4,5,6"
        )
        assert sample.assay_ids == [4, 5, 6]


# ── NodeRow validators ────────────────────────────────────────────────────


class TestNodeRow:
    def test_valid(self):
        n = NodeRow(sample_id=1, sample_uuid="abc-123", sample_type="Fly")
        assert n.sample_id == 1

    def test_invalid_id(self):
        with pytest.raises(ValidationError):
            NodeRow(sample_id=0, sample_uuid="abc", sample_type="Fly")

    def test_empty_uuid(self):
        with pytest.raises(ValidationError):
            NodeRow(sample_id=1, sample_uuid="", sample_type="Fly")


# ── PermissionCreate validators ───────────────────────────────────────────


class TestPermissionCreate:
    def test_valid(self):
        p = PermissionCreate(contributor_id=1, policy_id=10)
        assert p.access_type == 4
        assert p.contributor_type == "Project"

    def test_negative_contributor(self):
        with pytest.raises(ValidationError):
            PermissionCreate(contributor_id=-1, policy_id=10)

    def test_uppercase_type(self):
        p = PermissionCreate(contributor_id=1, policy_id=10, contributor_type="project")
        assert p.contributor_type == "Project"


# ── SampleTypeNodeRow ─────────────────────────────────────────────────────


class TestSampleTypeNodeRow:
    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            SampleTypeNodeRow(title="")


# ── DerivedFromRelRow ─────────────────────────────────────────────────────


class TestDerivedFromRelRow:
    def test_valid(self):
        r = DerivedFromRelRow(
            child_id=1, child_uuid="c-uuid",
            parent_id=2, parent_uuid="p-uuid",
        )
        assert r.child_id == 1

    def test_zero_id_rejected(self):
        with pytest.raises(ValidationError):
            DerivedFromRelRow(
                child_id=0, child_uuid="c", parent_id=2, parent_uuid="p"
            )


# ── SampleMetadata ────────────────────────────────────────────────────────


class TestSampleMetadata:
    def test_extra_fields_allowed(self):
        m = SampleMetadata(UID="X", custom_field="val")
        assert m.model_extra.get("custom_field") == "val"


# ── Dataclasses ───────────────────────────────────────────────────────────


class TestStreamResult:
    def test_with_data(self):
        row = InputRowModel(UID="X", SampleType="Y", json_metadata="{}", assay_ids=[])
        sr = StreamResult(row_index=0, data=row, errors=[])
        assert sr.data is not None
        assert sr.errors == []

    def test_with_errors(self):
        sr = StreamResult(row_index=1, data=None, errors=["bad json"])
        assert sr.data is None
        assert len(sr.errors) == 1


class TestBatchResult:
    def test_defaults(self):
        br = BatchResult()
        assert br.inserted_count == 0
        assert br.stopped_early is False


# ── Error system ──────────────────────────────────────────────────────────


class TestErrorSystem:
    def test_classify_severity(self):
        assert classify_severity(ErrorType.DB_CONN) == Severity.CRITICAL
        assert classify_severity(ErrorType.DB_CONSTRAINT) == Severity.ERROR
        assert classify_severity(ErrorType.VALIDATION_JSON) == Severity.WARNING
        assert classify_severity(ErrorType.DUPLICATE) == Severity.INFO

    def test_classify_validation_error(self):
        assert _classify_validation_error("bad json format") == ErrorType.VALIDATION_JSON
        assert _classify_validation_error("invalid assay") == ErrorType.VALIDATION_ASSAY
        assert _classify_validation_error("unknown sampletype") == ErrorType.VALIDATION_SAMPLE_TYPE
        assert _classify_validation_error("something else") == ErrorType.UNKNOWN

    def test_error_collector(self):
        ec = ErrorCollector()
        ec.add(0, "UID-1", ErrorType.VALIDATION_JSON, "bad json")
        ec.add(1, "UID-2", ErrorType.DB_CONN, "connection lost")

        assert len(ec.all_errors()) == 2
        assert len(ec.errors_for_uid("UID-1")) == 1
        assert ec.has_critical() is True
        counts = ec.count_by_type()
        assert counts[ErrorType.VALIDATION_JSON] == 1
        assert counts[ErrorType.DB_CONN] == 1

    def test_error_collector_no_critical(self):
        ec = ErrorCollector()
        ec.add(0, "UID-1", ErrorType.DUPLICATE, "dup")
        assert ec.has_critical() is False
