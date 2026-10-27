"""Typed artifacts of a paper reingest run (spec §4.1): paper.json and evidence.json.

Pure: pydantic only. Every model forbids unknown keys, so an artifact written by one
version and read by another fails loudly instead of dropping a field.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from NessieAI.ns.paper.dates import parse_partial_date

Repo = Literal["geo", "ena", "pride", "zenodo", "figshare", "sdr"]


def _partial_iso(value):
    """A date, or reduced-precision ISO text ('2024', '2024-02', '2024-02-03') exactly as written."""
    if isinstance(value, (date, str)):
        iso = parse_partial_date(value)
        if iso is not None and (isinstance(value, date) or iso == value):
            return iso
    raise ValueError("a date must be YYYY, YYYY-MM or YYYY-MM-DD")


#: A date at the precision its source gives: a year or a month is never padded into a day.
PartialDate = Annotated[str, BeforeValidator(_partial_iso)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperInput(_Strict):
    kind: Literal["pmid", "pmcid", "doi", "file"]
    value: str


class Accession(_Strict):
    repo: Repo
    id: str


class Author(_Strict):
    """One author; collective is a consortium or group (Europe PMC's collectiveName), never a person's lab."""
    name: str
    affiliation: str = ""
    collective: bool = False


class PaperRef(_Strict):
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str = ""
    journal: str = ""
    published: PartialDate | None = None
    authors: list[Author] = []
    open_access: bool = False
    source_file: str | None = None


class Paragraph(_Strict):
    id: str
    text: str


class Section(_Strict):
    slug: str
    title: str
    paragraphs: list[Paragraph]


class SupplementRef(_Strict):
    name: str
    caption: str = ""


class PaperText(_Strict):
    sections: list[Section] = []
    captions: list[Paragraph] = []
    supplements: list[SupplementRef] = []

    def has(self, slug: str) -> bool:
        return any(s.slug == slug and s.paragraphs for s in self.sections)

    def all_text(self) -> list[str]:
        return [p.text for s in self.sections for p in s.paragraphs] + [c.text for c in self.captions]


class PaperArtifact(_Strict):
    """paper.json: what the launch check found."""
    ref: PaperRef
    text: PaperText
    text_source: Literal["europepmc", "pdf"]
    mode: Literal["new", "update"]
    existing_project_ids: list[int] = []


class FileRef(_Strict):
    name: str
    url: str
    size: int | None = None
    checksum: str | None = None
    checksum_type: Literal["md5", "sha1"] | None = None


class DepositRecord(_Strict):
    """One record a repository returned, its fields kept verbatim."""
    repo: Repo
    record_id: str
    kind: Literal["project", "sample", "run", "file"]
    title: str = ""
    fields: dict[str, str] = {}
    files: list[FileRef] = []
    links: list[str] = []
    submitted: date | None = None


class EvidenceItem(_Strict):
    id: str
    text: str
    source: str


class SkeletonRow(_Strict):
    """One data row the deposits prove exists (spec §4.1); the agent may map or exclude it, never invent one."""
    row_key: str
    repo: Repo
    record_id: str
    kind: Literal["raw", "analysis", "file"]
    title: str = ""
    files: list[FileRef] = []
    evidence_ids: list[str] = []
    submitted: date | None = None


GapKind = Literal["supplement_unavailable", "supplement_unparsed", "deposit_unsupported",
                  "deposit_unavailable", "deposit_empty"]


class Gap(_Strict):
    kind: GapKind
    detail: str


class EvidenceArtifact(_Strict):
    """evidence.json: everything quotable, the deposits, the skeleton rows and the gaps.

    Item IDs are unique and every skeleton row cites only items that exist: a later stage quotes by ID, so a
    duplicate or a dangling ID fails here, loudly, instead of being dropped or resolved to the wrong text."""
    paper: PaperRef
    items: list[EvidenceItem]
    deposits: list[DepositRecord]
    skeleton: list[SkeletonRow]
    gaps: list[Gap]

    @model_validator(mode="after")
    def _ids_resolve(self) -> "EvidenceArtifact":
        counts = Counter(i.id for i in self.items)
        duplicates = sorted(i for i, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"evidence item IDs are not unique: {', '.join(duplicates[:5])}")
        for row in self.skeleton:
            dangling = [e for e in row.evidence_ids if e not in counts]
            if dangling:
                raise ValueError(f"skeleton row {row.row_key} cites evidence IDs with no item: {', '.join(dangling[:5])}")
        return self
