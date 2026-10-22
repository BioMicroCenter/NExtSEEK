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

from NessieAI.ns.reingest import build_records, workbook_diff

logger = logging.getLogger(__name__)

# How many workbooks one confirmation may cover: "all" (the ruling, for now)
# or "per_workbook" (exactly one build per call).
CONFIRMATION_SCOPE = "all"


_PASSING = {"CLEAN", "SOFT_FLAG"}


class UploadRefused(ValueError):
    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class AssayCreateRefused(Exception):
    """A host seam could not create a study assay; the message is user-facing."""


def _manual_fix(entry: dict) -> str:
    return (f"create '{entry['assay_title']}' in study {entry['study_id']} "
            f"({entry.get('study_title') or 'untitled'}) in SEEK, tag it with the house assay "
            f"'{entry['internal_assay_title']}', then build again")


def _created_line(created: dict[tuple, int]) -> str:
    """What a refusal says about assays this call already made (spec 3.5 step 1)."""
    return "already created, empty, and reused next time: " + ", ".join(
        f"assay {i} in study {s}" for (s, _), i in created.items())


def _create_assays(records: list[dict], create_study_assay, rebuild=None) -> dict[tuple, int]:
    """Create every study assay the builds list, once each. Any failure refuses
    the call before anything is staged; assays already created stay, empty, and
    a retry reuses them (the seam re-resolves first). A server that cannot
    rebuild is refused before the first create, so no assay is made and then
    abandoned."""
    wanted = [e for r in records for e in (r.get("assays_to_create") or [])]
    if not wanted:
        return {}
    if not callable(create_study_assay):
        raise UploadRefused(["this server cannot create study assays; "
                             + "; ".join(_manual_fix(e) for e in wanted)])
    if not callable(rebuild):
        raise UploadRefused(["this server cannot rebuild a workbook; build it again in this chat"])
    created: dict[tuple, int] = {}
    for entry in wanted:
        key = (entry["study_id"], entry["internal_assay_title"])
        if key in created:
            continue
        try:
            created[key] = int(create_study_assay(
                study_id=entry["study_id"], internal_assay_title=entry["internal_assay_title"],
                assay_title=entry["assay_title"]))
        except Exception as exc:  # noqa: BLE001 -- one reason, never raw text
            logger.exception("upload-reingest: could not create %s", entry["assay_title"])
            why = str(exc) if isinstance(exc, AssayCreateRefused) else type(exc).__name__
            reasons = [f"could not create '{entry['assay_title']}' in study "
                       f"{entry['study_id']}: {why}. To go on, {_manual_fix(entry)}"]
            if created:
                reasons.append(_created_line(created))
            raise UploadRefused(reasons) from exc
    return created


def _rebuild_after_create(records: list[dict], created: dict[tuple, int], rebuild,
                          user_id) -> list[dict]:
    """Each record that listed assays to create, replaced by its rebuild, but
    only when the rebuild is the reviewed workbook plus Assay rows for them.
    Every refusal here names the assays already created."""
    from NessieAI.ns.artifacts import _safe_artifact_path

    if not created:
        return records

    def refuse(reason: str) -> UploadRefused:
        return UploadRefused([f"{reason}; build it again", _created_line(created)])

    if not callable(rebuild):
        raise refuse("this server cannot rebuild a workbook")
    rebuilt_by_run: dict[tuple, list[dict]] = {}
    out: list[dict] = []
    for record in records:
        if not record.get("assays_to_create"):
            out.append(record)
            continue
        key = record["artifact_key"]
        run_key = (record.get("manifest_id"), record["mode"])
        if run_key not in rebuilt_by_run:
            try:
                rebuilt_by_run[run_key] = rebuild(record)
            except Exception as exc:  # noqa: BLE001 -- the class name only, never raw text
                logger.exception("upload-reingest: the rebuild for %s failed", key)
                raise refuse(f"{key}: the rebuild after creating its assay failed "
                             f"({type(exc).__name__}); see the server log") from exc
        new = next((b for b in rebuilt_by_run[run_key]
                    if b.get("sample_type") == record["sample_type"]), None)
        if new is None:
            raise refuse(f"{key}: the rebuild did not include its {record['sample_type']} workbook")
        # The rebuild hands back the public records (no path); the stored one has it.
        try:
            stored = build_records.load(new["build_id"], user_id)
        except build_records.BuildRecordError:
            raise refuse(f"{key}: the rebuilt workbook has no build record of yours") from None
        safe = _safe_artifact_path(stored.get("path"))
        if safe is None or not safe.is_file():
            raise refuse(f"{key}: the rebuilt workbook is missing or outside the artifact folder")
        if build_records.sha256_of(str(safe)) != stored.get("build_id"):
            raise refuse(f"{key}: the rebuilt workbook changed after it was written")
        if stored.get("built_by_user_id") != user_id:
            raise refuse(f"{key}: the rebuilt workbook was built by another user")
        if stored.get("test_only") is not False:
            raise refuse(f"{key}: the rebuilt workbook is a test-only build")
        if stored.get("project_id") != record.get("project_id"):
            raise refuse(f"{key}: the rebuilt workbook belongs to another project")
        if (stored.get("mode") != record["mode"]
                or stored.get("manifest_id") != record.get("manifest_id")
                or stored.get("rebuilt_from") != record["build_id"]):
            raise refuse(f"{key}: the rebuilt workbook is not a rebuild of the one you reviewed")
        if stored.get("disposition") not in _PASSING:
            raise refuse(f"{key}: the rebuild after creating its assay did not pass QA")
        if stored.get("assays_to_create"):
            raise refuse(f"{key}: its study assay still cannot be found after creating it")
        ids = [created[(e["study_id"], e["internal_assay_title"])]
               for e in record["assays_to_create"]]
        try:
            changes = workbook_diff.assay_only_changes(record["path"], str(safe), ids)
        except Exception as exc:  # noqa: BLE001 -- the class name only, never raw text
            logger.exception("upload-reingest: could not compare the rebuild of %s", key)
            raise refuse(f"{key}: could not compare the rebuilt workbook with the one you "
                         f"reviewed ({type(exc).__name__}); see the server log") from exc
        if changes:
            raise refuse(f"{key}: the rebuilt workbook is not the one you reviewed "
                         f"({'; '.join(changes)})")
        out.append(dict(stored, path=str(safe)))
    return out


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
        # A rebuild carries its reviewed record's shown_in, so the chat-turn check
        # alone would pass it; it is accepted only inside the call that made it,
        # through _rebuild_after_create's comparison (spec 3.5 step 4).
        if record.get("rebuilt_from"):
            reasons.append(f"{key}: a rebuilt workbook is uploaded only by the upload that "
                           "made it; build it again")
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


def _parents_registered(parents: dict | None) -> bool:
    """True when a job has no parents entry, or every parent link was made."""
    return not parents or (parents["status"] == "registered" and not parents.get("unresolved"))


def _reply(jobs: list[dict]) -> str:
    lines = []
    for job in jobs:
        lines.append(_outcome_line(job))
        if (extra := _parents_line(job)):
            lines.append(extra)
    outcomes = [job.get("outcome") for job in jobs]
    if any(o is None for o in outcomes):
        return "Upload jobs:\n" + "\n".join(lines)
    if any(o["status"] == "running" for o in outcomes):
        head = "Upload started; some jobs are still running:"
    elif all(o["status"] == "succeeded" for o in outcomes):
        # Succeeded jobs only wrote the samples; a parent that was not added is not "written".
        if all(_parents_registered(job.get("parents")) for job in jobs):
            head = "Upload finished: everything was written to NExtSEEK."
        else:
            head = ("Upload finished: the samples were written to NExtSEEK, but not every "
                    "parent sample was added to its assay:")
    else:
        head = "Upload finished with problems:"
    tail = ""
    if any("pending" in str(o.get("graph") or "") for o in outcomes if o):
        tail = "\nThe new samples are in NExtSEEK now; graph search picks them up on its next sync."
    return head + "\n" + "\n".join(lines) + tail


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _link_label(pair: dict, titles: dict) -> str:
    """"D.SEQ-2 → Gene Expression Analysis": the assay by its title, by id when unknown."""
    title = titles.get(pair["assay_id"]) or f"assay {pair['assay_id']}"
    return f"{pair['parent_uid']} \u2192 {title}"


def _register_parents(entry: dict, record: dict, register_parents) -> dict | None:
    """Add a new-mode job's parents to their assays, once its samples all landed.
    Whenever they are not added, the status says why, so the reply can say so."""
    if record["mode"] != "new" or not record.get("parent_assays"):
        return None
    # After _rebuild_after_create every pair carries an assay id; one without is a
    # surprise, and it is counted and reported, never dropped.
    pairs = [p for p in record["parent_assays"] if p.get("assay_id") is not None]
    base = {"written": 0, "already_present": 0, "failed": [], "pending": len(pairs),
            "parents": len({p["parent_uid"] for p in pairs}),
            "unresolved": len(record["parent_assays"]) - len(pairs)}
    if not pairs:
        return {**base, "status": "unresolved"}
    outcome = entry.get("outcome")
    if outcome is None:
        return {**base, "status": "skipped"}
    if outcome.get("status") == "running":
        return {**base, "status": "waiting"}
    if outcome.get("status") != "succeeded":
        return {**base, "status": "not_added"}
    if not callable(register_parents):
        return {**base, "status": "unavailable"}
    titles = {p["assay_id"]: p.get("internal_assay_title") for p in pairs}
    try:
        body = register_parents(pairs) or {}
    except Exception as exc:  # noqa: BLE001 -- the upload already landed; report, never raise
        logger.exception("upload-reingest: registering parents failed")
        return {**base, "status": "failed", "failed": [_link_label(p, titles) for p in pairs],
                "error": type(exc).__name__}
    counts = body.get("counts") or {}
    failed = [_link_label({"parent_uid": row.get("sample_uid"), "assay_id": row.get("assay_id")},
                          titles)
              for row in body.get("rows") or [] if row.get("status") == "failed"]
    written = int(counts.get("written") or 0)
    already = int(counts.get("already_present") or 0)
    # A failure is never hidden: a failed count, or fewer links than pairs sent, is partial
    # even when the rows do not list it.
    missing = max(len(failed), int(counts.get("failed") or 0), len(pairs) - written - already)
    result = {**base, "status": "partial" if missing else "registered",
              "written": written, "already_present": already, "failed": failed}
    if missing:
        result["failed_count"] = missing
    return result


_AFTERWARDS = ("An administrator can add them afterwards through NExtSEEK's assay "
               "registration; it is safe to repeat.")


def _parents_line(job: dict) -> str | None:
    parents = job.get("parents")
    if not parents:
        return None
    n = parents["parents"]
    samples = _plural(n, "parent sample")
    status = parents["status"]
    lines = []
    if status == "skipped":
        lines.append(f"  {samples} {'was' if n == 1 else 'were'} not added to their assays: "
                     f"this call could not wait for the upload to finish. {_AFTERWARDS}")
    elif status == "waiting":
        lines.append(f"  {samples} will not be added to their assays automatically: the upload "
                     f"was still running when the wait ended. {_AFTERWARDS}")
    elif status == "unavailable":
        lines.append(f"  This server could not add the {samples} to their assays. {_AFTERWARDS}")
    elif status == "not_added":
        lines.append(f"  {samples} {'was' if n == 1 else 'were'} not added to their assays "
                     "because not every new sample was created. An administrator can add them once "
                     "the samples exist; it is safe to repeat.")
    elif status == "registered":
        links = parents["pending"]
        lines.append(
            f"  {samples} {'is' if n == 1 else 'are'} in {'its' if n == 1 else 'their'} "
            f"{'assay' if links == 1 else 'assays'} now ({_plural(parents['written'], 'link')} "
            f"added, {parents['already_present']} already there).")
    elif status == "partial":
        done = parents["written"] + parents["already_present"]
        not_made = parents.get("failed_count", len(parents["failed"]))
        listed = (": " + _uid_list(parents["failed"], len(parents["failed"]))
                  if parents["failed"] else "")
        lines.append(f"  {done} of {_plural(parents['pending'], 'link')} "
                     f"{'was' if done == 1 else 'were'} made; {not_made} could not be made"
                     f"{listed}. {_AFTERWARDS}")
    elif status == "failed":
        lines.append(f"  Could not confirm that the {samples} joined their assays (the "
                     f"registration call failed): "
                     f"{_uid_list(parents['failed'], len(parents['failed']))}. {_AFTERWARDS}")
    if parents.get("unresolved"):
        k = parents["unresolved"]
        lines.append(f"  {_plural(k, 'link')} could not be made: "
                     f"{'its' if k == 1 else 'their'} assay was not found.")
    return "\n".join(lines) or None


def run(*, build_ids_raw, user, upload_context, dispatch, stage, chat_turn,
        turn_delivered, job_state=None, create_study_assay=None, rebuild=None,
        register_parents=None) -> dict:
    """``dispatch`` and ``stage`` are the host's batch-upload seams
    (``dispatch_batch_job`` and ``stage_workbook_copy``), and ``chat_turn`` and
    ``turn_delivered`` its chat-turn lookups, all handed in by the REST layer so
    this engine module never imports Django. ``job_state`` (job id -> the job's
    Celery state, totals and errors) lets the call wait for the jobs and report
    how each ended; without it the reply says only that they started.
    ``create_study_assay``, ``rebuild`` and ``register_parents`` carry out spec
    2026-10-20 3.5; without them a build that needs a new study assay is refused
    with the manual fix."""
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
    created = _create_assays(records, create_study_assay, rebuild)
    records = _rebuild_after_create(records, created, rebuild, user_id)

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

    by_build = {r["build_id"]: r for r in records}

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
    for entry in jobs:
        if "job_id" in entry and (parents := _register_parents(
                entry, by_build[entry["build_id"]], register_parents)):
            entry["parents"] = parents
    return {"jobs": jobs, "reply": _reply(jobs)}
