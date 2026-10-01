"""plan_study_moves (tool spec section 6): what a run will do, decided from one snapshot. It writes nothing.

Pure given the reader (``snapshot.SnapshotReader``; the suite's ``FakeReader``). Sorted reads, sorted output: the
same snapshot and input give the same plan.

1. Targets (6.1). A target is refused whole, every one of its samples reported with the reason, when its
   investigation has no bucket or several; it is the bucket; a new target's title is already a study of its
   investigation or of another; an existing target is missing or sits in another investigation; its title is longer
   than SEEK's column; SEEK's next study id is not above every graph ``Study.id`` (a new target only, T24).
2. Samples (6.2). From ``assay_assets``: a sample in no assay, a sample with an assay in another investigation's
   study that it shares no project with (a misfiling, ``cross_investigation``), a sample whose source assay has no
   internal-assay mapping, a sample sharing no project with the target's investigation: each is skipped whole for
   that target. A membership in another investigation's study is a share when the sample shares a project with that
   investigation: it is ignored (neither a source nor removed) and the target is warned (``shared_elsewhere``). A
   sample's source assays are its assays in the bucket; a sample in none of them but in another study of the
   investigation is copied from there, never removed; a sample in no assay but the target study's is ``no_change``.
3. Clones (6.3). Per target and source assay A: in an existing target, the one assay with A's title and the same set
   of internal assay ids is reused (several refuse the target); otherwise A is cloned. The payload is built from
   ``GET /assays/A`` (ontology fields keep only their uri; the study is the target, filled at apply; samples, data
   files, documents, models and publications dropped) and validated as SEEK's proxy would. A clone to create gets a
   placeholder id above every SEEK assay id, for the label preview only.
4. Links (6.4). Per source assay A: the movers are the targets' samples whose source assay is A; each goes into its
   target's clone with its direction in A (1 where A holds none); each parent of a mover that is a member of A goes
   in with direction 1 and stays in A. A member stays in A when it is not a mover or one of its children in A stays
   (a fixpoint); the others leave A, each in the last unit, in apply order, that moves it. A parent that shares no
   project with the investigation skips its child (``parent_project_mismatch``). One unit per target, ordered by
   investigation and key, each with the digest its source assays must have just before it runs.
5. Publications (6.5): one row per sample the run's units insert, movers and parents, across the whole run: every
   DOI of the targets that touch it (with its PMID, blank where none), in unit order, a DOI compared case-insensitively.
   A target with a PMID and no DOI writes nothing and is warned about.
6. The graph (6.6): each unit's sync ids; the stored labels of every edge incident to them (read only), classed twice,
   against today's MySQL and against the planned memberships with each new clone mapped as its source (and given its
   placeholder id); the move's own changes and the differences already pending are listed apart. An edge the move
   itself would clear refuses the plan (``PlannerDefect``). A graph-only target lists its paper's links to retire.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.graph_sync import labels
from nextseek_api.graph_sync.sources import declared_lineage
from nextseek_api.models import AssayCreateRequest, StudyCreateRequest
from nextseek_api.studies.buckets import NO_BUCKET, SEVERAL_BUCKETS, is_bucket_title, title_key  # noqa: F401
from nextseek_api.studies.models import (PLAN_VERSION, AssociationSet, ClonePlan, GraphPlan, LabelChange, LinkInsert,
                                         LinkRemoval, LinkUnit, PaperLinks, PlanWarning, PublicationRow, Skip,
                                         StudyAction, StudyMovePlan, StudyTarget, TargetPlan)

STUDY_TITLE_MAX = 255        # provisional: the tool spec's section 10, check 6
ASSAY_TITLE_MAX = 255
TARGET_STUDY_REF = "__target_study__"

SAMPLE_IN_NO_ASSAY = "sample_in_no_assay"
CROSS_INVESTIGATION = "cross_investigation"
SOURCE_ASSAY_UNMAPPED = "source_assay_unmapped"
PROJECT_MISMATCH = "project_mismatch"
PARENT_PROJECT_MISMATCH = "parent_project_mismatch"
NO_CHANGE = "no_change"

STUDY_EXISTS = "study_exists"
STUDY_TITLE_IN_OTHER_INVESTIGATION = "study_title_in_other_investigation"
STUDY_NOT_IN_INVESTIGATION = "study_not_in_investigation"
SEEK_STUDY_NOT_FOUND = "seek_study_id_not_found"
TARGET_IS_BUCKET = "target_is_bucket"
TITLE_TOO_LONG = "title_too_long"
SOURCE_ASSAYS_SHARE_TITLE = "source_assays_share_title"
TARGET_ASSAY_AMBIGUOUS = "target_assay_ambiguous"
CLONE_PAYLOAD_INVALID = "clone_payload_invalid"
STUDY_PAYLOAD_INVALID = "study_payload_invalid"
SEEK_STUDY_ID_NOT_ABOVE_GRAPH = "seek_study_id_not_above_graph"

SOURCE_ASSAY_SEVERAL_MAPPINGS = "source_assay_several_mappings"
SHARED_ELSEWHERE = "shared_elsewhere"
DESCRIPTION_DIFFERS = "description_differs"
PMID_WITHOUT_DOI = "pmid_without_doi"


class PlannerDefect(Exception):
    """The move itself would clear an edge's label: a defect of this planner, never a state to apply (6.6)."""

    def __init__(self, edges):
        self.edges = edges
        super().__init__(f"{len(edges)} edge(s) would lose their label through the move")


# --- helpers shared with apply --------------------------------------------------------------------------------

def code_sha() -> str:
    """sha256 over the studies package's source (tests left out): apply refuses a plan another code made."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "tests" not in p.relative_to(root).parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def parent_tokens(raw) -> list[str]:
    """A sample's parent tokens from its ``json_metadata`` text (batch upload's rule); [] when not a JSON object."""
    if not raw:
        return []
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return list(collect_parent_tokens(meta)) if isinstance(meta, dict) else []


def unit_digest(rows, tokens) -> str:
    """sha256 over a unit's source assays' Sample rows ``(assay_id, sample_id, direction)`` and each member's parent
    tokens; order-free. The planner computes it after the earlier units' planned changes; the link unit reads it
    again under its row locks (7.2)."""
    payload = {"rows": sorted([int(a), int(s), -1 if d is None else int(d)] for a, s, d in rows),
               "tokens": sorted([int(s), sorted(t)] for s, t in tokens.items())}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _drop_none(values: dict) -> dict:
    return {k: v for k, v in values.items() if v is not None}


def _uri(value) -> Optional[dict]:
    return {"uri": value["uri"]} if isinstance(value, dict) and value.get("uri") else None


def clone_payload(rep: dict) -> dict:
    """The POST /assays payload for a clone of the assay ``rep`` represents (tool spec T7, 6.3)."""
    data = rep.get("data") or {}
    attrs = data.get("attributes") or {}
    rels = data.get("relationships") or {}

    def refs(name):
        items = [i for i in ((rels.get(name) or {}).get("data") or []) if isinstance(i, dict) and "id" in i]
        return {"data": [{"id": str(i["id"]), "type": i["type"]} for i in items]} if items else None

    tags = [t for t in (attrs.get("tags") or []) if isinstance(t, str)] or None
    attributes = _drop_none({"title": attrs.get("title"), "description": attrs.get("description"),
                             "assay_class": {"key": (attrs.get("assay_class") or {}).get("key")},
                             "assay_type": _uri(attrs.get("assay_type")),
                             "technology_type": _uri(attrs.get("technology_type")), "tags": tags,
                             "policy": attrs.get("policy"), "other_creators": attrs.get("other_creators")})
    relationships = _drop_none({"study": {"data": {"id": TARGET_STUDY_REF, "type": "studies"}},
                                "sops": refs("sops"), "organisms": refs("organisms"), "creators": refs("creators")})
    request = AssayCreateRequest.model_validate(
        {"data": {"type": "assays", "attributes": attributes, "relationships": relationships}})
    return request.to_seek_payload()


def study_payload(target: StudyTarget, bucket_rep: dict) -> dict:
    """The POST /studies payload of a new target: its title and description, the bucket's policy (T6)."""
    policy = (((bucket_rep or {}).get("data") or {}).get("attributes") or {}).get("policy")
    request = StudyCreateRequest.model_validate({"data": {
        "type": "studies",
        "attributes": _drop_none({"title": target.title, "description": target.description, "policy": policy}),
        "relationships": {"investigation": {"data": {"id": str(target.investigation_id),
                                                     "type": "investigations"}}}}})
    return request.to_seek_payload()


def fill_study(payload: dict, study_id: int) -> dict:
    """A copy of a clone payload with the target study's SEEK id in place of ``TARGET_STUDY_REF``."""
    out = copy.deepcopy(payload)
    out["data"]["relationships"]["study"]["data"]["id"] = str(study_id)
    return out


# --- the snapshot the steps share -----------------------------------------------------------------------------

class _Snapshot:
    def __init__(self, reader):
        self.reader = reader
        self.buckets = reader.buckets()
        self.studies = reader.studies()
        self.studies_by_id = {s.id: s for s in self.studies}
        self.next_study_id = reader.next_study_id()
        self.graph_max_study_id = reader.graph_max_study_id()
        self._assays: dict = {}
        self._mapping: dict = {}
        self._inv_projects: dict = {}

    def assays(self, ids) -> dict:
        missing = sorted({int(i) for i in ids} - set(self._assays))
        if missing:
            self._assays.update(self.reader.assays(missing))
        return {i: self._assays[i] for i in ids if i in self._assays}

    def mapping(self, ids) -> dict:
        missing = sorted({int(i) for i in ids} - set(self._mapping))
        if missing:
            self._mapping.update(self.reader.mapping_rows(missing))
            for i in missing:
                self._mapping.setdefault(i, [])
        return {i: self._mapping[i] for i in ids}

    def inv_projects(self, inv: int) -> set:
        if inv not in self._inv_projects:
            self._inv_projects.update(self.reader.investigation_projects([inv]))
            self._inv_projects.setdefault(inv, set())
        return self._inv_projects[inv]

    def investigation_of_assay(self, assay_id: int) -> Optional[int]:
        row = self._assays.get(assay_id)
        study = self.studies_by_id.get(row.study_id) if row else None
        return study.investigation_id if study else None


@dataclass
class _Work:
    """One accepted target while it is planned."""

    t: StudyTarget
    action: str
    study_id: Optional[int]
    bucket: int
    sources: dict = field(default_factory=dict)          # sample id -> its source assay ids
    copy: set = field(default_factory=set)                # samples whose sources are copy sources
    no_change: set = field(default_factory=set)
    clones: dict = field(default_factory=dict)            # source assay id -> ClonePlan
    shared_elsewhere: set = field(default_factory=set)    # samples with a membership shared into another investigation
    existing_assay_ids: list = field(default_factory=list)
    payload: Optional[dict] = None


def _skip(skipped: list, key: str, sample_ids, reason: str, detail: str = "") -> None:
    skipped.extend(Skip(target_key=key, sample_id=s, reason=reason, detail=detail) for s in sorted(sample_ids))


def _target_refusal(t: StudyTarget, snap: _Snapshot) -> tuple[Optional[str], str]:
    inv = t.investigation_id
    refusal = snap.buckets.refusal(inv)
    if refusal:
        return refusal, ""
    bucket = snap.buckets.bucket_of(inv)
    if t.seek_study_id is not None:
        study = snap.studies_by_id.get(t.seek_study_id)
        if study is None:
            return SEEK_STUDY_NOT_FOUND, str(t.seek_study_id)
        if study.id == bucket or is_bucket_title(study.title):
            return TARGET_IS_BUCKET, ""
        if study.investigation_id != inv:
            return STUDY_NOT_IN_INVESTIGATION, f"study {study.id} is in investigation {study.investigation_id}"
        return None, ""
    if is_bucket_title(t.title):
        return TARGET_IS_BUCKET, ""
    same = [s for s in snap.studies if title_key(s.title) == title_key(t.title)]
    if any(s.investigation_id == inv for s in same):
        return STUDY_EXISTS, f"studies {[s.id for s in same if s.investigation_id == inv]}"
    if same:
        return STUDY_TITLE_IN_OTHER_INVESTIGATION, f"studies {[s.id for s in same]}"
    if len(t.title) > STUDY_TITLE_MAX:
        return TITLE_TOO_LONG, f"{len(t.title)} characters"
    if snap.graph_max_study_id is not None and snap.next_study_id <= snap.graph_max_study_id:
        return SEEK_STUDY_ID_NOT_ABOVE_GRAPH, (f"next study id {snap.next_study_id}, graph Study.id up to "
                                               f"{snap.graph_max_study_id}")
    return None, ""


def misfiled_assays(member, target_investigation: int, sample_projects, snap: _Snapshot) -> tuple[list, list]:
    """``(misfiled, shared)``: the sample's assays (``member``) whose study sits in another investigation than
    ``target_investigation``, split by whether the sample shares a project with that investigation (a share) or not (a
    misfiling, as is an assay whose investigation is unknown). Both sorted. A known limit, accepted: when one of the
    sample's projects is linked to both investigations, a misfiling reads as a share."""
    misfiled, shared = [], []
    for a in sorted(member):
        inv = snap.investigation_of_assay(a)
        if inv == target_investigation:
            continue
        if inv is not None and set(sample_projects) & snap.inv_projects(inv):
            shared.append(a)
        else:
            misfiled.append(a)
    return misfiled, shared


def _decide_samples(targets, snap: _Snapshot, skipped: list, warnings: list) -> list[_Work]:
    """Steps 1 and 2 of the module docstring."""
    reader = snap.reader
    ordered = sorted(targets, key=lambda t: (t.investigation_id, t.key))
    all_samples = sorted({s for t in ordered for s in t.sample_ids})
    members_of = reader.memberships(all_samples) if all_samples else {}
    snap.assays(sorted({a for m in members_of.values() for a in m}))
    projects = reader.sample_projects(all_samples) if all_samples else {}
    works: list[_Work] = []
    for t in ordered:
        refusal, detail = _target_refusal(t, snap)
        if refusal:
            _skip(skipped, t.key, t.sample_ids, refusal, detail)
            continue
        bucket = snap.buckets.bucket_of(t.investigation_id)
        w = _Work(t=t, action="existing" if t.seek_study_id is not None else "create", study_id=t.seek_study_id,
                  bucket=bucket)
        for s in t.sample_ids:
            member = members_of.get(s, {})
            if not member:
                _skip(skipped, t.key, [s], SAMPLE_IN_NO_ASSAY)
                continue
            misfiled, shared = misfiled_assays(member, t.investigation_id, projects.get(s, set()), snap)
            if misfiled:
                _skip(skipped, t.key, [s], CROSS_INVESTIGATION, f"assays {misfiled}")
                continue
            if shared:
                w.shared_elsewhere.add(s)
                member = {a: d for a, d in member.items() if a not in shared}
                if not member:
                    _skip(skipped, t.key, [s], SAMPLE_IN_NO_ASSAY, f"only in shared assays {shared}")
                    continue
            in_bucket = sorted(a for a in member if snap.assays([a])[a].study_id == bucket)
            if in_bucket:
                w.sources[s] = in_bucket
                continue
            other = sorted(a for a in member if snap.assays([a])[a].study_id not in (bucket, w.study_id))
            if not other:
                w.no_change.add(s)
                continue
            w.sources[s] = other
            w.copy.add(s)
        mapping = snap.mapping(sorted({a for src in w.sources.values() for a in src}))
        for s in sorted(w.sources):
            unmapped = [a for a in w.sources[s] if not mapping[a]]
            if unmapped:
                _skip(skipped, t.key, [s], SOURCE_ASSAY_UNMAPPED, f"assays {unmapped}")
            elif not (projects.get(s, set()) & snap.inv_projects(t.investigation_id)):
                _skip(skipped, t.key, [s], PROJECT_MISMATCH)
            else:
                continue
            del w.sources[s]
            w.copy.discard(s)
        if w.shared_elsewhere:
            warnings.append(PlanWarning(code=SHARED_ELSEWHERE, target_key=t.key,
                                        detail=f"{len(w.shared_elsewhere)} samples"))
        works.append(w)
    return works


def _decide_clones(works: list[_Work], snap: _Snapshot, skipped: list, warnings: list) -> list[_Work]:
    """Step 3 of the module docstring. Returns the targets still accepted."""
    reader = snap.reader
    existing_ids = sorted({w.study_id for w in works if w.study_id is not None})
    target_assays = reader.study_assays(existing_ids) if existing_ids else {}
    for rows in target_assays.values():
        snap.assays([r.id for r in rows])
    placeholder = reader.max_assay_id()
    warned: set = set()
    accepted: list[_Work] = []
    for w in works:
        used = sorted({a for src in w.sources.values() for a in src})
        rows = target_assays.get(w.study_id, []) if w.study_id is not None else []
        mapping = snap.mapping(used + [r.id for r in rows])
        assays = snap.assays(used)
        refusal, detail = None, ""
        groups: dict = defaultdict(list)
        for a in used:
            groups[(title_key(assays[a].title), tuple(sorted(set(mapping[a]))))].append(a)
        shared = [ids for ids in groups.values() if len(ids) > 1]
        if shared:
            refusal, detail = SOURCE_ASSAYS_SHARE_TITLE, f"assays {shared}"
        clones: dict = {}
        for a in used:
            if refusal:
                break
            if len(assays[a].title or "") > ASSAY_TITLE_MAX:
                refusal, detail = TITLE_TOO_LONG, f"assay {a}"
                break
            if len(mapping[a]) > 1 and a not in warned:
                warned.add(a)
                warnings.append(PlanWarning(code=SOURCE_ASSAY_SEVERAL_MAPPINGS, target_key=w.t.key, assay_id=a,
                                            detail=f"internal assays {mapping[a]}"))
            reuse = [r.id for r in rows if title_key(r.title) == title_key(assays[a].title)
                     and set(mapping[r.id]) == set(mapping[a])]
            if len(reuse) > 1:
                refusal, detail = TARGET_ASSAY_AMBIGUOUS, f"assays {reuse} for source {a}"
                break
            if reuse:
                clones[a] = ClonePlan(source_assay_id=a, title=assays[a].title, internal_assay_ids=mapping[a],
                                      action="reuse", seek_assay_id=reuse[0])
                continue
            try:
                payload = clone_payload(reader.assay_representation(a))
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                refusal, detail = CLONE_PAYLOAD_INVALID, f"assay {a}: {str(exc)[:300]}"
                break
            placeholder += 1
            clones[a] = ClonePlan(source_assay_id=a, title=assays[a].title, internal_assay_ids=mapping[a],
                                  action="create", payload=payload, placeholder_id=placeholder)
        if refusal is None and w.action == "create":
            try:
                w.payload = study_payload(w.t, reader.study_representation(w.bucket))
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                refusal, detail = STUDY_PAYLOAD_INVALID, str(exc)[:300]
        if refusal:
            _skip(skipped, w.t.key, set(w.sources) | w.no_change, refusal, detail)
            continue
        if w.action == "existing":
            study = snap.studies_by_id[w.study_id]
            if w.t.description and (w.t.description or "").strip() != (study.description or "").strip():
                warnings.append(PlanWarning(code=DESCRIPTION_DIFFERS, target_key=w.t.key,
                                            detail="the target's description differs from the study's; it is not "
                                                   "written"))
        w.clones = clones
        w.existing_assay_ids = [r.id for r in rows]
        accepted.append(w)
    return accepted


def _paper_id(key: str) -> Optional[int]:
    return int(key.split(":", 1)[1]) if key.startswith("graph_only:") else None


def _target_plan(w: _Work) -> TargetPlan:
    t = w.t
    study = (StudyAction(action="create", payload=w.payload) if w.action == "create"
             else StudyAction(action="existing", seek_study_id=w.study_id))
    return TargetPlan(key=t.key, investigation_id=t.investigation_id, title=t.title, description=t.description,
                      doi=t.doi, pmid=t.pmid, paper_id=_paper_id(t.key), study=study,
                      existing_assay_ids=w.existing_assay_ids, clones=[w.clones[a] for a in sorted(w.clones)])


@dataclass
class _Lineage:
    rows: dict        # assay id -> [(sample id, direction)], every Sample row
    members: dict     # assay id -> {sample id: direction of its first row}
    parents: dict     # (assay id, child id) -> parents that are members of that assay
    children: dict    # (assay id, parent id) -> children that are members of that assay
    tokens: dict      # sample id -> parent tokens


def _lineage(reader, assay_ids) -> _Lineage:
    rows = reader.assay_rows(assay_ids) if assay_ids else {}
    members: dict = {}
    for a in assay_ids:
        members[a] = {}
        for s, d in rows.get(a, []):
            members[a].setdefault(s, d)
    samples = sorted({s for m in members.values() for s in m})
    sample_rows = reader.sample_rows(samples) if samples else {}
    tokens = {s: parent_tokens((sample_rows.get(s) or {}).get("json_metadata")) for s in samples}
    uids = sorted({t for found in tokens.values() for t in found if UID_RE.match(t)})
    index = reader.uuid_index(uids) if uids else {}
    declared: dict = defaultdict(set)
    for child, parent in declared_lineage([sample_rows[s] for s in samples if s in sample_rows], index):
        declared[child].add(parent)
    parents: dict = defaultdict(set)
    children: dict = defaultdict(set)
    for a, m in members.items():
        for c in m:
            for par in declared.get(c, ()):
                if par in m and par != c:
                    parents[(a, c)].add(par)
                    children[(a, par)].add(c)
    return _Lineage(rows={a: list(rows.get(a, [])) for a in assay_ids}, members=members, parents=dict(parents),
                    children=dict(children), tokens=tokens)


def _parent_check(works: list, lin: _Lineage, snap: _Snapshot, skipped: list) -> None:
    needed = sorted({par for w in works for s, src in w.sources.items() for a in src
                     for par in lin.parents.get((a, s), ())})
    projects = snap.reader.sample_projects(needed) if needed else {}
    for w in works:
        allowed = snap.inv_projects(w.t.investigation_id)
        for s in sorted(w.sources):
            bad = sorted({par for a in w.sources[s] for par in lin.parents.get((a, s), ())
                          if not (projects.get(par, set()) & allowed)})
            if bad:
                _skip(skipped, w.t.key, [s], PARENT_PROJECT_MISMATCH, f"parents {bad}")
                del w.sources[s]
                w.copy.discard(s)


def _direction(value) -> int:
    return 1 if value is None else int(value)


def _plan_links(works: list, lin: _Lineage, snap: _Snapshot) -> tuple[list, list]:
    """Step 4 of the module docstring. Returns the units and the bucket assays left with no member."""
    reused = sorted({c.seek_assay_id for w in works for c in w.clones.values() if c.action == "reuse"})
    reused_rows = snap.reader.assay_rows(reused) if reused else {}
    present = {a: {s for s, _d in rows} for a, rows in reused_rows.items()}

    movers: dict = defaultdict(set)
    for i, w in enumerate(works):
        for s, src in w.sources.items():
            for a in src:
                movers[(i, a)].add(s)

    inserts: dict = defaultdict(list)
    for i, w in enumerate(works):
        for a in sorted({a for src in w.sources.values() for a in src}):
            clone = w.clones[a]
            here = present.get(clone.seek_assay_id, set()) if clone.action == "reuse" else set()
            ms = movers[(i, a)]
            planned: set = set()
            for s in sorted(ms):
                if s not in here:
                    inserts[i].append(LinkInsert(target_key=w.t.key, source_assay_id=a, sample_id=s,
                                                 direction=_direction(lin.members[a].get(s)), role="mover"))
                    planned.add(s)
            for s in sorted(ms):
                for par in sorted(lin.parents.get((a, s), ())):
                    if par in ms or par in here or par in planned:
                        continue
                    inserts[i].append(LinkInsert(target_key=w.t.key, source_assay_id=a, sample_id=par, direction=1,
                                                 role="parent"))
                    planned.add(par)

    removals: dict = defaultdict(list)
    removed: set = set()
    bucket_assays = sorted({a for w in works for s, src in w.sources.items() if s not in w.copy for a in src})
    for a in bucket_assays:
        moving = {s for (i, aa), ss in movers.items() if aa == a for s in ss if s not in works[i].copy}
        stays = {m for m in lin.members[a] if m not in moving}
        grew = True
        while grew:
            grew = False
            for m in sorted(moving - stays):
                if lin.children.get((a, m), set()) & stays:
                    stays.add(m)
                    grew = True
        for s in sorted(moving - stays):
            last = max(i for i in range(len(works)) if s in movers.get((i, a), set()) and s not in works[i].copy)
            removals[last].append((a, s))
            removed.add(s)

    for i, w in enumerate(works):
        inserted = {x.sample_id for x in inserts[i] if x.role == "mover"}
        w.no_change.update(s for s in w.sources if s not in inserted and s not in removed)

    state = {a: list(rows) for a, rows in lin.rows.items()}
    for a, rows in reused_rows.items():
        state.setdefault(a, list(rows))
    units: list = []
    for i, w in enumerate(works):
        ins, rem = inserts[i], sorted(removals[i])
        if not ins and not rem:
            continue
        src = sorted({x.source_assay_id for x in ins} | {a for a, _s in rem})
        rows = [(a, s, d) for a in src for s, d in state.get(a, [])]
        digest = unit_digest(rows, {s: lin.tokens.get(s, []) for _a, s, _d in rows})
        moved = {x.sample_id for x in ins if x.role == "mover"} | {s for _a, s in rem}
        added = {x.sample_id for x in ins if x.role == "parent"}
        kids = {c for a in src for m in moved for c in lin.children.get((a, m), ())}
        units.append(LinkUnit(unit=len(units) + 1, target_key=w.t.key, investigation_id=w.t.investigation_id,
                              source_assay_ids=src, digest=digest, inserts=ins,
                              removals=[LinkRemoval(assay_id=a, sample_id=s) for a, s in rem],
                              sync_ids=sorted(moved | added | kids)))
        for a, s in rem:
            state[a] = [(x, d) for x, d in state.get(a, []) if x != s]
        for x in ins:
            clone = w.clones[x.source_assay_id]
            rows_of = state.get(clone.seek_assay_id) if clone.action == "reuse" else None
            if rows_of is not None and all(y != x.sample_id for y, _d in rows_of):
                rows_of.append((x.sample_id, x.direction))
    empty = sorted(a for a in bucket_assays if not state.get(a))
    return units, empty


TARGET_REASONS = frozenset({NO_BUCKET, SEVERAL_BUCKETS, STUDY_EXISTS, STUDY_TITLE_IN_OTHER_INVESTIGATION,
                            STUDY_NOT_IN_INVESTIGATION, SEEK_STUDY_NOT_FOUND, TARGET_IS_BUCKET, TITLE_TOO_LONG,
                            SOURCE_ASSAYS_SHARE_TITLE, TARGET_ASSAY_AMBIGUOUS, CLONE_PAYLOAD_INVALID,
                            STUDY_PAYLOAD_INVALID, SEEK_STUDY_ID_NOT_ABOVE_GRAPH})


def _publications(units: list, works: list, warnings: list) -> list:
    by_key = {w.t.key: w.t for w in works}
    per_sample: dict = {}
    inv_of: dict = {}
    warned: set = set()
    for u in units:
        t = by_key[u.target_key]
        if not t.doi:
            if t.pmid and t.key not in warned:
                warned.add(t.key)
                warnings.append(PlanWarning(code=PMID_WITHOUT_DOI, target_key=t.key,
                                            detail="a PMID with no DOI is not written"))
            continue
        doi, pmid = t.doi.strip(), (t.pmid or "").strip()
        for s in sorted({x.sample_id for x in u.inserts}):
            papers = per_sample.setdefault(s, [])
            if doi.casefold() not in {d.casefold() for d, _p in papers}:
                papers.append((doi, pmid))
            inv_of[s] = t.investigation_id
    return [PublicationRow(sample_id=s, investigation_id=inv_of[s], dois=[d for d, _p in papers],
                           pmids=[x for _d, x in papers]) for s, papers in sorted(per_sample.items())]


def _label_changes(edges, now: dict, after: dict, assay_map: dict, sops: dict, metas: dict) -> tuple[list, list]:
    index = labels.sop_title_index(sops)
    move, pending, defects = [], [], []
    for e in edges:
        child, parent, stored = e["child_id"], e["parent_id"], e["stored"]
        if not (isinstance(child, int) and isinstance(parent, int)):
            continue
        protocol = labels.resolve_protocol(labels.protocol_value_of((metas.get(child) or {}).get("json_metadata")),
                                           sops, index)
        before = labels.edge_labels(now.get(child), now.get(parent), assay_map, protocol)
        later = labels.edge_labels(after.get(child), after.get(parent), assay_map, protocol)
        before_class, after_class = labels.classify(stored, before), labels.classify(stored, later)
        if after_class in (labels.EQUAL, labels.NEW):
            continue
        change = LabelChange(child_id=child, parent_id=parent, before_class=before_class, after_class=after_class,
                             properties=labels.differences(stored, later), stored=dict(stored), after=later)
        if after_class == labels.CLEARED and before_class != labels.CLEARED:
            defects.append(change)
        (move if before_class == labels.EQUAL else pending).append(change)
    if defects:
        raise PlannerDefect(defects)
    return move, pending


def _graph_plan(units: list, works: list, reader, lin: _Lineage) -> GraphPlan:
    sync_ids = {u.unit: u.sync_ids for u in units}
    # A no_change sample (a replan after apply, or a paper finished later) is synced by the graph step too, with the
    # parents its sources give it, so the step still writes what is pending for it and rebuilds its study links.
    no_change_ids = {w.t.key: sorted(w.no_change | {par for s in w.no_change for a in w.sources.get(s, [])
                                                    for par in lin.parents.get((a, s), ())})
                     for w in works if w.no_change}
    paper_links = [PaperLinks(paper_id=_paper_id(w.t.key), target_key=w.t.key,
                              sample_ids=sorted(set(w.sources) | w.no_change))
                   for w in works if _paper_id(w.t.key) is not None and (w.sources or w.no_change)]
    ids = sorted({s for found in sync_ids.values() for s in found} | {s for found in no_change_ids.values()
                                                                        for s in found})
    if not ids:
        return GraphPlan(sync_ids=sync_ids, no_change_sync_ids=no_change_ids, paper_links=paper_links)
    edges = reader.stored_edges(ids)
    endpoints = sorted({v for e in edges for v in (e["child_id"], e["parent_id"]) if isinstance(v, int)})
    now = {s: set(m) for s, m in reader.memberships(endpoints).items()} if endpoints else {}
    after = {s: set(v) for s, v in now.items()}
    clone_ids = {(w.t.key, a): (c.seek_assay_id if c.action == "reuse" else c.placeholder_id)
                 for w in works for a, c in w.clones.items()}
    for u in units:
        for x in u.inserts:
            after.setdefault(x.sample_id, set()).add(clone_ids[(x.target_key, x.source_assay_id)])
        for r in u.removals:
            after.setdefault(r.sample_id, set()).discard(r.assay_id)
    assay_map = dict(reader.assay_map())
    for w in works:
        for a, c in w.clones.items():
            if c.action == "create":
                assay_map[c.placeholder_id] = assay_map.get(a, (None, c.title))
    children = sorted({e["child_id"] for e in edges if isinstance(e["child_id"], int)})
    metas = reader.sample_rows(children) if children else {}
    move, pending = _label_changes(edges, now, after, assay_map, reader.sops(), metas)
    return GraphPlan(sync_ids=sync_ids, no_change_sync_ids=no_change_ids, move=move, pending=pending,
                     paper_links=paper_links)


def _summary(targets: list, units: list, publications: list, skipped: list, no_change: dict, graph: GraphPlan,
             empty: list, inv_of_key: dict) -> dict:
    per_inv: dict = defaultdict(lambda: {"targets": 0, "units": 0, "inserts": 0, "removals": 0, "skipped": 0})
    for t in targets:
        per_inv[str(t.investigation_id)]["targets"] += 1
    for u in units:
        row = per_inv[str(u.investigation_id)]
        row["units"] += 1
        row["inserts"] += len(u.inserts)
        row["removals"] += len(u.removals)
    for s in skipped:
        per_inv[str(inv_of_key.get(s.target_key, "?"))]["skipped"] += 1
    return {
        "targets": len(targets),
        "targets_refused": len({s.target_key for s in skipped if s.reason in TARGET_REASONS}),
        "studies_to_create": sum(1 for t in targets if t.study.action == "create"),
        "clones_to_create": sum(1 for t in targets for c in t.clones if c.action == "create"),
        "clones_reused": sum(1 for t in targets for c in t.clones if c.action == "reuse"),
        "units": len(units),
        "inserts": sum(len(u.inserts) for u in units),
        "removals": sum(len(u.removals) for u in units),
        "publication_rows": len(publications),
        "skipped_by_reason": dict(sorted(Counter(s.reason for s in skipped).items())),
        "no_change": sum(len(v) for v in no_change.values()),
        "label_changes": {"move": dict(sorted(Counter(c.after_class for c in graph.move).items())),
                          "pending": dict(sorted(Counter(c.after_class for c in graph.pending).items()))},
        "paper_links": sum(len(x.sample_ids) for x in graph.paper_links),
        "empty_bucket_assays": len(empty),
        "per_investigation": {k: dict(per_inv[k]) for k in sorted(per_inv)},
    }


def plan_study_moves(associations: AssociationSet, reader, *, run_id: str, now: Optional[str] = None) -> StudyMovePlan:
    snap = _Snapshot(reader)
    skipped: list = []
    warnings: list = []
    works = _decide_samples(associations.targets, snap, skipped, warnings)
    lin = _lineage(reader, sorted({a for w in works for src in w.sources.values() for a in src}))
    _parent_check(works, lin, snap, skipped)
    works = _decide_clones(works, snap, skipped, warnings)
    units, empty = _plan_links(works, lin, snap)
    publications = _publications(units, works, warnings)
    graph = _graph_plan(units, works, reader, lin)
    no_change = {w.t.key: sorted(w.no_change) for w in works if w.no_change}
    targets = [_target_plan(w) for w in works]
    inv_of_key = {t.key: t.investigation_id for t in associations.targets}
    return StudyMovePlan(
        plan_version=PLAN_VERSION, created_at=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        code_sha=code_sha(), associations_sha256=associations.sha256(), run_id=run_id,
        buckets=dict(snap.buckets.by_investigation), seek_next_study_id=snap.next_study_id,
        graph_max_study_id=snap.graph_max_study_id, targets=targets, units=units, publications=publications,
        graph=graph, skipped=skipped, no_change=no_change, empty_bucket_assays=empty, warnings=warnings,
        summary=_summary(targets, units, publications, skipped, no_change, graph, empty, inv_of_key))
