"""The fix loop's allowlist: a curator answer may only fill a cell QA flagged,
that is empty, and that the run itself does not measure. The first test is the
guarantee the whole design rests on."""
from __future__ import annotations

import pytest

from NessieAI.ns.reingest import answers, manifest as manifest_mod, maps
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


BAM_A = "star_salmon/S1.markdup.sorted.bam"
BAM_B = "hisat2/S1.markdup.sorted.bam"


def _group(sample_type="A.ALN", attribute="File_PrimaryData", candidates=(BAM_A, BAM_B)):
    return {"sample_type": sample_type, "attribute": attribute,
            "chosen": candidates[0], "candidates": list(candidates), "sample_count": 1}


def _manifest(metrics=None, checksums=None):
    return manifest_mod.RunManifest(
        run_dir="/net/cluster/runs/r1",
        pipeline=manifest_mod.PipelineInfo(name="nf-core/rnaseq", version="3.18.0",
                                           run_name="r1"),
        samples=[manifest_mod.SampleRecord(
            nfcore_sample="S1", d_seq_uid="D.SEQ-EXAMPLE-1",
            uid_resolution=manifest_mod.RESOLUTION_LAUNCH_RECORD,
            parent_sample_type="D.SEQ", metrics=metrics or {})],
        checksums=checksums or {},
        sources={"metrics": "multiqc/general_stats.txt"})


def test_choose_accepts_only_a_listed_candidate():
    ok = answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData", path=BAM_B)
    bad = answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData",
                               path="elsewhere/S1.bam")
    assert answers.check_choose(ok, groups=[_group()]) == []
    assert "not one of the candidates" in answers.check_choose(bad, groups=[_group()])[0]


def test_choose_without_an_ambiguous_pick_is_refused():
    ok = answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData", path=BAM_B)
    assert answers.check_choose(ok, groups=[])


def test_place_needs_an_uncovered_key_and_a_defined_attribute():
    rows = [MappedRow(sample_type="D.SEQ", uid="D.SEQ-EXAMPLE-1", nfcore_sample="S1")]
    unmapped = [{"raw_key": "star-foo_rate", "example_value": 1.0}]
    place = answers.PlaceAnswer(raw_key="star-foo_rate", sample_type="D.SEQ",
                                attribute="FooRate")
    assert answers.check_place(place, unmapped=unmapped, rows=rows,
                               attribute_exists=lambda st, a: True) == []
    assert "not defined" in answers.check_place(
        place, unmapped=unmapped, rows=rows, attribute_exists=lambda st, a: False)[0]
    assert "not an uncovered key" in answers.check_place(
        place, unmapped=[], rows=rows, attribute_exists=lambda st, a: True)[0]


def test_place_onto_a_mapped_attribute_is_refused():
    rows = [MappedRow(sample_type="D.SEQ", uid="D.SEQ-EXAMPLE-1", nfcore_sample="S1",
                      attributes={"MappedPercent": MappedAttribute(
                          attribute="MappedPercent", value=91.0, origin="map")})]
    place = answers.PlaceAnswer(raw_key="star-foo", sample_type="D.SEQ",
                                attribute="MappedPercent")
    assert answers.check_place(place, unmapped=[{"raw_key": "star-foo"}], rows=rows,
                               attribute_exists=lambda st, a: True)


def test_place_onto_a_type_without_existing_rows_is_refused():
    place = answers.PlaceAnswer(raw_key="star-foo", sample_type="A.ALN", attribute="X")
    rows = [MappedRow(sample_type="A.ALN")]   # new rows carry no per-sample metrics
    assert answers.check_place(place, unmapped=[{"raw_key": "star-foo"}], rows=rows,
                               attribute_exists=lambda st, a: True)


def test_one_bad_answer_rejects_the_whole_set_with_every_reason():
    bundle = answers.Answers(
        fill=[_fill("A.ALN", "Protocol", "P-9"), _fill("A.ALN", "Genome", "hg19")],
        choose=[answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData",
                                     path="nope")])
    rows = {"A.ALN": [MappedRow(sample_type="A.ALN")]}
    findings = {"A.ALN": [_finding("A.ALN", "Protocol", 0), _finding("A.ALN", "Genome", 0)]}
    with pytest.raises(answers.AnswerRejected) as exc:
        answers.validate(bundle, findings_by_type=findings, mapped_by_type=rows,
                         unmapped=[], groups=[_group()],
                         run_sourced_for=lambda st: answers.run_sourced_attributes(RNASEQ, {}, st),
                         attribute_exists=lambda st, a: True)
    assert len(exc.value.reasons) == 2   # Genome (run-sourced) and the bad path


def test_split_defers_the_other_calls_types_and_rejects_unknown_ones():
    bundle = answers.Answers(fill=[_fill("A.ALN", "Protocol")],
                             place=[answers.PlaceAnswer(raw_key="k", sample_type="D.SEQ",
                                                        attribute="X")])
    mine, deferred = answers.split_for_call(bundle, this_call={"A.ALN"},
                                            other_call={"D.SEQ"})
    assert [f.attribute for f in mine.fill] == ["Protocol"] and not mine.place
    assert deferred == [{"kind": "place", "sample_type": "D.SEQ", "attribute": "X"}]
    with pytest.raises(answers.AnswerRejected):
        answers.split_for_call(answers.Answers(fill=[_fill("A.XYZ", "Protocol")]),
                               this_call={"A.ALN"}, other_call={"D.SEQ"})


def test_apply_fill_marks_the_cell_as_curator_supplied():
    rows = {"A.ALN": [MappedRow(sample_type="A.ALN"), MappedRow(sample_type="A.ALN")]}
    findings = {"A.ALN": [_finding("A.ALN", "Protocol", 1)]}
    answers.apply_answers(answers.Answers(fill=[_fill("A.ALN", "Protocol", "P-9")]),
                          mapped_by_type=rows, findings_by_type=findings,
                          run_manifest=_manifest(), answered_by="A. Curator on 2026-09-30")
    assert "Protocol" not in rows["A.ALN"][0].attributes
    cell = rows["A.ALN"][1].attributes["Protocol"]
    assert (cell.value, cell.origin, cell.answered_by) == (
        "P-9", "curator", "A. Curator on 2026-09-30")


def test_apply_choose_swaps_file_and_checksum_and_drops_a_stale_checksum():
    row = MappedRow(sample_type="A.ALN", nfcore_sample="S1", attributes={
        "File_PrimaryData": MappedAttribute(attribute="File_PrimaryData",
                                            value="S1.markdup.sorted.bam", origin="map",
                                            source_file=BAM_A, candidates=[BAM_A, BAM_B]),
        "Checksum_PrimaryData": MappedAttribute(attribute="Checksum_PrimaryData",
                                                value="aaa", origin="map")})
    rows = {"A.ALN": [row]}
    choose = answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData",
                                  path=BAM_B)
    answers.apply_answers(answers.Answers(choose=[choose]), mapped_by_type=rows,
                          findings_by_type={}, run_manifest=_manifest(),
                          answered_by="c")
    picked = row.attributes["File_PrimaryData"]
    assert (picked.source_file, picked.origin, picked.candidates) == (BAM_B, "curator", [])
    assert "Checksum_PrimaryData" not in row.attributes   # "aaa" was BAM_A's

    row2 = MappedRow(sample_type="A.ALN", nfcore_sample="S1", attributes={
        "File_PrimaryData": MappedAttribute(attribute="File_PrimaryData", value="x",
                                            origin="map", candidates=[BAM_A, BAM_B])})
    answers.apply_answers(answers.Answers(choose=[choose]),
                          mapped_by_type={"A.ALN": [row2]}, findings_by_type={},
                          run_manifest=_manifest(checksums={BAM_B: "bbb"}),
                          answered_by="c")
    assert row2.attributes["Checksum_PrimaryData"].value == "bbb"


def test_apply_place_takes_the_value_from_the_manifest_not_the_answer():
    row = MappedRow(sample_type="D.SEQ", uid="D.SEQ-EXAMPLE-1", nfcore_sample="S1")
    place = answers.PlaceAnswer(raw_key="star-foo_rate", sample_type="D.SEQ",
                                attribute="FooRate")
    answers.apply_answers(answers.Answers(place=[place]), mapped_by_type={"D.SEQ": [row]},
                          findings_by_type={}, run_manifest=_manifest({"star-foo_rate": 4.2}),
                          answered_by="c")
    cell = row.attributes["FooRate"]
    assert (cell.value, cell.raw_key, cell.origin) == (4.2, "star-foo_rate", "curator")


def test_a_whitespace_only_fill_value_is_refused_and_values_are_stripped():
    with pytest.raises(answers.AnswerRejected):
        answers.parse_answers({"fill": [{"sample_type": "A.ALN",
                                          "attribute": "Protocol", "value": "   "}]})
    parsed = answers.parse_answers({"fill": [{"sample_type": "A.ALN",
                                               "attribute": "Protocol", "value": " P-1 "}]})
    assert parsed.fill[0].value == "P-1"


def test_an_explicit_empty_rows_list_is_refused():
    rows = [MappedRow(sample_type="A.ALN")]
    findings = [_finding("A.ALN", "Protocol", 0)]
    errors = answers.check_fill(_fill("A.ALN", "Protocol", rows=[]), findings=findings,
                                rows=rows, run_sourced=frozenset())
    assert errors == ["fill A.ALN.Protocol: rows is empty"]


def _validate(bundle, findings=None, rows=None, unmapped=(), groups=()):
    answers.validate(bundle, findings_by_type=findings or {}, mapped_by_type=rows or {},
                     unmapped=list(unmapped), groups=list(groups),
                     run_sourced_for=lambda st: frozenset(),
                     attribute_exists=lambda st, a: True)


def test_two_fills_on_overlapping_rows_are_refused():
    findings = {"A.ALN": [_finding("A.ALN", "Protocol", 0)]}
    rows = {"A.ALN": [MappedRow(sample_type="A.ALN")]}
    bundle = answers.Answers(fill=[_fill("A.ALN", "Protocol", "P-1"),
                                   _fill("A.ALN", "Protocol", "P-2")])
    with pytest.raises(answers.AnswerRejected) as exc:
        _validate(bundle, findings, rows)
    assert "fill A.ALN.Protocol: answered twice for rows [0]" in exc.value.reasons


def test_two_fills_on_disjoint_rows_are_allowed():
    findings = {"A.ALN": [_finding("A.ALN", "Protocol", 0), _finding("A.ALN", "Protocol", 1)]}
    rows = {"A.ALN": [MappedRow(sample_type="A.ALN"), MappedRow(sample_type="A.ALN")]}
    bundle = answers.Answers(fill=[_fill("A.ALN", "Protocol", "P-1", rows=[0]),
                                   _fill("A.ALN", "Protocol", "P-2", rows=[1])])
    _validate(bundle, findings, rows)


def test_two_chooses_for_one_cell_are_refused():
    bundle = answers.Answers(choose=[
        answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData", path=BAM_A),
        answers.ChooseAnswer(sample_type="A.ALN", attribute="File_PrimaryData", path=BAM_B)])
    with pytest.raises(answers.AnswerRejected) as exc:
        _validate(bundle, groups=[_group()])
    assert "choose A.ALN.File_PrimaryData: answered twice" in exc.value.reasons


def _place(raw_key="star-foo", attribute="FooRate"):
    return answers.PlaceAnswer(raw_key=raw_key, sample_type="D.SEQ", attribute=attribute)


_SEQ_ROWS = {"D.SEQ": [MappedRow(sample_type="D.SEQ", uid="D.SEQ-EXAMPLE-1",
                                 nfcore_sample="S1")]}
_UNMAPPED = [{"raw_key": "star-foo"}, {"raw_key": "star-bar"}]


def test_two_places_on_one_attribute_or_one_key_are_refused():
    same_attr = answers.Answers(place=[_place("star-foo"), _place("star-bar")])
    with pytest.raises(answers.AnswerRejected) as exc:
        _validate(same_attr, rows=_SEQ_ROWS, unmapped=_UNMAPPED)
    assert "place D.SEQ.FooRate: answered twice" in exc.value.reasons
    same_key = answers.Answers(place=[_place(attribute="FooRate"), _place(attribute="BarRate")])
    with pytest.raises(answers.AnswerRejected) as exc:
        _validate(same_key, rows=_SEQ_ROWS, unmapped=_UNMAPPED)
    assert "place star-foo: placed twice" in exc.value.reasons


def test_a_fill_and_a_place_on_one_cell_are_refused():
    findings = {"D.SEQ": [_finding("D.SEQ", "FooRate", 0)]}
    bundle = answers.Answers(fill=[_fill("D.SEQ", "FooRate", "1")], place=[_place()])
    with pytest.raises(answers.AnswerRejected) as exc:
        _validate(bundle, findings, _SEQ_ROWS, _UNMAPPED)
    assert "place D.SEQ.FooRate: also set by a fill" in exc.value.reasons
