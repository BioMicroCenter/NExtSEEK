"""upload-reingest: start the batch-upload jobs for reviewed reingest workbooks.

The first write to NExtSEEK in the reingest work. Every check runs before any
job starts, and one failing build blocks all of them. Once started, each
workbook is its own job, new mode first, and a job that fails to start never
stops the next (user ruling, 2026-09-30). Files only, never rows.

The write gate is called by granular._upload_reingest before anything here runs.
"""
from __future__ import annotations

import logging
import os

from NessieAI.ns.reingest import build_records

logger = logging.getLogger(__name__)

# How many workbooks one confirmation may cover: "all" (the ruling, for now)
# or "per_workbook" (exactly one build per call).
CONFIRMATION_SCOPE = "all"


_PASSING = {"CLEAN", "SOFT_FLAG"}


class UploadRefused(ValueError):
    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def parse_build_ids(raw) -> list[str]:
    ids: list[str] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part and part not in ids:
            ids.append(part)
    if not ids:
        raise UploadRefused(["no build ids given"])
    return ids


def _check_scope(count: int) -> None:
    if CONFIRMATION_SCOPE == "all":
        return
    if CONFIRMATION_SCOPE == "per_workbook":
        if count != 1:
            raise UploadRefused([f"one confirmation covers one workbook; got {count}"])
        return
    raise ValueError(f"unknown CONFIRMATION_SCOPE {CONFIRMATION_SCOPE!r}")


def verify(build_ids: list[str], *, user_id) -> list[dict]:
    from NessieAI.ns.artifacts import _safe_artifact_path

    reasons: list[str] = []
    records: list[dict] = []
    for build_id in build_ids:
        try:
            record = build_records.load(build_id, user_id)
        except build_records.BuildRecordError as exc:
            reasons.append(str(exc))
            continue
        key = record.get("artifact_key", build_id[:12])
        if record.get("built_by_user_id") != user_id:
            reasons.append(f"{key}: built by another user")
            continue
        if record.get("build_id") != build_id:
            reasons.append(f"{key}: record does not match its id")
            continue
        safe = _safe_artifact_path(record.get("path"))
        if safe is None or not safe.is_file():
            reasons.append(f"{key}: workbook missing or outside the artifact root")
            continue
        if build_records.sha256_of(str(safe)) != build_id:
            reasons.append(f"{key}: the workbook changed after it was reviewed")
            continue
        if record.get("disposition") not in _PASSING:
            reasons.append(f"{key}: QA did not pass this workbook ({record.get('disposition')})")
            continue
        if record.get("project_id") is None:
            reasons.append(f"{key}: {record.get('project_note') or 'no project'}")
            continue
        record["path"] = str(safe)
        records.append(record)
    if len({r["project_id"] for r in records}) > 1:
        reasons.append("these workbooks belong to different projects; upload them separately")
    if reasons:
        raise UploadRefused(reasons)
    return sorted(records, key=lambda r: r["mode"] != "new")


def _reply(jobs: list[dict]) -> str:
    lines = []
    for job in jobs:
        what = f"{job['sample_type']} ({'new samples' if job['mode'] == 'new' else 'update existing'})"
        if "job_id" in job:
            lines.append(f"- {what}: started, job {job['job_id']}")
        else:
            lines.append(f"- {what}: did not start: {job['error']}")
    return "Upload jobs:\n" + "\n".join(lines)


def run(*, build_ids_raw, user, upload_context, dispatch, stage) -> dict:
    """``dispatch`` and ``stage`` are the host's batch-upload seams
    (``dispatch_batch_job`` and ``stage_workbook_copy``), handed in by the REST
    layer so this engine module never imports Django."""
    if not callable(dispatch) or not callable(stage):
        raise UploadRefused(["upload is not wired on this server"])
    user_id = getattr(user, "pk", None)
    if not user_id:
        raise UploadRefused(["no signed-in user"])
    if not upload_context:
        raise UploadRefused(["could not resolve your SEEK identity"])
    ids = parse_build_ids(build_ids_raw)
    _check_scope(len(ids))
    records = verify(ids, user_id=user_id)

    # Phase A: stage every copy and prove it is the reviewed file, before any job starts.
    staged_paths: list[tuple[dict, str]] = []

    def _discard() -> None:
        for _, staged in staged_paths:
            try:
                os.remove(staged)
            except FileNotFoundError:
                pass

    for record in records:
        try:
            staged = stage(record["path"])
        except Exception:  # noqa: BLE001
            logger.exception("upload-reingest: could not stage %s", record["artifact_key"])
            _discard()
            raise UploadRefused([f"{record['artifact_key']}: could not stage the workbook"])
        staged_paths.append((record, staged))
        if build_records.sha256_of(staged) != record["build_id"]:
            _discard()
            raise UploadRefused(
                [f"{record['artifact_key']}: the staged copy does not match the reviewed workbook"])

    # Phase B: new mode first; a failed start is recorded and never stops the next.
    jobs: list[dict] = []
    for record, staged in staged_paths:
        entry = {"build_id": record["build_id"], "artifact_key": record["artifact_key"],
                 "sample_type": record["sample_type"], "mode": record["mode"]}
        try:
            entry["job_id"] = dispatch(
                user_pk=user_id, user_ctx=upload_context,
                lababbv=upload_context["lababbv"], project_id=record["project_id"],
                config_overrides={"update_existing": record["mode"] == "update"},
                xlsx_paths=[staged])
        except Exception as exc:  # noqa: BLE001 -- one failed start never stops the next
            logger.exception("upload-reingest: job for %s did not start", record["artifact_key"])
            entry["error"] = f"did not start ({type(exc).__name__}); see the server log"
        jobs.append(entry)
    return {"jobs": jobs, "reply": _reply(jobs)}
