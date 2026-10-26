"""Deposit records → skeleton rows (spec §4.1, spec delta 3).

The deposits, not the agent, decide how many data rows there are:
- a GEO sample is one row, with its SRA runs folded in through its SRX link;
- ENA runs with no GEO sample are grouped one row per experiment;
- a PRIDE RAW file is a raw row, a SEARCH or RESULT file an analysis row, other PRIDE files no row;
- a Zenodo, figshare or SDR file is a file row.
"""
from __future__ import annotations

from NessieAI.ns.paper.evidence import record_evidence_ids
from NessieAI.ns.paper.models import DepositRecord, SkeletonRow

_PRIDE_KINDS = {"RAW": "raw", "SEARCH": "analysis", "RESULT": "analysis"}


def build_skeleton(deposits: list[DepositRecord]) -> list[SkeletonRow]:
    rows: list[SkeletonRow] = []
    by_link: dict[str, SkeletonRow] = {}
    for rec in deposits:
        if rec.repo == "geo" and rec.kind == "sample":
            row = SkeletonRow(row_key=f"geo:{rec.record_id}", repo="geo", record_id=rec.record_id, kind="raw",
                              title=rec.title, files=list(rec.files), evidence_ids=record_evidence_ids(rec),
                              submitted=rec.submitted)
            rows.append(row)
            for link in rec.links:
                by_link[link] = row
    experiments: dict[str, SkeletonRow] = {}
    for rec in deposits:
        if rec.repo != "ena" or rec.kind != "run":
            continue
        target = next((by_link[link] for link in rec.links if link in by_link), None)
        if target is None:
            exp = rec.fields.get("experiment_accession", rec.record_id)
            target = experiments.get(exp)
            if target is None:
                target = experiments[exp] = SkeletonRow(row_key=f"ena:{exp}", repo="ena", record_id=exp,
                                                        kind="raw", title=rec.title, submitted=rec.submitted)
                rows.append(target)
        target.files.extend(rec.files)
        target.evidence_ids.extend(record_evidence_ids(rec))
    for rec in deposits:
        if rec.kind != "file":
            continue
        kind = _PRIDE_KINDS.get(rec.fields.get("fileCategory", "")) if rec.repo == "pride" else "file"
        if kind is None:
            continue
        rows.append(SkeletonRow(row_key=f"{rec.repo}:{rec.record_id}", repo=rec.repo, record_id=rec.record_id,
                                kind=kind, title=rec.title, files=list(rec.files),
                                evidence_ids=record_evidence_ids(rec), submitted=rec.submitted))
    return rows
