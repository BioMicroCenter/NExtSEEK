"""The studies tool's data: the input every source becomes (tool spec 4.2) and the plan (6.7).

pydantic, ``extra='forbid'``. JSON written by ``canonical_json`` (sorted keys, one space indent, a final newline), so a
file's sha256 is stable and a saved ``associations.json`` can be planned again anywhere.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

PLAN_VERSION = 1   # the share's fields joined version 1 with defaults: no plan existed before them


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_file(cls, path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# --- the input ---------------------------------------------------------------------------------------------------

class StudyTarget(_Model):
    key: str
    investigation_id: int
    seek_study_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    sample_ids: list[int]
    provenance: dict[str, list[str]] = {}

    @field_validator("sample_ids")
    @classmethod
    def _unique_sorted(cls, value):
        return sorted({int(v) for v in value})

    @field_validator("title")
    @classmethod
    def _has_title(cls, value):
        if not value.strip():
            raise ValueError("a target needs a title")
        return value


class Unmatched(_Model):
    reason: str
    target_key: str
    submitted: str
    provenance: list[str] = []


class AssociationSet(_Model):
    source: Literal["sheet", "dev_export", "graph_only", "replay"]
    source_ref: str
    created_at: str
    targets: list[StudyTarget]
    unmatched: list[Unmatched] = []
    notes: dict[str, int] = {}

    def core(self) -> dict:
        """What the three sources must agree on for one logical input: each target's investigation, study, title,
        description, DOI, PMID and samples, and each unmatched row's reason and submitted value. Not the keys, the
        provenance, the time or the source's own reference, which name the source by design."""
        targets = sorted(({"investigation_id": t.investigation_id, "seek_study_id": t.seek_study_id,
                           "title": t.title, "description": t.description, "doi": t.doi, "pmid": t.pmid,
                           "sample_ids": t.sample_ids} for t in self.targets),
                         key=lambda d: (d["investigation_id"], d["title"]))
        return {"targets": targets, "unmatched": sorted([u.reason, u.submitted] for u in self.unmatched)}

    def core_json(self) -> str:
        return canonical_json(self.core())


class ShareInput(_Model):
    """A share's request (tool spec 16.2): samples of one project linked into an existing study of another."""

    sample_uids: list[str]
    source_project_id: int
    destination_project_id: int
    destination_study_id: int
    created_at: str


# --- the plan ----------------------------------------------------------------------------------------------------

class Skip(_Model):
    target_key: str
    sample_id: Optional[int] = None
    reason: str
    detail: str = ""


class PlanWarning(_Model):
    code: str
    target_key: Optional[str] = None
    assay_id: Optional[int] = None
    detail: str = ""


class StudyAction(_Model):
    action: Literal["create", "existing"]
    seek_study_id: Optional[int] = None
    payload: Optional[dict] = None


class ClonePlan(_Model):
    source_assay_id: int
    title: str
    internal_assay_ids: list[int]
    action: Literal["create", "reuse"]
    seek_assay_id: Optional[int] = None
    payload: Optional[dict] = None
    placeholder_id: Optional[int] = None
    group_source_assay_ids: list[int] = []       # a share's group (every source assay of one title and mapping)
    policy_from_study: Optional[int] = None      # a share's clone takes this SEEK study's policy (T33)
    policy: Optional[dict] = None                # that policy, read from SEEK's tables when the share is planned


class TargetPlan(_Model):
    key: str
    investigation_id: int
    title: str
    description: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    paper_id: Optional[int] = None
    study: StudyAction
    existing_assay_ids: list[int] = []
    clones: list[ClonePlan] = []


class LinkInsert(_Model):
    target_key: str
    source_assay_id: int
    sample_id: int
    direction: int
    role: Literal["mover", "parent"]


class LinkRemoval(_Model):
    assay_id: int
    sample_id: int


class ProjectInsert(_Model):
    project_id: int
    sample_id: int
    role: Literal["mover", "parent"]


class LinkUnit(_Model):
    unit: int
    target_key: str
    investigation_id: int
    source_assay_ids: list[int]
    digest: str
    inserts: list[LinkInsert]
    removals: list[LinkRemoval]
    sync_ids: list[int]
    project_inserts: list[ProjectInsert] = []    # a share's projects_samples rows (tool spec 16.2 step 4)


class PublicationRow(_Model):
    sample_id: int
    investigation_id: int
    dois: list[str]
    pmids: list[str]


class LabelChange(_Model):
    child_id: int
    parent_id: int
    before_class: str
    after_class: str
    properties: list[str]
    stored: dict
    after: dict


class PaperLinks(_Model):
    paper_id: int
    target_key: str
    sample_ids: list[int]


class GraphPlan(_Model):
    sync_ids: dict[int, list[int]] = {}
    no_change_sync_ids: dict[str, list[int]] = {}
    move: list[LabelChange] = []
    pending: list[LabelChange] = []
    paper_links: list[PaperLinks] = []


class ShareParent(_Model):
    """A direct parent a share's sample brings from one of its source assays (tool spec 16.7): its link and project
    row when planned, or skipped whole when it sits outside the share's source project (its projects listed)."""

    sample_id: int
    uid: Optional[str] = None
    child_id: int
    child_uid: Optional[str] = None
    source_assay_id: int
    link: bool
    project: bool
    outside_source_project: bool = False
    projects: list[int] = []


class StudyMovePlan(_Model):
    plan_version: int
    created_at: str
    code_sha: str
    associations_sha256: str
    run_id: str
    buckets: dict[int, int]
    seek_next_study_id: int
    graph_max_study_id: Optional[int] = None
    targets: list[TargetPlan]
    units: list[LinkUnit]
    publications: list[PublicationRow]
    graph: GraphPlan
    skipped: list[Skip]
    no_change: dict[str, list[int]]
    empty_bucket_assays: list[int]
    warnings: list[PlanWarning]
    summary: dict
    mode: Literal["move", "share"] = "move"
    share: Optional[ShareInput] = None
    share_parents: list[ShareParent] = []        # every parent of a share, for its run directory's parents.csv

    def creates_study(self) -> bool:
        return any(t.study.action == "create" for t in self.targets)
