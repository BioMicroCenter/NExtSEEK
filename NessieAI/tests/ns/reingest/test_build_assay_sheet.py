"""The build files each analysis row under its study's assay (spec 3.3)."""
from __future__ import annotations

from unittest.mock import patch

import openpyxl
import pytest

from NessieAI.tests.ns.reingest.test_build_upload_answers import CATALOG, _new
from NessieAI.tests.ns.reingest.test_build_upload_manifest import (
    _A_ALN_ROW, _A_GEX_ROW, _D_SEQ_ROW, _dispatch, _save_manifest,
)
from nextseek_api.services.reingest_lookups import StudyAssayPlan

pytestmark = pytest.mark.django_db
LOOKUPS = "nextseek_api.services.reingest_lookups"


def _fake(monkeypatch, *, study=51, assay=315, missing_house=False):
    def study_assays(parent_uids, title):
        if assay is None:
            return StudyAssayPlan({study: []}, {study: sorted(parent_uids)}, [], {study: "Study"})
        return StudyAssayPlan({study: [assay]}, {study: sorted(parent_uids)}, [], {study: "Study"})
    monkeypatch.setattr(f"{LOOKUPS}.study_assays_for_parents_strict", study_assays)
    monkeypatch.setattr(f"{LOOKUPS}.house_assay_title_strict",
                        lambda name: None if missing_house else name)


def _assay_column(path):
    sheet = openpyxl.load_workbook(path)["Assay"]
    return [row[2] for row in sheet.iter_rows(min_row=2, values_only=True)]


def _build(out, key):
    return next(b for b in out["builds"] if b["artifact_key"] == key)


@patch(CATALOG)
def test_resolved_assays_fill_the_sheet_and_the_record(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch)
    out = _new(tmp_path, manifest_id)
    gex = _build(out, "reingest_A_GEX")
    assert _assay_column(out["saved_files"]["reingest_A_GEX"]) == [315]
    assert gex["assays_to_create"] == []
    assert {p["assay_id"] for p in gex["parent_assays"]} == {315}
    assert gex["answers"] == {"fill": [], "choose": [], "place": []}


@patch(CATALOG)
def test_a_missing_study_assay_is_recorded_to_create(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch, study=25, assay=None)
    out = _new(tmp_path, manifest_id)
    aln = _build(out, "reingest_A_ALN")
    # The reviewed workbook shows the row the upload will file it under.
    assert _assay_column(out["saved_files"]["reingest_A_ALN"]) == [
        "to be created at upload: Genome Alignment - Data Linked (study 25)"]
    assert [(e["study_id"], e["assay_title"]) for e in aln["assays_to_create"]] == \
        [(25, "Genome Alignment - Data Linked")]
    assert any("assay_to_create" in item for item in out["qa"]["A.ALN"]["soft"])
    assert "will create" in out["reply"]


@patch(CATALOG)
def test_a_missing_house_assay_blocks_the_workbook(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch, missing_house=True)
    out = _new(tmp_path, manifest_id)
    assert out["builds"] == []
    assert out["qa"]["A.ALN"]["disposition"] == "HARD_REJECT"


@patch(CATALOG)
def test_the_backfill_rows_are_not_planned(rows, tmp_path, monkeypatch):
    rows.return_value = [_D_SEQ_ROW]
    monkeypatch.setattr(f"{LOOKUPS}.notes_for_uids", lambda uids: {u: "" for u in uids})
    manifest_id = _save_manifest(tmp_path, monkeypatch, metrics={"star-uniquely_mapped_percent": 91.4})
    asked = []
    monkeypatch.setattr(f"{LOOKUPS}.house_assay_title_strict", lambda n: asked.append(n) or n)
    monkeypatch.setattr(f"{LOOKUPS}.study_assays_for_parents_strict",
                        lambda u, t: StudyAssayPlan({51: [315]}, {51: sorted(u)}, [], {51: "S"}))
    out = _dispatch("build-upload-xlsx", {"manifest_id": manifest_id, "mode": "update"},
                    outputs_dir=str(tmp_path))
    assert set(out["saved_files"]) == {"reingest_D_SEQ_update"}
    assert asked == []


@patch(CATALOG)
def test_two_house_assays_for_one_name_block_the_workbook_in_plain_words(rows, tmp_path,
                                                                         monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch)

    def two_titles(name):
        if name == "Genome Alignment":
            raise ValueError("'Genome Alignment' names more than one house assay: [3, 4]")
        return name
    monkeypatch.setattr(f"{LOOKUPS}.house_assay_title_strict", two_titles)
    out = _new(tmp_path, manifest_id)
    assert "reingest_A_ALN" not in out["saved_files"]
    assert out["qa"]["A.ALN"]["disposition"] == "HARD_REJECT"
    assert out["qa"]["A.GEX"]["disposition"] != "HARD_REJECT"
    assert ("NExtSEEK has more than one assay type that matches 'Genome Alignment'; an "
            "administrator needs to resolve that, then rebuild.") in out["reply"]
    assert "[3, 4]" not in out["reply"]
    assert "house_assay_ambiguous" not in out["reply"]


@patch(CATALOG)
def test_an_outage_in_the_house_assay_lookup_still_stops_the_build(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch)

    def down(name):
        raise RuntimeError("house assays unreachable")
    monkeypatch.setattr(f"{LOOKUPS}.house_assay_title_strict", down)
    with pytest.raises(RuntimeError):
        _new(tmp_path, manifest_id)
