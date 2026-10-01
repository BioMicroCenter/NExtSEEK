"""rollback_study_moves (tool spec 7, the Undo column, and 7.7): one run, or one investigation of it, undone in
reverse from its journal.

1. the graph step's paper nodes and links, restored from ``graph/`` (``paper_studies.restore_paper_links``), only for
   the papers in scope and the samples whose membership this rollback moves back;
2. the publications: old text back where the new text still stands (``restore_publication_text``);
3. the units, last first, each one transaction (``links.undo_link_unit``; a share's unit also deletes the
   ``projects_samples`` rows it journaled). A unit journaled ``links.prepared`` with no ``links.committed`` is
   decided first as apply decides it (``apply._recover``): committed is undone, rolled back is skipped, anything else
   refuses the rollback before any write;
4. per clone, once it holds nothing but the SOP links its creation copied from its source: those links (SEEK refuses
   to delete an assay holding any asset), its internal-assay rows (``assay_map`` enqueued), then the clone in SEEK;
   a clone that still holds samples keeps its links and rows and is listed. Then the studies, deleted once empty.
   SEEK's 404 reads as already gone; any other refusal is listed for the operator; ``isa`` enqueued;
5. ``sync_samples`` with the approval over the sync ids of the units this scope committed: the bucket's IN_STUDY
   comes back from MySQL (the switch stays on). It first reads the live label preview and stops, writing nothing
   more, when an edge it would write under approval is not one the undo accounts for: the labels MySQL gives once the
   undo's own changes are made (read before the undo, saved in the run directory, and corrected by what the undo
   could not restore), against the graph's stored labels. Deleting that saved file and running again accepts them.
   A deleted study's SEEK-keyed Study node stays (Study nodes of SEEK studies are not deleted; gate G reports it as
   ``12.studies.nodes_not_in_seek``).

Every undo line carries the investigation it undoes (None for the whole run), so a second investigation's rollback
still undoes its own parts, and apply and the graph step refuse only the scopes a rollback touched.

Without ``confirm`` it lists what it would undo, with the live label preview (read before the undo) and the label
changes the undo implies, and writes nothing. Rows changed since apply are not restored: they are reported. A run
that a later, started run built on (it reuses one of this run's clones or studies in an investigation not rolled
back) is refused until that one is rolled back.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Optional

from nextseek_api.graph_sync import hooks, labels, paper_studies, state, targeted
from nextseek_api.graph_sync.writer import _batches
from nextseek_api.management.commands import backfill_publication_attributes as backfill
from nextseek_api.studies import apply as apply_mod
from nextseek_api.studies import links, mapping, preflight, share_jobs
from nextseek_api.studies.apply import (DONE, REFUSED, STOPPED, RunResult, in_scope, labels_outside_plan,
                                        scope_refusal, state_of)
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import LabelChange, StudyMovePlan
from nextseek_api.studies.report import PLAN_FILE
from nextseek_api.studies.snapshot import SnapshotReader

LABELS_UNDO_FILE = "labels_undo_implies{}.json"
LABELS_OUTSIDE_UNDO_FILE = "labels_outside_undo.json"


def later_runs_using(run_dir, seek_ids) -> Optional[str]:
    """The name of a sibling run directory whose plan reuses one of ``seek_ids`` (a clone or a study) in an
    investigation its own rollback has not undone, and whose run started; None when there is none."""
    run_dir = Path(run_dir)
    if not seek_ids:
        return None
    for other in sorted(run_dir.parent.iterdir()):
        if other == run_dir or not (other / PLAN_FILE).exists():
            continue
        try:
            plan = StudyMovePlan.from_file(other / PLAN_FILE)
        except (OSError, ValueError):
            continue
        st = journal_state(read_journal(other / JOURNAL_FILE)[0])
        if not st.started or st.undone:
            continue
        live = [t for t in plan.targets if t.investigation_id not in st.undone_investigations]
        reused = {c.seek_assay_id for t in live for c in t.clones if c.action == "reuse"}
        reused |= {t.study.seek_study_id for t in live if t.study.action == "existing"}
        if reused & set(seek_ids):
            return other.name
    return None


def _copied_sops(slot: dict) -> list[int]:
    """The SOP ids a clone's journaled POST payload linked (copied from its source assay)."""
    rels = (((slot or {}).get("intent") or {}).get("data") or {}).get("relationships") or {}
    return sorted({int(i["id"]) for i in ((rels.get("sops") or {}).get("data") or [])
                   if isinstance(i, dict) and str(i.get("id", "")).isdigit()})


def _lost_posts(st, targets, session) -> list:
    """A study or clone of this run whose POST intent has no outcome (its answer was lost and the lookups found
    nothing then): what SEEK's MySQL holds under its title now, listed for the operator, never deleted (a study of
    that title may be another run's)."""
    found = []
    for t in targets:
        slot = st.studies.get(t.key)
        if slot and slot.get("intent") and not slot.get("seek_id"):
            found += [["study", i, "answer lost: look in SEEK"] for i in session.find_study(t.investigation_id,
                                                                                              t.title)]
        study_id = (st.studies.get(t.key) or {}).get("seek_id") or t.study.seek_study_id
        journaled = {v.get("seek_id") for v in st.clones.values()}
        for c in t.clones:
            slot = st.clones.get((t.key, c.source_assay_id))
            if slot and slot.get("intent") and not slot.get("seek_id") and study_id:
                found += [["assay", i, "answer lost: look in SEEK"] for i in session.find_assay(study_id, c.title)
                          if i not in journaled and i not in set(t.existing_assay_ids)]
    return found


def _verdicts(st, units, plan) -> dict:
    """Apply's recovery for each unit journaled prepared and not committed (read only); a share's unit by the share's
    rules (its destination project)."""
    share_project_id = plan.share.destination_project_id if plan.mode == "share" else None
    return {u.unit: apply_mod._recover(u, st.units[u.unit], plan.run_id, share_project_id=share_project_id)
            for u in units if (st.units.get(u.unit) or {}).get("prepared") and not st.units[u.unit].get("committed")}


def _undo_effects(st, units, reports=None) -> tuple[set, set]:
    """The (assay, sample) pairs the units' undo removes and restores: as journaled, less what each report says it
    could not do."""
    removed, restored = set(), set()
    for u in units:
        us = st.units[u.unit]
        report = (reports or {}).get(u.unit) or {}
        kept = set(report.get("not_deleted_changed") or []) | set(report.get("not_deleted_gone") or [])
        skipped = set(report.get("not_reinserted") or [])
        removed |= {(int(a), int(s)) for i, a, s in (us.get("prepared") or {}).get("inserted") or [] if i not in kept}
        restored |= {(int(r["assay_id"]), int(r["asset_id"])) for r in (us.get("intent") or {}).get("deleted_rows")
                     or [] if r["id"] not in skipped}
    return removed, restored


def _label_inputs(reader, ids) -> dict:
    """What the label check reads before the undo: the stored edges of ``ids``, their endpoints' memberships, the
    assay map, the SOPs and the children's metadata."""
    edges = reader.stored_edges(ids) if ids else []
    if not edges:
        return {"edges": []}
    endpoints = sorted({v for e in edges for v in (e["child_id"], e["parent_id"]) if isinstance(v, int)})
    children = sorted({e["child_id"] for e in edges if isinstance(e["child_id"], int)})
    return {"edges": edges, "memberships": {s: set(m) for s, m in reader.memberships(endpoints).items()},
            "assay_map": reader.assay_map(), "sops": reader.sops(), "metas": reader.sample_rows(children)}


def _implied(inputs: dict, removed, restored) -> list[dict]:
    """The label changes an approved sync writes once the undo's own changes are made: each stored edge against the
    labels of the memberships read before the undo, less ``removed`` plus ``restored`` (``targeted.preview_labels``'s
    rule), the classes needing approval only."""
    edges = inputs["edges"]
    if not edges:
        return []
    after = {s: set(m) for s, m in inputs["memberships"].items()}
    for a, s in removed:
        after.get(s, set()).discard(a)
    for a, s in restored:
        after.setdefault(s, set()).add(a)
    assay_map, sops, metas = inputs["assay_map"], inputs["sops"], inputs["metas"]
    index = labels.sop_title_index(sops)
    out = []
    for e in edges:
        child, parent = e["child_id"], e["parent_id"]
        if not (isinstance(child, int) and isinstance(parent, int)):
            continue
        protocol = labels.resolve_protocol(labels.protocol_value_of((metas.get(child) or {}).get("json_metadata")),
                                           sops, index)
        later = labels.edge_labels(after.get(child), after.get(parent), assay_map, protocol)
        cls = labels.classify(e["stored"], later)
        if cls != labels.EQUAL and cls not in labels.WRITABLE_WITHOUT_APPROVAL:
            out.append({"child_id": child, "parent_id": parent, "class": cls,
                        "properties": labels.differences(e["stored"], later)})
    return out


def _as_planned(items) -> list:
    return [LabelChange(child_id=i["child_id"], parent_id=i["parent_id"], before_class="", after_class=i["class"],
                        properties=i["properties"], stored={}, after={}) for i in items]


def rollback_study_moves(run_dir, session, driver, db, *, confirm: bool, investigation: Optional[int] = None,
                         reader=None) -> RunResult:
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    refusal = scope_refusal(plan, investigation)
    if refusal:
        return RunResult(REFUSED, refusal)
    targets, units = in_scope(plan, investigation)
    reader = reader or SnapshotReader(session, driver, db)
    graph_dir = run_dir / apply_mod.GRAPH_DIR

    def scope(st):
        keys = {t.key for t in targets}
        clone_slots = {k: v for k, v in st.clones.items() if k[0] in keys and v.get("seek_id")}
        return (sorted({v["seek_id"] for v in clone_slots.values()}),
                sorted({v["seek_id"] for k, v in st.studies.items() if k in keys and v.get("seek_id")}),
                clone_slots)

    st = state_of(run_dir)
    if not st.started:
        return RunResult(REFUSED, "this run never started: nothing to undo")
    clone_ids, study_ids, _slots = scope(st)
    blocker = later_runs_using(run_dir, set(clone_ids) | set(study_ids))
    if blocker:
        return RunResult(REFUSED, f"run {blocker} built on this run's clones or studies: roll it back first")
    if not confirm:
        verdicts = _verdicts(st, units, plan)
        committed = [u for u in units if st.units.get(u.unit, {}).get("committed") or verdicts.get(u.unit) ==
                     "committed"]
        todo_units = [u for u in committed if u.unit not in st.undone_units]
        ids = sorted({s for u in committed for s in plan.graph.sync_ids.get(u.unit, [])})
        pub_ids = {r.sample_id for r in plan.publications if r.investigation_id in {t.investigation_id
                                                                                     for t in targets}}
        preview = targeted.preview_labels(driver, db, ids) if ids else []
        implied = _implied(_label_inputs(reader, ids), *_undo_effects(st, todo_units))
        todo = {"graph_restore": graph_dir.exists(),
                "publications": sum(1 for sid in st.pubs_rows if sid in pub_ids),
                "units": [u.unit for u in reversed(todo_units)], "clones": clone_ids, "studies": study_ids,
                "sync_ids": len(ids),
                "project_rows": sum(len(((st.units.get(u.unit) or {}).get("prepared") or {})
                                        .get("project_pairs_inserted") or []) for u in todo_units),
                "units_unknown": sorted(n for n, v in verdicts.items() if v == "unknown"),
                "answers_lost": _lost_posts(st, targets, session)}
        return RunResult(DONE, "dry run: nothing written; label_preview is the graph against MySQL now, before the "
                               "undo; undo_label_changes is what the final approved sync writes after it (any other "
                               "edge stops the rollback before that sync); pass --confirm to undo",
                         {"would_undo": todo, "label_preview": dict(Counter(e["class"] for e in preview)),
                          "undo_label_changes": dict(Counter(e["class"] for e in implied))})
    refused = preflight.graph_version_refusal(driver, db) or preflight.studies_release_refusal(driver, db)
    if refused:
        return RunResult(REFUSED, refused)
    with preflight.run_lock() as held:
        if not held:
            return RunResult(REFUSED, f"another studies run holds the lock {preflight.LOCK_NAME}")
        st = state_of(run_dir)
        clone_ids, study_ids, clone_slots = scope(st)
        verdicts = _verdicts(st, units, plan)
        unknown = sorted(n for n, v in verdicts.items() if v == "unknown")
        if unknown:
            return RunResult(REFUSED, f"units {unknown} read as neither committed nor rolled back; look at their rows "
                                      "before rolling back")
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        for n, verdict in sorted(verdicts.items()):
            if verdict == "committed":
                journal.append("links", "committed", unit=n, recovered=True)
                st.units[n]["committed"] = True
        committed = [u for u in units if st.units.get(u.unit, {}).get("committed")]
        todo_units = [u for u in committed if u.unit not in st.undone_units]
        ids = sorted({s for u in committed for s in plan.graph.sync_ids.get(u.unit, [])})
        suffix = "" if investigation is None else f"-{investigation}"
        implied_path = run_dir / LABELS_UNDO_FILE.format(suffix)
        # A rerun finds what the first pass implied (read before its undo) saved; recomputing it from MySQL after
        # the undo would agree with any live state.
        implied = (json.loads(implied_path.read_text(encoding="utf-8")) if implied_path.exists() else None)
        inputs = _label_inputs(reader, ids) if implied is None else None
        counts: dict = {"units": [], "not_deleted": _lost_posts(st, targets, session)}
        if plan.mode == "share":   # never applied again, whatever its state
            share_jobs.end_rolled_back(run_dir.name)

        def done(part):
            return (part, None) in st.undo_parts or (part, investigation) in st.undo_parts

        def undo_line(event, part, **fields):
            journal.append("undo", event, part=part, investigation=investigation, **fields)

        if graph_dir.exists() and not done("graph"):
            undo_line("intent", "graph")
            paper_ids = sorted({t.paper_id for t in targets if t.paper_id is not None})
            moved = sorted({x.sample_id for u in todo_units for x in u.inserts if x.role == "mover"}
                           | {r.sample_id for u in todo_units for r in u.removals})
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as got:
                if not got:
                    return RunResult(STOPPED, "the graph-write lock stayed busy: run the rollback again")
                restored = paper_studies.restore_paper_links(driver, db, graph_dir, paper_ids=paper_ids,
                                                             sample_ids=moved)
            undo_line("done", "graph", **restored)
            counts["graph"] = restored

        pub_ids = {r.sample_id for r in plan.publications if r.investigation_id in {t.investigation_id
                                                                                     for t in targets}}
        pub_rows = [[sid, old, new] for sid, (old, new) in sorted(st.pubs_rows.items()) if sid in pub_ids]
        if pub_rows and not done("pubs"):
            undo_line("intent", "pubs", samples=len(pub_rows))
            prefix = f"batch:studies:{plan.run_id}:undo:pubs" + ("" if investigation is None else f":{investigation}")
            restored = backfill.restore_publication_text(pub_rows, enqueue_prefix=prefix)
            undo_line("done", "pubs", restored=len(restored["restored"]), changed_since=restored["changed_since"])
            counts["publications"] = restored

        reports = {}
        for unit in reversed(todo_units):
            undo_line("intent", "unit", unit=unit.unit)
            with apply_mod._connection() as conn:
                report = links.undo_link_unit(conn, unit.unit, st.units[unit.unit], journal, run_id=plan.run_id)
            undo_line("done", "unit", unit=unit.unit, deleted=report["deleted"], reinserted=report["reinserted"],
                      not_deleted_changed=report["not_deleted_changed"], not_deleted_gone=report["not_deleted_gone"],
                      not_reinserted=report["not_reinserted"], project_pairs_deleted=report["project_pairs_deleted"],
                      project_pairs_kept_in_use=report["project_pairs_kept_in_use"],
                      project_pairs_gone=report["project_pairs_gone"])
            if not report["outbox_in_transaction"]:   # chunked as in its transaction (links.outbox_rows)
                for key, part in links.outbox_rows(links.undo_key(plan.run_id, unit.unit), report["sample_ids"]):
                    hooks.enqueue("samples", key, part)
            counts["units"].append({"unit": unit.unit, **report})
            reports[unit.unit] = report
        if implied is None:   # what the undo could not restore stays as MySQL holds it
            implied = _implied(inputs, *_undo_effects(st, todo_units, reports))
            implied_path.write_text(json.dumps(implied, sort_keys=True), encoding="utf-8")

        copied: dict = {}
        for slot in clone_slots.values():
            copied.setdefault(slot["seek_id"], set()).update(_copied_sops(slot))
        for clone in sorted(copied):
            sops = sorted(copied[clone])
            if session.assay_link_count(clone, except_sops=sops):
                counts["not_deleted"].append(["assay", clone, "not empty"])
                undo_line("done", "clone", seek_id=clone, deleted=False, reason="not empty")
                continue
            if sops:
                with apply_mod._connection() as conn:
                    links.unlink_clone_sops(conn, clone, sops, lambda rows, c=clone: undo_line(
                        "intent", "sops", seek_id=c, rows=rows))
            rows = [r for r in st.map_rows or [] if r[1] == clone]
            held = {(r[1], r[2]) for r in rows}
            rows += mapping.rows_holding([p for p in st.map_pairs or [] if p[0] == clone and tuple(p) not in held])
            if rows:
                undo_line("intent", "map", rows=rows)
                removed_rows = mapping.delete_clone_mappings(rows)
                hooks.enqueue("assay_map", "*")
                undo_line("done", "map", **removed_rows)
            ok, status = session.delete_assay(clone)
            undo_line("done", "clone", seek_id=clone, deleted=ok, status=status)
            if not ok and status != 404:
                counts["not_deleted"].append(["assay", clone, status])
        for study in study_ids:
            if session.study_assay_count(study):
                counts["not_deleted"].append(["study", study, "not empty"])
                undo_line("done", "study", seek_id=study, deleted=False, reason="not empty")
                continue
            ok, status = session.delete_study(study)
            undo_line("done", "study", seek_id=study, deleted=ok, status=status)
            if not ok and status != 404:
                counts["not_deleted"].append(["study", study, status])
        hooks.enqueue("isa", "*")

        live = targeted.preview_labels(driver, db, ids) if ids else []
        outside = labels_outside_plan(live, _as_planned(implied))
        if outside:
            (run_dir / LABELS_OUTSIDE_UNDO_FILE).write_text(json.dumps(outside, sort_keys=True, default=str),
                                                           encoding="utf-8")
            undo_line("stopped", "sync", edges=len(outside))
            return RunResult(STOPPED, f"MySQL and SEEK are undone, but {len(outside)} edge(s) would be written that "
                                      f"the undo does not account for (see {run_dir / LABELS_OUTSIDE_UNDO_FILE}); "
                                      "the graph keeps their labels. Look at them; to write them with the approval "
                                      f"anyway, delete {implied_path} and run the rollback again", counts)
        for chunk in _batches(ids, apply_mod.SYNC_CALL_IDS):
            result = targeted.sync_samples(driver, db, chunk, run_dir=str(graph_dir), apply_label_changes=True)
            if result.get("status") != targeted.OK:
                return RunResult(STOPPED, f"sync_samples answered {result.get('status')}: run the rollback again",
                                 counts)
        undo_line("done", "sync", samples=len(ids))
        undo_line("done", "run", not_deleted=counts["not_deleted"])
        message = "rollback done"
        if counts["not_deleted"]:
            message += (f"; SEEK kept {len(counts['not_deleted'])} object(s): delete them in SEEK's UI once empty: "
                        f"{counts['not_deleted']}")
        return RunResult(DONE, message, counts)
