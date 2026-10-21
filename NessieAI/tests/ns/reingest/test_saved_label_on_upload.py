"""upload-reingest tells the weekly Luria cleanup that a run's results are saved."""
import types

from NessieAI.ns import granular

LURIA = {"working_path": "/w", "key": "/k"}


def _patch(monkeypatch, write=None):
    from NessieAI.ns.reingest import launch_record, store
    import chat_nextseek.luria.status_labels as sl

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
    granular._mark_runs_reingested([{"manifest_id": "m1", "job_id": "j1"},
                                    {"manifest_id": "m1", "job_id": "j2"}], config)
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
    granular._mark_runs_reingested([{"manifest_id": "m1", "job_id": "j1"}],
                                   types.SimpleNamespace(LURIA_ENV=LURIA))
