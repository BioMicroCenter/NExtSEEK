# NessieAI/ns/reingest/assay_plan.py
"""Which study assay one reingest analysis row is filed under (spec 2026-10-20, 3.3).

Pure: no Django, no I/O. The lookups come in as callables, so granular.py feeds
it the real ones and the tests feed it plain functions. A row ends with ASSAY
ids, with an assay the upload will create, or with a finding saying why not:
never silently empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from NessieAI.ns.reingest_qa import (
    ASSAY_AMBIGUOUS, ASSAY_NO_PARENTS, ASSAY_NOT_DECLARED, ASSAY_TO_CREATE,
    ASSAY_UNRESOLVED_TEST_ONLY, HARD, HOUSE_ASSAY_MISSING, PARENT_IN_NO_STUDY, SOFT, Finding,
)

DATA_LINKED = " - Data Linked"


@dataclass
class RowAssayPlan:
    assay_ids: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    to_create: list = field(default_factory=list)
    parent_assays: list = field(default_factory=list)
    studies: frozenset = frozenset()


def plan_row(*, sample_type: str, row_index: int, house_name, parents: list[str],
             test_only: bool, house_title, study_assays) -> RowAssayPlan:
    """``house_title(name)`` is the house assay's title or None;
    ``study_assays(parent_uids, title)`` is a StudyAssayPlan. Either may raise
    on an outage, and that propagates: a database blink never reads as no assay."""
    plan = RowAssayPlan()

    def _finding(code, severity, **detail):
        plan.findings.append(Finding(code=code, severity=severity, sample_type=sample_type,
                                     row_index=row_index, detail=detail))

    if not house_name:
        _finding(ASSAY_NOT_DECLARED, HARD)
        return plan
    title = house_title(house_name)
    if title is None:
        _finding(HOUSE_ASSAY_MISSING, HARD, assay=house_name)
        return plan
    if not parents:
        if test_only:
            plan.assay_ids.append(f"{title} (unresolved: no parents)")
            _finding(ASSAY_UNRESOLVED_TEST_ONLY, SOFT, assay=title)
        else:
            # No parents, no study: a real row would reach NExtSEEK outside every study.
            _finding(ASSAY_NO_PARENTS, HARD, assay=title)
        return plan
    placed = study_assays(parents, title)
    if placed.parents_without_study:
        _finding(PARENT_IN_NO_STUDY, HARD, parents=list(placed.parents_without_study))
        return plan
    plan.studies = frozenset(placed.studies)
    for study_id, ids in placed.studies.items():
        study_title = placed.study_titles.get(study_id, "")
        if len(ids) > 1:
            _finding(ASSAY_AMBIGUOUS, HARD, study_id=study_id, study_title=study_title,
                     assay_ids=list(ids))
            continue
        assay_id = ids[0] if ids else None
        if assay_id is None:
            plan.to_create.append({
                "study_id": study_id, "study_title": study_title,
                "internal_assay_title": title, "assay_title": f"{title}{DATA_LINKED}",
                "sample_type": sample_type, "rows": [row_index]})
            _finding(ASSAY_TO_CREATE, SOFT, study_id=study_id, study_title=study_title,
                     assay_title=f"{title}{DATA_LINKED}")
        else:
            plan.assay_ids.append(assay_id)
        plan.parent_assays += [
            {"parent_uid": uid, "study_id": study_id, "internal_assay_title": title,
             "assay_id": assay_id}
            for uid in placed.parents_by_study.get(study_id, [])]
    return plan


def split_across_studies(plans: list[RowAssayPlan]) -> bool:
    """True when the rows of one workbook sit in different studies.

    The ASSAY sheet gives every row every assay it lists, so such a workbook
    would file each row under the other rows' studies too."""
    placed = {p.studies for p in plans if p.studies}
    return len(placed) > 1


def merge_to_create(entries: list[dict]) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for entry in entries:
        key = (entry["study_id"], entry["internal_assay_title"])
        if key in merged:
            merged[key]["rows"] = sorted(set(merged[key]["rows"]) | set(entry["rows"]))
        else:
            merged[key] = dict(entry, rows=sorted(entry["rows"]))
    return list(merged.values())


def merge_parent_assays(entries: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for entry in entries:
        key = (entry["parent_uid"], entry["study_id"], entry["internal_assay_title"])
        seen.setdefault(key, dict(entry))
    return list(seen.values())
