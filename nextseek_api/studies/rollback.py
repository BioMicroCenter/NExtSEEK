"""rollback_study_moves (tool spec 7, the Undo column, and 7.7): one run, or one investigation of it, undone in
reverse from its journal.

1. the graph step's paper nodes and links, restored from ``graph/`` (``paper_studies.restore_paper_links``);
2. the publications: old text back where the new text still stands (``restore_publication_text``);
3. the units, last first, each one transaction (``links.undo_link_unit``; a share's unit also deletes the
   ``projects_samples`` rows it journaled);
4. the clones' internal-assay rows; ``assay_map`` enqueued;
5. the clones, then the studies, deleted in SEEK once empty; a refusal is listed for the operator; ``isa`` enqueued;
6. ``sync_samples`` with the approval over the run's sync ids: the bucket's IN_STUDY comes back from MySQL (the switch
   stays on). A deleted study's SEEK-keyed Study node stays (Study nodes of SEEK studies are not deleted; gate G
   reports it as ``12.studies.nodes_not_in_seek``).

Without ``confirm`` it lists what it would undo with the live label preview of those ids (what the final sync's
approval covers), and writes nothing. Rows changed since apply are not restored: they are reported. A run that a
later, started run built on (it reuses one of this run's clones or studies) is refused until that one is rolled back.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from nextseek_api.graph_sync import hooks, paper_studies, state, targeted
from nextseek_api.graph_sync.writer import _batches
from nextseek_api.management.commands import backfill_publication_attributes as backfill
from nextseek_api.studies import apply as apply_mod
from nextseek_api.studies import links, mapping, preflight
from nextseek_api.studies.apply import DONE, REFUSED, STOPPED, RunResult, in_scope, load_run
from nextseek_api.studies.journal import JOURNAL_FILE, Journal, journal_state, read_journal
from nextseek_api.studies.models import StudyMovePlan
from nextseek_api.studies.report import PLAN_FILE


def later_runs_using(run_dir, seek_ids) -> Optional[str]:
    """The name of a sibling run directory whose plan reuses one of ``seek_ids`` (a clone or a study) and whose run
    started and is not rolled back; None when there is none."""
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
        reused = {c.seek_assay_id for t in plan.targets for c in t.clones if c.action == "reuse"}
        reused |= {t.study.seek_study_id for t in plan.targets if t.study.action == "existing"}
        if reused & set(seek_ids):
            return other.name
    return None


def rollback_study_moves(run_dir, session, driver, db, *, confirm: bool,
                         investigation: Optional[int] = None) -> RunResult:
    run_dir = Path(run_dir)
    plan, st, _bad = load_run(run_dir)
    targets, units = in_scope(plan, investigation)
    keys = {t.key for t in targets}
    clone_ids = sorted({v["seek_id"] for (k, _a), v in st.clones.items() if k in keys and v.get("seek_id")})
    study_ids = sorted({v["seek_id"] for k, v in st.studies.items() if k in keys and v.get("seek_id")})
    blocker = later_runs_using(run_dir, set(clone_ids) | set(study_ids))
    if blocker:
        return RunResult(REFUSED, f"run {blocker} built on this run's clones or studies: roll it back first")
    committed = [u for u in units if (st.units.get(u.unit) or {}).get("committed") and u.unit not in st.undone_units]
    ids = sorted({s for u in units for s in plan.graph.sync_ids.get(u.unit, [])})
    pub_ids = {r.sample_id for r in plan.publications if r.investigation_id in {t.investigation_id for t in targets}}
    pub_rows = [[sid, old, new] for sid, (old, new) in sorted(st.pubs_rows.items()) if sid in pub_ids]
    graph_dir = run_dir / apply_mod.GRAPH_DIR
    todo = {"graph_restore": graph_dir.exists(), "publications": len(pub_rows),
            "units": [u.unit for u in reversed(committed)], "clones": clone_ids, "studies": study_ids,
            "sync_ids": len(ids)}
    if not confirm:
        preview = targeted.preview_labels(driver, db, ids) if ids else []
        return RunResult(DONE, "dry run: nothing written; pass --confirm to undo",
                         {"would_undo": todo, "label_preview": dict(Counter(e["class"] for e in preview))})
    refused = preflight.graph_version_refusal(driver, db) or preflight.studies_release_refusal(driver, db)
    if refused:
        return RunResult(REFUSED, refused)
    with preflight.run_lock() as held:
        if not held:
            return RunResult(REFUSED, f"another studies run holds the lock {preflight.LOCK_NAME}")
        journal = Journal(run_dir / JOURNAL_FILE, run_id=plan.run_id)
        counts: dict = {"units": [], "not_deleted": []}

        if graph_dir.exists() and "graph" not in st.undo_parts:
            journal.append("undo", "intent", part="graph")
            with state.graph_write_lock(targeted.LOCK_WAIT_S) as got:
                if not got:
                    return RunResult(STOPPED, "the graph-write lock stayed busy: run the rollback again")
                restored = paper_studies.restore_paper_links(driver, db, graph_dir)
            journal.append("undo", "done", part="graph", **restored)
            counts["graph"] = restored

        if pub_rows and "pubs" not in st.undo_parts:
            journal.append("undo", "intent", part="pubs", samples=len(pub_rows))
            prefix = f"batch:studies:{plan.run_id}:undo:pubs" + ("" if investigation is None else f":{investigation}")
            restored = backfill.restore_publication_text(pub_rows, enqueue_prefix=prefix)
            journal.append("undo", "done", part="pubs", restored=len(restored["restored"]),
                           changed_since=restored["changed_since"])
            counts["publications"] = restored

        for unit in reversed(committed):
            journal.append("undo", "intent", part="unit", unit=unit.unit)
            with apply_mod._connection() as conn:
                report = links.undo_link_unit(conn, unit.unit, st.units[unit.unit], journal, run_id=plan.run_id)
            journal.append("undo", "done", part="unit", unit=unit.unit, deleted=report["deleted"],
                           reinserted=report["reinserted"], not_deleted_changed=report["not_deleted_changed"],
                           not_reinserted=report["not_reinserted"],
                           project_pairs_deleted=report["project_pairs_deleted"],
                           project_pairs_gone=report["project_pairs_gone"])
            if not report["outbox_in_transaction"]:   # chunked as in its transaction (links.outbox_rows)
                for key, part in links.outbox_rows(links.undo_key(plan.run_id, unit.unit), report["sample_ids"]):
                    hooks.enqueue("samples", key, part)
            counts["units"].append({"unit": unit.unit, **report})

        map_rows = [row for row in (st.map_rows or []) if row[1] in set(clone_ids)]
        if map_rows and "map" not in st.undo_parts:
            journal.append("undo", "intent", part="map", rows=map_rows)
            removed = mapping.delete_clone_mappings(map_rows)
            hooks.enqueue("assay_map", "*")
            journal.append("undo", "done", part="map", **removed)

        for kind, seek_ids in (("clone", clone_ids), ("study", study_ids)):
            for seek_id in seek_ids:
                in_use = (session.assay_link_count(seek_id) if kind == "clone"
                          else session.study_assay_count(seek_id))
                if in_use:
                    counts["not_deleted"].append(["assay" if kind == "clone" else "study", seek_id, "not empty"])
                    journal.append("undo", "done", part=kind, seek_id=seek_id, deleted=False, reason="not empty")
                    continue
                ok, status = (session.delete_assay(seek_id) if kind == "clone" else session.delete_study(seek_id))
                journal.append("undo", "done", part=kind, seek_id=seek_id, deleted=ok, status=status)
                if not ok:
                    counts["not_deleted"].append(["assay" if kind == "clone" else "study", seek_id, status])
        hooks.enqueue("isa", "*")

        for chunk in _batches(ids, apply_mod.SYNC_CALL_IDS):
            result = targeted.sync_samples(driver, db, chunk, run_dir=str(graph_dir), apply_label_changes=True)
            if result.get("status") != targeted.OK:
                return RunResult(STOPPED, f"sync_samples answered {result.get('status')}: run the rollback again",
                                 counts)
        journal.append("undo", "done", part="sync", samples=len(ids))
        journal.append("undo", "done", part="run", investigation=investigation, not_deleted=counts["not_deleted"])
        message = "rollback done"
        if counts["not_deleted"]:
            message += (f"; SEEK kept {len(counts['not_deleted'])} object(s): delete them in SEEK's UI once empty: "
                        f"{counts['not_deleted']}")
        return RunResult(DONE, message, counts)
