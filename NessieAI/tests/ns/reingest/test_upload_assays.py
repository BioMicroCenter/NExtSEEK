"""upload-reingest creates the study assays its builds list, rebuilds and compares (spec 3.5)."""
from unittest.mock import MagicMock

import pytest

from NessieAI.ns.reingest import build_records, upload
from NessieAI.tests.ns.reingest.test_upload_reingest import (  # noqa: F401 -- fixture
    CTX, NOW, SHOWN, USER, _copy_stage, _edit_record, env,
)

TO_CREATE = [{"study_id": 51, "study_title": "Kamm", "internal_assay_title": "Genome Alignment",
              "assay_title": "Genome Alignment - Data Linked", "sample_type": "A.ALN", "rows": [0]}]
PAIRS = [{"parent_uid": "D.SEQ-1", "study_id": 51, "internal_assay_title": "Genome Alignment",
          "assay_id": None}]


def _record(env, name="reingest_A_ALN", data=b"reviewed", *, sample_type="A.ALN", project_id=14,
            test_only=False, disposition="SOFT_FLAG", manifest_id="abc", mode="new", **extra):
    path = env / f"{name}.xlsx"
    path.write_bytes(data)
    return build_records.write(
        path=str(path), artifact_key=name, sample_type=sample_type, mode=mode,
        manifest_id=manifest_id, disposition=disposition, open_warnings=[], row_count=1,
        project_id=project_id, project_note="", answers_digest="d", user_id=7,
        test_only=test_only, chat_turn=SHOWN, answers={"fill": [], "choose": [], "place": []},
        **extra)


def _run(record, *, create=None, rebuild=None, dispatch=None):
    return upload.run(build_ids_raw=record["build_id"], user=USER, upload_context=CTX,
                      dispatch=dispatch or MagicMock(return_value="job-1"), stage=_copy_stage,
                      chat_turn=lambda: NOW, turn_delivered=lambda task_id: True,
                      create_study_assay=create, rebuild=rebuild)


def _rebuilt(env, reviewed, *, data=b"rebuilt", assays_to_create=(), after=None, **fields):
    """A rebuild seam. ``fields`` change the record it writes; ``after(new)`` tampers with
    the stored record or file once it is written."""
    def rebuild(record):
        assert record["build_id"] == reviewed["build_id"]
        new = _record(env, name="reingest_A_ALN_rebuilt", data=data,
                      assays_to_create=list(assays_to_create),
                      rebuilt_from=fields.pop("rebuilt_from", record["build_id"]),
                      parent_assays=[dict(PAIRS[0], assay_id=900)], **fields)
        if after:
            after(new)
        return [new]
    return rebuild


CREATED_LINE = "already created, empty, and reused next time: assay 900 in study 51"


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
    assert "is not the one you reviewed" in str(caught.value)
    assert "Samples: differs from the reviewed workbook" in str(caught.value)
    assert "build it again" in str(caught.value)
    assert CREATED_LINE in str(caught.value)
    dispatch.assert_not_called()


def test_a_rebuild_still_missing_its_assay_is_refused(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    dispatch = MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), dispatch=dispatch,
             rebuild=_rebuilt(env, reviewed, assays_to_create=TO_CREATE))
    assert "its study assay still cannot be found after creating it" in str(caught.value)
    assert CREATED_LINE in str(caught.value)
    dispatch.assert_not_called()


def test_a_server_that_cannot_create_assays_refuses_with_the_manual_fix(env):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=None, rebuild=None)
    assert "create 'Genome Alignment - Data Linked' in study 51" in str(caught.value)


def _tamper(**changes):
    return lambda new: _edit_record(new["build_id"], **changes)


def _hash_mismatch(new):
    stored = build_records.load(new["build_id"], 7)
    with open(stored["path"], "ab") as fh:
        fh.write(b"edited after it was written")


def _outside_root(tmp_path_factory):
    def tamper(new):
        stored = build_records.load(new["build_id"], 7)
        elsewhere = tmp_path_factory.mktemp("elsewhere") / "rebuilt.xlsx"
        elsewhere.write_bytes(open(stored["path"], "rb").read())
        _edit_record(new["build_id"], path=str(elsewhere))
    return tamper


def _refusals(env, tmp_path_factory):
    """(id, rebuild kwargs, the message part that names this failure)."""
    return [
        ("another-user", {"after": _tamper(built_by_user_id=8)}, "was built by another user"),
        ("test-only", {"test_only": True}, "is a test-only build"),
        ("other-project", {"project_id": 15}, "belongs to another project"),
        ("outside-root", {"after": _outside_root(tmp_path_factory)},
         "is missing or outside the artifact folder"),
        ("hash-mismatch", {"after": _hash_mismatch}, "changed after it was written"),
        ("did-not-pass-qa", {"disposition": "HARD_REJECT"}, "did not pass QA"),
        ("other-mode", {"after": _tamper(mode="update")}, "is not a rebuild of the one you reviewed"),
        ("other-manifest", {"after": _tamper(manifest_id="other")},
         "is not a rebuild of the one you reviewed"),
        ("other-source", {"after": _tamper(rebuilt_from="f" * 64)},
         "is not a rebuild of the one you reviewed"),
    ]


@pytest.mark.parametrize("case", range(9))
def test_a_rebuild_that_fails_a_recheck_is_refused_in_its_own_words(
        env, monkeypatch, tmp_path_factory, case):
    name, fields, wording = _refusals(env, tmp_path_factory)[case]
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    dispatch = MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), dispatch=dispatch,
             rebuild=_rebuilt(env, reviewed, **fields))
    text = str(caught.value)
    assert wording in text, name
    assert text.count("did not pass QA") == (1 if name == "did-not-pass-qa" else 0), name
    assert "build it again" in text
    assert CREATED_LINE in text
    dispatch.assert_not_called()


def test_a_rebuild_without_the_sample_type_is_refused(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    dispatch = MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), dispatch=dispatch,
             rebuild=_rebuilt(env, reviewed, sample_type="A.GEX"))
    assert "did not include its A.ALN workbook" in str(caught.value)
    assert CREATED_LINE in str(caught.value)
    dispatch.assert_not_called()


def test_a_rebuild_that_raises_is_refused_naming_the_created_assay(env):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    dispatch = MagicMock()
    rebuild = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), rebuild=rebuild, dispatch=dispatch)
    text = str(caught.value)
    assert "RuntimeError" in text
    assert "boom" not in text
    assert "build it again" in text
    assert CREATED_LINE in text
    dispatch.assert_not_called()


def test_a_comparison_that_raises_is_refused_naming_the_created_assay(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes",
                        MagicMock(side_effect=ValueError("secret path /x/y")))
    dispatch = MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=MagicMock(return_value=900), rebuild=_rebuilt(env, reviewed),
             dispatch=dispatch)
    text = str(caught.value)
    assert "ValueError" in text
    assert "secret path" not in text
    assert CREATED_LINE in text
    dispatch.assert_not_called()


def test_a_server_that_cannot_rebuild_creates_nothing(env):
    reviewed = _record(env, assays_to_create=TO_CREATE)
    create, dispatch = MagicMock(return_value=900), MagicMock()
    with pytest.raises(upload.UploadRefused) as caught:
        _run(reviewed, create=create, rebuild=None, dispatch=dispatch)
    assert "this server cannot rebuild a workbook" in str(caught.value)
    create.assert_not_called()
    dispatch.assert_not_called()


def test_the_records_it_returns_carry_the_parents_with_their_assay_ids(env, monkeypatch):
    reviewed = _record(env, assays_to_create=TO_CREATE, parent_assays=PAIRS)
    monkeypatch.setattr(upload.workbook_diff, "assay_only_changes", lambda a, b, ids: [])
    records = [build_records.load(reviewed["build_id"], 7)]
    out = upload._rebuild_after_create(
        records, {(51, "Genome Alignment"): 900}, _rebuilt(env, reviewed), 7)
    assert out[0]["parent_assays"] == [dict(PAIRS[0], assay_id=900)]
    assert out[0]["rebuilt_from"] == reviewed["build_id"]


RESOLVED = [{"parent_uid": "D.SEQ-1", "study_id": 51,
             "internal_assay_title": "Gene Expression Analysis", "assay_id": 315},
            {"parent_uid": "D.SEQ-2", "study_id": 51,
             "internal_assay_title": "Gene Expression Analysis", "assay_id": 315}]


def _run_waiting(record, *, register, state="SUCCESS", failed_rows=0):
    def job_state(job_id):
        return {"state": state, "totals": {"uids_generated": 1, "failed": failed_rows,
                                           "processed": 1}, "errors": [], "rows": []}
    return upload.run(build_ids_raw=record["build_id"], user=USER, upload_context=CTX,
                      dispatch=MagicMock(return_value="job-1"), stage=_copy_stage,
                      chat_turn=lambda: NOW, turn_delivered=lambda task_id: True,
                      job_state=job_state, register_parents=register)


def test_parents_join_the_assay_after_the_new_samples_land(env):
    record = _record(env, parent_assays=RESOLVED)
    register = MagicMock(return_value={"counts": {"written": 1, "already_present": 1,
                                                  "failed": 0}, "rows": []})
    result = _run_waiting(record, register=register)
    register.assert_called_once_with([{"parent_uid": "D.SEQ-1", "study_id": 51,
                                       "internal_assay_title": "Gene Expression Analysis",
                                       "assay_id": 315}, RESOLVED[1]])
    assert result["jobs"][0]["parents"]["status"] == "registered"
    assert "2 parent samples are in their assays" in result["reply"]


@pytest.mark.parametrize("state, failed_rows", [("FAILURE", 0), ("SUCCESS", 1)])
def test_no_parent_joins_when_the_new_samples_did_not_all_land(env, state, failed_rows):
    record = _record(env, parent_assays=RESOLVED)
    register = MagicMock()
    _run_waiting(record, register=register, state=state, failed_rows=failed_rows)
    register.assert_not_called()


def test_a_registration_failure_is_reported_and_keeps_the_upload(env):
    record = _record(env, parent_assays=RESOLVED)
    register = MagicMock(return_value={
        "counts": {"written": 1, "already_present": 0, "failed": 1},
        "rows": [{"sample_uid": "D.SEQ-2", "assay_id": 315, "status": "failed",
                  "error": {"code": "permission_denied", "message": "no"}}]})
    result = _run_waiting(record, register=register)
    job = result["jobs"][0]
    assert job["outcome"]["status"] == "succeeded"
    assert job["parents"]["failed"] == ["D.SEQ-2 → assay 315"]
    assert "assay-registrations" in result["reply"]


def test_a_raising_registration_is_reported_not_raised(env):
    record = _record(env, parent_assays=RESOLVED)
    result = _run_waiting(record, register=MagicMock(side_effect=RuntimeError("down")))
    assert result["jobs"][0]["parents"]["status"] == "failed"


def test_without_waiting_no_parent_is_registered(env):
    record = _record(env, parent_assays=RESOLVED)
    register = MagicMock()
    result = upload.run(build_ids_raw=record["build_id"], user=USER, upload_context=CTX,
                        dispatch=MagicMock(return_value="job-1"), stage=_copy_stage,
                        chat_turn=lambda: NOW, turn_delivered=lambda task_id: True,
                        register_parents=register)
    register.assert_not_called()
    assert "parent samples were not added" in result["reply"]
