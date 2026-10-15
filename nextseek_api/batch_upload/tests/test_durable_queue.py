"""The batch-upload queue state lives on one durable directory, and a restart ends the jobs it killed."""
import os
import re
from pathlib import Path

import pytest
from celery import Celery
from celery.app.task import Task
from celery.backends.database import DatabaseBackend

from nextseek_api.batch_upload import celery_app as celery_module
from nextseek_api.batch_upload import job_index, views
from nextseek_api.management.commands.mark_interrupted_batch_jobs import (
    INTERRUPTED_MESSAGE,
    mark_interrupted_batch_jobs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_state_paths_are_on_the_volume(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_UPLOAD_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)
    assert job_index.state_dir() == str(tmp_path)
    assert job_index._user_file(7).startswith(str(tmp_path / "jobs"))
    saved = views._save_uploaded_file(_Upload("a.xlsx"))
    assert saved.startswith(str(tmp_path / "uploads"))
    broker, backend = celery_module.default_urls()
    assert broker == f"sqla+sqlite:///{tmp_path}/broker.sqlite"
    assert backend == f"db+sqlite:///{tmp_path}/results.sqlite"


def test_state_dir_defaults_to_var_celery(monkeypatch):
    monkeypatch.delenv("BATCH_UPLOAD_STATE_DIR", raising=False)
    assert job_index.state_dir() == "/var/celery"


def test_compose_mounts_the_state_volume_on_the_app():
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    assert re.search(r"^\s+- batch_upload_state:/var/celery\s*$", text, re.M)
    assert re.search(r"^  batch_upload_state:\s*$", text, re.M)


def test_interrupted_jobs_are_failed_not_requeued(tmp_path, monkeypatch):
    published = []
    # Every way the command could re-publish: any task's apply_async/delay, and send_task on any app.
    monkeypatch.setattr(Task, "apply_async", lambda *a, **k: published.append(("apply_async", a, k)))
    monkeypatch.setattr(Task, "delay", lambda *a, **k: published.append(("delay", a, k)))
    monkeypatch.setattr(Celery, "send_task", lambda *a, **k: published.append(("send_task", a, k)))
    app = Celery("t")
    backend = DatabaseBackend(app=app, url=f"sqlite:///{tmp_path}/results.sqlite")
    backend.store_result("started", None, "STARTED")
    backend.store_result("progress", {"pct": 40}, "PROGRESS")
    backend.store_result("done", {"ok": 1}, "SUCCESS")
    backend.store_result("waiting", None, "PENDING")

    assert mark_interrupted_batch_jobs(backend) == 2

    for job in ("started", "progress"):
        meta = backend.get_task_meta(job)
        assert meta["status"] == "FAILURE"
        assert INTERRUPTED_MESSAGE in str(meta["result"])
    assert backend.get_task_meta("done")["status"] == "SUCCESS"
    assert backend.get_task_meta("waiting")["status"] == "PENDING"
    assert published == []
    assert "re-upload" in INTERRUPTED_MESSAGE


class _Upload:
    def __init__(self, name):
        self.name = name

    def chunks(self):
        yield b"x"


def test_housekeeping_is_consumed_results_expire_and_the_purge_is_scheduled():
    conf = celery_module.app.conf
    assert conf.task_routes["celery.*"] == {"queue": "batch_upload"}
    assert conf.result_expires == job_index._get_ttl_seconds()
    assert "batch_upload.purge_durable_state" in {e["task"] for e in conf.beat_schedule.values()}
    for name in ("cc_assistant.sweep_cc_summaries", "batch_upload.reap_stale_query_tasks",
                 "batch_upload.clear_expired_sessions", "batch_upload.purge_durable_state"):
        assert conf.task_annotations[name] == {"ignore_result": True}


def test_the_purge_removes_old_state_and_keeps_live_state(tmp_path):
    import base64
    import json
    import sqlite3
    import time
    from datetime import datetime, timedelta

    from celery.backends.database.models import Task
    from nextseek_api.periodic_tasks import purge_durable_state

    ttl = job_index._get_ttl_seconds()
    now = time.time()
    old = datetime.fromtimestamp(now - ttl - 3600)
    fresh = datetime.fromtimestamp(now - 60)

    # results
    backend = DatabaseBackend(app=Celery("t"), url=f"sqlite:///{tmp_path}/results.sqlite", expires=ttl)
    backend.store_result("old-done", {"ok": 1}, "SUCCESS")
    backend.store_result("new-done", {"ok": 1}, "SUCCESS")
    s = backend.ResultSession()
    s.query(Task).filter(Task.task_id == "old-done").update({"date_done": old})
    s.commit()
    s.close()

    # broker: a consumed old message, a consumed fresh one, and a still-queued old one naming queued.xlsx
    db = tmp_path / "broker.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE kombu_message (id INTEGER PRIMARY KEY, visible BOOLEAN, timestamp DATETIME, payload TEXT, version INT, queue_id INT)")
    body = base64.b64encode(json.dumps([["/x/uploads/queued.xlsx"], {}, {}]).encode()).decode()
    rows = [(1, 0, old, "{}"), (2, 0, fresh, "{}"), (3, 1, old, json.dumps({"body": body}))]
    for i, vis, ts, payload in rows:
        con.execute("INSERT INTO kombu_message VALUES (?,?,?,?,1,1)", (i, vis, ts.isoformat(" "), payload))
    con.commit()
    con.close()

    # sheets
    up = tmp_path / "uploads"
    up.mkdir()
    for name, age in (("stale.xlsx", ttl + 3600), ("queued.xlsx", ttl + 3600), ("recent.xlsx", 60)):
        f = up / name
        f.write_text("x")
        os.utime(f, (now - age, now - age))

    out = purge_durable_state(now=now, backend=backend, broker_url=f"sqla+sqlite:///{db}", uploads_dir=str(up))

    assert out["results"] == 1 and out["messages"] == 1 and out["sheets"] == 1
    assert backend.get_task_meta("old-done")["status"] == "PENDING"  # gone: unknown ids read as PENDING
    assert backend.get_task_meta("new-done")["status"] == "SUCCESS"
    con = sqlite3.connect(db)
    assert sorted(r[0] for r in con.execute("SELECT id FROM kombu_message")) == [2, 3]
    con.close()
    assert sorted(p.name for p in up.iterdir()) == ["queued.xlsx", "recent.xlsx"]
