"""The plain-text labels Nessie writes beside a Luria run, for the weekly cleanup.

A label is `key: value` lines. cleanup.py (staged as bin/nessie_cleanup.py) reads them and
luria/README.md lists them. Writing one is best-effort everywhere: bookkeeping never fails a
run the user already paid cluster time for, or an upload that has already started.
"""
from __future__ import annotations

import logging
import os
import shlex
import time

from .ssh import prepare_key, ssh_run

log = logging.getLogger(__name__)

STATUS_DIR = "nessie_status"
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SAVED_SSH_TIMEOUT_S = 30

STATUS_README = """\
Nessie's labels for this run
============================

These small text files record what happened to this run, so that the weekly cleanup
(<working path>/bin/nessie_cleanup.py) can tell what is still needed.

launched.txt   written when Nessie submitted the run: its work folder, its SLURM job id
               and the SRA/ENA accessions it downloads.
finished.txt   written by the job when it exits: "succeeded" or "failed", and when.
saved.txt      written when the run's results were uploaded to NExtSEEK.

The cleanup never deletes this run folder. It deletes the run's work folder 7 days after
saved.txt, or 30 days after finished.txt if the results were never saved. Downloads are
deleted 30 days after the last run that used them. To see what it would delete and why:

    python3 <working path>/bin/nessie_cleanup.py
"""


def now_iso(now: float | None = None) -> str:
    return time.strftime(TIME_FMT, time.gmtime(time.time() if now is None else now))


def render_label(fields: dict) -> str:
    """`key: value` lines. A newline inside a value becomes a space, so no value adds a key."""
    return "".join(f"{key}: {' '.join(str(value).splitlines())}\n" for key, value in fields.items())


def launched_from(env: dict | None = None) -> str:
    """Which NExtSEEK instance launched the run: its first DJANGO_ALLOWED_HOSTS entry."""
    hosts = ((env if env is not None else os.environ).get("DJANGO_ALLOWED_HOSTS") or "").split()
    return hosts[0] if hosts else "unknown"


def launched_label(*, run_dir: str, work_dir: str, pipeline: str, revision: str,
                   slurm_job_id: str, accessions: list[str], now: float | None = None,
                   env: dict | None = None) -> str:
    return render_label({
        "run_dir": run_dir, "work_dir": work_dir, "pipeline": pipeline, "revision": revision,
        "slurm_job_id": slurm_job_id, "accessions": ",".join(accessions),
        "launched_at": now_iso(now), "launched_from": launched_from(env),
    })


def saved_label(*, pipeline_run_id, now: float | None = None) -> str:
    return render_label({"saved_at": now_iso(now),
                         "pipeline_run_id": "" if pipeline_run_id is None else pipeline_run_id})


def write_saved_label(luria_env: dict, run_dir: str, pipeline_run_id) -> bool:
    """Write <run_dir>/nessie_status/saved.txt on Luria. True when written; never raises.

    `run_dir` must sit under <working_path>/runs/ (checked lexically, as granular's
    _validate_run_dir does) and must already exist there, so this never creates a folder
    anywhere else. When it fails, the cleanup waits 30 days instead of 7."""
    try:
        working = str(luria_env.get("working_path") or "").rstrip("/")
        run_dir = os.path.normpath(str(run_dir or ""))
        if not working or not luria_env.get("key") or not run_dir.startswith(working + "/runs/"):
            log.warning("saved label skipped for %r: Luria not configured, or not under runs/",
                        run_dir)
            return False
        status = f"{run_dir}/{STATUS_DIR}"
        body = saved_label(pipeline_run_id=pipeline_run_id)
        cmd = (f"test -d {shlex.quote(run_dir)} && mkdir -p {shlex.quote(status)} && "
               f"printf %s {shlex.quote(body)} > {shlex.quote(status + '/saved.txt')}")
        key_path = prepare_key(luria_env["key"])
        try:
            ssh_run(luria_env, cmd, key_path=key_path, timeout=_SAVED_SSH_TIMEOUT_S)
        finally:
            try:
                os.remove(key_path)
            except OSError:
                pass
        return True
    except Exception:  # bookkeeping never fails an upload
        log.exception("saved label failed for %s", run_dir)
        return False
