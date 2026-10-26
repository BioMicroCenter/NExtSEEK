"""The real rebuild after a study assay is created differs from the reviewed build
only by Assay rows for the new assay (spec 3.5 steps 2-4).

The upload tests fake the rebuild seam; this one builds a reviewed workbook with a
missing study assay, lets the lookup find the assay, and runs the real
``granular._rebuild_record`` against it, with and without a chat answer.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import NessieAI.ns.granular as g
from NessieAI.ns.artifacts import _safe_artifact_path
from NessieAI.ns.reingest import assay_plan, build_records, workbook_diff
from NessieAI.tests.ns.reingest.test_build_assay_sheet import _assay_column, _fake
from NessieAI.tests.ns.reingest.test_build_upload_answers import CATALOG
from NessieAI.tests.ns.reingest.test_build_upload_manifest import (
    _A_ALN_ROW, _A_GEX_ROW, _save_manifest,
)

pytestmark = pytest.mark.django_db

USER = SimpleNamespace(pk=7, is_superuser=False, username="ab", get_full_name=lambda: "")
TURN = {"session_id": "s1", "turn_number": 1, "task_id": "t1"}
CREATED = 999
FILL = {"fill": [{"sample_type": "A.ALN", "attribute": "Scientist", "value": "A. Curator"}]}


@pytest.mark.parametrize("answers", [None, FILL], ids=["no-answers", "fill-answer"])
@patch(CATALOG)
def test_the_real_rebuild_differs_only_by_the_new_assay(rows, tmp_path, monkeypatch, answers):
    rows.return_value = [_A_ALN_ROW, _A_GEX_ROW]
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path))
    manifest_id = _save_manifest(tmp_path, monkeypatch)
    _fake(monkeypatch, study=25, assay=None)
    monkeypatch.setattr("nextseek_api.services.reingest_lookups.project_ids_for_uids_strict",
                        lambda uids: [14])
    args = {"manifest_id": manifest_id, "mode": "new"}
    if answers:
        args["answers"] = json.dumps(answers)
    (tmp_path / "turn1").mkdir()
    out = g._build_upload_xlsx_from_manifest(
        args, str(tmp_path / "turn1"), session=SimpleNamespace(user=USER, chat_turn=lambda: TURN))
    aln = next(b for b in out["builds"] if b["sample_type"] == "A.ALN")
    reviewed = build_records.load(aln["build_id"], 7)
    assert reviewed["assays_to_create"]
    assert bool(reviewed["answers"]["fill"]) is bool(answers)
    assert {p["assay_id"] for p in reviewed["parent_assays"]} == {None}

    # The upload created the study assay; the lookup now finds it.
    _fake(monkeypatch, study=25, assay=CREATED)
    rebuilt = g._rebuild_record(reviewed, USER, str(tmp_path / "upload"))
    new = next(b for b in rebuilt if b["sample_type"] == "A.ALN")
    stored = build_records.load(new["build_id"], 7)

    assert stored["rebuilt_from"] == reviewed["build_id"]
    assert stored["shown_in"] == reviewed["shown_in"]
    assert stored["assays_to_create"] == []
    assert {p["assay_id"] for p in stored["parent_assays"]} == {CREATED}
    assert stored["disposition"] in ("CLEAN", "SOFT_FLAG")
    assert stored["test_only"] is False
    assert stored["project_id"] == reviewed["project_id"] == 14
    assert _safe_artifact_path(stored["path"]) is not None
    assert stored["path"] != reviewed["path"]
    planned = assay_plan.planned_assay_label(25, "Genome Alignment - Data Linked")
    assert planned in _assay_column(reviewed["path"])
    assert _assay_column(stored["path"]) == [CREATED]
    assert workbook_diff.assay_only_changes(
        reviewed["path"], stored["path"], {planned: CREATED}) == []


# --- With no run-root of its own, the rebuild writes under an absolute artifact root.

def _rebuild_folder(monkeypatch, outputs_dir=None):
    seen = {}

    def build(args, folder, session=None, rebuilt_from=None):
        seen["folder"] = folder
        return {"builds": []}
    monkeypatch.setattr(g, "_build_upload_xlsx_from_manifest", build)
    monkeypatch.setattr(g.os, "makedirs", lambda *a, **k: None)
    record = {"build_id": "ab" * 32, "manifest_id": "m", "mode": "new", "shown_in": TURN}
    g._rebuild_record(record, USER, outputs_dir)
    return seen["folder"]


def test_the_rebuild_folder_is_absolute_under_the_outputs_dir_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", "relative-outputs")
    monkeypatch.chdir(tmp_path)
    folder = _rebuild_folder(monkeypatch)
    assert folder == str((tmp_path / "relative-outputs").resolve() / "rebuilds" / ("ab" * 8))


def test_without_the_setting_the_rebuild_folder_is_under_the_project_outputs(tmp_path,
                                                                             monkeypatch):
    from django.conf import settings
    from pathlib import Path

    monkeypatch.delenv("NEXTSEEK_OUTPUTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    folder = _rebuild_folder(monkeypatch)
    assert folder == str(Path(settings.BASE_DIR, "outputs").resolve() / "rebuilds" / ("ab" * 8))
    assert _safe_artifact_path(str(Path(folder) / "reingest_A_ALN.xlsx")) is not None


def test_the_default_outputs_dir_is_the_settings_base_dirs_outputs(monkeypatch):
    """artifacts.default_outputs_dir reads no settings; it must still name BASE_DIR/outputs."""
    from django.conf import settings
    from pathlib import Path

    from NessieAI.ns.artifacts import default_outputs_dir

    monkeypatch.delenv("NEXTSEEK_OUTPUTS_DIR", raising=False)
    assert default_outputs_dir() == str(Path(settings.BASE_DIR, "outputs").resolve())
