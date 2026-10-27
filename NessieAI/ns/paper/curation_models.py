"""The stage 2–4 artifacts (spec §4.1): context.json, curation.json and verified.json.

curation.json is the agent's output. Its shape is also checked, without pydantic, by curation_check.shape_findings,
because the container that writes it has no pydantic: keep the two in step.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Literal, Union

from pydantic import model_validator

from NessieAI.ns.paper.models import _Strict

#: The row-key prefix of a row the agent adds for a tier the deposits have no row for (MUS, TIS, CEL ...).
UPSTREAM_PREFIX = "node:"


# ---- context.json -----------------------------------------------------------------------------------------------

class ProjectCandidate(_Strict):
    project_id: int
    title: str
    score: int
    basis: list[str] = []


class ProjectChoice(_Strict):
    """The inferred target project; the review always shows it as a decision to confirm (spec §2 ruling 3)."""
    chosen: ProjectCandidate | None = None
    candidates: list[ProjectCandidate] = []


class AttributeDef(_Strict):
    title: str
    required: bool = False


class SampleTypeDef(_Strict):
    code: str
    name: str = ""
    clade: str = ""
    parents: list[str] = []
    attributes: list[AttributeDef] = []


class ExistingRecord(_Strict):
    uid: str
    sample_type: str
    matched_on: Literal["doi", "accession", "filename", "name"]
    value: str
    skeleton_row: str | None = None


class PrecedentStudy(_Strict):
    study_id: int
    title: str
    assays: list[str] = []


class ExportSummary(_Strict):
    project_ids: list[int]
    pulled_at: datetime
    samples: int
    uids: list[str]


class RulebookVersion(_Strict):
    file_sha256: str
    approved_max_id: int | None = None


class ContextArtifact(_Strict):
    mode: Literal["new", "update"]
    project: ProjectChoice
    sample_types: list[SampleTypeDef]
    house_assays: list[str]
    existing: list[ExistingRecord] = []
    precedent: list[PrecedentStudy] = []
    lab_code: str | None = None
    lab_basis: str = ""
    export: ExportSummary
    rulebook: RulebookVersion


# ---- curation.json ----------------------------------------------------------------------------------------------

class Value(_Strict):
    value: str
    evidence: list[str] = []
    rule: str | None = None
    decision: str | None = None


class Blank(_Strict):
    blank: Literal["not_in_sources", "conflict", "pi_only", "held_for_admin"]


Cell = Union[Value, Blank]


class ParentLink(_Strict):
    node_id: str
    assay: str


class TreeNode(_Strict):
    node_id: str
    sample_type: str
    label: str
    count: int | None = None
    skeleton_rows: list[str] = []
    parents: list[ParentLink] = []
    evidence: list[str] = []


class Row(_Strict):
    row_key: str
    node_id: str
    status: Literal["new", "update", "unchanged", "orphaned"] = "new"
    reuse_uid: str | None = None
    reuse_evidence: list[str] = []
    values: dict[str, Cell] = {}


class Excluded(_Strict):
    row_key: str
    reason: str
    rule: str | None = None


class Protocol(_Strict):
    assay: str
    title: str
    paragraphs: list[str]


class Decision(_Strict):
    id: str
    topic: str
    choice: str
    basis: Literal["rule", "precedent", "evidence"]
    refs: list[str] = []


class Note(_Strict):
    topic: str
    detail: str
    refs: list[str] = []


class CurationArtifact(_Strict):
    tree: list[TreeNode]
    rows: list[Row]
    excluded: list[Excluded] = []
    protocols: list[Protocol] = []
    decisions: list[Decision] = []
    gaps: list[Note] = []
    errata: list[Note] = []

    @model_validator(mode="after")
    def _unique_keys(self) -> "CurationArtifact":
        for name, keys in (("node_id", [n.node_id for n in self.tree]), ("row_key", [r.row_key for r in self.rows]),
                           ("decision id", [d.id for d in self.decisions])):
            repeated = sorted(k for k, n in Counter(keys).items() if n > 1)
            if repeated:
                raise ValueError(f"{name} repeated: {', '.join(repeated[:5])}")
        return self


# ---- verified.json ----------------------------------------------------------------------------------------------

class Finding(_Strict):
    code: str
    severity: Literal["error", "warn"]
    detail: str
    row_key: str | None = None
    node_id: str | None = None
    attribute: str | None = None
    rule: str | None = None


class HeldRow(_Strict):
    row_key: str
    reason: str


class PlannedUid(_Strict):
    row_key: str
    sample_type: str
    uid: str
    stamp: str
    lab: str
    reused: bool = False
    basis: str


class VerifiedArtifact(_Strict):
    """What stage 4 let through: the curation with failing values blanked, the rows held, the UID plan."""
    curation: CurationArtifact
    held: list[HeldRow]
    gaps: list[Note]
    findings: list[Finding]
    uid_plan: list[PlannedUid]
    bounced: bool
