"""Coverage tests for batch_upload/models.py — targeting uncovered lines.

Covers:
- SampleTypeDescription: save_to_json, from_pd_df, load_from_json (lines 60-77, 81-82)
- InputRowModel: assay_titles coercion edge cases, assay_ids coercion, validators
- InsertableSample: coerce_assay_ids edge cases, normalize_json_metadata None
- RelRow validators
- DerivedFromRelRow validators
- OfTypeRelRow validators
- AssaySheetRow direction validator
- InstructionRow property accessors
"""

import json
import os
import tempfile
import pytest

from nextseek_api.batch_upload.models import (
    SampleTypeDescription,
    InputRowModel,
    InsertableSample,
    RelRow,
    DerivedFromRelRow,
    OfTypeRelRow,
    InstructionRow,
    AssaySheetRow,
    SampleTypeNodeRow,
    StudyNodeRow,
    InvestigationNodeRow,
    InInvestigationRelRow,
    InStudyRelRow,
    RowOutcome,
    PermissionCreate,
    StreamResult,
    BatchResult,
    PayloadStats,
    ConstraintStatus,
)


# ---------------------------------------------------------------------------
# SampleTypeDescription
# ---------------------------------------------------------------------------

class TestSampleTypeDescription:

    def test_save_to_json_and_load(self):
        desc = SampleTypeDescription(
            st_id=1,
            sampletype="NHP_blood",
            st_description="Blood sample",
            st_attributes=["Name", "Date"],
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name

        try:
            desc.save_to_json(path)
            loaded = SampleTypeDescription.load_from_json(path)
            assert loaded.st_id == 1
            assert loaded.sampletype == "NHP_blood"
            assert loaded.st_attributes == ["Name", "Date"]
        finally:
            os.unlink(path)

    def test_from_pd_df(self):
        import pandas as pd
        df = pd.DataFrame([
            {"st_id": 1, "sampletype": "NHP", "st_description": "Monkey", "st_attributes": "Name, Age"},
            {"st_id": 2, "sampletype": "TIS", "st_description": "Tissue", "st_attributes": ["Brain", "Liver"]},
        ])
        results = SampleTypeDescription.from_pd_df(df)
        assert len(results) == 2
        assert results[0].st_attributes == ["Name", "Age"]
        assert results[1].st_attributes == ["Brain", "Liver"]


# ---------------------------------------------------------------------------
# InputRowModel — coerce_assay_titles
# ---------------------------------------------------------------------------

class TestInputRowModelAssayTitles:

    def test_none_returns_none(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles=None)
        assert row.assay_titles is None

    def test_list_with_empty_strings(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles=["", " "])
        assert row.assay_titles is None

    def test_nan_string_returns_none(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="nan")
        assert row.assay_titles is None

    def test_empty_string_returns_none(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="")
        assert row.assay_titles is None

    def test_json_array_string(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles='["A","B"]')
        assert row.assay_titles == ["A", "B"]

    def test_bracketed_single_item(self):
        """Excel unquoted bracketed string [Title]."""
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="[ADCD Analysis]")
        assert row.assay_titles == ["ADCD Analysis"]

    def test_semicolon_delimited(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="A; B; C")
        assert row.assay_titles == ["A", "B", "C"]

    def test_comma_delimited(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="A, B")
        assert row.assay_titles == ["A", "B"]

    def test_single_string(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles="Single Assay")
        assert row.assay_titles == ["Single Assay"]

    def test_valid_list(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_titles=["X", "Y"])
        assert row.assay_titles == ["X", "Y"]


# ---------------------------------------------------------------------------
# InputRowModel — coerce_assay_ids
# ---------------------------------------------------------------------------

class TestInputRowModelAssayIds:

    def test_float_nan(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_ids="nan")
        assert row.assay_ids == []

    def test_int_value(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_ids=42)
        assert row.assay_ids == [42]

    def test_string_with_ids(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_ids="123, 456")
        assert row.assay_ids == [123, 456]

    def test_none_value(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_ids=None)
        assert row.assay_ids == []

    def test_list_with_none(self):
        row = InputRowModel(SampleType="NHP", json_metadata="{}", assay_ids=[1, None, 3])
        assert row.assay_ids == [1, 3]


# ---------------------------------------------------------------------------
# InputRowModel — json_metadata normalization
# ---------------------------------------------------------------------------

class TestInputRowModelJsonMetadata:

    def test_dict_input(self):
        row = InputRowModel(SampleType="NHP", json_metadata={"Name": "X"})
        parsed = json.loads(row.json_metadata)
        assert parsed["Name"] == "X"

    def test_bom_stripped(self):
        row = InputRowModel(SampleType="NHP", json_metadata='\ufeff{"Name": "X"}')
        parsed = json.loads(row.json_metadata)
        assert parsed["Name"] == "X"

    def test_none_becomes_empty_object(self):
        row = InputRowModel(SampleType="NHP", json_metadata=None)
        assert row.json_metadata == "{}"


# ---------------------------------------------------------------------------
# InputRowModel — validate_optional_consistency
# ---------------------------------------------------------------------------

class TestInputRowModelConsistencyValidator:

    def test_uid_mismatch_raises(self):
        with pytest.raises(Exception, match="UID column does not match"):
            InputRowModel(
                SampleType="NHP",
                UID="AAA-111111BB-1",
                json_metadata='{"UID": "BBB-222222CC-2"}',
            )

    def test_sop_id_mismatch_raises(self):
        with pytest.raises(Exception, match="sop_id does not match"):
            InputRowModel(
                SampleType="NHP",
                UID="AAA-111111BB-1",
                sop_id=99,
                json_metadata='{"UID": "AAA-111111BB-1", "Protocol": "/sops/100"}',
            )

    def test_assay_titles_length_mismatch_raises(self):
        with pytest.raises(Exception, match="assay_titles length"):
            InputRowModel(
                SampleType="NHP",
                json_metadata="{}",
                assay_ids=[1, 2],
                assay_titles=["A", "B", "C"],
            )


# ---------------------------------------------------------------------------
# InsertableSample — edge cases
# ---------------------------------------------------------------------------

class TestInsertableSample:

    def test_coerce_assay_ids_float(self):
        s = InsertableSample(
            uuid="TEST-001",
            title="Test",
            sample_type_id=1,
            json_metadata="{}",
            assay_ids="123",
        )
        assert s.assay_ids == [123]

    def test_json_metadata_none(self):
        s = InsertableSample(
            uuid="TEST-001",
            title="Test",
            sample_type_id=1,
            json_metadata=None,
        )
        assert s.json_metadata == "{}"

    def test_json_metadata_dict(self):
        s = InsertableSample(
            uuid="TEST-001",
            title="Test",
            sample_type_id=1,
            json_metadata={"Name": "X"},
        )
        parsed = json.loads(s.json_metadata)
        assert parsed["Name"] == "X"


# ---------------------------------------------------------------------------
# RelRow validators
# ---------------------------------------------------------------------------

class TestRelRow:

    def test_positive_child_id(self):
        with pytest.raises(Exception, match="child_id must be positive"):
            RelRow(child_id=0, child_uuid="A", parent_uuid="B")

    def test_empty_uuid_raises(self):
        with pytest.raises(Exception, match="uuid must be non-empty"):
            RelRow(child_id=1, child_uuid="", parent_uuid="B")

    def test_valid_row(self):
        r = RelRow(child_id=1, child_uuid=" A ", parent_uuid=" B ")
        assert r.child_uuid == "A"
        assert r.parent_uuid == "B"


# ---------------------------------------------------------------------------
# DerivedFromRelRow validators
# ---------------------------------------------------------------------------

class TestDerivedFromRelRow:

    def test_positive_ids(self):
        with pytest.raises(Exception, match="id must be positive"):
            DerivedFromRelRow(child_id=0, child_uuid="A", parent_id=1, parent_uuid="B")

    def test_empty_uuid_raises(self):
        with pytest.raises(Exception, match="uuid must be non-empty"):
            DerivedFromRelRow(child_id=1, child_uuid="  ", parent_id=2, parent_uuid="B")

    def test_valid_row(self):
        r = DerivedFromRelRow(child_id=1, child_uuid="A", parent_id=2, parent_uuid="B")
        assert r.child_id == 1


# ---------------------------------------------------------------------------
# OfTypeRelRow validators
# ---------------------------------------------------------------------------

class TestOfTypeRelRow:

    def test_positive_ids(self):
        with pytest.raises(Exception, match="id must be positive"):
            OfTypeRelRow(sample_id=0, sample_uuid="A", sample_type_id=1)


# ---------------------------------------------------------------------------
# InStudyRelRow validators
# ---------------------------------------------------------------------------

class TestInStudyRelRow:

    def test_positive_study_id(self):
        with pytest.raises(Exception, match="study_id must be positive"):
            InStudyRelRow(sample_uuid="A", study_id=0)

    def test_empty_uuid_raises(self):
        with pytest.raises(Exception, match="sample_uuid must be non-empty"):
            InStudyRelRow(sample_uuid="  ", study_id=1)


# ---------------------------------------------------------------------------
# StudyNodeRow validators
# ---------------------------------------------------------------------------

class TestStudyNodeRow:

    def test_empty_title_raises(self):
        with pytest.raises(Exception, match="title must be non-empty"):
            StudyNodeRow(id=1, title="  ")


# ---------------------------------------------------------------------------
# InvestigationNodeRow validators
# ---------------------------------------------------------------------------

class TestInvestigationNodeRow:

    def test_empty_title_raises(self):
        with pytest.raises(Exception, match="title must be non-empty"):
            InvestigationNodeRow(id=1, title="")


# ---------------------------------------------------------------------------
# InInvestigationRelRow validators
# ---------------------------------------------------------------------------

class TestInInvestigationRelRow:

    def test_positive_ids(self):
        with pytest.raises(Exception, match="id must be positive"):
            InInvestigationRelRow(study_id=0, investigation_id=1)


# ---------------------------------------------------------------------------
# SampleTypeNodeRow validators
# ---------------------------------------------------------------------------

class TestSampleTypeNodeRow:

    def test_empty_title_raises(self):
        with pytest.raises(Exception, match="title must be non-empty"):
            SampleTypeNodeRow(title="  ")


# ---------------------------------------------------------------------------
# InstructionRow
# ---------------------------------------------------------------------------

class TestInstructionRow:

    def test_sample_type_property(self):
        row = InstructionRow(field="Name", database_field="NHP::Name")
        assert row.sample_type == "NHP"

    def test_attribute_name_property(self):
        row = InstructionRow(field="Name", database_field="NHP::Name")
        assert row.attribute_name == "Name"

    def test_missing_delimiter_raises(self):
        with pytest.raises(Exception, match="database_field must be"):
            InstructionRow(field="Name", database_field="NHPName")


# ---------------------------------------------------------------------------
# AssaySheetRow
# ---------------------------------------------------------------------------

class TestAssaySheetRow:

    def test_invalid_direction_raises(self):
        with pytest.raises(Exception, match="direction must be 1 or 2"):
            AssaySheetRow(SampleType="NHP", Assay=1, Direction=3)

    def test_valid_row(self):
        row = AssaySheetRow(SampleType="NHP", Assay=1, Direction=1)
        assert row.direction == 1


# ---------------------------------------------------------------------------
# Simple dataclass/model coverage
# ---------------------------------------------------------------------------

class TestSimpleModels:

    def test_stream_result(self):
        sr = StreamResult(row_index=0, data=None, errors=["bad row"])
        assert sr.row_index == 0
        assert sr.errors == ["bad row"]

    def test_batch_result_defaults(self):
        br = BatchResult()
        assert br.inserted_count == 0
        assert br.updated_count == 0
        assert br.stopped_early is False

    def test_payload_stats(self):
        ps = PayloadStats(eligible_children=10, skipped_children_missing_parents=2)
        assert ps.created_count == 0

    def test_constraint_status(self):
        cs = ConstraintStatus()
        assert cs.sample_id == "skipped"

    def test_row_outcome(self):
        ro = RowOutcome(status="success", sample_id=1)
        assert ro.uid_generated is False

    def test_permission_create_negative_contributor(self):
        with pytest.raises(Exception, match="contributor_id must be positive"):
            PermissionCreate(contributor_id=0, policy_id=1)

    def test_permission_create_negative_policy(self):
        with pytest.raises(Exception, match="policy_id must be positive"):
            PermissionCreate(contributor_id=1, policy_id=0)

    def test_permission_create_uppercase_type(self):
        p = PermissionCreate(contributor_id=1, policy_id=1, contributor_type="project")
        assert p.contributor_type == "Project"
