"""A share's apply, one call at a time, and the worker's two passes (tool spec 16.4, 16.6; T39, T40).

``apply_step`` is one ``POST .../apply/`` call, made with the caller's own proved SEEK session: it checks the share's
state and plan, the studies release, the run lock and the destination, then either resolves or makes the next clone
(at most ONE ``POST /assays`` a call, never a sleep: a lost answer is ``clone_outcome_unknown`` with a retry time), or,
once every group has its destination assay, writes the clones' internal-assay rows and queues the share. Everything
long runs in the worker, which holds no SEEK credential: ``plan_job`` plans a ``planning`` share into its run
directory, and ``run_share_unit`` runs a ``queued`` share's link unit under the tool's run lock. The journal, the
link unit, its recovery and rollback are the tool's own (``journal``, ``links``, ``apply``, ``rollback``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from django.conf import settings
from pydantic import ValidationError

from nextseek_api.graph_sync import hooks
from nextseek_api.studies import apply as apply_mod
from nextseek_api.studies import links, mapping, planner, preflight, report, share_jobs
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import ShareInput, StudyMovePlan
from nextseek_api.studies.seek import ADOPT_POLL_S, ADOPT_WAIT_S, WRITE_TIMEOUT_S, SeekError, SeekUnknownOutcome
from nextseek_api.studies.share import ShareRefused, plan_share
from nextseek_api.studies.snapshot import SnapshotReader

CLONE_OUTCOME_UNKNOWN = "clone_outcome_unknown"
SHARE_WRITE_TIMEOUT_S = WRITE_TIMEOUT_S   # below the edge proxy's read timeout on a box (operator task O6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StepAnswer:
    status_code: int
    state: str
    clones_done: int = 0
    clones_remaining: int = 0
    retry_after_s: Optional[int] = None
    code: Optional[str] = None
    message: Optional[str] = None


def share_root() -> Path:
    return Path(settings.LOG_DIR) / "studies"


def run_dir_of(share) -> Path:
    return share_root() / share.run_dir


def _new_run_dir(now: datetime) -> Path:
    base = share_root() / f"{now.strftime('%Y%m%dT%H%M%SZ')}-share"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    return path


# --- the worker's planning pass ------------------------------------------------------------------------------------

def plan_job(share, owner: str, *, reader, now: Optional[datetime] = None) -> str:
    """Plan a claimed ``planning`` share: its run directory, then ``planned``, ``refused`` or ``plan_failed``."""
    stamp = now or _now()
    try:
        inp = ShareInput.model_validate(share.request)
        run_dir = _new_run_dir(stamp)
        plan = plan_share(inp, reader, run_id=run_dir.name, now=stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
    except ShareRefused as exc:
        share_jobs.finish_plan(share, owner, state="refused", error={"code": exc.code, "detail": exc.detail})
        return "refused"
    except Exception as exc:  # noqa: BLE001 - recorded on the share row; the worker carries on
        share_jobs.finish_plan(share, owner, state="plan_failed",
                               error={"code": "plan_failed", "detail": f"{type(exc).__name__}: {exc}"[:500]})
        return "plan_failed"
    sha = plan.sha256()
    summary = report.share_summary(plan, run_dir_name=run_dir.name, plan_sha256=sha)
    report.write_share_run(run_dir, plan, summary)
    share_jobs.finish_plan(share, owner, state="planned", run_dir=run_dir.name, plan_sha256=sha, summary=summary)
    return "planned"


# --- one apply call --------------------------------------------------------------------------------------------------

def _counts(plan: StudyMovePlan, st) -> tuple[int, int]:
    creates = [c for t in plan.targets for c in t.clones if c.action == "create"]
    done = sum(1 for c in creates if (st.clones.get((plan.targets[0].key, c.source_assay_id)) or {}).get("seek_id"))
    return done, len(creates) - done


def _intent_at(lines: list, key: str, source_assay_id: int) -> Optional[datetime]:
    found = None
    for line in lines:
        if (line.get("step"), line.get("event"), line.get("target_key"), line.get("source_assay_id")) == (
                "clone", "intent", key, source_assay_id):
            found = line["at"]
    return datetime.strptime(found, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) if found else None


def _not_applicable(share) -> StepAnswer:
    code = (share.error or {}).get("code")
    why = f" ({code}): make a new share" if share.state == "apply_failed" and code in share_jobs.TERMINAL_ERRORS else ""
    return StepAnswer(409, share.state, code="share_not_applicable", message=f"the share is {share.state}{why}")


def _seek_answer(exc: SeekError, state: str, done: int, left: int) -> StepAnswer:
    if exc.status in (401, 403):
        return StepAnswer(403, state, done, left, code="seek_refused", message=exc.message)
    if exc.status == 422:
        return StepAnswer(422, state, done, left, code="seek_payload_rejected", message=exc.message)
    return StepAnswer(502, state, done, left, code="seek_error", message=exc.message)


def apply_step(share, session, driver, db, *, plan_sha256: str, reader=None,
               now: Optional[datetime] = None) -> StepAnswer:
    """One apply call (tool spec 16.6, steps 1 and 3 to 7; the view does step 2, the caller's credential)."""
    stamp = now or _now()
    if not share_jobs.applicable(share):
        return _not_applicable(share)
    if plan_sha256 != share.plan_sha256:
        return StepAnswer(409, share.state, code="plan_changed", message="plan_sha256 is not this share's plan")
    run_dir = run_dir_of(share)
    plan = StudyMovePlan.from_file(run_dir / report.PLAN_FILE)
    if plan.sha256() != share.plan_sha256:
        return StepAnswer(409, share.state, code="plan_changed", message="the run directory's plan changed")
    if not plan.units:
        return StepAnswer(409, share.state, code="nothing_to_apply", message="every sample reads no_change")
    refused = preflight.graph_version_refusal(driver, db) or preflight.studies_release_refusal(driver, db)
    if refused:
        return StepAnswer(409, share.state, code="not_ready", message=refused)
    reader = reader or SnapshotReader(session, driver, db)
    with preflight.run_lock() as held:
        if not held:
            return StepAnswer(409, share.state, code="busy", message=f"{preflight.LOCK_NAME} is held")
        lines, _bad = read_journal(run_dir / JOURNAL_FILE)
        st = journal_state(lines)
        if st.undo_parts:
            share_jobs.end_rolled_back(share.run_dir)
            return StepAnswer(409, "rolled_back", code="share_rolled_back",
                              message="this share was rolled back: make a new share")
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        if not st.started:
            journal.append("run", "start", plan_sha256=plan.sha256(), login=session.login, person_id=session.person_id,
                           mode="share")
        inp, target = plan.share, plan.targets[0]
        study = {s.id: s for s in reader.studies()}.get(inp.destination_study_id)
        if study is None or study.investigation_id not in reader.project_investigations(inp.destination_project_id):
            share_jobs.to_apply_failed(share, {"code": "destination_changed"})
            return StepAnswer(409, share.state, code="destination_changed",
                              message="the destination study moved or is gone: make a new share")
        share_jobs.to_applying(share)
        done, left = _counts(plan, st)
        journaled = {v["seek_id"] for v in st.clones.values() if v.get("seek_id")}
        excluded = set(target.existing_assay_ids) | journaled
        clone_ids = {}
        for c in target.clones:
            key = (target.key, c.source_assay_id)
            if c.action == "reuse":
                clone_ids[key] = c.seek_assay_id
                continue
            slot = st.clones.get(key) or {}
            if slot.get("seek_id"):
                clone_ids[key] = slot["seek_id"]
                continue
            if slot.get("intent") is not None:
                found = [i for i in session.find_assay(inp.destination_study_id, c.title) if i not in excluded]
                if len(found) > 1:
                    return StepAnswer(409, share.state, done, left, code="clone_outcome_ambiguous",
                                      message=f"several assays {found} match a create whose answer was lost")
                if found:
                    journal.append("clone", "adopted", target_key=target.key, source_assay_id=c.source_assay_id,
                                   seek_id=found[0])
                    clone_ids[key] = found[0]
                    excluded.add(found[0])
                    done, left = done + 1, left - 1
                    continue
                since = _intent_at(lines, target.key, c.source_assay_id)
                if since is not None and (stamp - since).total_seconds() < ADOPT_WAIT_S:
                    return StepAnswer(202, share.state, done, left, retry_after_s=ADOPT_POLL_S,
                                      code=CLONE_OUTCOME_UNKNOWN, message="checking whether SEEK finished a create")
            else:
                # One clone per group (title, internal assays): another share may have made this group's assay in D
                # since the plan. Adopt it; one whose mapping is not written yet is another share mid-apply.
                fresh = [i for i in session.find_assay(inp.destination_study_id, c.title) if i not in excluded]
                have = mapping.internal_ids(fresh) if fresh else {}
                same = [i for i in fresh if have[i] == set(c.internal_assay_ids)]
                if len(same) > 1:
                    share_jobs.to_apply_failed(share, {"code": "destination_changed"})
                    return StepAnswer(409, share.state, done, left, code="destination_changed",
                                      message=f"study {inp.destination_study_id} now holds several assays {same} of "
                                              f"the group {c.title!r}: decide in SEEK, then make a new share")
                if same:
                    journal.append("clone", "adopted", target_key=target.key, source_assay_id=c.source_assay_id,
                                   seek_id=same[0])
                    clone_ids[key] = same[0]
                    excluded.add(same[0])
                    done, left = done + 1, left - 1
                    continue
                unmapped = [i for i in fresh if not have[i]]
                if unmapped:
                    return StepAnswer(409, share.state, done, left, code="busy",
                                      message=f"study {inp.destination_study_id} gained assay(s) {unmapped} titled "
                                              f"{c.title!r} with no internal assays yet (another share may be making "
                                              "it): call again shortly; if this stays, look in SEEK")
            try:
                rep = session.get_assay(c.source_assay_id)
                policy = ((session.get_study(inp.destination_study_id).get("data") or {}).get("attributes")
                          or {}).get("policy")
                payload = planner.fill_study(planner.clone_payload(rep, policy=policy), inp.destination_study_id)
            except SeekError as exc:
                return _seek_answer(exc, share.state, done, left)
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                return StepAnswer(422, share.state, done, left, code="clone_payload_invalid", message=str(exc)[:300])
            journal.append("clone", "intent", target_key=target.key, source_assay_id=c.source_assay_id,
                           payload=payload)
            try:
                new_id = session.create_assay(payload)
            except SeekUnknownOutcome:
                return StepAnswer(202, share.state, done, left, retry_after_s=ADOPT_POLL_S,
                                  code=CLONE_OUTCOME_UNKNOWN, message="SEEK's answer was lost; checking next call")
            except SeekError as exc:
                return _seek_answer(exc, share.state, done, left)
            journal.append("clone", "done", target_key=target.key, source_assay_id=c.source_assay_id, seek_id=new_id)
            return StepAnswer(200, share.state, done + 1, left - 1)
        if apply_mod._mapping(journal, st, [target], clone_ids):
            hooks.enqueue("assay_map", "*")
        share_jobs.to_queued(share)
        return StepAnswer(202, share.state, done, left)


# --- the worker's unit pass ----------------------------------------------------------------------------------------

def run_share_unit(share, owner: str) -> str:
    """Run a claimed share's link unit under the run lock; ``applied``, ``apply_failed``, ``rolled_back`` (its run
    was undone), or back to ``queued``."""
    run_dir = run_dir_of(share)
    plan = StudyMovePlan.from_file(run_dir / report.PLAN_FILE)
    with preflight.run_lock() as held:
        if not held:
            share_jobs.back_to_queued(share, owner)
            return "queued"
        st = journal_state(read_journal(run_dir / JOURNAL_FILE)[0])
        if st.undo_parts:
            share_jobs.end_rolled_back(share.run_dir)
            return "rolled_back"
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        target, unit = plan.targets[0], plan.units[0]
        clone_ids = {(target.key, c.source_assay_id): (c.seek_assay_id if c.action == "reuse"
                                                       else (st.clones.get((target.key, c.source_assay_id)) or {})
                                                       .get("seek_id")) for c in target.clones}
        key = links.unit_key(plan.run_id, unit.unit)
        us = st.units.get(unit.unit) or {}
        if us.get("committed"):
            share_jobs.finish_apply(share, owner, state="applied", receipt=_receipt(unit, us.get("prepared"), key))
            return "applied"
        verdict = apply_mod._recover(unit, us, plan.run_id) if us.get("prepared") else "rolled_back"
        if verdict == "committed":
            journal.append("links", "committed", unit=unit.unit, recovered=True)
            share_jobs.finish_apply(share, owner, state="applied", receipt=_receipt(unit, us["prepared"], key))
            return "applied"
        if verdict == "unknown":
            share_jobs.finish_apply(share, owner, state="apply_failed", error={
                "code": "unit_state_unknown", "detail": "the unit reads as neither committed nor rolled back"})
            return "apply_failed"
        try:
            with apply_mod._connection() as conn:
                result = links.run_link_unit(conn, unit, journal, clone_ids, run_id=plan.run_id,
                                             share_project_id=plan.share.destination_project_id)
        except links.LinkRefused as exc:
            journal.append("links", "refused", unit=unit.unit, reason=exc.reason, detail=exc.detail)
            code = "plan_stale" if exc.reason in ("digest_mismatch", "clone_changed") else exc.reason
            share_jobs.finish_apply(share, owner, state="apply_failed", error={"code": code, "detail": exc.detail})
            return "apply_failed"
        journal.append("links", "committed", unit=unit.unit)
        if not result.outbox_in_transaction:   # chunked as in the transaction (links.outbox_rows)
            for row_key, part in links.outbox_rows(key, result.sample_ids):
                hooks.enqueue("samples", row_key, part)
        prepared = journal_state(read_journal(run_dir / JOURNAL_FILE)[0]).units[unit.unit]["prepared"]
        share_jobs.finish_apply(share, owner, state="applied", receipt=_receipt(unit, prepared, key))
        return "applied"


def _receipt(unit, prepared: Optional[dict], key: str) -> dict:
    prepared = prepared or {}
    return {"links_inserted": len(prepared.get("inserted") or []),
            "project_rows_added": len(prepared.get("project_pairs_inserted") or []),
            "outbox_key": key, "outbox": prepared.get("outbox"), "samples": len(unit.sync_ids)}
