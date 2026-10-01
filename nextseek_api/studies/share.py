"""The studies tool's share mode (tool spec section 16): samples of one project linked into an existing study of
another, planned as a one-unit ``StudyMovePlan`` (mode ``share``) that apply, the journal, the link unit and rollback
carry as they carry a move. ``plan_share`` reads, decides and writes nothing:

1. Whole-share refusals (``ShareRefused``): the two projects are one; either project or the destination study is not
   SEEK's; the study's investigation is not linked to the destination project.
2. The UIDs, through the registration resolver's count rule (the shared matching): one on no row or on several is
   listed, never guessed.
3. Per sample: ``not_in_source_project``; its source assays (its assays in a study of an investigation linked to the
   source project, less the destination study's), none being ``no_source_assay``; one with no internal-assay row
   makes it ``source_assay_unmapped``.
4. Groups by (title, internal-assay set): the destination study's one assay of a group is reused; none: one clone,
   made at apply with the destination study's policy, read here from SEEK's tables (none readable refuses the share,
   ``destination_policy_unreadable``); several: the group's samples are ``target_assay_ambiguous``.
5. Inserts: each sample into its groups' destination assays with its direction in the group's smallest source assay
   it is in; each direct parent that is a member of one of those source assays into that group's destination assay
   with direction 1 (a grandparent never); the destination project for every sample and parent lacking it. A sample
   with nothing to add is ``no_change``.
6. One unit, keyed ``share:<study id>``, whose digest covers the planned samples' rows only, and the label preview.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

from nextseek_api.graph_sync import labels
from nextseek_api.studies import planner
from nextseek_api.studies.buckets import title_key
from nextseek_api.studies.models import (PLAN_VERSION, ClonePlan, GraphPlan, LinkInsert, LinkUnit, ProjectInsert,
                                         ShareInput, Skip, StudyAction, StudyMovePlan, TargetPlan, canonical_json)
from nextseek_api.studies.sources.matching import SAMPLE_UID_NOT_FOUND, SAMPLE_UID_NOT_UNIQUE

MAX_SHARE_UIDS = 10_000
SAME_PROJECT = "same_project"
SOURCE_PROJECT_UNKNOWN = "source_project_unknown"
DESTINATION_PROJECT_UNKNOWN = "destination_project_unknown"
DESTINATION_STUDY_UNKNOWN = "destination_study_unknown"
DESTINATION_STUDY_NOT_IN_DESTINATION_PROJECT = "destination_study_not_in_destination_project"
DESTINATION_POLICY_UNREADABLE = "destination_policy_unreadable"
NOT_IN_SOURCE_PROJECT = "not_in_source_project"
NO_SOURCE_ASSAY = "no_source_assay"
SOURCE_ASSAY_UNMAPPED = planner.SOURCE_ASSAY_UNMAPPED
TARGET_ASSAY_AMBIGUOUS = planner.TARGET_ASSAY_AMBIGUOUS
NO_CHANGE = planner.NO_CHANGE
SHARED = "shared"
OUTCOMES = (SHARED, NO_CHANGE, SAMPLE_UID_NOT_FOUND, SAMPLE_UID_NOT_UNIQUE, NOT_IN_SOURCE_PROJECT, NO_SOURCE_ASSAY,
            SOURCE_ASSAY_UNMAPPED, TARGET_ASSAY_AMBIGUOUS)
EXAMPLES = 50


class ShareRefused(Exception):
    """A whole-share refusal: nothing is planned."""

    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def share_target_key(inp: ShareInput) -> str:
    return f"share:{inp.destination_study_id}"


def group_key(title, internal_ids) -> tuple[str, tuple[int, ...]]:
    return title_key(title), tuple(sorted({int(i) for i in internal_ids}))


def share_digest(rows, project_rows) -> str:
    """sha256 over a share unit's planned samples only: their sorted ``(assay_id, sample_id, direction)`` rows in the
    unit's assays and their sorted ``(project_id, sample_id)`` rows for the destination project (tool spec 16.6)."""
    payload = {"rows": sorted([int(a), int(s), -1 if d is None else int(d)] for a, s, d in rows),
               "projects": sorted([int(p), int(s)] for p, s in project_rows)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _refuse(inp: ShareInput, reader) -> tuple[dict, int]:
    """The studies by id and the destination study's investigation, after the whole-share refusals (step 1)."""
    p, q, d = inp.source_project_id, inp.destination_project_id, inp.destination_study_id
    if p == q:
        raise ShareRefused(SAME_PROJECT, f"source and destination are both project {p}")
    present = reader.project_ids_present([p, q])
    if p not in present:
        raise ShareRefused(SOURCE_PROJECT_UNKNOWN, f"project {p}")
    if q not in present:
        raise ShareRefused(DESTINATION_PROJECT_UNKNOWN, f"project {q}")
    studies = {s.id: s for s in reader.studies()}
    study = studies.get(d)
    if study is None:
        raise ShareRefused(DESTINATION_STUDY_UNKNOWN, f"study {d}")
    if study.investigation_id not in reader.project_investigations(q):
        raise ShareRefused(DESTINATION_STUDY_NOT_IN_DESTINATION_PROJECT,
                           f"study {d} is in investigation {study.investigation_id}, not linked to project {q}")
    return studies, study.investigation_id


def plan_share(inp: ShareInput, reader, *, run_id: str, now: Optional[str] = None) -> StudyMovePlan:
    studies, investigation = _refuse(inp, reader)
    p, q, d = inp.source_project_id, inp.destination_project_id, inp.destination_study_id
    key = share_target_key(inp)
    skipped: list = []
    uid_of: dict = {}

    def skip(reason: str, sample_id: Optional[int], uid: str, detail: str = "") -> None:
        skipped.append(Skip(target_key=key, sample_id=sample_id, reason=reason,
                            detail=uid + (f": {detail}" if detail else "")))

    # 2. the UIDs, COUNT not EXISTS
    uids = sorted({u.strip() for u in inp.sample_uids if u and u.strip()})
    counts = reader.uid_counts(uids) if uids else {}
    unique = [u for u in uids if counts.get(u, 0) == 1]
    ids_of = reader.sample_ids_for_uids(unique) if unique else {}
    for u in uids:
        if counts.get(u, 0) > 1:
            skip(SAMPLE_UID_NOT_UNIQUE, None, u)
        elif u not in ids_of:
            skip(SAMPLE_UID_NOT_FOUND, None, u)
    uid_of.update({sid: u for u, sid in ids_of.items()})
    samples = sorted(set(ids_of.values()))

    # 3. per sample: the source project and the source assays
    source_invs = reader.project_investigations(p)
    projects = reader.sample_projects(samples) if samples else {}
    members_of = reader.memberships(samples) if samples else {}
    assays = reader.assays(sorted({a for m in members_of.values() for a in m}))

    def in_source(a: int) -> bool:
        study = studies.get(assays[a].study_id) if a in assays else None
        return study is not None and study.id != d and study.investigation_id in source_invs

    sources: dict = {}
    for s in samples:
        if p not in projects.get(s, set()):
            skip(NOT_IN_SOURCE_PROJECT, s, uid_of[s])
            continue
        src = sorted(a for a in members_of.get(s, {}) if in_source(a))
        if not src:
            skip(NO_SOURCE_ASSAY, s, uid_of[s])
            continue
        sources[s] = src
    mapping = reader.mapping_rows(sorted({a for src in sources.values() for a in src})) if sources else {}
    for s in sorted(sources):
        unmapped = [a for a in sources[s] if not mapping.get(a)]
        if unmapped:
            skip(SOURCE_ASSAY_UNMAPPED, s, uid_of[s], f"assays {unmapped}")
            del sources[s]

    # 4. groups and their destination assays
    group_of = {a: group_key(assays[a].title, mapping[a]) for src in sources.values() for a in src}
    dest_rows = reader.study_assays([d]).get(d, [])
    dest_map = reader.mapping_rows([r.id for r in dest_rows]) if dest_rows else {}
    dest_by_group: dict = defaultdict(list)
    for r in dest_rows:
        dest_by_group[group_key(r.title, dest_map.get(r.id) or [])].append(r.id)
    for s in sorted(sources):
        bad = sorted(a for a in sources[s] if len(dest_by_group.get(group_of[a], [])) > 1)
        if bad:
            skip(TARGET_ASSAY_AMBIGUOUS, s, uid_of[s], f"source assays {bad}")
            del sources[s]
    group_sources: dict = defaultdict(set)
    for src in sources.values():
        for a in src:
            group_sources[group_of[a]].add(a)
    policy = None
    if any(not dest_by_group.get(g) for g in group_sources):
        policy = reader.study_policy(d)
        if policy is None:
            raise ShareRefused(DESTINATION_POLICY_UNREADABLE, f"study {d}: its policy could not be read from SEEK's "
                                                              "tables, so an assay made in it could not take it")
    placeholder = reader.max_assay_id()
    clones: dict = {}
    for g in sorted(group_sources):
        first = min(group_sources[g])
        common = dict(source_assay_id=first, title=assays[first].title, internal_assay_ids=mapping[first],
                      group_source_assay_ids=sorted(group_sources[g]))
        if dest_by_group.get(g):
            clones[g] = ClonePlan(action="reuse", seek_assay_id=dest_by_group[g][0], **common)
        else:
            placeholder += 1
            clones[g] = ClonePlan(action="create", placeholder_id=placeholder, policy_from_study=d, policy=policy,
                                  **common)

    # 5. inserts: movers, then direct parents in their source assay, then the destination project
    source_assays = sorted({a for ids in group_sources.values() for a in ids})
    lin = planner._lineage(reader, source_assays)
    reused = sorted(c.seek_assay_id for c in clones.values() if c.action == "reuse")
    present_rows = reader.assay_rows(reused) if reused else {}
    present = {a: {s for s, _d in rows} for a, rows in present_rows.items()}

    def dest_holds(clone: ClonePlan, sample: int) -> bool:
        return clone.action == "reuse" and sample in present.get(clone.seek_assay_id, set())

    movers: dict = {}            # (clone ref, sample) -> direction
    for s in sorted(sources):
        for g in sorted({group_of[a] for a in sources[s]}):
            first = min(a for a in sources[s] if group_of[a] == g)
            movers[(clones[g].source_assay_id, s)] = planner._direction(lin.members[first].get(s))
    parents: dict = {}           # (clone ref, parent) -> (child, source assay)
    for s in sorted(sources):
        for a in sources[s]:
            ref = clones[group_of[a]].source_assay_id
            for par in sorted(lin.parents.get((a, s), ())):
                if (ref, par) not in movers:
                    parents.setdefault((ref, par), (s, a))
    parent_ids = sorted({par for _ref, par in parents} - set(sources))
    if parent_ids:
        projects.update(reader.sample_projects(parent_ids))
    clone_by_ref = {c.source_assay_id: c for c in clones.values()}

    inserts: list = []
    brought: set = set()
    for (ref, s), direction in sorted(movers.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not dest_holds(clone_by_ref[ref], s):
            inserts.append(LinkInsert(target_key=key, source_assay_id=ref, sample_id=s, direction=direction,
                                      role="mover"))
            brought.add(s)
    parent_links: set = set()
    for (ref, par), (child, _a) in sorted(parents.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not dest_holds(clone_by_ref[ref], par):
            inserts.append(LinkInsert(target_key=key, source_assay_id=ref, sample_id=par, direction=1,
                                      role="parent"))
            brought.add(child)
            parent_links.add(par)
    project_inserts: list = []
    for s in sorted(sources):
        if q not in projects.get(s, set()):
            project_inserts.append(ProjectInsert(project_id=q, sample_id=s, role="mover"))
            brought.add(s)
    parent_projects: set = set()
    for par in parent_ids:
        if q not in projects.get(par, set()):
            project_inserts.append(ProjectInsert(project_id=q, sample_id=par, role="parent"))
            parent_projects.add(par)
    for (_ref, par), (child, _a) in parents.items():
        if par in parent_projects:
            brought.add(child)
    no_change = sorted(s for s in sources if s not in brought)
    shared = sorted(s for s in sources if s in brought)
    sync_ids = sorted(set(shared) | parent_links | parent_projects)

    # 6. the unit, its digest and the label preview
    target = TargetPlan(key=key, investigation_id=investigation, title=studies[d].title,
                        description=studies[d].description, study=StudyAction(action="existing", seek_study_id=d),
                        existing_assay_ids=[r.id for r in dest_rows], clones=[clones[g] for g in sorted(clones)])
    units: list = []
    graph = GraphPlan()
    if inserts or project_inserts:
        unit_assays = sorted(set(source_assays) | set(reused))
        wanted = set(sync_ids)
        rows = [(a, s, dr) for a, found in reader.assay_rows(unit_assays).items() for s, dr in found if s in wanted]
        project_rows = [(q, s) for s in sync_ids if q in projects.get(s, set())]
        units = [LinkUnit(unit=1, target_key=key, investigation_id=investigation, source_assay_ids=unit_assays,
                          digest=share_digest(rows, project_rows), inserts=inserts, removals=[],
                          project_inserts=project_inserts, sync_ids=sync_ids)]
        graph = _label_preview(units[0], clone_by_ref, reader)
    if not units:
        target = target.model_copy(update={"clones": []})
    every = sorted(set(samples) | set(parent_ids))
    rows_of = reader.sample_rows([i for i in every if i not in uid_of]) if every else {}
    uid_of.update({i: r["uuid"] for i, r in rows_of.items()})
    plan = StudyMovePlan(
        plan_version=PLAN_VERSION, created_at=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        code_sha=planner.code_sha(), associations_sha256=inp.sha256(), run_id=run_id, buckets={},
        seek_next_study_id=0, targets=[target], units=units, publications=[], graph=graph, skipped=skipped,
        no_change={key: no_change} if no_change else {}, empty_bucket_assays=[], warnings=[], summary={},
        mode="share", share=inp)
    summary = _summary(plan, uid_of, shared, parents)
    return plan.model_copy(update={"summary": summary})


def _label_preview(unit: LinkUnit, clone_by_ref: dict, reader) -> GraphPlan:
    """Tool spec 16.7 and 6.6: the stored labels of every edge the unit's samples touch, classed against today's
    MySQL and against the share's after-state (a new clone mapped as its group's smallest source assay)."""
    edges = reader.stored_edges(unit.sync_ids)
    endpoints = sorted({v for e in edges for v in (e["child_id"], e["parent_id"]) if isinstance(v, int)})
    now = {s: set(m) for s, m in reader.memberships(endpoints).items()} if endpoints else {}
    after = {s: set(v) for s, v in now.items()}
    assay_map = dict(reader.assay_map())
    for c in clone_by_ref.values():
        if c.action == "create":
            assay_map[c.placeholder_id] = assay_map.get(c.source_assay_id, (None, c.title))
    for x in unit.inserts:
        c = clone_by_ref[x.source_assay_id]
        after.setdefault(x.sample_id, set()).add(c.seek_assay_id if c.action == "reuse" else c.placeholder_id)
    children = sorted({e["child_id"] for e in edges if isinstance(e["child_id"], int)})
    metas = reader.sample_rows(children) if children else {}
    move, pending = planner._label_changes(edges, now, after, assay_map, reader.sops(), metas)
    return GraphPlan(sync_ids={unit.unit: unit.sync_ids}, move=move, pending=pending)


def _summary(plan: StudyMovePlan, uid_of: dict, shared: list, parents: dict) -> dict:
    """The dry run's summary (tool spec 16.7): what the GET answer shows and plan.txt prints."""
    by_reason: dict = defaultdict(list)
    for s in plan.skipped:
        by_reason[s.reason].append(s.detail.split(":", 1)[0])
    by_reason[SHARED] = [uid_of[s] for s in shared]
    by_reason[NO_CHANGE] = [uid_of[s] for s in plan.no_change.get(plan.targets[0].key, [])]
    unit = plan.units[0] if plan.units else None
    planned_parents = {(x.source_assay_id, x.sample_id) for x in (unit.inserts if unit else []) if x.role == "parent"}
    parent_projects = {x.sample_id for x in (unit.project_inserts if unit else []) if x.role == "parent"}
    listed = [{"uid": uid_of.get(par), "child_uid": uid_of.get(child), "source_assay_id": a,
               "link": (ref, par) in planned_parents, "project": par in parent_projects}
              for (ref, par), (child, a) in sorted(parents.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    policy = next((c.policy for c in plan.targets[0].clones if c.action == "create"), None)
    needing = Counter(c.after_class for c in plan.graph.move + plan.graph.pending
                      if c.after_class != labels.EQUAL and c.after_class not in labels.WRITABLE_WITHOUT_APPROVAL)
    inp = plan.share
    return {
        "share": {"source_project_id": inp.source_project_id, "destination_project_id": inp.destination_project_id,
                  "destination_study_id": inp.destination_study_id, "uids_submitted": len(inp.sample_uids)},
        "outcomes": {code: len(by_reason.get(code, [])) for code in OUTCOMES},
        "uids": {code: sorted(by_reason[code])[:EXAMPLES] for code in OUTCOMES if by_reason.get(code)},
        "groups": [{"source_assay_ids": c.group_source_assay_ids, "title": c.title,
                    "internal_assay_ids": sorted(set(c.internal_assay_ids)), "action": c.action,
                    "destination_assay_id": c.seek_assay_id} for c in plan.targets[0].clones],
        "clone_policy": policy,
        "links": {"mover": sum(1 for x in (unit.inserts if unit else []) if x.role == "mover"),
                  "parent": len(planned_parents)},
        "project_rows": len(unit.project_inserts) if unit else 0,
        "parents_count": len(listed),
        "parents": listed[:EXAMPLES],
        "label_changes_needing_approval": dict(sorted(needing.items())),
    }
