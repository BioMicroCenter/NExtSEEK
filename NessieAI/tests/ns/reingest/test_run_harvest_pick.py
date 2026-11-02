"""run-harvest with no --run-dir: the caller's launched runs pick the run.

"My run is done" with no folder must find the run Nessie launched for this
user. One waiting run is harvested; several come back as candidates for the
agent to ask about; none is a refusal that asks for the folder. A run with no
recorded launcher (every launch before launches recorded one) is visible to a
superuser only, and a run whose upload finished (a job done) is no longer waiting.
"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

import NessieAI.ns.granular as g
from NessieAI.ns.reingest import launch_record
from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

ROOT = "/net/cluster/runs"


class _Cfg:
    LURIA_ENV = {"working_path": "/net/cluster", "key": "/dev/null",
                 "user": "user", "host": "cluster.example.edu"}


def _user(name, superuser=False):
    return get_user_model().objects.create(username=name, is_superuser=superuser)


def _run(name, user=None, status="submitted", cohort=2):
    return PipelineRun.objects.create(
        run_dir=f"{ROOT}/{name}", run_name=name, pipeline="nf-core/rnaseq",
        launched_by=user, status=status,
        cohort=[{"d_seq_uid": f"D.SEQ-X-{i}", "nfcore_sample": f"s{i}"} for i in range(cohort)])


def _harvest(args, user, monkeypatch):
    """Run the op with staging stubbed out, recording which run_dir it staged."""
    staged = []

    def _fake_stage(_luria_env, run_dir, *_a, **_k):
        staged.append(run_dir)
        raise RuntimeError("staged")  # stop before any parsing; the pick is what is tested

    monkeypatch.setattr(g, "_stage_run_dir", _fake_stage)
    # A real throwaway file: _run_harvest removes its key copy in a finally (as root, "/dev/null" is deleted).
    fd, key_copy = tempfile.mkstemp(prefix="luria_key_")
    os.close(fd)
    monkeypatch.setattr("chat_nextseek.luria.ssh.prepare_key", lambda key: key_copy)
    session = SimpleNamespace(user=user)
    try:
        result = g.run_op("run-harvest", args, config=_Cfg(), session=session,
                          write_gate=None, neo4j_exec=None, outputs_dir=None)
    except RuntimeError as exc:
        assert str(exc) == "staged"
        result = None
    return result, staged


def test_a_user_sees_only_their_own_waiting_runs():
    me, other = _user("me"), _user("other")
    _run("mine", me)
    _run("theirs", other)
    _run("nobodys")
    _run("mine_done", me, status=launch_record.STATUS_REINGESTED)
    assert [r.run_name for r in launch_record.waiting_runs(me)] == ["mine"]


def test_a_superuser_also_sees_runs_with_no_recorded_launcher():
    admin, other = _user("admin", superuser=True), _user("other")
    _run("nobodys")
    _run("theirs", other)
    assert [r.run_name for r in launch_record.waiting_runs(admin)] == ["nobodys"]


def test_waiting_runs_are_newest_first():
    me = _user("me")
    _run("older", me)
    _run("newer", me)
    assert [r.run_name for r in launch_record.waiting_runs(me)] == ["newer", "older"]


def test_no_user_has_no_waiting_runs():
    _run("nobodys")
    assert launch_record.waiting_runs(None) == []


def test_one_waiting_run_is_harvested_without_a_folder(monkeypatch):
    me = _user("me")
    _run("mine", me)
    result, staged = _harvest({}, me, monkeypatch)
    assert staged == [f"{ROOT}/mine"]


def test_several_waiting_runs_come_back_as_candidates(monkeypatch):
    me = _user("me")
    _run("first", me)
    _run("second", me, cohort=3)
    result, staged = _harvest({}, me, monkeypatch)
    assert staged == []
    assert result["manifest_id"] is None
    names = [c["run_name"] for c in result["candidates"]]
    assert names == ["second", "first"]
    second = result["candidates"][0]
    assert second["run_dir"] == f"{ROOT}/second"
    assert second["pipeline"] == "nf-core/rnaseq"
    assert second["samples"] == 3
    assert second["launched_at"]


def test_no_waiting_run_asks_for_the_folder(monkeypatch):
    me = _user("me")
    _run("mine_done", me, status=launch_record.STATUS_REINGESTED)
    with pytest.raises(g.OpValidationError) as err:
        _harvest({}, me, monkeypatch)
    assert "--run-dir" in str(err.value) or "folder" in str(err.value)


def test_a_named_folder_is_used_as_before(monkeypatch):
    me = _user("me")
    _run("mine", me)
    _run("other_mine", me)
    result, staged = _harvest({"run_dir": f"{ROOT}/other_mine"}, me, monkeypatch)
    assert staged == [f"{ROOT}/other_mine"]


def test_mark_reingested_takes_the_run_off_the_waiting_list():
    me = _user("me")
    run = _run("mine", me)
    launch_record.mark_reingested(run.pk)
    run.refresh_from_db()
    assert run.status == launch_record.STATUS_REINGESTED
    assert launch_record.waiting_runs(me) == []


def test_mark_reingested_never_raises():
    launch_record.mark_reingested(None)
    launch_record.mark_reingested(987654)


def test_a_started_upload_marks_its_run_reingested(monkeypatch):
    from NessieAI.ns.reingest import upload

    me = _user("me")
    run = _run("mine", me)
    monkeypatch.setattr(upload, "run", lambda **_k: {
        "jobs": [{"build_id": "b1", "manifest_id": "m1", "job_id": "j1", "outcome": {"status": "succeeded"}},
                 {"build_id": "b2", "manifest_id": "m2", "error": "did not start"}],
        "reply": "ok"})
    monkeypatch.setattr("NessieAI.ns.reingest.store.load_manifest",
                        lambda mid: SimpleNamespace(launch_record_id=run.pk if mid == "m1" else None))
    session = SimpleNamespace(user=me)
    g.run_op("upload-reingest", {"build_ids": "b1,b2", "confirmed_write": True},
             config=_Cfg(), session=session, write_gate=lambda *a: None,
             neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == launch_record.STATUS_REINGESTED


def test_an_upload_whose_jobs_all_failed_leaves_the_run_waiting(monkeypatch):
    from NessieAI.ns.reingest import upload

    me = _user("me")
    run = _run("mine", me)
    monkeypatch.setattr(upload, "run", lambda **_k: {
        "jobs": [{"build_id": "b1", "manifest_id": "m1", "error": "did not start"}],
        "reply": "ok"})
    monkeypatch.setattr("NessieAI.ns.reingest.store.load_manifest",
                        lambda mid: SimpleNamespace(launch_record_id=run.pk))
    g.run_op("upload-reingest", {"build_ids": "b1", "confirmed_write": True},
             config=_Cfg(), session=SimpleNamespace(user=me), write_gate=lambda *a: None,
             neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == "submitted"


def test_upload_jobs_name_their_manifest(tmp_path, monkeypatch):
    """upload.run carries each record's manifest_id onto its job entry, so the
    caller can mark the run without re-reading build records."""
    from NessieAI.ns.reingest import upload

    records = [{"build_id": "b1", "artifact_key": "reingest_A_ALN", "sample_type": "A.ALN",
                "mode": "new", "project_id": 1, "path": "/x", "manifest_id": "m1"}]
    import NessieAI.ns.reingest.build_records as build_records

    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    orig_verify, orig_sha = upload.verify, build_records.sha256_of
    upload.verify = lambda ids, user_id, **_turn: records
    build_records.sha256_of = lambda path: "b1"
    try:
        out = upload.run(build_ids_raw="b1", user=SimpleNamespace(pk=1),
                         upload_context={"lababbv": "LAB"},
                         dispatch=lambda **_k: "job-1", stage=lambda path: path,
                         chat_turn=lambda: {"session_id": "c", "turn_number": 2, "task_id": "t"},
                         turn_delivered=lambda task_id: True)
    finally:
        upload.verify, build_records.sha256_of = orig_verify, orig_sha
    assert out["jobs"][0]["manifest_id"] == "m1"


def test_the_key_cleanup_never_removes_a_file_it_did_not_copy(tmp_path):
    """The source key (or a device) handed back as the 'copy' must survive the finally."""
    source = tmp_path / "shared_key"
    source.write_text("k")
    g._remove_key_copy(str(source), str(source))
    assert source.exists()
    g._remove_key_copy("/dev/null", str(source))
    assert os.path.exists("/dev/null")
    copy = tmp_path / "luria_key_abc"
    copy.write_text("k")
    g._remove_key_copy(str(copy), str(source))
    assert not copy.exists()


def test_an_all_skipped_upload_still_takes_the_run_off_the_pick(monkeypatch):
    # N2: the second upload of a run is all skipped; the run must still leave the waiting pick.
    from NessieAI.ns.reingest import upload

    me = _user("me")
    run = _run("mine", me)
    job = {"build_id": "b1", "manifest_id": "m1", "job_id": "j1",
           "outcome": {"status": "skipped", "failed_rows": 0}}
    monkeypatch.setattr(upload, "run", lambda **_k: {"jobs": [job], "reply": "ok"})
    monkeypatch.setattr("NessieAI.ns.reingest.store.load_manifest",
                        lambda mid: SimpleNamespace(launch_record_id=run.pk))
    g.run_op("upload-reingest", {"build_ids": "b1", "confirmed_write": True},
             config=_Cfg(), session=SimpleNamespace(user=me), write_gate=lambda *a: None,
             neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == launch_record.STATUS_REINGESTED


@pytest.mark.parametrize("outcome", [{"status": "failed", "error": "worker lost"}, {"status": "running"},
                                     {"status": "skipped", "failed_rows": 2}, None])
def test_a_job_that_started_but_did_not_succeed_leaves_the_run_waiting(monkeypatch, outcome):
    """A run is marked reingested on success only (PR 135 SF-3): a started job that failed, is still
    running, or wrote nothing must not take the run off the pick or start the cleanup clock."""
    from NessieAI.ns.reingest import upload

    me = _user("me")
    run = _run("mine", me)
    job = {"build_id": "b1", "manifest_id": "m1", "job_id": "j1"}
    if outcome is not None:
        job["outcome"] = outcome
    monkeypatch.setattr(upload, "run", lambda **_k: {"jobs": [job], "reply": "ok"})
    monkeypatch.setattr("NessieAI.ns.reingest.store.load_manifest",
                        lambda mid: SimpleNamespace(launch_record_id=run.pk))
    g.run_op("upload-reingest", {"build_ids": "b1", "confirmed_write": True},
             config=_Cfg(), session=SimpleNamespace(user=me), write_gate=lambda *a: None,
             neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == "submitted"


_DONE = {"state": "SUCCESS", "totals": {"processed": 1, "uids_generated": 1}, "errors": []}


def _late_upload(monkeypatch, tmp_path, user, run):
    """A build of ``run`` whose upload answered while its job still ran (PR 135 F1): claimed, and
    remembering the job. Returns the build id, plus the list each saved.txt write is appended to."""
    from NessieAI.ns.reingest import build_records

    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    path = tmp_path / "reingest_A_ALN.xlsx"
    path.write_bytes(b"workbook")
    record = build_records.write(
        path=str(path), artifact_key="reingest_A_ALN", sample_type="A.ALN", mode="new",
        manifest_id="m1", disposition="CLEAN", open_warnings=[], row_count=1, project_id=1,
        project_note="", answers_digest="d", user_id=user.pk, test_only=False)
    assert build_records.claim(record["build_id"], user.pk)
    build_records.mark_waiting(record["build_id"], user.pk, job_id="j1", manifest_id="m1",
                               uploaded_build_id=record["build_id"])
    monkeypatch.setattr("NessieAI.ns.reingest.store.load_manifest",
                        lambda mid: SimpleNamespace(launch_record_id=run.pk, run_dir=run.run_dir))
    saved = []
    # Both the label helper and the SSH write it starts are caught: any Luria write lands here.
    monkeypatch.setattr(g, "_write_saved_label", lambda config, manifest: saved.append(manifest.run_dir))
    monkeypatch.setattr(g, "_write_saved_label_now", lambda *a: saved.append(a))
    return record["build_id"], saved


def _harvest_with(session, monkeypatch):
    staged = []

    def _fake_stage(_luria_env, run_dir, *_a, **_k):
        staged.append(run_dir)
        raise RuntimeError("staged")

    monkeypatch.setattr(g, "_stage_run_dir", _fake_stage)
    fd, key_copy = tempfile.mkstemp(prefix="luria_key_")
    os.close(fd)
    monkeypatch.setattr("chat_nextseek.luria.ssh.prepare_key", lambda key: key_copy)
    try:
        g.run_op("run-harvest", {}, config=_Cfg(), session=session,
                 write_gate=None, neo4j_exec=None, outputs_dir=None)
    except RuntimeError as exc:
        assert str(exc) == "staged"
    return staged


@pytest.mark.parametrize("state, said", [
    (_DONE, "its upload has finished"),
    ({"state": "PROGRESS"}, "its upload is still being confirmed"),
    (None, "its upload is still being confirmed"),  # no job lookup on the session
])
def test_run_harvest_writes_nothing_for_a_late_upload_and_does_not_offer_its_run(
        monkeypatch, tmp_path, state, said):
    """run-harvest is read-only (gate_class read; CONTRACT.md). A run whose upload job outlived its call
    is not offered again, and the refusal says why, but run-harvest settles nothing: no build stamp or
    release, no PipelineRun status, no saved.txt on Luria. The next upload-reingest settles it."""
    from NessieAI.ns.reingest import build_records

    me = _user("me")
    run = _run("mine", me)
    build_id, saved = _late_upload(monkeypatch, tmp_path, me, run)
    before = build_records.load(build_id, me.pk)
    session = SimpleNamespace(user=me) if state is None else SimpleNamespace(
        user=me, job_state=lambda job_id: state)
    with pytest.raises(g.OpValidationError) as err:
        _harvest_with(session, monkeypatch)
    assert "no run you launched is waiting" in str(err.value)
    assert said in str(err.value) and "mine" in str(err.value)
    run.refresh_from_db()
    assert run.status == "submitted"
    assert saved == []
    assert build_records.load(build_id, me.pk) == before
    assert not build_records.claim(build_id, me.pk)  # still claimed by the late job


def test_a_late_upload_holds_only_its_own_run(monkeypatch, tmp_path):
    me = _user("me")
    held = _run("uploaded", me)
    _run("other", me)
    _late_upload(monkeypatch, tmp_path, me, held)
    staged = _harvest_with(SimpleNamespace(user=me, job_state=lambda job_id: {"state": "PROGRESS"}),
                           monkeypatch)
    assert staged == [f"{ROOT}/other"]


def test_a_late_upload_that_failed_offers_its_run_again_without_releasing_it(monkeypatch, tmp_path):
    from NessieAI.ns.reingest import build_records

    me = _user("me")
    run = _run("mine", me)
    build_id, saved = _late_upload(monkeypatch, tmp_path, me, run)
    staged = _harvest_with(SimpleNamespace(
        user=me, job_state=lambda job_id: {"state": "FAILURE", "error": "lost"}), monkeypatch)
    assert staged == [run.run_dir]
    assert build_records.load(build_id, me.pk).get("waiting_job")  # the upload path gives it back
    assert saved == []


def test_the_next_upload_settles_a_late_upload(monkeypatch, tmp_path):
    """PR 135 F1, on the upload path: the late job is done by the next upload-reingest, which stamps the
    build uploaded, marks the run reingested and writes saved.txt; run-harvest then has nothing waiting."""
    from NessieAI.ns.reingest import build_records, upload

    me = _user("me")
    run = _run("mine", me)
    build_id, saved = _late_upload(monkeypatch, tmp_path, me, run)
    monkeypatch.setattr(upload, "run", lambda **_k: {"jobs": [], "reply": "ok"})
    session = SimpleNamespace(user=me, job_state=lambda job_id: _DONE)
    g.run_op("upload-reingest", {"build_ids": "b1", "confirmed_write": True},
             config=_Cfg(), session=session, write_gate=lambda *a: None,
             neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == launch_record.STATUS_REINGESTED
    assert saved == [run.run_dir]
    record = build_records.load(build_id, me.pk)
    assert record.get("uploaded_at") and not record.get("waiting_job")
    with pytest.raises(g.OpValidationError) as err:
        _harvest_with(session, monkeypatch)
    assert "pass --run-dir" in str(err.value)


def test_an_unconfirmed_upload_settles_nothing(monkeypatch, tmp_path):
    """The settle runs after upload-reingest's write gate: a refused call leaves the late job waiting."""
    from NessieAI.ns.reingest import build_records

    me = _user("me")
    run = _run("mine", me)
    build_id, saved = _late_upload(monkeypatch, tmp_path, me, run)

    def refuse(*_a):
        raise RuntimeError("write blocked")

    with pytest.raises(RuntimeError, match="write blocked"):
        g.run_op("upload-reingest", {"build_ids": "b1"}, config=_Cfg(),
                 session=SimpleNamespace(user=me, job_state=lambda job_id: _DONE),
                 write_gate=refuse, neo4j_exec=None, outputs_dir=None)
    run.refresh_from_db()
    assert run.status == "submitted" and saved == []
    assert build_records.load(build_id, me.pk).get("waiting_job")


def test_the_run_harvest_session_carries_the_hosts_job_lookup():
    from nextseek_api.batch_upload import views as batch_views
    from nextseek_api.services.assistant import AssistantViewSet

    request = SimpleNamespace(user=SimpleNamespace(pk=7))
    session = AssistantViewSet()._granular_op_session(request, None, "run-harvest")
    assert session.user is request.user
    assert session.job_state is batch_views.job_outcome
