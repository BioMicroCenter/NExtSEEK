"""upload-reingest: the first write to NExtSEEK in the reingest work."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import json
import os
import shutil

import pytest

import NessieAI.ns.granular as g
from NessieAI.ns.reingest import build_records, upload
from NessieAI.ns.write_gate import WriteBlockedError, build_gate

USER = SimpleNamespace(pk=7, username="curator")
CTX = {"contributor_id": 42, "lababbv": "MIT"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    return tmp_path


def _build(env, name, mode, *, user_id=7, project_id=14, disposition="SOFT_FLAG",
           data=None):
    path = env / f"{name}.xlsx"
    path.write_bytes(data or name.encode())
    return build_records.write(
        path=str(path), artifact_key=name, sample_type="A.ALN" if mode == "new" else "D.SEQ",
        mode=mode, manifest_id="abc", disposition=disposition, open_warnings=[],
        row_count=2, project_id=project_id, project_note="" if project_id else "no project",
        answers_digest="d", user_id=user_id)


def _copy_stage(p):
    shutil.copyfile(p, p + ".staged")
    return p + ".staged"


def _edit_record(file_id, **changes):
    path = os.path.join(build_records._ROOT, "7", f"{file_id}.json")
    with open(path) as fh:
        record = json.load(fh)
    record.update(changes)
    with open(path, "w") as fh:
        json.dump(record, fh)


def _session(*, dispatch=None, stage=_copy_stage, upload_context=CTX, user=USER):
    """The REST layer puts the batch-upload seams on the session; the engine
    never imports them."""
    return SimpleNamespace(user=user, upload_context=upload_context,
                           dispatch_job=dispatch if dispatch is not None else MagicMock(),
                           stage_workbook=stage)


def _op(args, session=None, **seams):
    gate = build_gate(set())
    return g.run_op("upload-reingest", args, config=None,
                    session=session or _session(**seams),
                    write_gate=gate)


@pytest.mark.parametrize("confirmed", [None, False, "true", 1, "yes"])
def test_anything_but_true_is_blocked_before_any_lookup(env, confirmed):
    with patch.object(upload, "run") as run:
        with pytest.raises(WriteBlockedError):
            _op({"build_ids": "x", "confirmed_write": confirmed})
        run.assert_not_called()


def test_new_mode_starts_first_and_files_only(env):
    dispatch = MagicMock()
    update = _build(env, "reingest_D_SEQ_update", "update")
    new = _build(env, "reingest_A_ALN", "new")
    dispatch.side_effect = ["job-new", "job-update"]
    result = _op({"build_ids": f"{update['build_id']},{new['build_id']}",
                  "confirmed_write": True}, dispatch=dispatch)
    calls = dispatch.call_args_list
    assert [c.kwargs["config_overrides"]["update_existing"] for c in calls] == [False, True]
    assert all("rows" not in c.kwargs and c.kwargs["xlsx_paths"] for c in calls)
    assert all(c.kwargs["project_id"] == 14 and c.kwargs["user_ctx"] == CTX for c in calls)
    assert [j["job_id"] for j in result["jobs"]] == ["job-new", "job-update"]


def test_the_second_job_starts_after_the_first_fails(env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    update = _build(env, "reingest_D_SEQ_update", "update")
    dispatch.side_effect = [RuntimeError("broker down"), "job-update"]
    result = _op({"build_ids": f"{new['build_id']},{update['build_id']}",
                  "confirmed_write": True}, dispatch=dispatch)
    error = result["jobs"][0]["error"]
    assert "RuntimeError" in error and "broker down" not in error
    assert result["jobs"][1]["job_id"] == "job-update"


def test_an_edited_workbook_is_refused_and_nothing_starts(env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    (env / "reingest_A_ALN.xlsx").write_bytes(b"edited after review")
    with pytest.raises(g.OpValidationError, match="changed after"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


def test_another_users_build_is_refused(env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new", user_id=8)
    with pytest.raises(g.OpValidationError):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


def test_one_bad_build_blocks_the_whole_upload(env):
    dispatch = MagicMock()
    good = _build(env, "reingest_A_ALN", "new")
    blocked = _build(env, "reingest_A_GEX", "new", disposition="HARD_REJECT")
    with pytest.raises(g.OpValidationError):
        _op({"build_ids": f"{good['build_id']},{blocked['build_id']}",
             "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


def test_a_build_without_a_single_project_is_refused_with_its_reason(env):
    new = _build(env, "reingest_A_ALN", "new", project_id=None)
    with pytest.raises(g.OpValidationError, match="no project"):
        _op({"build_ids": new["build_id"], "confirmed_write": True})


def test_no_resolved_identity_is_refused(env):
    new = _build(env, "reingest_A_ALN", "new")
    with pytest.raises(g.OpValidationError, match="SEEK identity"):
        _op({"build_ids": new["build_id"], "confirmed_write": True},
            session=_session(upload_context=None))


def test_per_workbook_scope_requires_exactly_one_build(env, monkeypatch):
    monkeypatch.setattr(upload, "CONFIRMATION_SCOPE", "per_workbook")
    a = _build(env, "reingest_A_ALN", "new")
    b = _build(env, "reingest_A_GEX", "new")
    with pytest.raises(g.OpValidationError, match="one workbook"):
        _op({"build_ids": f"{a['build_id']},{b['build_id']}", "confirmed_write": True})


def test_identity_is_resolved_only_after_the_gate_passes(env):
    """The REST layer hands the identity lookup over as a callable, so an
    unconfirmed call never reaches SEEK to resolve who is asking."""
    resolve = []

    def _lazy():
        resolve.append(1)
        return CTX

    with pytest.raises(WriteBlockedError):
        _op({"build_ids": "x", "confirmed_write": "true"},
            session=_session(upload_context=_lazy))
    assert resolve == []


def test_a_callable_identity_is_resolved_once_confirmed(env):
    dispatch = MagicMock(return_value="job-1")
    new = _build(env, "reingest_A_ALN", "new")
    result = _op({"build_ids": new["build_id"], "confirmed_write": True},
                 session=_session(dispatch=dispatch, upload_context=lambda: CTX))
    assert dispatch.call_args.kwargs["user_ctx"] == CTX
    assert result["jobs"][0]["job_id"] == "job-1"


def test_a_staged_copy_with_different_bytes_is_refused_and_removed(env):
    new = _build(env, "reingest_A_ALN", "new")
    staged_paths = []

    def _tamper(p):
        staged_paths.append(p + ".staged")
        with open(p + ".staged", "wb") as fh:
            fh.write(b"not the reviewed bytes")
        return p + ".staged"

    dispatch = MagicMock()
    with pytest.raises(g.OpValidationError, match="does not match the reviewed workbook"):
        _op({"build_ids": new["build_id"], "confirmed_write": True},
            dispatch=dispatch, stage=_tamper)
    dispatch.assert_not_called()
    assert staged_paths and not any(os.path.exists(p) for p in staged_paths)


def test_a_staging_failure_removes_earlier_copies_and_starts_nothing(env):
    new = _build(env, "reingest_A_ALN", "new")
    update = _build(env, "reingest_D_SEQ_update", "update")
    calls = []

    def _second_raises(p):
        calls.append(p)
        if len(calls) == 2:
            raise OSError("disk full at /secret/path")
        return _copy_stage(p)

    dispatch = MagicMock()
    with pytest.raises(g.OpValidationError) as info:
        _op({"build_ids": f"{new['build_id']},{update['build_id']}",
             "confirmed_write": True}, dispatch=dispatch, stage=_second_raises)
    assert "reingest_D_SEQ_update: could not stage the workbook" in str(info.value)
    assert "disk full" not in str(info.value)
    dispatch.assert_not_called()
    assert not os.path.exists(calls[0] + ".staged")


def test_a_record_built_by_another_user_is_refused_even_under_this_users_id(env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    _edit_record(new["build_id"], built_by_user_id=8)
    with pytest.raises(g.OpValidationError, match="built by another user"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


def test_a_record_that_does_not_match_its_id_is_refused(env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    _edit_record(new["build_id"], build_id="0" * 64)
    with pytest.raises(g.OpValidationError, match="record does not match its id"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


@pytest.mark.parametrize("disposition", [None, "", "HARD_REJECT", "MAYBE"])
def test_only_clean_or_soft_flag_builds_pass(disposition, env):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    _edit_record(new["build_id"], disposition=disposition)
    with pytest.raises(g.OpValidationError, match="QA did not pass this workbook"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


def test_a_record_path_outside_the_artifact_roots_is_refused(env, tmp_path_factory):
    dispatch = MagicMock()
    new = _build(env, "reingest_A_ALN", "new")
    outside = tmp_path_factory.mktemp("elsewhere") / "reingest_A_ALN.xlsx"
    outside.write_bytes(b"reingest_A_ALN")
    _edit_record(new["build_id"], path=str(outside))
    with pytest.raises(g.OpValidationError, match="outside the artifact root"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, dispatch=dispatch)
    dispatch.assert_not_called()


@pytest.mark.parametrize("missing", ["dispatch_job", "stage_workbook"])
def test_an_unwired_server_refuses_the_upload(env, missing):
    """The engine never imports the batch-upload views: the host hands both
    seams in on the session, and a host that does not is refused."""
    new = _build(env, "reingest_A_ALN", "new")
    session = _session()
    delattr(session, missing)
    with pytest.raises(g.OpValidationError, match="upload is not wired on this server"):
        _op({"build_ids": new["build_id"], "confirmed_write": True}, session=session)
