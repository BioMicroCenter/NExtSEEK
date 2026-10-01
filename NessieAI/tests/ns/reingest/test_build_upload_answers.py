# NessieAI/tests/ns/reingest/test_build_upload_answers.py
"""build-upload-xlsx with answers, end to end through run_op."""
from __future__ import annotations

import json
from unittest.mock import patch

import openpyxl
import pytest

from NessieAI.tests.ns.reingest.test_build_upload_manifest import (
    _A_ALN_ROW, _A_GEX_ROW, _D_SEQ_ROW, _dispatch, _meta, _save_manifest)
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import manifest as manifest_mod

pytestmark = pytest.mark.django_db
CATALOG = "nextseek_api.services.context_catalog._sample_type_rows"
EXISTING_VALUES = "nextseek_api.services.reingest_lookups.attribute_values_for_uids_strict"


def _new(tmp_path, manifest_id, answers=None):
    args = {"manifest_id": manifest_id, "mode": "new"}
    if answers is not None:
        args["answers"] = json.dumps(answers)
    return _dispatch("build-upload-xlsx", args, outputs_dir=str(tmp_path))


def _aln_meta(result):
    from nextseek_api.batch_upload.convert import parse_traditional_file
    return _meta(parse_traditional_file(result["saved_files"]["reingest_A_ALN"]).rows[0])


@patch(CATALOG)
def test_a_flagged_scientist_is_filled_and_marked_curator(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    first = _new(tmp_path, manifest_id)
    assert any("Scientist" in s for s in first["qa"]["A.ALN"]["soft"])

    result = _new(tmp_path, manifest_id, {"fill": [
        {"sample_type": "A.ALN", "attribute": "Scientist", "value": "A. Curator"}]})

    assert _aln_meta(result)["Scientist"] == "A. Curator"
    assert not any("Scientist" in s for s in result["qa"]["A.ALN"]["soft"])
    prov = openpyxl.load_workbook(result["saved_files"]["reingest_A_ALN"])["Provenance"]
    header = [c.value for c in prov[1]]
    by_attr = {r[1]: r for r in prov.iter_rows(min_row=2, values_only=True)}
    assert by_attr["Scientist"][header.index("Origin")] == "curator"
    assert by_attr["Scientist"][header.index("Answered by")]


@patch(CATALOG)
def test_a_fill_of_a_measured_backfill_cell_is_refused_end_to_end(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.notes_for_uids",
                        lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(tmp_path, monkeypatch,
                                 metrics={"star-uniquely_mapped_percent": 91.4})
    with pytest.raises(g.OpValidationError, match="run data"):
        _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update",
                                        "answers": json.dumps({"fill": [
                                            {"sample_type": "D.SEQ",
                                             "attribute": "MappedPercent",
                                             "value": "99"}]})},
                  outputs_dir=str(tmp_path))


@patch(CATALOG)
def test_one_bad_answer_renders_nothing(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    before = set(tmp_path.rglob("*.xlsx"))
    with pytest.raises(g.OpValidationError):
        _new(tmp_path, manifest_id, {"fill": [
            {"sample_type": "A.ALN", "attribute": "Scientist", "value": "A. Curator"},
            {"sample_type": "A.ALN", "attribute": "Aligner", "value": "bwa"}]})
    assert set(tmp_path.rglob("*.xlsx")) == before


@patch(CATALOG)
def test_answers_for_the_other_mode_are_deferred_not_refused(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW, _D_SEQ_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch,
                                 metrics={"star-uniquely_mapped_percent": 91.4})
    result = _new(tmp_path, manifest_id, {"place": [
        {"raw_key": "star-foo", "sample_type": "D.SEQ", "attribute": "FooRate"}]})
    assert result["answers_deferred"] == [
        {"kind": "place", "sample_type": "D.SEQ", "attribute": "FooRate"}]


@patch(CATALOG)
def test_choose_resolves_an_ambiguous_pick_and_clears_it_from_the_reply(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    gex_a, gex_b = "star_salmon/all.merged.gene_counts.tsv", "salmon/all.merged.gene_counts.tsv"
    manifest_id = _save_manifest(tmp_path, monkeypatch, outputs=[
        manifest_mod.OutputRecord(path="star_salmon/SAMPLE_1.markdup.sorted.bam",
                                  bytes=1, sample="SAMPLE_1"),
        manifest_mod.OutputRecord(path=gex_a, bytes=1, sample=None),
        manifest_mod.OutputRecord(path=gex_b, bytes=1, sample=None)])
    result = _new(tmp_path, manifest_id, {"choose": [
        {"sample_type": "A.GEX", "attribute": "File_PrimaryData", "path": gex_b}]})
    assert "PRIMARY-FILE PICK" not in result["reply"]


@patch(CATALOG)
def test_a_placed_key_lands_from_the_manifest_and_is_proposed_for_review(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.notes_for_uids",
                        lambda uids: {u: "" for u in uids})
    monkeypatch.setattr("NessieAI.ns.reingest.proposals.attribute_exists",
                        lambda st, a: a in ("MappedPercent", "Genome", "Notes", "FooRate"))
    monkeypatch.setattr(EXISTING_VALUES, lambda uids, attribute: {})
    manifest_id = _save_manifest(tmp_path, monkeypatch, metrics={
        "star-uniquely_mapped_percent": 91.4, "star-foo_rate": 4.2})
    result = _dispatch("build-upload-xlsx", {
        "manifest_id": manifest_id, "mode": "update",
        "answers": json.dumps({"place": [{"raw_key": "star-foo_rate",
                                          "sample_type": "D.SEQ",
                                          "attribute": "FooRate"}]})},
        outputs_dir=str(tmp_path))
    from nextseek_api.batch_upload.convert import parse_traditional_file
    meta = _meta(parse_traditional_file(result["saved_files"]["reingest_D_SEQ_update"]).rows[0])
    assert meta["FooRate"] == 4.2
    proposal = next(p for p in result["proposals"] if p["raw_key"] == "star-foo_rate")
    assert proposal["proposed_attribute"] == "FooRate"
    assert proposal["proposed_target"] == "D.SEQ"
    assert proposal.get("status", "pending") == "pending"


def _place_foo_rate(tmp_path, monkeypatch, existing_values):
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.notes_for_uids",
                        lambda uids: {u: "" for u in uids})
    monkeypatch.setattr("NessieAI.ns.reingest.proposals.attribute_exists",
                        lambda st, a: a in ("MappedPercent", "Genome", "Notes", "FooRate"))
    monkeypatch.setattr(EXISTING_VALUES, existing_values)
    manifest_id = _save_manifest(tmp_path, monkeypatch, metrics={
        "star-uniquely_mapped_percent": 91.4, "star-foo_rate": 4.2})
    before = set(tmp_path.rglob("*.xlsx"))
    with pytest.raises(g.OpValidationError) as info:
        _dispatch("build-upload-xlsx", {
            "manifest_id": manifest_id, "mode": "update",
            "answers": json.dumps({"place": [{"raw_key": "star-foo_rate",
                                              "sample_type": "D.SEQ",
                                              "attribute": "FooRate"}]})},
            outputs_dir=str(tmp_path))
    assert set(tmp_path.rglob("*.xlsx")) == before
    return str(info.value)


@patch(CATALOG)
def test_a_place_onto_a_value_already_in_nextseek_is_refused(rows, tmp_path, monkeypatch):
    """An update row carries only the backfill, and the upload deep-merges, so
    the sample's current SEEK value is what a place would overwrite."""
    rows.return_value = [_D_SEQ_ROW]
    asked = []

    def _held(uids, attribute):
        asked.append(attribute)
        return {uid: "curated" for uid in uids}

    message = _place_foo_rate(tmp_path, monkeypatch, _held)
    assert asked == ["FooRate"]
    assert "1 sample(s) already hold a value in NExtSEEK; place never overwrites" in message


@patch(CATALOG)
def test_a_place_is_refused_when_existing_values_cannot_be_read(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]

    def _outage(uids, attribute):
        raise RuntimeError("samples table unreachable at db-host:3306")

    message = _place_foo_rate(tmp_path, monkeypatch, _outage)
    assert "could not read existing values to check for overwrites" in message
    assert "db-host" not in message


@patch(CATALOG)
def test_no_answers_renders_the_same_cells_as_before(rows, tmp_path, monkeypatch):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    plain = _new(tmp_path / "a", manifest_id)
    empty = _new(tmp_path / "b", manifest_id, {})
    assert plain["qa"] == empty["qa"]
    assert _aln_meta(plain) == _aln_meta(empty)


@patch(CATALOG)
def test_each_rendered_workbook_leaves_a_build_record(rows, tmp_path, monkeypatch):
    from NessieAI.ns.reingest import build_records
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.project_ids_for_uids_strict",
                        lambda uids: [14])
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    result = _new(tmp_path, manifest_id)
    keys = {b["artifact_key"] for b in result["builds"]}
    assert keys == set(result["saved_files"])
    for build in result["builds"]:
        assert build["build_id"] == build_records.sha256_of(result["saved_files"][build["artifact_key"]])
        assert build["project_id"] == 14 and build["mode"] == "new"


@patch(CATALOG)
def test_parents_in_two_projects_leave_no_project_and_say_why(rows, tmp_path, monkeypatch):
    from NessieAI.ns.reingest import build_records
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.project_ids_for_uids_strict",
                        lambda uids: [14, 56])
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    result = _new(tmp_path, _save_manifest(tmp_path, monkeypatch))
    assert all(b["project_id"] is None and "2 projects" in b["project_note"]
               for b in result["builds"])


@patch(CATALOG)
def test_a_project_lookup_outage_hides_driver_text(rows, tmp_path, monkeypatch):
    from NessieAI.ns.reingest import build_records
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))

    def _down(uids):
        raise RuntimeError("secret-host:3306 down")
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.project_ids_for_uids_strict",
                        _down)
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    result = _new(tmp_path, _save_manifest(tmp_path, monkeypatch))
    assert result["builds"]
    assert all(b["project_id"] is None
               and b["project_note"] == "project lookup failed (catalog unreachable)"
               for b in result["builds"])
    assert not any("secret-host" in b["project_note"] for b in result["builds"])


@patch(CATALOG)
def test_parents_in_no_project_say_so(rows, tmp_path, monkeypatch):
    from NessieAI.ns.reingest import build_records
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.project_ids_for_uids_strict",
                        lambda uids: [])
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    result = _new(tmp_path, _save_manifest(tmp_path, monkeypatch))
    assert result["builds"]
    assert all(b["project_id"] is None
               and b["project_note"] == "the parent samples belong to no project"
               for b in result["builds"])
