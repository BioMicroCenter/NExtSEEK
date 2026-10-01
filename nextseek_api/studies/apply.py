"""apply_study_moves (tool spec 7.1 to 7.5): the plan written to SEEK, each write journaled before it happens.

1. Preflight (read only): the plan's version and this code's sha; the run lock; the buckets, the studies release,
   and SEEK's next study id (``preflight``). The caller has proved the login (``SeekSession.prove``).
2. Studies to create, one ``POST /studies`` each; ``isa`` enqueued.
3. Clones to create, one ``POST /assays`` at a time, the target's SEEK id filled in.
4. The clones' internal-assay rows, one transaction; ``assay_map`` enqueued.
5. The link units in order (``links.run_link_unit``), one transaction each.
6. The publications (the backfill's ``write_publication_attributes``), in batches of 500, the samples enqueued under
   the run's own keys.

A POST that raised is resolved, never retried blindly: the object it would have made is looked for in SEEK's MySQL,
every ``ADOPT_POLL_S`` for up to ``ADOPT_WAIT_S`` (a create Rails is still finishing does not show at once); one is
adopted, none means a new POST, several stop the run. A clone this run journaled already is never adopted for
another. Every step checks the journal and the database before it writes again, so the same command resumes a run
that stopped. Exit statuses: 0 done, 1 stopped part way (the journal says where), 2 refused with nothing written.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nextseek_api.assay_registration.planner import existing_membership_ids
from nextseek_api.batch_upload.db_engine import get_connection
from nextseek_api.graph_sync import hooks, sources
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.management.commands import backfill_publication_attributes as backfill
from nextseek_api.studies import links, mapping, preflight
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import PLAN_VERSION, StudyMovePlan
from nextseek_api.studies.planner import code_sha, fill_study
from nextseek_api.studies.report import PLAN_FILE
from nextseek_api.studies.seek import ADOPT_POLL_S, ADOPT_WAIT_S, SeekError, SeekUnknownOutcome
from nextseek_api.studies.snapshot import SnapshotReader

DONE, STOPPED, REFUSED = "done", "stopped", "refused"
EXIT_CODES = {DONE: 0, STOPPED: 1, REFUSED: 2}
PUBLICATION_BATCH = 500
MAX_POSTS = 3
SYNC_CALL_IDS = 5_000

_connection = get_connection
_sleep = time.sleep
_clock = time.monotonic


def _unit_outbox_exists_orm(key: str) -> bool:
    return GraphSyncOutbox.objects.filter(kind="samples", key=key).exists()


_unit_outbox_exists = _unit_outbox_exists_orm


@dataclass
class RunResult:
    status: str
    message: str = ""
    counts: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


class _Stop(Exception):
    def __init__(self, status: str, message: str):
        self.status, self.message = status, message
        super().__init__(message)


def load_run(run_dir):
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    lines, bad = read_journal(run_dir / JOURNAL_FILE)
    return plan, journal_state(lines), bad


def in_scope(plan: StudyMovePlan, investigation: Optional[int]) -> tuple[list, list]:
    targets = [t for t in plan.targets if investigation is None or t.investigation_id == investigation]
    keys = {t.key for t in targets}
    return targets, [u for u in plan.units if u.target_key in keys]


def merge_publications(raw, dois, pmids) -> Optional[tuple[str, str]]:
    """The DOI and PMID text a sample holds after the run: its own (DOI, PMID) pairs, a blank DOI read as none, then
    each of the run's DOIs it lacks, in order, PMIDs aligned. None when its json_metadata is not a JSON object."""
    try:
        meta = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    pairs = list(backfill.publication_pairs(meta.get("DOI"), meta.get("PMID")))
    have = {d.casefold() for d, _p in pairs}
    for doi, pmid in zip(dois, pmids):
        doi = (doi or "").strip()
        if doi and doi.casefold() not in have:
            pairs.append((doi, (pmid or "").strip()))
            have.add(doi.casefold())
    return backfill.SEPARATOR.join(d for d, _p in pairs), backfill.SEPARATOR.join(p for _d, p in pairs)


# --- creates, with adoption ------------------------------------------------------------------------------------

def _adopt(find) -> Optional[int]:
    start = _clock()
    while True:
        found = find()
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise _Stop(STOPPED, f"several objects match a create whose answer was lost: {found}; decide in SEEK")
        if _clock() - start >= ADOPT_WAIT_S:
            return None
        _sleep(ADOPT_POLL_S)


def _create_or_adopt(journal, step: str, fields: dict, payload: dict, post, find, *, prior_intent: bool) -> int:
    if prior_intent:
        found = _adopt(find)
        if found is not None:
            journal.append(step, "adopted", **fields, seek_id=found)
            return found
    for _attempt in range(MAX_POSTS):
        journal.append(step, "intent", **fields, payload=payload)
        try:
            new_id = post(payload)
        except SeekUnknownOutcome:
            found = _adopt(find)
            if found is not None:
                journal.append(step, "adopted", **fields, seek_id=found)
                return found
            continue
        except SeekError as exc:
            raise _Stop(STOPPED, f"{step} {fields}: {exc.message}") from exc
        journal.append(step, "done", **fields, seek_id=new_id)
        return new_id
    raise _Stop(STOPPED, f"{step} {fields}: no answer after {MAX_POSTS} POSTs and nothing found in SEEK; look there")


def _studies(journal, st, targets, session) -> tuple[dict, bool]:
    ids, created = {}, False
    for t in targets:
        if t.study.action == "existing":
            ids[t.key] = t.study.seek_study_id
            continue
        slot = st.studies.get(t.key) or {}
        if slot.get("seek_id"):
            ids[t.key] = slot["seek_id"]
            continue
        ids[t.key] = _create_or_adopt(
            journal, "study", {"target_key": t.key}, t.study.payload, session.create_study,
            lambda t=t: session.find_study(t.investigation_id, t.title), prior_intent=bool(slot))
        created = True
    return ids, created


def _clones(journal, st, targets, study_ids, session) -> dict:
    ids: dict = {}
    journaled = {v["seek_id"] for v in st.clones.values() if v.get("seek_id")}
    for t in targets:
        for c in t.clones:
            key = (t.key, c.source_assay_id)
            if c.action == "reuse":
                ids[key] = c.seek_assay_id
                continue
            slot = st.clones.get(key) or {}
            if slot.get("seek_id"):
                ids[key] = slot["seek_id"]
                continue
            study_id = study_ids[t.key]
            excluded = set(t.existing_assay_ids) | journaled | set(ids.values())
            new = _create_or_adopt(
                journal, "clone", {"target_key": t.key, "source_assay_id": c.source_assay_id},
                fill_study(c.payload, study_id), session.create_assay,
                lambda study_id=study_id, c=c, excluded=excluded: [
                    i for i in session.find_assay(study_id, c.title) if i not in excluded],
                prior_intent=bool(slot))
            ids[key] = new
            journaled.add(new)
    return ids


def _mapping(journal, st, targets, clone_ids) -> bool:
    done = {(a, i) for _r, a, i in (st.map_rows or [])}
    pairs = sorted({(clone_ids[(t.key, c.source_assay_id)], iid) for t in targets for c in t.clones
                    if c.action == "create" for iid in c.internal_assay_ids} - done)
    if not pairs:
        return False
    journal.append("map", "intent", pairs=[list(p) for p in pairs])
    rows = mapping.insert_clone_mappings(pairs)
    journal.append("map", "done", rows=rows)
    return True


# --- units, with recovery ----------------------------------------------------------------------------------------

def _recover(unit, unit_state: dict, run_id: str) -> str:
    prepared = unit_state["prepared"]
    if prepared.get("outbox") == "in_transaction":
        return "committed" if _unit_outbox_exists(links.unit_key(run_id, unit.unit)) else "rolled_back"
    inserted = prepared.get("inserted") or []
    removals = sorted({(r.assay_id, r.sample_id) for r in unit.removals})
    with _connection() as conn:
        held = links.rows_by_id(conn, [i for i, _a, _s in inserted])
        ids_hold = all(held.get(i) == (a, s) for i, a, s in inserted)
        left = existing_membership_ids(removals, conn)
        if ids_hold and not left:
            return "committed"
        digest, _rows = links.current_digest(conn, unit.source_assay_ids)
        if digest == unit.digest:
            return "rolled_back"
    return "unknown"


def _units(journal, st, units, clone_ids, run_id):
    """Yields (unit, whether its outbox row must be enqueued after the commit, its sample ids) per committed unit."""
    for unit in units:
        us = st.units.get(unit.unit) or {}
        if us.get("committed"):
            continue
        if us.get("refused"):
            raise _Stop(STOPPED, f"unit {unit.unit} was refused (its source assays changed): plan the wave again")
        if us.get("prepared"):
            verdict = _recover(unit, us, run_id)
            if verdict == "committed":
                journal.append("links", "committed", unit=unit.unit, recovered=True)
                yield unit, us["prepared"].get("outbox") != "in_transaction", sorted(set(unit.sync_ids))
                continue
            if verdict == "unknown":
                raise _Stop(STOPPED, f"unit {unit.unit} reads as neither committed nor rolled back; look at its "
                                     "rows before running again")
        try:
            with _connection() as conn:
                result = links.run_link_unit(conn, unit, journal, clone_ids, run_id=run_id)
        except links.LinkRefused as exc:
            journal.append("links", "refused", unit=unit.unit, reason=exc.reason, detail=exc.detail)
            raise _Stop(STOPPED, f"unit {unit.unit} refused ({exc.reason}): plan the wave again; the units already "
                                 "done read no_change") from exc
        journal.append("links", "committed", unit=unit.unit)
        yield unit, not result.outbox_in_transaction, result.sample_ids


# --- publications ------------------------------------------------------------------------------------------------

def _publications(journal, st, plan, targets, investigation) -> dict:
    invs = {t.investigation_id for t in targets}
    rows = [r for r in plan.publications if r.investigation_id in invs]
    if not rows:
        return {"publication_rows": 0}
    ids = sorted(r.sample_id for r in rows)
    current = {r["id"]: r["json_metadata"] for r in sources.samples_by_ids(ids)}
    prior = {sid: v for sid, v in st.pubs_rows.items() if sid in set(ids)}
    changed = sorted(sid for sid, (old, new) in prior.items() if (current.get(sid) or "") not in (old, new))
    if changed:
        raise _Stop(STOPPED, f"publications: {len(changed)} sample(s) changed since this run wrote them "
                             f"(ids {changed[:20]}); look before running again")
    pairs, unreadable = {}, []
    for r in rows:
        merged = merge_publications(current.get(r.sample_id), r.dois, r.pmids)
        if merged is None:
            unreadable.append(r.sample_id)
        else:
            pairs[r.sample_id] = merged

    def on_batch(batch):
        journal.append("pubs", "intent", rows=[[sid, old, new] for sid, old, new in batch])

    prefix = f"batch:studies:{plan.run_id}:pubs" + ("" if investigation is None else f":{investigation}")
    report = backfill.write_publication_attributes(pairs, apply=True, batch=PUBLICATION_BATCH, on_batch=on_batch,
                                                   enqueue_prefix=prefix, also_enqueue=sorted(prior))
    unreadable = sorted(set(unreadable) | set(report["unreadable"]))
    journal.append("pubs", "done", investigation=investigation, written=len(report["updated"]),
                   unreadable=unreadable, queued=report["queued"])
    return {"publication_rows": len(rows), "publications_written": len(report["updated"]),
            "publications_unreadable": len(unreadable)}


# --- apply -------------------------------------------------------------------------------------------------------

def apply_study_moves(run_dir, session, driver, db, *, investigation: Optional[int] = None,
                      reader=None) -> RunResult:
    run_dir = Path(run_dir)
    plan, st, _bad = load_run(run_dir)
    if plan.plan_version != PLAN_VERSION or plan.code_sha != code_sha():
        return RunResult(REFUSED, "the plan was made by other code: plan again")
    if st.undo_parts:
        return RunResult(REFUSED, "this run was rolled back: plan again for a new run")
    targets, units = in_scope(plan, investigation)
    reader = reader or SnapshotReader(session, driver, db)
    with preflight.run_lock() as held:
        if not held:
            return RunResult(REFUSED, f"another studies run holds the lock {preflight.LOCK_NAME}")
        refusal = preflight.apply_refusal(plan, targets, driver, db, reader)
        if refusal:
            return RunResult(REFUSED, refusal)
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        journal.append("run", "start", plan_sha256=plan.sha256(), login=session.login, person_id=session.person_id,
                       investigation=investigation)
        counts: dict = defaultdict(int)
        try:
            study_ids, _created = _studies(journal, st, targets, session)
            if any(t.study.action == "create" for t in targets):
                hooks.enqueue("isa", "*")        # again on a resume: harmless, and never lost to a crash
            clone_ids = _clones(journal, st, targets, study_ids, session)
            _mapping(journal, st, targets, clone_ids)
            if any(c.action == "create" for t in targets for c in t.clones):
                hooks.enqueue("assay_map", "*")
            for unit, enqueue_after, sample_ids in _units(journal, st, units, clone_ids, plan.run_id):
                counts["units_committed"] += 1
                if enqueue_after:   # the unit's rows, chunked as in its transaction (links.outbox_rows)
                    for key, part in links.outbox_rows(links.unit_key(plan.run_id, unit.unit), sample_ids):
                        hooks.enqueue("samples", key, part)
            counts.update(_publications(journal, st, plan, targets, investigation))
        except _Stop as stop:
            return RunResult(stop.status, stop.message, dict(counts))
        journal.append("apply", "done", investigation=investigation)
        counts.update(studies=len(study_ids), clones=len(clone_ids))
        return RunResult(DONE, "apply done; the graph step is next", dict(counts))
