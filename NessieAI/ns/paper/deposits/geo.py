"""GEO: a series and its samples, from GEO's SOFT text (brief view).

Fields are kept verbatim. `characteristics` lines become `characteristics.<key>`, other
repeated keys get `.2`, `.3`. Relation lines give the links: SRX, SRP and BioProject
accessions, which fold the SRA runs in (skeleton) and fetch them (acquire).

A SuperSeries' sample list already holds every SubSeries' samples, so each SubSeries is read
only as itself: its own project record carries its SRA project, which an umbrella BioProject
may not resolve to. A SubSeries is never followed up to its SuperSeries.
"""
from __future__ import annotations

import logging
import re

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher, SourceNotFound
from NessieAI.ns.paper.models import DepositRecord, FileRef

URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
_KINDS = {"SERIES": "project", "SAMPLE": "sample"}
_SRA = re.compile(r"term=([SED]R[XP]\d+)")
_BIOPROJECT = re.compile(r"(PRJ(?:NA|EB|DB)\d+)")
_SUBSERIES = re.compile(r"^SuperSeries of:\s*(GSE\d+)", re.I)
logger = logging.getLogger(__name__)


def fetch(acc: str, fetcher: Fetcher) -> list[DepositRecord]:
    common = {"acc": acc, "form": "text", "view": "brief"}
    records = []
    if acc.startswith("GSE"):
        records += parse_soft(fetcher.get_text(URL, params={**common, "targ": "self"}))
        for sub in subseries(records):
            try:
                text = fetcher.get_text(URL, params={**common, "acc": sub, "targ": "self"})
            except SourceNotFound:  # its samples still come with the SuperSeries'; only its own record is lost
                logger.warning("paper reingest: GEO SubSeries %s of %s could not be read", sub, acc)
                continue
            records += [r for r in parse_soft(text) if r.kind == "project" and r.record_id == sub]
    records += parse_soft(fetcher.get_text(URL, params={**common, "targ": "gsm"}))
    return records


def subseries(records: list[DepositRecord]) -> list[str]:
    """The SubSeries a SuperSeries record names (its `SuperSeries of:` relations), in order."""
    found = [m.group(1) for rec in records if rec.kind == "project"
             for key, value in rec.fields.items() if key.split(".")[0] == "relation"
             for m in [_SUBSERIES.match(value)] if m]
    return list(dict.fromkeys(found))


def parse_soft(text: str) -> list[DepositRecord]:
    raw: list[dict] = []
    current = None
    for line in (l.strip() for l in text.splitlines()):
        if line.startswith("^"):
            entity, _, acc = line[1:].partition("=")
            kind = _KINDS.get(entity.strip().upper())
            current = {"kind": kind, "id": acc.strip(), "lines": []} if kind else None
            if current:
                raw.append(current)
        elif current and line.startswith("!") and "=" in line:
            name, _, value = line[1:].partition("=")
            current["lines"].append((name.strip(), value.strip()))
    return [_record(r) for r in raw]


def _record(raw: dict) -> DepositRecord:
    fields: dict[str, str] = {}
    counts: dict[str, int] = {}
    files: list[FileRef] = []
    links: list[str] = []
    title, submitted = "", None
    for name, value in raw["lines"]:
        name = re.sub(r"^(?:Series|Sample)_", "", name)
        if name.startswith("characteristics") and ":" in value:
            sub, _, val = value.partition(":")
            name, value = f"characteristics.{sub.strip()}", val.strip()
        if name == "title":
            title = value
        elif name == "submission_date":
            submitted = parse_date(value)
        elif name.startswith("supplementary_file"):
            file_name = value.rsplit("/", 1)[-1]
            if file_name and value.upper() != "NONE":
                files.append(FileRef(name=file_name, url=value))
        elif name == "relation":
            links += _BIOPROJECT.findall(value) + _SRA.findall(value)
        _put(fields, counts, name, value)
    return DepositRecord(repo="geo", record_id=raw["id"], kind=raw["kind"], title=title,
                         fields=fields, files=files, links=list(dict.fromkeys(links)), submitted=submitted)


def _put(fields: dict[str, str], counts: dict[str, int], name: str, value: str) -> None:
    """Store a repeated key as name, name.2, name.3 ... with one counter per name (linear)."""
    counts[name] = counts.get(name, 0) + 1
    key = name if counts[name] == 1 else f"{name}.{counts[name]}"
    while key in fields:  # a literal key such as relation.2 already holds this name
        counts[name] += 1
        key = f"{name}.{counts[name]}"
    fields[key] = value
