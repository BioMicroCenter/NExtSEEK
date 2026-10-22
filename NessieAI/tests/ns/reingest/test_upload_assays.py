"""upload-reingest creates the study assays its builds list, rebuilds and compares (spec 3.5)."""
from unittest.mock import MagicMock

import pytest

from NessieAI.ns.reingest import build_records, upload
from NessieAI.tests.ns.reingest.test_upload_reingest import (  # noqa: F401 -- fixture
    CTX, NOW, SHOWN, USER, _copy_stage, env,
)

TO_CREATE = [{"study_id": 51, "study_title": "Kamm", "internal_assay_title": "Genome Alignment",
              "assay_title": "Genome Alignment - Data Linked", "sample_type": "A.ALN", "rows": [0]}]
PAIRS = [{"parent_uid": "D.SEQ-1", "study_id": 51, "internal_assay_title": "Genome Alignment",
          "assay_id": None}]


def _record(env, name="reingest_A_ALN", data=b"reviewed", **extra):
    path = env / f"{name}.xlsx"
    path.write_bytes(data)
    return build_records.write(
        path=str(path), artifact_key=name, sample_type="A.ALN", mode="new", manifest_id="abc",
        disposition="SOFT_FLAG", open_warnings=[], row_count=1, project_id=14, project_note="",
        answers_digest="d", user_id=7, test_only=False, chat_turn=SHOWN,
        answers={"fill": [], "choose": [], "place": []}, **extra)


def _run(record, *, create=None, rebuild=None, dispatch=None):
    return upload.run(build_ids_raw=record["build_id"], user=USER, upload_context=CTX,
                      dispatch=dispatch or MagicMock(return_value="job-1"), stage=_copy_stage,
                      chat_turn=lambda: NOW, turn_delivered=lambda task_id: True,
                      create_study_assay=create, rebuild=rebuild)


def _rebuilt(env, reviewed, *, data=b"rebuilt", assays_to_create=()):
    def rebuild(record):
        assert record["build_id"] == reviewed["build_id"]
        new = _record(env, name="reingest_A_ALN_rebuilt", data=data,
                      assays_to_create=list(assays_to_create), rebuilt_from=record["build_id"],
                      parent_assays=[dict(PAIRS[0], assay_id=900)])
        return [new]
    return rebuild


def test_a_build_with_nothing_to_create_is_uploaded_as_reviewed(env, monkeypatch):
    reviewed = _record(env)
    create, rebuild = MagicMock(), MagicMock()
    dispatch = MagicMock(return_value="job-1")
    _run(reviewed, create=create, rebuild=rebuild, dispatch=dispatch)
    create.assert_not_called()
    rebuild.assert_not_called()
    stored = build_records.load(reviewed["build_id"], 7)  # the public record carries no path
    assert dispatch.call_args.kwargs["xlsx_paths"][0].startswith(stored["path"])


def test_the_assay_is_created_then_the_rebuilt_workbook_is_uploaded(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE, parent_assays=PAIRS)
    create = MagicMock(return_value=900)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    dispatch = MagicMock(return_value="job-1")
    _run(reviewed, create=create, rebuild=_rebuilt(env, reviewed), dispatch=dispatch)
    create.assert_called_once_with(study_id=51, internal_assay_title="Genome Alignment",
                                   assay_title="Genome Alignment - Data Linked")
    assert "reingest_A_ALN_rebuilt" in dispatch.call_args.kwargs["xlsx_paths"][0]


def test_a_failed_create_refuses_the_call_before_anything_starts(env):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    dispatch = MagicMock()
    create = MagicMock(side_effect=upload.AssayCreateRefused("SEEK said 403"))
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=create, rebuild=MagicMock(), dispatch=dispatch)
    assert "SEEK said 403" in str(caught.value)
    assert "Genome Alignment - Data Linked" in str(caught.value)
    dispatch.assert_not_called()


def test_a_rebuild_that_changed_more_than_the_assay_sheet_is_refused(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes",
                        lambda a, b, ids: ["Samples: differs from the reviewed workbook"])
    dispatch = MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), rebuild=_rebuilt(env, reviewed),
             dispatch=dispatch)
    assert "build it again" in str(caught.value)
    dispatch.assert_not_called()


def test_a_rebuild_still_missing_its_assay_is_refused(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    with pytest.raises(upload.UploadRefused):
        _run(reviewed, create=MagicMock(return_value=900),
             rebuild=_rebuilt(env, reviewed, assays_to_create=TO_CREATE))


def test_a_server_that_cannot_create_assays_refuses_with_the_manual_fix(env):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=None, rebuild=None)
    assert "create 'Genome Alignment - Data Linked' in study 51" in str(caught.value)
