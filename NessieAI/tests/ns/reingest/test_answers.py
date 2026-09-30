"""The fix loop's allowlist: a curator answer may only fill a cell QA flagged,
that is empty, and that the run itself does not measure. The first test is the
guarantee the whole design rests on."""
from __future__ import annotations

import pytest

from NessieAI.ns.reingest import answers, maps
from NessieAI.ns.reingest.mapper import MappedAttribute, MappedRow
from NessieAI.ns.reingest_qa import (
    CATALOG_REQUIRED_MISSING, HARD, MISSING_REQUIRED, SOFT, Finding)

RNASEQ = maps.load("nf-core/rnaseq")


def _finding(sample_type, attribute, row, code=MISSING_REQUIRED, severity=HARD):
    return Finding(code=code, severity=severity, sample_type=sample_type,
                   attribute=attribute, row_index=row)


def _fill(sample_type, attribute, value="x", rows=None):
    return answers.FillAnswer(sample_type=sample_type, attribute=attribute,
                              value=value, rows=rows)


def test_a_measured_cell_cannot_be_filled_even_when_qa_flags_it():
    rows = [MappedRow(sample_type="D.SEQ", uid="D.SEQ-EXAMPLE-1")]
    findings = [_finding("D.SEQ", "TotalReads", 0)]
    errors = answers.check_fill(
        _fill("D.SEQ", "TotalReads", "12345678"), findings=findings, rows=rows,
        run_sourced=answers.run_sourced_attributes(RNASEQ, {}, "D.SEQ"))
    assert errors and "run data" in errors[0]


def test_every_qc_attribute_of_the_map_is_run_sourced():
    sourced = answers.run_sourced_attributes(RNASEQ, {}, "D.SEQ")
    assert {"TotalReads", "NumAligned", "MappedPercent", "UnalignedReads"} <= sourced


def test_dollar_refs_and_file_attributes_are_run_sourced_but_session_values_are_not():
    sourced = answers.run_sourced_attributes(RNASEQ, {}, "A.ALN")
    assert {"Aligner", "Genome", "Software", "File_PrimaryData",
            "Checksum_PrimaryData", "File_SecondaryData"} <= sourced
    assert "Scientist" not in sourced   # @nextseek_user: a person, not a measurement
    assert "Protocol" not in sourced


def test_an_approved_rule_makes_its_attribute_run_sourced():
    rule = maps.AttributeRule(**{"from": "star-foo", "target": "D.SEQ"})
    assert "FooRate" in answers.run_sourced_attributes(RNASEQ, {"FooRate": rule}, "D.SEQ")


def test_a_flagged_empty_person_field_is_fillable():
    rows = [MappedRow(sample_type="A.ALN")]
    findings = [_finding("A.ALN", "Scientist", 0, CATALOG_REQUIRED_MISSING, SOFT)]
    assert answers.check_fill(
        _fill("A.ALN", "Scientist", "A. Curator"), findings=findings, rows=rows,
        run_sourced=answers.run_sourced_attributes(RNASEQ, {}, "A.ALN")) == []


def test_an_unflagged_attribute_is_refused():
    rows = [MappedRow(sample_type="A.ALN")]
    errors = answers.check_fill(_fill("A.ALN", "Protocol"), findings=[], rows=rows,
                                run_sourced=frozenset())
    assert errors and "did not flag" in errors[0]


def test_a_flag_on_another_sample_type_does_not_count():
    rows = [MappedRow(sample_type="A.GEX")]
    findings = [_finding("A.ALN", "Protocol", 0)]
    assert answers.check_fill(_fill("A.GEX", "Protocol"), findings=findings, rows=rows,
                              run_sourced=frozenset())


def test_a_fill_never_overwrites_a_mapped_value():
    rows = [MappedRow(sample_type="A.ALN", attributes={
        "Protocol": MappedAttribute(attribute="Protocol", value="P-1", origin="map")})]
    findings = [_finding("A.ALN", "Protocol", 0)]
    errors = answers.check_fill(_fill("A.ALN", "Protocol"), findings=findings,
                                rows=rows, run_sourced=frozenset())
    assert errors and "already hold" in errors[0]


def test_rows_outside_the_flagged_rows_are_refused():
    rows = [MappedRow(sample_type="A.ALN"), MappedRow(sample_type="A.ALN")]
    findings = [_finding("A.ALN", "Protocol", 0)]
    errors = answers.check_fill(_fill("A.ALN", "Protocol", rows=[1]), findings=findings,
                                rows=rows, run_sourced=frozenset())
    assert errors and "[1]" in errors[0]


def test_omitted_rows_target_exactly_the_flagged_rows():
    findings = [_finding("A.ALN", "Protocol", 0), _finding("A.ALN", "Protocol", 2)]
    assert answers.fill_targets(_fill("A.ALN", "Protocol"), findings) == [0, 2]


@pytest.mark.parametrize("attribute", sorted(answers.NEVER_FILLABLE))
def test_structural_keys_are_never_fillable(attribute):
    rows = [MappedRow(sample_type="A.ALN")]
    findings = [_finding("A.ALN", attribute, 0)]
    assert answers.check_fill(_fill("A.ALN", attribute), findings=findings, rows=rows,
                              run_sourced=frozenset())


def test_a_group_label_finding_licenses_its_members():
    from NessieAI.ns.reingest_qa import group_label, group_members_for_label
    label = group_label(("File_PrimaryData", "Link_PrimaryData"))
    assert group_members_for_label(label)
    rows = [MappedRow(sample_type="A.ALN")]
    findings = [_finding("A.ALN", label, 0)]
    assert answers.check_fill(_fill("A.ALN", "Link_PrimaryData", "s3://bucket/x"),
                              findings=findings, rows=rows,
                              run_sourced=frozenset()) == []


def test_parse_answers_refuses_unknown_keys_and_bad_json():
    with pytest.raises(answers.AnswerRejected):
        answers.parse_answers("{not json")
    with pytest.raises(answers.AnswerRejected):
        answers.parse_answers({"fill": [], "edit": []})
    assert answers.parse_answers(None).is_empty()
    assert answers.parse_answers("").is_empty()


def test_a_fill_value_must_not_be_blank():
    with pytest.raises(answers.AnswerRejected):
        answers.parse_answers({"fill": [{"sample_type": "A.ALN",
                                          "attribute": "Protocol", "value": ""}]})
