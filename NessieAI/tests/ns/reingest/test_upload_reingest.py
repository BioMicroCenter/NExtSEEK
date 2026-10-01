"""upload-reingest: the first write to NExtSEEK in the reingest work."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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


def _op(args, session=None):
    gate = build_gate(set())
    return g.run_op("upload-reingest", args, config=None,
                    session=session or SimpleNamespace(user=USER, upload_context=CTX),
                    write_gate=gate)


@pytest.mark.parametrize("confirmed", [None, False, "true", 1, "yes"])
def test_anything_but_true_is_blocked_before_any_lookup(env, confirmed):
    with patch.object(upload, "run") as run:
        with pytest.raises(WriteBlockedError):
            _op({"build_ids": "x", "confirmed_write": confirmed})
        run.assert_not_called()


@patch("nextseek_api.batch_upload.views.stage_workbook_copy", side_effect=lambda p: p + ".staged")
@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_new_mode_starts_first_and_files_only(dispatch, _stage, env):
    update = _build(env, "reingest_D_SEQ_update", "update")
    new = _build(env, "reingest_A_ALN", "new")
    dispatch.side_effect = ["job-new", "job-update"]
    result = _op({"build_ids": f"{update['build_id']},{new['build_id']}",
                  "confirmed_write": True})
    calls = dispatch.call_args_list
    assert [c.kwargs["config_overrides"]["update_existing"] for c in calls] == [False, True]
    assert all("rows" not in c.kwargs and c.kwargs["xlsx_paths"] for c in calls)
    assert all(c.kwargs["project_id"] == 14 and c.kwargs["user_ctx"] == CTX for c in calls)
    assert [j["job_id"] for j in result["jobs"]] == ["job-new", "job-update"]


@patch("nextseek_api.batch_upload.views.stage_workbook_copy", side_effect=lambda p: p)
@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_the_second_job_starts_after_the_first_fails(dispatch, _stage, env):
    new = _build(env, "reingest_A_ALN", "new")
    update = _build(env, "reingest_D_SEQ_update", "update")
    dispatch.side_effect = [RuntimeError("broker down"), "job-update"]
    result = _op({"build_ids": f"{new['build_id']},{update['build_id']}",
                  "confirmed_write": True})
    assert "broker down" in result["jobs"][0]["error"]
    assert result["jobs"][1]["job_id"] == "job-update"


@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_an_edited_workbook_is_refused_and_nothing_starts(dispatch, env):
    new = _build(env, "reingest_A_ALN", "new")
    (env / "reingest_A_ALN.xlsx").write_bytes(b"edited after review")
    with pytest.raises(g.OpValidationError, match="changed after"):
        _op({"build_ids": new["build_id"], "confirmed_write": True})
    dispatch.assert_not_called()


@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_another_users_build_is_refused(dispatch, env):
    new = _build(env, "reingest_A_ALN", "new", user_id=8)
    with pytest.raises(g.OpValidationError):
        _op({"build_ids": new["build_id"], "confirmed_write": True})
    dispatch.assert_not_called()


@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_one_bad_build_blocks_the_whole_upload(dispatch, env):
    good = _build(env, "reingest_A_ALN", "new")
    blocked = _build(env, "reingest_A_GEX", "new", disposition="HARD_REJECT")
    with pytest.raises(g.OpValidationError):
        _op({"build_ids": f"{good['build_id']},{blocked['build_id']}",
             "confirmed_write": True})
    dispatch.assert_not_called()


@patch("nextseek_api.batch_upload.views.dispatch_batch_job")
def test_a_build_without_a_single_project_is_refused_with_its_reason(dispatch, env):
    new = _build(env, "reingest_A_ALN", "new", project_id=None)
    with pytest.raises(g.OpValidationError, match="no project"):
        _op({"build_ids": new["build_id"], "confirmed_write": True})


def test_no_resolved_identity_is_refused(env):
    new = _build(env, "reingest_A_ALN", "new")
    with pytest.raises(g.OpValidationError, match="SEEK identity"):
        _op({"build_ids": new["build_id"], "confirmed_write": True},
            session=SimpleNamespace(user=USER, upload_context=None))


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
            session=SimpleNamespace(user=USER, upload_context=_lazy))
    assert resolve == []


@patch("nextseek_api.batch_upload.views.stage_workbook_copy", side_effect=lambda p: p)
@patch("nextseek_api.batch_upload.views.dispatch_batch_job", return_value="job-1")
def test_a_callable_identity_is_resolved_once_confirmed(dispatch, _stage, env):
    new = _build(env, "reingest_A_ALN", "new")
    result = _op({"build_ids": new["build_id"], "confirmed_write": True},
                 session=SimpleNamespace(user=USER, upload_context=lambda: CTX))
    assert dispatch.call_args.kwargs["user_ctx"] == CTX
    assert result["jobs"][0]["job_id"] == "job-1"
