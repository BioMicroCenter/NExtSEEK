"""The run directory's files (tool spec 6.8) and a run's progress.

``associations.json`` (the core's input), ``plan.json``, ``plan.txt`` (what the operator reads before apply),
``unmatched.json`` and ``unmatched.csv`` (every row the adapter could not match and every sample the plan skipped, by
reason, investigation and study: the curators' worklist), then ``journal.jsonl`` and ``graph/`` once apply runs. The
directory holds a box's titles, sample ids and UIDs; it lives under ``LOG_DIR``, which git ignores, and stays on the
box.
"""
from __future__ import annotations

import csv
from pathlib import Path

from nextseek_api.studies.journal import JOURNAL_FILE, journal_state, read_journal
from nextseek_api.studies.models import AssociationSet, StudyMovePlan, canonical_json

ASSOCIATIONS_FILE = "associations.json"
PLAN_FILE = "plan.json"
PLAN_TEXT = "plan.txt"
UNMATCHED_JSON = "unmatched.json"
UNMATCHED_CSV = "unmatched.csv"
UNMATCHED_COLUMNS = ("reason", "target_key", "investigation_id", "study_title", "submitted", "sample_id",
                     "provenance", "detail")
EXAMPLES = 20


def unmatched_rows(plan: StudyMovePlan, associations: AssociationSet) -> list[dict]:
    about = {t.key: t for t in associations.targets}
    rows = []
    for u in associations.unmatched:
        t = about.get(u.target_key)
        rows.append({"reason": u.reason, "target_key": u.target_key,
                     "investigation_id": t.investigation_id if t else None,
                     "study_title": t.title if t else None, "submitted": u.submitted, "sample_id": None,
                     "provenance": "; ".join(u.provenance), "detail": ""})
    for s in plan.skipped:
        t = about.get(s.target_key)
        found = (t.provenance.get(str(s.sample_id)) if t and s.sample_id is not None else None) or []
        rows.append({"reason": s.reason, "target_key": s.target_key,
                     "investigation_id": t.investigation_id if t else None, "study_title": t.title if t else None,
                     "submitted": "; ".join(found), "sample_id": s.sample_id, "provenance": "; ".join(found),
                     "detail": s.detail})
    return sorted(rows, key=lambda r: (r["reason"], r["investigation_id"] or -1, r["study_title"] or "",
                                       str(r["sample_id"] or ""), r["submitted"]))


def render_plan_text(plan: StudyMovePlan) -> str:
    out = [f"Studies tool plan {plan.run_id} (created {plan.created_at}, plan version {plan.plan_version}, "
           f"code {plan.code_sha[:12]})",
           f"Buckets (investigation -> study): {plan.buckets}",
           f"SEEK next study id: {plan.seek_next_study_id}; graph Study.id up to: {plan.graph_max_study_id}", ""]
    units = {u.target_key: u for u in plan.units}
    out.append(f"Targets ({len(plan.targets)}):")
    for t in plan.targets:
        study = "create" if t.study.action == "create" else f"existing {t.study.seek_study_id}"
        out.append(f"  {t.key}  investigation {t.investigation_id}  {t.title!r}  study: {study}  "
                   f"DOI {t.doi or '-'}  PMID {t.pmid or '-'}")
        for c in t.clones:
            what = (f"create (placeholder {c.placeholder_id})" if c.action == "create"
                    else f"reuse {c.seek_assay_id}")
            out.append(f"    clone: {c.source_assay_id} -> {what}  {c.title!r}  internal {c.internal_assay_ids}")
        u = units.get(t.key)
        if u:
            movers = sum(1 for x in u.inserts if x.role == "mover")
            out.append(f"    unit {u.unit}: {len(u.inserts)} inserts ({movers} movers, {len(u.inserts) - movers} "
                       f"parents), {len(u.removals)} removals, {len(u.sync_ids)} samples to sync")
        else:
            out.append("    no link changes")
    out += ["", "Label changes the graph step will write (approve with --approve-label-changes):",
            "  (renamed and protocol_filled edges are written by the loop without approval; listed for information)"]
    for name, changes in (("move", plan.graph.move), ("pending", plan.graph.pending)):
        out.append(f"  {name}: {plan.summary.get('label_changes', {}).get(name, {})}")
        for c in changes[:EXAMPLES]:
            out.append(f"    {c.child_id} -> {c.parent_id}  {c.after_class}  {','.join(c.properties)}")
    out += ["", "Skipped samples by reason:"]
    out += [f"  {reason}: {n}" for reason, n in plan.summary.get("skipped_by_reason", {}).items()] or ["  none"]
    out.append(f"No change: {plan.summary.get('no_change', 0)} samples")
    out += ["", "Warnings:"]
    out += [f"  {w.code}  {w.target_key or ''}  {w.assay_id or ''}  {w.detail}" for w in plan.warnings] or ["  none"]
    out.append(f"Empty bucket assays after the run (a curator deletes them in SEEK): {plan.empty_bucket_assays}")
    out.append(f"Publications: {len(plan.publications)} rows")
    for links in plan.graph.paper_links:
        out.append(f"Paper links to retire in the graph step: paper {links.paper_id}: {len(links.sample_ids)} samples")
    return "\n".join(out) + "\n"


def write_plan_files(run_dir, plan: StudyMovePlan, associations: AssociationSet) -> list[Path]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = unmatched_rows(plan, associations)
    files = {ASSOCIATIONS_FILE: associations.to_json(), PLAN_FILE: plan.to_json(), PLAN_TEXT: render_plan_text(plan),
             UNMATCHED_JSON: canonical_json(rows)}
    written = []
    for name, text in files.items():
        (run_dir / name).write_text(text, encoding="utf-8")
        written.append(run_dir / name)
    with (run_dir / UNMATCHED_CSV).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNMATCHED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row[k] is None else row[k] for k in UNMATCHED_COLUMNS})
    written.append(run_dir / UNMATCHED_CSV)
    return written


def progress(run_dir) -> dict:
    run_dir = Path(run_dir)
    plan = StudyMovePlan.from_file(run_dir / PLAN_FILE)
    lines, bad = read_journal(run_dir / JOURNAL_FILE)
    st = journal_state(lines)
    creates = [t.key for t in plan.targets if t.study.action == "create"]
    clones = [(t.key, c.source_assay_id) for t in plan.targets for c in t.clones if c.action == "create"]
    return {
        "run_id": plan.run_id, "summary": plan.summary,
        "studies": {"planned": len(creates),
                    "done": sum(1 for k in creates if (st.studies.get(k) or {}).get("seek_id"))},
        "clones": {"planned": len(clones), "done": sum(1 for k in clones if (st.clones.get(k) or {}).get("seek_id"))},
        "mapping_done": st.map_rows is not None,
        "units": {"planned": len(plan.units), "committed": sum(1 for u in plan.units
                                                               if (st.units.get(u.unit) or {}).get("committed")),
                  "undone": len(st.undone_units)},
        "publications_done": st.pubs_done, "apply_done": st.apply_done, "graph_done": st.graph_done,
        "undone": st.undone, "journal_unreadable_lines": bad,
    }


# --- the share mode (tool spec 16.7) ------------------------------------------------------------------------------

SHARE_FILE = "share.json"


def share_summary(plan: StudyMovePlan, *, run_dir_name: str, plan_sha256: str) -> dict:
    """The GET answer's summary of a share's dry run: the plan's own summary, its sha and its run directory's name."""
    return {**plan.summary, "plan_sha256": plan_sha256, "run_dir": run_dir_name}


def render_share_text(plan: StudyMovePlan) -> str:
    s, share = plan.summary, plan.share
    out = [f"Studies tool share {plan.run_id} (created {plan.created_at}, code {plan.code_sha[:12]})",
           f"Share: project {share.source_project_id} -> project {share.destination_project_id}, "
           f"study {share.destination_study_id}; {len(share.sample_uids)} UIDs submitted", "", "Outcomes:"]
    out += [f"  {code}: {n}" for code, n in s.get("outcomes", {}).items()]
    out += ["", "Groups (source assays -> destination assay):"]
    for g in s.get("groups", []):
        what = f"reuse {g['destination_assay_id']}" if g["action"] == "reuse" else "create (destination's policy)"
        out.append(f"  {g['source_assay_ids']} {g['title']!r} internal {g['internal_assay_ids']} -> {what}")
    links = s.get("links", {})
    out += ["", f"Links to insert: {links.get('mover', 0)} movers, {links.get('parent', 0)} parents; "
                f"project rows to add: {s.get('project_rows', 0)}; parents brought: {s.get('parents_count', 0)}",
            f"Label changes needing approval after the drain: {s.get('label_changes_needing_approval') or 'none'}",
            "  (renamed and protocol_filled edges are written by the loop without approval)"]
    for unit in plan.units:
        out.append(f"Unit {unit.unit}: {len(unit.inserts)} inserts, {len(unit.project_inserts)} project rows, "
                   f"{len(unit.sync_ids)} samples to sync, digest {unit.digest[:12]}")
    if not plan.units:
        out.append("Nothing to apply.")
    return "\n".join(out) + "\n"


def write_share_run(run_dir, plan: StudyMovePlan, summary: dict) -> list[Path]:
    """A share's run directory: ``share.json`` (the request), ``plan.json``, ``plan.txt``, ``unmatched.json`` and
    ``unmatched.csv`` (every UID or sample not shared, with its reason)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"reason": s.reason, "target_key": s.target_key, "investigation_id": plan.targets[0].investigation_id,
             "study_title": plan.targets[0].title, "submitted": s.detail.split(":", 1)[0],
             "sample_id": s.sample_id, "provenance": "", "detail": s.detail} for s in plan.skipped]
    files = {SHARE_FILE: plan.share.to_json(), PLAN_FILE: plan.to_json(), PLAN_TEXT: render_share_text(plan),
             UNMATCHED_JSON: canonical_json(rows)}
    written = []
    for name, text in files.items():
        (run_dir / name).write_text(text, encoding="utf-8")
        written.append(run_dir / name)
    with (run_dir / UNMATCHED_CSV).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=UNMATCHED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row[k] is None else row[k] for k in UNMATCHED_COLUMNS})
    written.append(run_dir / UNMATCHED_CSV)
    return written
