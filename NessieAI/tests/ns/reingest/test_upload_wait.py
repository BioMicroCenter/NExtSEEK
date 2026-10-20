"""upload-reingest waits for its batch-upload jobs and reports how each ended.

A reply of "started, job <uuid>" left the user to find out from the batch-upload
page whether anything was written. The jobs a reingest starts are small and finish
in seconds, so the op now waits (up to NEXTSEEK_UPLOAD_WAIT_S, 45 s by default,
inside the sidecar's 60 s call) and says what was created, updated or failed. A job
still running at the deadline is named as such, never as done.
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from NessieAI.ns.reingest import build_records, upload

USER = SimpleNamespace(pk=7, username="curator")
CTX = {"contributor_id": 42, "lababbv": "MIT"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path))
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    clock = {"now": 0.0}
    monkeypatch.setattr(upload, "_clock", lambda: clock["now"])
    monkeypatch.setattr(upload, "_sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    return SimpleNamespace(tmp=tmp_path, clock=clock)


def _build(env, name, mode, sample_type):
    path = env.tmp / f"{name}.xlsx"
    path.write_bytes(name.encode())
    return build_records.write(
        path=str(path), artifact_key=name, sample_type=sample_type, mode=mode,
        manifest_id="abc", disposition="CLEAN", open_warnings=[], row_count=2,
        project_id=14, project_note="", answers_digest="d", user_id=7, test_only=False,
        chat_turn={"session_id": "chat-1", "turn_number": 1, "task_id": "built"})


def _stage(p):
    shutil.copyfile(p, p + ".staged")
    return p + ".staged"


def _done(created=0, updated=0, failed=0, errors=()):
    return {"state": "SUCCESS", "totals": {"processed": created + updated + failed,
                                           "uids_generated": created, "updated": updated,
                                           "failed": failed, "graph": "pending (2)"},
            "errors": list(errors)}


def _upload(env, job_state, builds):
    ids = ",".join(b["build_id"] for b in builds)
    jobs = iter(f"job-{i}" for i in range(10))
    return upload.run(build_ids_raw=ids, user=USER, upload_context=CTX,
                      dispatch=lambda **kw: next(jobs), stage=_stage, job_state=job_state,
                      chat_turn=lambda: {"session_id": "chat-1", "turn_number": 2, "task_id": "now"},
                      turn_delivered=lambda task_id: True)


def test_the_reply_says_what_each_job_wrote(env):
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN"),
              _build(env, "reingest_D_SEQ_update", "update", "D.SEQ")]
    outcomes = {"job-0": _done(created=2), "job-1": _done(updated=2)}
    out = _upload(env, lambda job_id: outcomes[job_id], builds)
    assert "Upload finished" in out["reply"]
    assert "A.ALN (new samples): done, 2 samples created" in out["reply"]
    assert "D.SEQ (update existing): done, 2 samples updated" in out["reply"]
    assert "started, job" not in out["reply"]
    assert [j["outcome"]["status"] for j in out["jobs"]] == ["succeeded", "succeeded"]


def test_it_waits_for_a_job_that_is_still_running(env):
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN")]
    calls = {"n": 0}

    def job_state(job_id):
        calls["n"] += 1
        return _done(created=2) if calls["n"] >= 3 else {"state": "PROGRESS"}

    out = _upload(env, job_state, builds)
    assert calls["n"] == 3
    assert "2 samples created" in out["reply"]


def test_a_job_still_running_at_the_deadline_is_named_as_running(env, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_UPLOAD_WAIT_S", "10")
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN")]
    out = _upload(env, lambda job_id: {"state": "PROGRESS"}, builds)
    assert env.clock["now"] <= 11
    assert "still running" in out["reply"]
    assert "job-0" in out["reply"]
    assert "Upload finished" not in out["reply"]
    assert out["jobs"][0]["outcome"]["status"] == "running"


def test_failed_rows_and_a_failed_job_are_reported_with_their_errors(env):
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN"),
              _build(env, "reingest_A_GEX", "new", "A.GEX")]
    outcomes = {"job-0": _done(created=1, failed=1, errors=[{"row": 2, "error": "bad Parent"}]),
                "job-1": {"state": "FAILURE", "error": "worker lost"}}
    out = _upload(env, lambda job_id: outcomes[job_id], builds)
    assert "1 of 2 rows failed" in out["reply"]
    assert "bad Parent" in out["reply"]
    assert "A.GEX (new samples): failed: worker lost" in out["reply"]
    assert "Upload finished" not in out["reply"] or "problem" in out["reply"].lower()


def test_a_lookup_that_raises_is_treated_as_still_unknown(env, monkeypatch):
    monkeypatch.setenv("NEXTSEEK_UPLOAD_WAIT_S", "3")
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN")]

    def job_state(job_id):
        raise RuntimeError("broker down")

    out = _upload(env, job_state, builds)
    assert out["jobs"][0]["outcome"]["status"] == "running"


def test_without_a_lookup_the_reply_is_unchanged(env):
    builds = [_build(env, "reingest_A_ALN", "new", "A.ALN")]
    out = _upload(env, None, builds)
    assert "started, job job-0" in out["reply"]


def test_the_op_hands_the_hosts_lookup_to_the_upload(env, monkeypatch):
    import NessieAI.ns.granular as g

    captured = {}
    monkeypatch.setattr(upload, "run", lambda **kw: (captured.update(kw), {"jobs": [], "reply": ""})[1])
    lookup = lambda job_id: {"state": "SUCCESS"}
    session = SimpleNamespace(user=USER, upload_context=CTX, dispatch_job=lambda **k: "j",
                              stage_workbook=_stage, job_state=lookup)
    g.run_op("upload-reingest", {"build_ids": "a" * 64, "confirmed_write": True}, config=None,
             session=session, write_gate=lambda *a: None, neo4j_exec=None, outputs_dir=None)
    assert captured["job_state"] is lookup
