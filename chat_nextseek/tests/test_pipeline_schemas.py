"""Pipeline-agent Pydantic schemas — round-trip and validation checks."""
import pytest
from pydantic import ValidationError

from chat_nextseek.schemas.pipeline import (
    DirectiveParseOutput,
    EditDiffOutput,
    FieldRef,
    GroupByResolution,
    SamplesRef,
    SanityCheckOutput,
)


def test_samples_ref_last_search_minimal():
    s = SamplesRef(kind="last_search")
    assert s.kind == "last_search"
    assert s.uids == []
    assert s.accessions == []


def test_samples_ref_explicit_uids_round_trips():
    s = SamplesRef(kind="explicit_uids", uids=["UID-1", "UID-2"])
    assert s.uids == ["UID-1", "UID-2"]


def test_samples_ref_accessions_round_trips():
    s = SamplesRef(kind="accessions", accessions=["SRR1", "SRR2"])
    assert s.accessions == ["SRR1", "SRR2"]


def test_directive_parse_build_shape():
    out = DirectiveParseOutput(
        sub_mode="build",
        pipeline_key="rnaseq",
        samples_ref=SamplesRef(kind="last_search"),
        group_by_phrase="exposure",
    )
    assert out.sub_mode == "build"
    assert out.pipeline_key == "rnaseq"
    assert out.group_by_phrase == "exposure"
    assert out.multi_pipeline_attempt is False


def test_directive_parse_question_minimal():
    out = DirectiveParseOutput(sub_mode="question")
    assert out.sub_mode == "question"
    assert out.pipeline_key is None
    assert out.samples_ref is None


def test_directive_parse_reject_requires_reason():
    out = DirectiveParseOutput(sub_mode="reject", rejection_reason="not a directive")
    assert out.rejection_reason == "not a directive"


def test_directive_parse_rejects_invalid_sub_mode():
    with pytest.raises(ValidationError):
        DirectiveParseOutput(sub_mode="invalid")


def test_sanity_proceed_verdict():
    out = SanityCheckOutput(
        verdict="proceed",
        leaves_to_use=["D.SEQ-1", "D.SEQ-2"],
        confidence_note="all match",
    )
    assert out.verdict == "proceed"
    assert out.dropped_leaves_summary == ""


def test_sanity_mismatch_with_alternative():
    out = SanityCheckOutput(
        verdict="mismatch",
        suggested_alternative_pipeline="rnaseq",
        confidence_note="data is RNA-seq, not WGS",
    )
    assert out.suggested_alternative_pipeline == "rnaseq"


def test_groupby_resolution_committed():
    out = GroupByResolution(
        field=FieldRef(sample_type="NHP", field_name="Treatment1"),
        distinct_values=["NDMA", "vehicle"],
        rationale="exposure matches Treatment1",
    )
    assert out.requires_clarification is False
    assert out.field.sample_type == "NHP"
    assert out.distinct_values == ["NDMA", "vehicle"]


def test_groupby_resolution_clarifying():
    out = GroupByResolution(
        field=FieldRef(sample_type="NHP", field_name="Treatment1"),
        distinct_values=[],
        rationale="multiple candidates",
        requires_clarification=True,
        candidates=[
            FieldRef(sample_type="NHP", field_name="Treatment1"),
            FieldRef(sample_type="NHP", field_name="Treatment1Dose"),
        ],
        clarifying_question="Which exposure field?",
    )
    assert out.requires_clarification is True
    assert len(out.candidates) == 2


def test_edit_diff_apply_shape():
    out = EditDiffOutput(
        action="apply",
        rows=[{"sample": "S1", "fastq_1": "..."}, {"sample": "S2", "fastq_1": "..."}],
    )
    assert out.action == "apply"
    assert len(out.rows) == 2


def test_edit_diff_reject_shape():
    out = EditDiffOutput(action="reject", reject_reason="unknown UID")
    assert out.reject_reason == "unknown UID"
