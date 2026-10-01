"""apply_study_moves (tool spec 7.1 to 7.5): the plan written to SEEK, each write journaled before it happens.

1. Preflight (read only): the plan's version and this code's sha; the scope (an investigation the plan holds); the
   run lock, then the journal read under it (not rolled back, the same plan as the run started with); the buckets,
   the studies release, the plan's existing studies and reused assays, the titles of the studies to create, and SEEK's
   next study id (``preflight``). The caller has proved the login (``SeekSession.prove``).
2. Studies to create, one ``POST /studies`` each; ``isa`` enqueued.
3. Clones to create, one ``POST /assays`` at a time, the target's SEEK id filled in.
4. The clones' internal-assay rows, one transaction; ``assay_map`` enqueued.
5. The link units in order (``links.run_link_unit``), one transaction each.
6. The publications (the backfill's ``write_publication_attributes``), in batches of 500, the samples enqueued under
   the run's own keys. A unit refused part way still gets the committed units' publications written, with only
   their targets' DOIs, so a new plan (which reads their samples as no_change) loses none.

A POST that raised is resolved, never retried blindly: the object it would have made is looked for in SEEK's MySQL,
every ``ADOPT_POLL_S`` for up to ``ADOPT_WAIT_S`` (a create Rails is still finishing does not show at once); one is
adopted, none means a new POST, several stop the run. A clone this run journaled already is never adopted for
another. Every step checks the journal and the database before it writes again, so the same command resumes a run
that stopped. Exit statuses: 0 done, 1 stopped part way (the journal says where), 2 refused with nothing written.

``graph_step`` (7.6) is the separate graph mode, run under the operator's approval once apply has committed a wave.
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
from nextseek_api.graph_sync import hooks, labels, paper_studies, sources, state, targeted
from nextseek_api.graph_sync.models_db import GraphSyncOutbox
from nextseek_api.graph_sync.writer import _batches
from nextseek_api.management.commands import backfill_publication_attributes as backfill
from nextseek_api.studies import links, mapping, preflight
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import PLAN_VERSION, StudyMovePlan, canonical_json
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
    def __init__(self, status: str, message: str, *, unit_refused: bool = False):
        self.status, self.message, self.unit_refused = status, message, unit_refused
        super().__init__(message)


def load_run(run_dir):
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    lines, bad = read_journal(run_dir / JOURNAL_FILE)
    return plan, journal_state(lines), bad


def scope_refusal(plan: StudyMovePlan, investigation: Optional[int]) -> Optional[str]:
    if investigation is not None and all(t.investigation_id != investigation for t in plan.targets):
        return f"investigation {investigation} has no target in this plan"
    return None


def rolled_back(st, plan: StudyMovePlan, investigation: Optional[int]) -> bool:
    """Whether a rollback of this run, finished or not, touched the scope: the whole run, or an investigation of it
    (every investigation of the plan when no investigation is given)."""
    scope = {investigation} if investigation is not None else {t.investigation_id for t in plan.targets}
    return None in st.undo_scopes or bool(scope & st.undo_scopes)


def state_of(run_dir):
    return journal_state(read_journal(Path(run_dir) / JOURNAL_FILE)[0])


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


def _enqueue_after_commit(run_id: str, unit, sample_ids) -> None:
    """A unit's rows that went in after its commit, chunked as in its transaction (links.outbox_rows); sent before
    ``links.committed`` is journaled, so a crash in between resumes through the recovery and sends them again."""
    for key, part in links.outbox_rows(links.unit_key(run_id, unit.unit), sample_ids):
        hooks.enqueue("samples", key, part)


def _units(journal, st, units, clone_ids, run_id):
    """Yields each unit this call commits or recovers as committed."""
    for unit in units:
        us = st.units.get(unit.unit) or {}
        if us.get("committed"):
            continue
        if us.get("refused"):
            raise _Stop(STOPPED, f"unit {unit.unit} was refused (its source assays changed): plan the wave again",
                        unit_refused=True)
        if us.get("prepared"):
            verdict = _recover(unit, us, run_id)
            if verdict == "committed":
                if us["prepared"].get("outbox") != "in_transaction":
                    _enqueue_after_commit(run_id, unit, unit.sync_ids)
                journal.append("links", "committed", unit=unit.unit, recovered=True)
                yield unit
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
                                 "done read no_change", unit_refused=True) from exc
        if not result.outbox_in_transaction:
            _enqueue_after_commit(run_id, unit, result.sample_ids)
        journal.append("links", "committed", unit=unit.unit)
        yield unit


# --- publications ------------------------------------------------------------------------------------------------

def _publication_values(raw):
    """A metadata text's (DOI, PMID) pairs (``backfill.publication_pairs``): what the resume compares, not the text,
    so an edit to any other attribute is not a change. None when the text is not a JSON object."""
    try:
        meta = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return backfill.publication_pairs(meta.get("DOI"), meta.get("PMID"))


def _only_dois(rows, dois: set) -> list:
    """The publication rows cut to the DOIs in ``dois`` (casefolded), PMIDs kept aligned; a row left with none is
    dropped."""
    out = []
    for r in rows:
        kept = [(d, p) for d, p in zip(r.dois, r.pmids) if d.strip().casefold() in dois]
        if kept:
            out.append(r.model_copy(update={"dois": [d for d, _p in kept], "pmids": [p for _d, p in kept]}))
    return out


def _publications(journal, st, plan, targets, investigation, *, dois: Optional[set] = None) -> dict:
    """The publication step; ``dois`` (casefolded) limits it to those papers (the committed units' targets after a
    unit was refused)."""
    invs = {t.investigation_id for t in targets}
    rows = [r for r in plan.publications if r.investigation_id in invs]
    if dois is not None:
        rows = _only_dois(rows, dois)
    if not rows:
        return {"publication_rows": 0}
    ids = sorted(r.sample_id for r in rows)
    wanted = set(ids)
    current = {r["id"]: r["json_metadata"] for r in sources.samples_by_ids(ids)}
    prior = {sid: v for sid, v in st.pubs_rows.items() if sid in wanted}
    changed = sorted(sid for sid, (old, new) in prior.items()
                     if _publication_values(current.get(sid))
                     not in (_publication_values(old), _publication_values(new)))
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
                   unreadable=unreadable, queued=report["queued"], committed_units_only=dois is not None)
    return {"publication_rows": len(rows), "publications_written": len(report["updated"]),
            "publications_unreadable": len(unreadable)}


# --- apply -------------------------------------------------------------------------------------------------------

def apply_study_moves(run_dir, session, driver, db, *, investigation: Optional[int] = None,
                      reader=None) -> RunResult:
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    if plan.mode == "share":
        return RunResult(REFUSED, "a share is applied through the sample-shares endpoint, not --mode apply")
    if plan.plan_version != PLAN_VERSION or plan.code_sha != code_sha():
        return RunResult(REFUSED, "the plan was made by other code: plan again")
    refusal = scope_refusal(plan, investigation)
    if refusal:
        return RunResult(REFUSED, refusal)
    targets, units = in_scope(plan, investigation)
    reader = reader or SnapshotReader(session, driver, db)
    with preflight.run_lock() as held:
        if not held:
            return RunResult(REFUSED, f"another studies run holds the lock {preflight.LOCK_NAME}")
        st = state_of(run_dir)     # under the lock: a rollback or an apply that just ended is seen
        if rolled_back(st, plan, investigation):
            return RunResult(REFUSED, "this run was rolled back: plan again for a new run")
        if st.plan_sha256 is not None and st.plan_sha256 != plan.sha256():
            return RunResult(REFUSED, "the plan changed since this run started: plan again for a new run")
        refusal = preflight.apply_refusal(plan, targets, driver, db, reader, session=session, st=st)
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
            done = {u.unit for u in units if (st.units.get(u.unit) or {}).get("committed")}
            try:
                for unit in _units(journal, st, units, clone_ids, plan.run_id):
                    counts["units_committed"] += 1
                    done.add(unit.unit)
            except _Stop as stop:
                if stop.unit_refused and done:
                    keys = {u.target_key for u in units if u.unit in done}
                    dois = {t.doi.strip().casefold() for t in targets if t.key in keys and (t.doi or "").strip()}
                    try:
                        counts.update(_publications(journal, st, plan, targets, investigation, dois=dois))
                    except _Stop as also:
                        raise _Stop(STOPPED, f"{stop.message}; {also.message}") from also
                raise
            counts.update(_publications(journal, st, plan, targets, investigation))
        except _Stop as stop:
            return RunResult(stop.status, stop.message, dict(counts))
        journal.append("apply", "done", investigation=investigation)
        counts.update(studies=len(study_ids), clones=len(clone_ids))
        return RunResult(DONE, "apply done; the graph step is next", dict(counts))


# --- the graph step (tool spec 7.6) --------------------------------------------------------------------------------

GRAPH_DIR = "graph"
LABELS_OUTSIDE_FILE = "labels_outside_plan.json"
GRAPH_COUNT_KEYS = ("labels_written", "labels_refreshed", "labels_new", "labels_renamed", "labels_protocol_filled",
                    "labels_changed", "labels_plural_missing", "labels_cleared", "in_study_added", "in_study_removed",
                    "structural_gaps")


def labels_outside_plan(live: list, planned: list) -> list:
    """The live edges only an approved sync writes (a class neither ``equal`` nor in
    ``labels.WRITABLE_WITHOUT_APPROVAL``: new, renamed and protocol_filled edges the loop writes on its own) that the
    plan did not list with the same class and the same properties."""
    allowed = {(c.child_id, c.parent_id): (c.after_class, tuple(c.properties)) for c in planned}
    return [e for e in live if e["class"] != labels.EQUAL and e["class"] not in labels.WRITABLE_WITHOUT_APPROVAL
            and allowed.get((e["child_id"], e["parent_id"])) != (e["class"], tuple(e["properties"]))]


def _target_study_ids(targets, st) -> dict:
    return {t.key: (t.study.seek_study_id if t.study.action == "existing"
                    else (st.studies.get(t.key) or {}).get("seek_id")) for t in targets}


def graph_step(run_dir, driver, db, *, approve_label_changes: bool, investigation: Optional[int] = None) -> RunResult:
    if not approve_label_changes:
        return RunResult(REFUSED, "the graph step writes the plan's label changes, and only with "
                                  "--approve-label-changes")
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    refused = scope_refusal(plan, investigation)
    if refused:
        return RunResult(REFUSED, refused)
    targets, units = in_scope(plan, investigation)
    refused = preflight.graph_version_refusal(driver, db) or preflight.studies_release_refusal(driver, db)
    if refused:
        return RunResult(REFUSED, refused)
    ids = sorted({s for u in units for s in plan.graph.sync_ids.get(u.unit, [])}
                 | {s for t in targets for s in plan.graph.no_change_sync_ids.get(t.key, [])})
    graph_dir = run_dir / GRAPH_DIR
    with preflight.run_lock() as held:
        if not held:
            return RunResult(REFUSED, f"another studies run holds the lock {preflight.LOCK_NAME}")
        st = state_of(run_dir)
        if rolled_back(st, plan, investigation):
            return RunResult(REFUSED, "this run was rolled back")
        open_units = [u.unit for u in units if not (st.units.get(u.unit) or {}).get("committed")]
        if open_units:
            return RunResult(REFUSED, f"units {open_units} are not committed: run apply first")
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        live = targeted.preview_labels(driver, db, ids) if ids else []
        outside = labels_outside_plan(live, plan.graph.move + plan.graph.pending)
        if outside:
            graph_dir.mkdir(parents=True, exist_ok=True)
            (graph_dir / LABELS_OUTSIDE_FILE).write_text(canonical_json(outside), encoding="utf-8")
            journal.append("graph", "refused", edges=len(outside))
            return RunResult(REFUSED, f"{len(outside)} edge(s) would be written that the plan did not list, or of "
                                      f"another class: plan this wave again (see {graph_dir / LABELS_OUTSIDE_FILE})")
        counts: dict = defaultdict(int)
        study_ids = _target_study_ids(targets, st)
        for paper in plan.graph.paper_links:
            if paper.target_key not in study_ids:
                continue
            seek_id = study_ids[paper.target_key]
            placed = sorted({link["sample_id"] for link in sources.seek_study_links_for(paper.sample_ids)
                             if link["study_id"] == seek_id})
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as got:
                if not got:
                    journal.append("graph", "stopped", reason="lock_timeout")
                    return RunResult(STOPPED, "the graph-write lock stayed busy: run the graph step again")
                retired = paper_studies.retire_paper_links(driver, db, paper.paper_id, placed,
                                                           graph_dir / paper_studies.IN_STUDY_REMOVED_FILE)
                gone = paper_studies.delete_empty_paper_study_nodes(
                    driver, db, [paper.paper_id], archive_path=graph_dir / paper_studies.STUDY_NODES_REMOVED_FILE)
            counts["paper_links_retired"] += retired["paper_links_retired"]
            counts["study_nodes_deleted"] += gone["study_nodes_deleted"]
        for chunk in _batches(ids, SYNC_CALL_IDS):
            result = targeted.sync_samples(driver, db, chunk, run_dir=str(graph_dir), apply_label_changes=True)
            if result.get("status") != targeted.OK:
                journal.append("graph", "stopped", status=result.get("status"))
                return RunResult(STOPPED, f"sync_samples answered {result.get('status')}: run the graph step again")
            for key in GRAPH_COUNT_KEYS:
                counts[key] += int(result.get(key) or 0)
        journal.append("graph", "done", investigation=investigation, counts=dict(counts))
        gaps = counts.get("structural_gaps", 0)
        note = (f"; {gaps} structural link(s) left unwritten: the loop keeps those samples' rows open, read "
                "graph_sync_health" if gaps else "")
        return RunResult(DONE, "graph step done" + note, dict(counts))
