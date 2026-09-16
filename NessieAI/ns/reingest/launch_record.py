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


def record_launch(*, run_dir, run_name, pipeline, revision, slurm_job_id,
                  user_id, cohort_entries):
    """Upsert the PipelineRun for ``run_dir``. Returns it, or None on any failure.

    An entry whose sample has no known UID is recorded with ``d_seq_uid: None``
    rather than dropped: reingest needs to know the sample was in the run in
    order to report it as unresolved rather than silently retry it.
    """
    from nextseek_api.assistant.models_db import PipelineRun

    if not run_dir:
        log.warning("record_launch: refusing to record a run with no run_dir")
        return None
    try:
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
