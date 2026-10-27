"""Stage 2, the engine half (spec §4): context.json from facts the host's seams hand in.

Nothing here reads a database: nextseek_api/paper_reingest/context_sources.py builds the seams. The project is
inferred and always shown in the review as a decision to confirm (spec §2 ruling 3); a tie or a weak score
chooses nothing, and the review asks.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import urlsplit

from NessieAI.ns.paper.curation_models import (ContextArtifact, ExistingRecord, ExportSummary, PrecedentStudy,
                                               ProjectCandidate, ProjectChoice, RulebookVersion, SampleTypeDef)
from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact, PaperRef
from NessieAI.ns.paper.uid_plan import UID_RE

MIN_SCORE = 10
_DOI_PREFIX = re.compile(r"^(?:doi:|https?://(?:dx\.)?doi\.org/)", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ProjectFacts:
    project_id: int
    title: str
    alternative_names: tuple[str, ...] = ()
    pi_text: str = ""
    members: tuple[tuple[str, str], ...] = ()
    institutions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportRow:
    uid: str
    sample_type: str
    metadata: dict


@dataclass
class ContextSeams:
    projects: Callable[[], list[ProjectFacts]]
    sample_types: Callable[[], list[SampleTypeDef]]
    house_assays: Callable[[], list[str]]
    export_rows: Callable[[list[int]], list[ExportRow]]
    precedent: Callable[[int], list[PrecedentStudy]]
    now: Callable[[], datetime]
    rulebook_sha256: str


def _surname(name: str) -> str:
    return (name or "").split()[0] if (name or "").split() else ""


def infer_project(ref: PaperRef, existing_project_ids: list[int], projects: list[ProjectFacts]) -> ProjectChoice:
    people = [a for a in ref.authors if not a.collective and _surname(a.name)]
    last = people[-1] if people else None
    scored = []
    for p in projects:
        score, basis = 0, []
        if p.project_id in existing_project_ids:
            score += 100
            basis.append("this paper's DOI is already on samples in the project")
        for author in people:
            surname = _surname(author.name)
            if re.search(rf"\b{re.escape(surname)}\b", p.pi_text or "", re.I):
                score += 15 if author is last else 10
                basis.append(f"author {author.name} is named as the project's PI")
            elif any(surname.casefold() == m_last.casefold() for _, m_last in p.members):
                score += 4
                basis.append(f"author {author.name} is a member of the project")
        hits = [i for i in p.institutions if i and any(i.casefold() in (a.affiliation or "").casefold() for a in people)]
        if hits:
            score += min(3, len(hits))
            basis.append(f"an author's affiliation names {hits[0]}")
        if score:
            scored.append(ProjectCandidate(project_id=p.project_id, title=p.title, score=score, basis=basis))
    scored.sort(key=lambda c: (-c.score, c.project_id))
    top = scored[0] if scored else None
    lead = top is not None and top.score >= MIN_SCORE and (len(scored) == 1 or top.score > scored[1].score)
    return ProjectChoice(chosen=top if lead else None, candidates=scored[:5])


def _bare_doi(value: str) -> str:
    return _DOI_PREFIX.sub("", str(value or "").strip()).strip().casefold()


def match_existing(rows: list[ExportRow], evidence: EvidenceArtifact) -> list[ExistingRecord]:
    by_record = {r.record_id: r.row_key for r in evidence.skeleton}
    for dep in evidence.deposits:
        by_record.setdefault(dep.record_id, next((s.row_key for s in evidence.skeleton
                                                  if dep.record_id == s.record_id or dep.record_id in s.title), None))
    by_file = {f.name: s.row_key for s in evidence.skeleton for f in s.files}
    by_title = {s.title: s.row_key for s in evidence.skeleton if s.title}
    own_doi = _bare_doi(evidence.paper.doi or "")
    found: dict[tuple[str, str, str], ExistingRecord] = {}

    def add(row: ExportRow, on: str, value: str, skeleton_row: str | None) -> None:
        found.setdefault((row.uid, on, value), ExistingRecord(uid=row.uid, sample_type=row.sample_type, matched_on=on,
                                                              value=value, skeleton_row=skeleton_row))

    for row in rows:
        for key, raw in row.metadata.items():
            value = str(raw or "").strip()
            if not value:
                continue
            if key == "DOI":
                if own_doi and _bare_doi(value) == own_doi:
                    add(row, "doi", value, None)
                continue
            if key == "Name" and value in by_title:
                add(row, "name", value, by_title[value])
                continue
            name = urlsplit(value).path.rsplit("/", 1)[-1] if "/" in value else value
            if name in by_file:
                add(row, "filename", name, by_file[name])
                continue
            for token in _TOKEN.findall(value):
                if token in by_record:
                    add(row, "accession", token, by_record[token])
                    break
    return list(found.values())


def lab_from_uids(uids: list[str]) -> tuple[str | None, str]:
    labs = Counter(hit["lab"] for hit in (UID_RE.match(u.strip()) for u in uids) if hit)
    if not labs:
        return None, "no UID in the target project to take a lab code from"
    ranked = labs.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, f"labs {ranked[0][0]} and {ranked[1][0]} are used equally in the target project"
    return ranked[0][0], f"{ranked[0][1]} of {sum(labs.values())} UIDs in the target project"


def build_context(paper: PaperArtifact, evidence: EvidenceArtifact, seams: ContextSeams) -> ContextArtifact:
    choice = infer_project(paper.ref, paper.existing_project_ids, seams.projects())
    ids = sorted(set(paper.existing_project_ids) | ({choice.chosen.project_id} if choice.chosen else set()))
    rows = seams.export_rows(ids) if ids else []
    lab, lab_basis = lab_from_uids([r.uid for r in rows])
    return ContextArtifact(
        mode=paper.mode, project=choice, sample_types=seams.sample_types(), house_assays=seams.house_assays(),
        existing=match_existing(rows, evidence),
        precedent=seams.precedent(choice.chosen.project_id) if choice.chosen else [],
        lab_code=lab, lab_basis=lab_basis,
        export=ExportSummary(project_ids=ids, pulled_at=seams.now(), samples=len(rows), uids=[r.uid for r in rows]),
        rulebook=RulebookVersion(file_sha256=seams.rulebook_sha256))
