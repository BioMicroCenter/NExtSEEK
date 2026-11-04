"""A new-mode row whose data file is already a sample of its type is left out,
so reingesting an uploaded run never makes a second copy."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from NessieAI.tests.ns.reingest.test_build_upload_answers import CATALOG, _new
from NessieAI.tests.ns.reingest.test_build_upload_manifest import (
    _A_ALN_ROW, _A_GEX_ROW, _save_manifest, _save_manifest_multi,
)

pytestmark = pytest.mark.django_db
LOOKUP = "nextseek_api.services.reingest_lookups.existing_uids_for_primary_files_strict"


def _existing(monkeypatch, present: dict[str, dict[str, str]], changed=None):
    """``present`` and ``changed``: sample type -> {parent UID: existing UID}, for a sample
    holding the row's file and one whose file at that path has changed. Records the asks."""
    asked = []

    def lookup(sample_type, files):
        asked.append((sample_type, files))
        same = present.get(sample_type, {})
        moved = (changed or {}).get(sample_type, {})
        return [{"uids": [same[f["parent"]]] if f["parent"] in same else [],
                 "changed": [moved[f["parent"]]] if f["parent"] in moved else []}
                for f in files]
    monkeypatch.setattr(LOOKUP, lookup)
    return asked


@patch(CATALOG)
def test_a_run_already_uploaded_builds_no_workbook_and_says_so(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    asked = _existing(monkeypatch, {"A.ALN": {"D.SEQ-EXAMPLE-1": "A.ALN-OLD-1"},
                                    "A.GEX": {"D.SEQ-EXAMPLE-1": "A.GEX-OLD-1"}})
    out = _new(tmp_path, manifest_id)
    assert out["builds"] == []
    assert out["saved_files"] == {}
    assert "already in NExtSEEK" in out["reply"]
    assert "A.ALN-OLD-1" in out["reply"] and "A.GEX-OLD-1" in out["reply"]
    aln = next(files for st, files in asked if st == "A.ALN")
    assert aln[0]["parent"] == "D.SEQ-EXAMPLE-1"
    assert aln[0]["link"].endswith("star_salmon/SAMPLE_1.markdup.sorted.bam")


@patch(CATALOG)
def test_only_the_rows_not_yet_uploaded_are_built(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest_multi(tmp_path, monkeypatch, n=3)
    _existing(monkeypatch, {"A.ALN": {"D.SEQ-EXAMPLE-2": "A.ALN-OLD-2"}})
    out = _new(tmp_path, manifest_id)
    aln = next(b for b in out["builds"] if b["sample_type"] == "A.ALN")
    assert aln["row_count"] == 2
    # The run's one gene-count matrix row has no sample yet, so it is still built.
    assert any(b["sample_type"] == "A.GEX" for b in out["builds"])
    assert "1 of 3" in out["reply"] and "A.ALN-OLD-2" in out["reply"]


@patch(CATALOG)
def test_nothing_uploaded_yet_changes_nothing(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _existing(monkeypatch, {})
    out = _new(tmp_path, manifest_id)
    assert {b["sample_type"] for b in out["builds"]} == {"A.ALN", "A.GEX"}
    assert "already in NExtSEEK" not in out["reply"]


@patch(CATALOG)
def test_a_changed_file_at_an_uploaded_path_is_left_out_and_flagged(rows, tmp_path, monkeypatch):
    """A run redone in the same folder: the path is a sample's, the checksum is new. The
    upload skips a new row whose data file name is already a sample's (NAME_CHECK in
    nextseek_api/batch_upload/orchestrator.py), so the row is left out of the workbook,
    and the reply names the older sample instead of promising a new one (PR 135 F2)."""
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _existing(monkeypatch, {}, changed={"A.ALN": {"D.SEQ-EXAMPLE-1": "A.ALN-OLD-1"}})
    out = _new(tmp_path, manifest_id)
    assert {b["sample_type"] for b in out["builds"]} == {"A.GEX"}
    assert "A.ALN-OLD-1" in out["reply"]
    assert "changed" in out["reply"] and "left out" in out["reply"]
    assert "built as new samples" not in out["reply"]
    assert "already in NExtSEEK (" not in out["reply"]


@patch(CATALOG)
def test_only_changed_files_left_says_nothing_new_to_upload(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _existing(monkeypatch, {}, changed={"A.ALN": {"D.SEQ-EXAMPLE-1": "A.ALN-OLD-1"},
                                        "A.GEX": {"D.SEQ-EXAMPLE-1": "A.GEX-OLD-1"}})
    out = _new(tmp_path, manifest_id)
    assert out["builds"] == [] and out["saved_files"] == {}
    assert out["reply"].startswith("Nothing new to upload")


@patch(CATALOG)
def test_an_outage_in_the_lookup_stops_the_build(rows, tmp_path, monkeypatch):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    manifest_id = _save_manifest(tmp_path, monkeypatch)

    def down(sample_type, files):
        raise RuntimeError("samples unreachable")
    monkeypatch.setattr(LOOKUP, down)
    with pytest.raises(RuntimeError):
        _new(tmp_path, manifest_id)
