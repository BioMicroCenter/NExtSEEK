"""upload-reingest tells the weekly Luria cleanup that a run's results are saved."""
import threading
import types

from NessieAI.ns import granular

LURIA = {"working_path": "/w", "key": "/k"}


def _patch(monkeypatch, write=None):
    from NessieAI.ns.reingest import launch_record, store
    import chat_nextseek.luria.status_labels as sl

    # the real _start_background is a daemon thread; run it inline so the tests can look at the call
    monkeypatch.setattr(granular, "_start_background", lambda fn, *args: fn(*args))
    manifests = {"m1": types.SimpleNamespace(run_dir="/w/runs/a_1", launch_record_id=5)}
    monkeypatch.setattr(store, "load_manifest", lambda mid: manifests[mid])
    monkeypatch.setattr(launch_record, "mark_reingested", lambda pk: None)
    calls = []
    monkeypatch.setattr(sl, "write_saved_label",
                        write or (lambda env, run_dir, pid: calls.append((env, run_dir, pid)) or True))
    return calls


def test_a_started_upload_writes_saved_once_per_run(monkeypatch):
    calls = _patch(monkeypatch)
    config = types.SimpleNamespace(LURIA_ENV=LURIA)
    granular._mark_runs_reingested([{"manifest_id": "m1", "job_id": "j1", "outcome": {"status": "succeeded"}},
                                    {"manifest_id": "m1", "job_id": "j2", "outcome": {"status": "succeeded"}}], config)
    assert calls == [(LURIA, "/w/runs/a_1", 5)]


def test_no_started_job_writes_nothing(monkeypatch):
    calls = _patch(monkeypatch)
    granular._mark_runs_reingested([{"manifest_id": "m1", "job_id": None}],
                                   types.SimpleNamespace(LURIA_ENV=LURIA))
    assert calls == []


def test_a_saved_label_failure_never_fails_the_upload(monkeypatch):
    def boom(env, run_dir, pid):
        raise RuntimeError("ssh down")

    _patch(monkeypatch, write=boom)
    granular._mark_runs_reingested([{"manifest_id": "m1", "job_id": "j1", "outcome": {"status": "succeeded"}}],
                                   types.SimpleNamespace(LURIA_ENV=LURIA))


def test_the_real_background_start_returns_before_a_slow_target_finishes():
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    seen = {}

    def slow(tag):
        seen["thread"] = threading.current_thread()
        seen["tag"] = tag
        started.set()
        release.wait(5)
        done.set()

    granular._start_background(slow, "x")
    try:
        assert started.wait(5) and not done.is_set()      # we are back while it still runs
        assert seen["thread"].daemon and seen["thread"] is not threading.current_thread()
        assert seen["thread"].name == "nessie-saved-label" and seen["tag"] == "x"
    finally:
        release.set()
    assert done.wait(5)

