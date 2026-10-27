"""Spec §7, rule R-UID-DATE: the UID each row will get, `<TYPE>-<YYMMDD><LAB>-<N>`.

The counter continues past the highest N the export shows for the same type, stamp and lab (spec delta 5), so a
shared deposit date never collides. The export is the one stage 2 pulled; piece 3 re-pulls before it builds and
piece 4 before it writes, and both re-run this plan.
"""
from __future__ import annotations

import re
from datetime import date

from NessieAI.ns.paper.curation_models import ContextArtifact, CurationArtifact, Decision, HeldRow, PlannedUid
from NessieAI.ns.paper.models import EvidenceArtifact

UID_RE = re.compile(r"^(?P<type>.+)-(?P<stamp>\d{6})(?P<lab>[A-Z]{3})-(?P<n>\d+)(?:-PUB)?$")
STAMP_DECISION = "D-UID-STAMP"


def used_counters(uids) -> dict[tuple[str, str, str], int]:
    out: dict[tuple[str, str, str], int] = {}
    for uid in uids:
        hit = UID_RE.match(str(uid or "").strip())
        if hit:
            key = (hit["type"], hit["stamp"], hit["lab"])
            out[key] = max(out.get(key, 0), int(hit["n"]))
    return out


def _publication_day(published: str | None) -> tuple[date | None, str]:
    if not published:
        return None, "the paper has no deposit date and no publication date"
    parts = published.split("-")
    if len(parts) == 3:
        return date.fromisoformat(published), f"the publication date {published}"
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1), f"the publication month {published}, on its 1st"
    return None, f"the paper has no deposit date and its publication date gives only the year {published}"


def plan_uids(curation: CurationArtifact, evidence: EvidenceArtifact,
              context: ContextArtifact) -> tuple[list[PlannedUid], list[Decision], list[HeldRow]]:
    skeleton = {r.row_key: r for r in evidence.skeleton}
    nodes = {n.node_id: n for n in curation.tree}
    deposit_days = [skeleton[r.row_key].submitted for r in curation.rows
                    if r.row_key in skeleton and skeleton[r.row_key].submitted]
    decisions: list[Decision] = []
    if deposit_days:
        earliest, upstream_basis = min(deposit_days), "the paper's earliest deposit date"
    else:
        earliest, upstream_basis = _publication_day(evidence.paper.published)
        if earliest is not None:
            decisions.append(Decision(id=STAMP_DECISION, topic="UID stamp", basis="rule", refs=["R-UID-DATE"],
                                      choice=f"No deposit date: every new UID is stamped with {upstream_basis}."))
    counters = used_counters(context.export.uids)
    lab = context.lab_code
    plan: list[PlannedUid] = []
    held: list[HeldRow] = []
    for row in curation.rows:
        sample_type = nodes[row.node_id].sample_type
        if row.reuse_uid:
            plan.append(PlannedUid(row_key=row.row_key, sample_type=sample_type, uid=row.reuse_uid, stamp="",
                                   lab="", reused=True, basis="an existing record, kept"))
            continue
        deposit = skeleton.get(row.row_key)
        if deposit is not None and deposit.submitted:
            day, basis = deposit.submitted, "its deposit's date"
        else:
            day, basis = earliest, upstream_basis
        if day is None:
            held.append(HeldRow(row_key=row.row_key, reason=f"no UID stamp: {upstream_basis}"))
            continue
        if lab is None:
            held.append(HeldRow(row_key=row.row_key, reason="no UID lab code: the target project has no precedent"))
            continue
        key = (sample_type, day.strftime("%y%m%d"), lab)
        counters[key] = counters.get(key, 0) + 1
        plan.append(PlannedUid(row_key=row.row_key, sample_type=sample_type, uid=f"{key[0]}-{key[1]}{lab}-{counters[key]}",
                               stamp=key[1], lab=lab, basis=basis))
    return plan, decisions, held
