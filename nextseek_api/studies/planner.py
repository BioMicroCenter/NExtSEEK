"""plan_study_moves (tool spec section 6): what a run will do, decided from one snapshot. It writes nothing.

Pure given the reader (``snapshot.SnapshotReader``; the suite's ``FakeReader``). Sorted reads, sorted output: the
same snapshot and input give the same plan.

1. Targets (6.1). A target is refused whole, every one of its samples reported with the reason, when its
   investigation has no bucket or several; it is the bucket; a new target's title is already a study of its
   investigation or of another; an existing target is missing or sits in another investigation; its title is longer
   than SEEK's column; SEEK's next study id is not above every graph ``Study.id`` (a new target only, T24).
2. Samples (6.2). From ``assay_assets``: a sample in no assay, a sample with any assay in another investigation's
   study, a sample whose source assay has no internal-assay mapping, a sample sharing no project with the target's
   investigation: each is skipped whole for that target. A sample's source assays are its assays in the bucket; a
   sample in none of them but in another study of the investigation is copied from there, never removed; a sample in
   no assay but the target study's is ``no_change``.
3. Clones (6.3). Per target and source assay A: in an existing target, the one assay with A's title and the same set
   of internal assay ids is reused (several refuse the target); otherwise A is cloned. The payload is built from
   ``GET /assays/A`` (ontology fields keep only their uri; the study is the target, filled at apply; samples, data
   files, documents, models and publications dropped) and validated as SEEK's proxy would. A clone to create gets a
   placeholder id above every SEEK assay id, for the label preview only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from nextseek_api.batch_upload.helpers import collect_parent_tokens
from nextseek_api.models import AssayCreateRequest, StudyCreateRequest
from nextseek_api.studies.buckets import NO_BUCKET, SEVERAL_BUCKETS, is_bucket_title, title_key  # noqa: F401
from nextseek_api.studies.models import (PLAN_VERSION, AssociationSet, ClonePlan, GraphPlan, PlanWarning, Skip,
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


def _decide_samples(targets, snap: _Snapshot, skipped: list) -> list[_Work]:
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
            elsewhere = sorted(a for a in member if snap.investigation_of_assay(a) != t.investigation_id)
            if elsewhere:
                _skip(skipped, t.key, [s], CROSS_INVESTIGATION, f"assays {elsewhere}")
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


def plan_study_moves(associations: AssociationSet, reader, *, run_id: str, now: Optional[str] = None) -> StudyMovePlan:
    snap = _Snapshot(reader)
    skipped: list = []
    warnings: list = []
    works = _decide_samples(associations.targets, snap, skipped)
    works = _decide_clones(works, snap, skipped, warnings)
    no_change = {w.t.key: sorted(w.no_change) for w in works if w.no_change}
    return StudyMovePlan(
        plan_version=PLAN_VERSION, created_at=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        code_sha=code_sha(), associations_sha256=associations.sha256(), run_id=run_id,
        buckets=dict(snap.buckets.by_investigation), seek_next_study_id=snap.next_study_id,
        graph_max_study_id=snap.graph_max_study_id, targets=[_target_plan(w) for w in works], units=[],
        publications=[], graph=GraphPlan(), skipped=skipped, no_change=no_change, empty_bucket_assays=[],
        warnings=warnings, summary={})
