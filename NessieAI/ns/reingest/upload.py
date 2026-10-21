"""upload-reingest: start the batch-upload jobs for reviewed reingest workbooks.

The first write to NExtSEEK in the reingest work. Every check runs before any
job starts, and one failing build blocks all of them. Once started, each
workbook is its own job, new mode first, and a job that fails to start never
stops the next (user ruling, 2026-09-30). Files only, never rows.

The write gate is called by granular._upload_reingest before anything here runs.

A build is uploaded only from a later turn of the chat it was built in, after
that turn's reply reached the user: the code-level twin of the pipeline agent's
``_launch_unconfirmed``. The host says which turn the call runs in
(``chat_turn``) and whether an earlier one was delivered (``turn_delivered``),
from its own records; nothing the agent sends is consulted.
"""
from __future__ import annotations

import logging
import os
import time

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


def _not_shown(record: dict, key: str, now: dict | None, turn_delivered) -> str | None:
    """Why this build cannot be uploaded from this turn, or None when it can."""
    shown = record.get("shown_in")
    if not isinstance(shown, dict) or not shown.get("session_id"):
        return (f"{key}: built outside a chat turn, so it was never shown to the user; "
                "build it again in this chat")
    if now is None:
        return None  # one reason for the whole call, added by verify
    if shown.get("session_id") != now.get("session_id"):
        return f"{key}: shown in another chat; build it again in this one"
    built, current = shown.get("turn_number"), now.get("turn_number")
    if not isinstance(built, int) or not isinstance(current, int) or built >= current:
        return (f"{key}: built in this turn, so the user has not seen it yet. Show them "
                "what will be uploaded, ask them to confirm, and upload only after their reply")
    if not turn_delivered(shown.get("task_id")):
        return (f"{key}: the turn that built it did not finish, so it was never shown "
                "to the user; build it again")
    return None


def verify(build_ids: list[str], *, user_id, chat_turn=None,
           turn_delivered=lambda task_id: False) -> list[dict]:
    """``chat_turn`` is the turn this call runs in ({session_id, turn_number,
    task_id}), or None when the host found no single running chat turn."""
    from NessieAI.ns.artifacts import _safe_artifact_path

    reasons: list[str] = []
    records: list[dict] = []
    if chat_turn is None:
        reasons.append("this call is not from a single running Nessie chat turn; upload "
                       "from the chat where the workbook was shown")
    for build_id in build_ids:
        try:
            record = build_records.load(build_id, user_id)
        except build_records.BuildRecordError as exc:
            reasons.append(str(exc))
            continue
        if not all(record.get(field) for field in ("sample_type", "mode", "artifact_key")):
            reasons.append(f"{build_id[:12]}: record is incomplete")
            continue
        key = record["artifact_key"]
        if record.get("built_by_user_id") != user_id:
            reasons.append(f"{key}: built by another user")
            continue
        if record.get("build_id") != build_id:
            reasons.append(f"{key}: record does not match its id")
            continue
        if record.get("test_only") is not False:
            reasons.append(f"{key}: built from a run Nessie didn't launch (test only); "
                           "it can't be uploaded")
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
        if (unshown := _not_shown(record, key, chat_turn, turn_delivered)):
            reasons.append(unshown)
            continue
        record["path"] = str(safe)
        records.append(record)
    if len({r["project_id"] for r in records}) > 1:
        reasons.append("these workbooks belong to different projects; upload them separately")
    # Two builds of one (sample_type, mode) would insert twice or race their
    # deep-merges; builds from two runs do not belong in one confirmation.
    seen: set[tuple] = set()
    duplicated: list[tuple] = []
    for record in records:
        pair = (record.get("sample_type"), record.get("mode"))
        if pair in seen and pair not in duplicated:
            duplicated.append(pair)
        seen.add(pair)
    reasons += [f"{st} ({mode}): more than one build; send only the latest"
                for st, mode in duplicated]
    if len({r.get("manifest_id") for r in records}) > 1:
        reasons.append("these builds come from different runs; upload one run at a time")
    if reasons:
        raise UploadRefused(reasons)
    return sorted(records, key=lambda r: r["mode"] != "new")


# Waiting for the jobs: they are small and finish in seconds, and the sidecar gives
# one op call 60 s, so the default keeps well inside that. Patched in tests.
_DEFAULT_WAIT_S = 45.0
_POLL_S = 1.0
_TERMINAL = ("SUCCESS", "FAILURE", "REVOKED")
# The reply lists a job's new UIDs up to this many, then says how many more.
_LISTED_UIDS = 20
_clock = time.monotonic
_sleep = time.sleep


def _wait_s() -> float:
    try:
        return max(0.0, float(os.environ.get("NEXTSEEK_UPLOAD_WAIT_S", _DEFAULT_WAIT_S)))
    except ValueError:
        return _DEFAULT_WAIT_S


def _summarise(job_id: str, raw: dict | None) -> dict:
    """One job's outcome from the host's lookup: status succeeded, failed or running,
    with the counts and errors the reply needs."""
    raw = raw or {}
    state = raw.get("state")
    if state not in _TERMINAL:
        return {"status": "running", "job_id": job_id}
    if state != "SUCCESS":
        return {"status": "failed", "error": str(raw.get("error") or state.lower())}
    totals = raw.get("totals") or {}
    errors = list(raw.get("errors") or [])
    failed = int(totals.get("failed") or 0)
    return {"status": "failed" if (failed or errors) else "succeeded",
            "created": int(totals.get("uids_generated") or 0),
            "updated": int(totals.get("updated") or 0),
            "failed_rows": failed, "processed": int(totals.get("processed") or 0),
            "errors": errors[:3], "graph": totals.get("graph"),
            "created_uids": _created_uids(raw.get("rows"))}


def _created_uids(rows) -> list[str] | None:
    """The UIDs of the rows the job inserted, in sheet order, or None when the host gave
    no rows. An inserted row is a success with no reason; an update's reason is "updated"."""
    if not isinstance(rows, list):
        return None
    return [str(row["uid"]) for row in rows
            if isinstance(row, dict) and row.get("uid")
            and row.get("status") == "success" and not row.get("reason")]


def _uid_list(uids: list[str], total: int) -> str:
    shown = uids[:_LISTED_UIDS]
    more = max(total, len(uids)) - len(shown)
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


def _await_jobs(jobs: list[dict], job_state) -> None:
    """Poll each started job until it finishes or the wait runs out, and put its
    outcome on the entry. A lookup that raises counts as not finished yet."""
    pending = [job for job in jobs if job.get("job_id")]
    deadline = _clock() + _wait_s()
    while pending:
        for job in list(pending):
            try:
                outcome = _summarise(job["job_id"], job_state(job["job_id"]))
            except Exception:  # noqa: BLE001 -- the upload already started; keep waiting
                logger.warning("upload-reingest: status of job %s unavailable", job["job_id"],
                               exc_info=True)
                continue
            if outcome["status"] != "running":
                job["outcome"] = outcome
                pending.remove(job)
        if not pending or _clock() >= deadline:
            break
        _sleep(_POLL_S)
    for job in pending:
        job["outcome"] = {"status": "running", "job_id": job["job_id"]}


def _error_text(error) -> str:
    if isinstance(error, dict):
        return str(error.get("error") or error.get("message") or error)
    return str(error)


def _outcome_line(job: dict) -> str:
    what = f"{job['sample_type']} ({'new samples' if job['mode'] == 'new' else 'update existing'})"
    if "job_id" not in job:
        return f"- {what}: did not start: {job['error']}"
    outcome = job.get("outcome")
    if outcome is None:
        return f"- {what}: started, job {job['job_id']}"
    if outcome["status"] == "running":
        return (f"- {what}: still running after the wait, job {job['job_id']}; its result "
                "will be on your batch-upload page")
    if "created" not in outcome:
        return f"- {what}: failed: {outcome['error']}"
    done = (f"{outcome['created']} samples created" if job["mode"] == "new"
            else f"{outcome['updated']} samples updated")
    if job["mode"] == "new" and outcome.get("created_uids"):
        done += ": " + _uid_list(outcome["created_uids"], outcome["created"])
    if outcome["status"] == "succeeded":
        return f"- {what}: done, {done}"
    errors = "; ".join(_error_text(e) for e in outcome["errors"])
    return (f"- {what}: {outcome['failed_rows']} of {outcome['processed']} rows failed"
            f" ({done}){': ' + errors if errors else ''}")


def _reply(jobs: list[dict]) -> str:
    lines = [_outcome_line(job) for job in jobs]
    outcomes = [job.get("outcome") for job in jobs]
    if any(o is None for o in outcomes):
        return "Upload jobs:\n" + "\n".join(lines)
    if any(o["status"] == "running" for o in outcomes):
        head = "Upload started; some jobs are still running:"
    elif all(o["status"] == "succeeded" for o in outcomes):
        head = "Upload finished: everything was written to NExtSEEK."
    else:
        head = "Upload finished with problems:"
    tail = ""
    if any("pending" in str(o.get("graph") or "") for o in outcomes if o):
        tail = "\nThe new samples are in NExtSEEK now; graph search picks them up on its next sync."
    return head + "\n" + "\n".join(lines) + tail


def run(*, build_ids_raw, user, upload_context, dispatch, stage, chat_turn,
        turn_delivered, job_state=None) -> dict:
    """``dispatch`` and ``stage`` are the host's batch-upload seams
    (``dispatch_batch_job`` and ``stage_workbook_copy``), and ``chat_turn`` and
    ``turn_delivered`` its chat-turn lookups, all handed in by the REST layer so
    this engine module never imports Django. ``job_state`` (job id -> the job's
    Celery state, totals and errors) lets the call wait for the jobs and report
    how each ended; without it the reply says only that they started."""
    if not all(callable(seam) for seam in (dispatch, stage, chat_turn, turn_delivered)):
        raise UploadRefused(["upload is not wired on this server"])
    user_id = getattr(user, "pk", None)
    if not user_id:
        raise UploadRefused(["no signed-in user"])
    if not upload_context:
        raise UploadRefused(["could not resolve your SEEK identity"])
    ids = parse_build_ids(build_ids_raw)
    _check_scope(len(ids))
    records = verify(ids, user_id=user_id, chat_turn=chat_turn(),
                     turn_delivered=turn_delivered)

    # Phase A: stage every copy and prove it is the reviewed file, before any job starts.
    staged_paths: list[tuple[dict, str]] = []

    def _discard() -> None:
        for _, staged in staged_paths:
            try:
                os.remove(staged)
            except OSError:
                logger.warning("upload-reingest: could not remove staged copy %s", staged)

    for record in records:
        try:
            staged = stage(record["path"])
            staged_paths.append((record, staged))
            matches = build_records.sha256_of(staged) == record["build_id"]
        except Exception:  # noqa: BLE001 -- the caller sees no raw text
            logger.exception("upload-reingest: could not stage %s", record["artifact_key"])
            _discard()
            raise UploadRefused([f"{record['artifact_key']}: could not stage the workbook"])
        if not matches:
            _discard()
            raise UploadRefused(
                [f"{record['artifact_key']}: the staged copy does not match the reviewed workbook"])

    # Phase B: new mode first; a failed start is recorded and never stops the next.
    jobs: list[dict] = []
    for record, staged in staged_paths:
        entry = {"build_id": record["build_id"], "artifact_key": record["artifact_key"],
                 "sample_type": record["sample_type"], "mode": record["mode"],
                 "manifest_id": record.get("manifest_id")}
        try:
            entry["job_id"] = dispatch(
                user_pk=user_id, user_ctx=upload_context,
                lababbv=upload_context["lababbv"], project_id=record["project_id"],
                # keep_assay_links: a backfill's ASSAY sheet is empty, and the update
                # path otherwise removes every assay link the sheet does not list.
                config_overrides={"update_existing": record["mode"] == "update",
                                  "keep_assay_links": True},
                xlsx_paths=[staged])
        except Exception as exc:  # noqa: BLE001 -- one failed start never stops the next
            logger.exception("upload-reingest: job for %s did not start", record["artifact_key"])
            entry["error"] = f"did not start ({type(exc).__name__}); see the server log"
        jobs.append(entry)
    if callable(job_state):
        _await_jobs(jobs, job_state)
    return {"jobs": jobs, "reply": _reply(jobs)}
