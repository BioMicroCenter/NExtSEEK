"""Stage 1, acquire (spec §4): paper.json → evidence.json.

Reads the supplement, finds every accession in the paper and SI, fetches each deposit
once (projects before their parts; a GEO series pulls its SRA project in), and builds the
skeleton. A deposit that is missing is a gap; a source that is down raises
SourceUnavailable so the run pauses and retries (spec §10).
"""
from __future__ import annotations

import re

from NessieAI.ns.paper import europepmc
from NessieAI.ns.paper.accessions import find_accessions
from NessieAI.ns.paper.deposits import ADAPTERS
from NessieAI.ns.paper.evidence import deposit_items, paper_items
from NessieAI.ns.paper.fetch import Fetcher, SourceNotFound
from NessieAI.ns.paper.models import (Accession, DepositRecord, EvidenceArtifact, EvidenceItem, Gap,
                                      PaperArtifact)
from NessieAI.ns.paper.si import supplement_items
from NessieAI.ns.paper.skeleton import build_skeleton

_ENA_PROJECT = re.compile(r"^(?:PRJ(?:NA|EB|DB)\d+|[SED]RP\d+)$")
_PROJECT_PREFIXES = ("GSE", "PRJ", "SRP", "ERP", "DRP", "PXD")


def _priority(acc: Accession) -> int:
    return 0 if acc.id.startswith(_PROJECT_PREFIXES) or acc.repo in ("zenodo", "figshare", "sdr") else 1


def acquire(paper: PaperArtifact, fetcher: Fetcher, adapters: dict | None = None) -> EvidenceArtifact:
    adapters = ADAPTERS if adapters is None else adapters
    items: list[EvidenceItem] = paper_items(paper.text)
    si_items, gaps = _supplements(paper, fetcher)
    items += si_items

    deposits: list[DepositRecord] = []
    covered: set[str] = set()
    tried: set[tuple[str, str]] = set()
    queue = sorted(find_accessions(paper.text.all_text() + [i.text for i in si_items]), key=_priority)
    while queue:
        acc = queue.pop(0)
        if (acc.repo, acc.id) in tried or acc.id in covered:
            continue
        tried.add((acc.repo, acc.id))
        adapter = adapters.get(acc.repo)
        if adapter is None:
            gaps.append(Gap(kind="deposit_unsupported", detail=f"{acc.repo} {acc.id}"))
            continue
        try:
            records = adapter(acc.id, fetcher)
        except SourceNotFound as exc:
            gaps.append(Gap(kind="deposit_unavailable", detail=f"{acc.repo} {acc.id}: HTTP {exc.status}"))
            continue
        if not any(rec.kind != "project" for rec in records):
            gaps.append(Gap(kind="deposit_empty", detail=f"{acc.repo} {acc.id}"))
        # Only a project-level fetch may cover project-level accessions: a cited run must not hide its study.
        project_fetch = bool(_ENA_PROJECT.match(acc.id))
        for rec in records:
            if rec.record_id in covered:
                continue
            covered.add(rec.record_id)
            if rec.repo == "ena":
                covered.update(rec.links if project_fetch else [l for l in rec.links if not _ENA_PROJECT.match(l)])
            deposits.append(rec)
            if rec.repo == "geo" and rec.kind == "project":
                queue += [Accession(repo="ena", id=link) for link in rec.links if _ENA_PROJECT.match(link)]
        queue.sort(key=_priority)
    for rec in deposits:
        items += deposit_items(rec)
    unique = list({item.id: item for item in items}.values())
    return EvidenceArtifact(paper=paper.ref, items=unique, deposits=deposits,
                            skeleton=build_skeleton(deposits), gaps=gaps)


def _supplements(paper: PaperArtifact, fetcher: Fetcher) -> tuple[list[EvidenceItem], list[Gap]]:
    expected = paper.text.supplements
    missing = [Gap(kind="supplement_unavailable", detail=s.name) for s in expected]
    if not (paper.ref.pmcid and paper.text_source == "europepmc"):
        return [], missing
    data = europepmc.supplementary_zip(paper.ref.pmcid, fetcher)
    return supplement_items(data, expected) if data else ([], missing)
