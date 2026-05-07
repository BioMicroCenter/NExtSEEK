"""Tests for Pydantic models, validators, and error system."""
import json
import pytest
from pydantic import ValidationError

from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.batch_upload.models import (
    BatchResult,
    DerivedFromRelRow,
    InStudyRelRow,
    InputRowModel,
    InsertableSample,
    NodeRow,
    PermissionCreate,
    SampleMetadata,
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

    def test_normalize_json_metadata_promotes_lowercase_name(self):
        row = InputRowModel(
            UID="X", SampleType="Y", json_metadata='{"name":"lower"}', assay_ids=[]
        )
        assert row.json_metadata == '{"Name":"lower"}'

    def test_normalize_json_metadata_prefers_existing_name(self):
        row = InputRowModel(
            UID="X",
            SampleType="Y",
            json_metadata='{"Name":"upper","name":"lower"}',
            assay_ids=[],
        )
        assert row.json_metadata == '{"Name":"upper"}'

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

    def test_none_uid_validates(self):
        """UID=None should be accepted (for UID_GEN stage to fill in later)."""
        row = InputRowModel(
            UID=None,
            SampleType="NHP",
            json_metadata='{"Name":"test"}',
            assay_ids=[1],
        )
        assert row.UID is None
        assert row.SampleType == "NHP"

    def test_none_uid_with_metadata_uid_rejected(self):
        with pytest.raises(ValidationError, match="row UID column is empty"):
            InputRowModel(
                UID=None,
                SampleType="NHP",
                json_metadata='{"Name":"test","UID":"NHP-260225MIT-1"}',
                assay_ids=[],
            )

    def test_none_uid_with_valid_fields(self):
        """UID=None row with all other fields valid."""
        row = InputRowModel(
            UID=None,
            SampleType="D.IMG_files",
            json_metadata='{"File_PrimaryData":"sample.fastq"}',
            assay_ids=[171, 172],
            study_id=20,
        )
        assert row.UID is None
        assert row.assay_ids == [171, 172]

    def test_whitespace_uid_normalized_to_none(self):
        row = InputRowModel(UID="   ", SampleType="NHP", json_metadata='{"Name":"test"}')
        assert row.UID is None

    def test_invalid_json_metadata_rejected(self):
        with pytest.raises(ValidationError, match="json_metadata is not valid JSON"):
            InputRowModel(SampleType="NHP", json_metadata='{"Name":"oops"')

    def test_json_metadata_array_rejected(self):
        with pytest.raises(ValidationError, match="json_metadata must be a JSON object"):
            InputRowModel(SampleType="NHP", json_metadata='["not","an","object"]')

    @pytest.mark.parametrize("n_chars", [600, 1000, 2500])
    def test_long_identity_accepted_post_amendment(self, n_chars):
        long_name = "x" * n_chars
        row = InputRowModel(
            UID="NHP-260225MIT-1",
            SampleType="NHP",
            json_metadata=json.dumps({"Name": long_name}),
        )
        assert row.UID == "NHP-260225MIT-1"
        hashed_identity = hash_identity(
            extract_identity(json.loads(row.json_metadata), uid=row.UID)
        )
        assert isinstance(hashed_identity, str)
        assert len(hashed_identity) == 64

    def test_sop_validation_still_nested_under_non_null_uid(self):
        row = InputRowModel(
            UID=None,
            SampleType="NHP",
            sop_id=99,
            json_metadata='{"Name":"test","Protocol":"/sops/100"}',
        )
        assert row.sop_id == 99


# ── InsertableSample ──────────────────────────────────────────────────────


class TestInsertableSample:
    def test_validators_match_input_row(self):
        sample = InsertableSample(
            uuid="ABC", title="Test", sample_type_id=1,
            json_metadata='{"x":1}', assay_ids="4,5,6"
        )
        assert sample.assay_ids == [4, 5, 6]


class TestSampleMetadataCanonicalFields:
    def test_canonical_file_primary_data_declared(self):
        m = SampleMetadata.model_validate({"File_PrimaryData": "x.csv"})
        assert m.File_PrimaryData == "x.csv"

    def test_canonical_forward_reverse_declared(self):
        m = SampleMetadata.model_validate({
            "File_PrimaryData_Forward": "f.fa",
            "File_PrimaryData_Reverse": "r.fa",
        })
        assert m.File_PrimaryData_Forward == "f.fa"
        assert m.File_PrimaryData_Reverse == "r.fa"

    def test_typo_forward_reverse_still_declared(self):
        m = SampleMetadata.model_validate({
            "File_PrimartyData_Forward": "f.fa",
            "File_PrimartyData_Reverse": "r.fa",
        })
        assert m.File_PrimartyData_Forward == "f.fa"
        assert m.File_PrimartyData_Reverse == "r.fa"

    def test_model_dump_canonicalizes_keys(self):
        m = SampleMetadata.model_validate({"File_PrimartyData": "x.csv"})
        dumped = m.model_dump(exclude_none=True)
        assert dumped["File_PrimaryData"] == "x.csv"
        assert "File_PrimartyData" not in dumped

    def test_model_dump_canonical_wins_over_typo(self):
        m = SampleMetadata.model_validate({
            "File_PrimaryData": "canon.csv",
            "File_PrimartyData": "typo.csv",
        })
        dumped = m.model_dump(exclude_none=True)
        assert dumped["File_PrimaryData"] == "canon.csv"
        assert "File_PrimartyData" not in dumped


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

    def test_parent_title_hashes_defaults_to_empty_list(self):
        """NodeRow exposes parent_title_hashes mirroring parent_titles."""
        n = NodeRow(sample_id=1, sample_uuid="abc-123", sample_type="Fly")
        assert n.parent_title_hashes == []

    def test_parent_title_hashes_accepts_list_of_strings(self):
        """parent_title_hashes accepts an explicit list of hex digests."""
        digests = ["a" * 64, "b" * 64]
        n = NodeRow(
            sample_id=1,
            sample_uuid="abc-123",
            sample_type="Fly",
            parent_title_hashes=digests,
        )
        assert n.parent_title_hashes == digests


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
        assert classify_severity(ErrorType.VALIDATION_UID_MISMATCH) == Severity.ERROR
        assert classify_severity(ErrorType.VALIDATION_METADATA_SHAPE) == Severity.ERROR
        assert classify_severity(ErrorType.AMBIGUOUS_IDENTITY) == Severity.ERROR
        assert classify_severity(ErrorType.DUPLICATE) == Severity.INFO

    def test_classify_validation_error(self):
        assert _classify_validation_error("bad json format") == ErrorType.VALIDATION_JSON
        assert _classify_validation_error(
            "json_metadata.UID is 'A' but row UID column is empty; provide the UID explicitly"
        ) == ErrorType.VALIDATION_UID_MISMATCH
        assert _classify_validation_error(
            "json_metadata must be a JSON object, not a JSON array/scalar"
        ) == ErrorType.VALIDATION_METADATA_SHAPE
        assert _classify_validation_error(
            "ambiguous identity match: 2 existing samples with name_identity='X' - duplicates must be resolved before batch can proceed"
        ) == ErrorType.AMBIGUOUS_IDENTITY
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


# ── InStudyRelRow ────────────────────────────────────────────────────────────


class TestInStudyRelRow:
    def test_valid(self):
        r = InStudyRelRow(sample_uuid="UID-1", study_id=5)
        assert r.sample_uuid == "UID-1"
        assert r.study_id == 5

    def test_strips_uuid(self):
        r = InStudyRelRow(sample_uuid="  UID-1  ", study_id=5)
        assert r.sample_uuid == "UID-1"

    def test_rejects_zero_study_id(self):
        with pytest.raises(ValidationError):
            InStudyRelRow(sample_uuid="UID-1", study_id=0)

    def test_rejects_negative_study_id(self):
        with pytest.raises(ValidationError):
            InStudyRelRow(sample_uuid="UID-1", study_id=-1)

    def test_rejects_empty_uuid(self):
        with pytest.raises(ValidationError):
            InStudyRelRow(sample_uuid="", study_id=5)

    def test_rejects_blank_uuid(self):
        with pytest.raises(ValidationError):
            InStudyRelRow(sample_uuid="   ", study_id=5)

    def test_forbids_extra(self):
        with pytest.raises(ValidationError):
            InStudyRelRow(sample_uuid="UID-1", study_id=5, extra_field="bad")


# ── StudyNodeRow ─────────────────────────────────────────────────────────


class TestStudyNodeRow:
    def test_valid_study_node(self):
        from nextseek_api.batch_upload.models import StudyNodeRow
        row = StudyNodeRow(id=1, title="Study A", description="desc")
        assert row.id == 1
        assert row.title == "Study A"

    def test_empty_title_raises(self):
        from nextseek_api.batch_upload.models import StudyNodeRow
        with pytest.raises(Exception):
            StudyNodeRow(id=1, title="", description="")

    def test_whitespace_title_raises(self):
        from nextseek_api.batch_upload.models import StudyNodeRow
        with pytest.raises(Exception):
            StudyNodeRow(id=1, title="   ", description="")

    def test_default_description(self):
        from nextseek_api.batch_upload.models import StudyNodeRow
        row = StudyNodeRow(id=1, title="Study A")
        assert row.description == ""


# ── InvestigationNodeRow ─────────────────────────────────────────────────


class TestInvestigationNodeRow:
    def test_valid(self):
        from nextseek_api.batch_upload.models import InvestigationNodeRow
        row = InvestigationNodeRow(id=1, title="Inv A", description="desc")
        assert row.id == 1

    def test_empty_title_raises(self):
        from nextseek_api.batch_upload.models import InvestigationNodeRow
        with pytest.raises(Exception):
            InvestigationNodeRow(id=1, title="")


# ── InInvestigationRelRow ────────────────────────────────────────────────


class TestInInvestigationRelRow:
    def test_valid(self):
        from nextseek_api.batch_upload.models import InInvestigationRelRow
        row = InInvestigationRelRow(study_id=1, investigation_id=2)
        assert row.study_id == 1
        assert row.investigation_id == 2

    def test_zero_study_id_raises(self):
        from nextseek_api.batch_upload.models import InInvestigationRelRow
        with pytest.raises(Exception):
            InInvestigationRelRow(study_id=0, investigation_id=1)

    def test_negative_investigation_id_raises(self):
        from nextseek_api.batch_upload.models import InInvestigationRelRow
        with pytest.raises(Exception):
            InInvestigationRelRow(study_id=1, investigation_id=-1)


# ── BatchResult.updated_count ────────────────────────────────────────────


class TestBatchResultUpdatedCount:
    def test_default_updated_count(self):
        from nextseek_api.batch_upload.models import BatchResult
        br = BatchResult()
        assert br.updated_count == 0

    def test_set_updated_count(self):
        from nextseek_api.batch_upload.models import BatchResult
        br = BatchResult(updated_count=5)
        assert br.updated_count == 5


# ── BatchUploadConfig.update_existing ────────────────────────────────────


class TestUpdateExistingConfig:
    def test_default_false(self):
        from nextseek_api.batch_upload.config import BatchUploadConfig
        config = BatchUploadConfig()
        assert config.update_existing is False

    def test_override_true(self):
        from nextseek_api.batch_upload.config import BatchUploadConfig
        config = BatchUploadConfig(update_existing=True)
        assert config.update_existing is True
