"""GEO: a series and its samples, from GEO's SOFT text (brief view).

Fields are kept verbatim. `characteristics` lines become `characteristics.<key>`, other
repeated keys get `.2`, `.3`. Relation lines give the links: SRX, SRP and BioProject
accessions, which fold the SRA runs in (skeleton) and fetch them (acquire).
"""
from __future__ import annotations

import re

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.models import DepositRecord, FileRef

URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
_KINDS = {"SERIES": "project", "SAMPLE": "sample"}
_SRA = re.compile(r"term=([SED]R[XP]\d+)")
_BIOPROJECT = re.compile(r"(PRJ(?:NA|EB|DB)\d+)")


def fetch(acc: str, fetcher: Fetcher) -> list[DepositRecord]:
    common = {"acc": acc, "form": "text", "view": "brief"}
    records = []
    if acc.startswith("GSE"):
        records += parse_soft(fetcher.get_text(URL, params={**common, "targ": "self"}))
    records += parse_soft(fetcher.get_text(URL, params={**common, "targ": "gsm"}))
    return records


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
        elif name.startswith("supplementary_file") and value.upper() != "NONE":
            files.append(FileRef(name=value.rsplit("/", 1)[-1], url=value))
        elif name == "relation":
            links += _BIOPROJECT.findall(value) + _SRA.findall(value)
        _put(fields, name, value)
    return DepositRecord(repo="geo", record_id=raw["id"], kind=raw["kind"], title=title,
                         fields=fields, files=files, links=links, submitted=submitted)


def _put(fields: dict[str, str], name: str, value: str) -> None:
    key, n = name, 1
    while key in fields:
        n += 1
        key = f"{name}.{n}"
    fields[key] = value
