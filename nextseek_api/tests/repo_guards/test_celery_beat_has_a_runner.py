"""A beat_schedule nobody runs is a promise nothing keeps: if celery_app.py declares one, the entrypoint
must start `celery ... beat`, keep its schedule on the durable dir, and every entry must expire."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = (ROOT / "nextseek_api" / "batch_upload" / "celery_app.py").read_text()
ENTRYPOINT = (ROOT / "docker" / "scripts" / "entrypoint.sh").read_text().replace("\\\n", " ")  # join continuations


def test_a_declared_schedule_has_a_beat_process():
    assert re.search(r"\"beat_schedule\":\s*\{", APP), "no beat_schedule declared: guard is moot"
    beat = [ln for ln in ENTRYPOINT.splitlines()
            if "celery" in ln and " beat" in ln and not ln.lstrip().startswith("#")]
    assert beat, "celery_app.py declares a beat_schedule but entrypoint.sh never starts celery beat"
    assert "--schedule /var/celery/celerybeat-schedule" in beat[0]


def test_every_schedule_entry_expires_and_the_three_jobs_are_there():
    from nextseek_api.batch_upload.celery_app import app

    sched = app.conf.beat_schedule
    assert {"cc-1c-summary-sweep", "reap-stale-query-tasks", "clear-expired-sessions"} <= set(sched)
    for name, entry in sched.items():
        assert entry["options"]["expires"] == entry["schedule"], name
