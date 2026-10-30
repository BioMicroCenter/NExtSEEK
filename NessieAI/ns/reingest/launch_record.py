"""Persist what a launch consumed, so reingest never has to guess it back.

Recording is best-effort by design: a bookkeeping failure must never fail a
pipeline submission the user already paid cluster time for. Every failure path
logs and returns None.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)


def read_cohort_sidecar(samplesheet_path: str) -> list[dict]:
    """The cohort.json written beside ``samplesheet_path``, or [] when unusable.

    Returning [] rather than raising is deliberate: an unreadable sidecar must
    degrade the launch record, never the launch.
    """
    try:
        path = os.path.join(os.path.dirname(samplesheet_path), "cohort.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def launch_record_for(run_dir):
    """The PipelineRun recorded for ``run_dir``, or None when Nessie never launched it."""
    from nextseek_api.assistant.models_db import PipelineRun

    return PipelineRun.objects.filter(run_dir=run_dir).first()
# Set when an upload of a run's workbooks has at least one job done (succeeded, or skipped with no failed row;
# ``upload.job_is_done``). A job that only started, failed or is still running does not set it. Any other status
# (``submitted``, the default) means the run still waits to be reingested.
STATUS_REINGESTED = "reingested"


def waiting_runs(user) -> list:
    """``user``'s launched runs not yet reingested, newest first.

    A user sees only the runs recorded as theirs. Runs with no recorded
    launcher (every launch before launches recorded one) are visible to a
    superuser only: on a shared box they may be anyone's.
    """
    from django.db.models import Q

    from nextseek_api.assistant.models_db import PipelineRun

    if user is None or not getattr(user, "pk", None):
        return []
    mine = Q(launched_by_id=user.pk)
    if getattr(user, "is_superuser", False):
        mine |= Q(launched_by__isnull=True)
    return list(PipelineRun.objects.filter(mine)
                .exclude(status=STATUS_REINGESTED)
                .order_by("-launched_at", "-pk"))


def mark_reingested(launch_record_id) -> None:
    """Take a run off the waiting list. Best-effort: an upload already started
    must never fail because this bookkeeping did."""
    if not launch_record_id:
        return
    try:
        from nextseek_api.assistant.models_db import PipelineRun

        PipelineRun.objects.filter(pk=launch_record_id).update(status=STATUS_REINGESTED)
    except Exception:
        log.exception("mark_reingested: failed for launch record %s", launch_record_id)


def record_launch(*, run_dir, run_name, pipeline, revision, slurm_job_id,
                  user_id, cohort_entries):
    """Upsert the PipelineRun for ``run_dir``. Returns it, or None on any failure.

    An entry whose sample has no known UID is recorded with ``d_seq_uid: None``
    rather than dropped: reingest needs to know the sample was in the run in
    order to report it as unresolved rather than silently retry it.
    """
    if not run_dir:
        log.warning("record_launch: refusing to record a run with no run_dir")
        return None
    try:
        from nextseek_api.assistant.models_db import PipelineRun

        cohort = [
            {
                "d_seq_uid": entry.get("d_seq_uid") or None,
                "nfcore_sample": str(entry.get("nfcore_sample") or ""),
                "fastq_1": str(entry.get("fastq_1") or ""),
                "fastq_2": str(entry.get("fastq_2") or "") or None,
            }
            for entry in (cohort_entries or [])
        ]
        run, _ = PipelineRun.objects.update_or_create(
            run_dir=run_dir,
            defaults={"run_name": run_name, "pipeline": pipeline,
                      "revision": revision, "slurm_job_id": slurm_job_id,
                      "launched_by_id": user_id, "status": "submitted",
                      "cohort": cohort},
        )
        return run
    except Exception:
        log.exception("record_launch: failed for run_dir=%s", run_dir)
        return None
