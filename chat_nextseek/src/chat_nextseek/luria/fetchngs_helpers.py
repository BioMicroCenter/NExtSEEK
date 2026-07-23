#!/usr/bin/env python3
"""Fixed, non-interpolated helper STAGED to Luria and invoked from run.sh:

  python3 fetchngs_helpers.py ids           -> write ids.csv from samplesheet.csv
  python3 fetchngs_helpers.py fill <cache>  -> fill fastq_1/2 in samplesheet.csv from <cache>/fastq

Because it is staged verbatim (never string-formatted with run-specific values), it
carries no shell-injection surface. Accessions are validated as bare ids before any
path is constructed, so a malformed accession cannot cause path traversal.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_ACC_RE = re.compile(r"^[A-Za-z0-9]+$")


def needs_fetch_accessions(rows: list[dict]) -> list[str]:
    """Bare-id accessions of rows with an empty fastq_1, deduped, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        if (row.get("fastq_1") or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if acc and _ACC_RE.match(acc) and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def _paths_for(cache: str, acc: str) -> tuple[str | None, str]:
    fq = Path(cache) / "fastq"
    r1, r2 = fq / f"{acc}_1.fastq.gz", fq / f"{acc}_2.fastq.gz"
    se = fq / f"{acc}.fastq.gz"
    if r1.exists():
        return str(r1), (str(r2) if r2.exists() else "")
    if se.exists():
        return str(se), ""
    return None, ""


def fill_rows(rows: list[dict], cache: str) -> tuple[list[dict], list[str]]:
    """Fill fastq_1/fastq_2 for blank SRR rows from the cache; return (rows, missing_accessions)."""
    missing: list[str] = []
    for row in rows:
        if (row.get("fastq_1") or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if not (acc and _ACC_RE.match(acc)):
            continue
        f1, f2 = _paths_for(cache, acc)
        if f1 is None:
            missing.append(acc)
            continue
        row["fastq_1"] = f1
        row["fastq_2"] = f2
    return rows, missing


def _main_ids(sheet: str = "samplesheet.csv", ids: str = "ids.csv") -> int:
    with open(sheet, newline="") as fh:
        rows = list(csv.DictReader(fh))
    accs = needs_fetch_accessions(rows)
    Path(ids).write_text("".join(a + "\n" for a in accs), encoding="utf-8")
    return 0


def _main_fill(cache: str, sheet: str = "samplesheet.csv") -> int:
    with open(sheet, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    rows, missing = fill_rows(rows, cache)
    if missing:
        sys.stderr.write(f"fetchngs fill: no fastqs found in {cache}/fastq for {missing}\n")
        return 1
    with open(sheet, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ids":
        sys.exit(_main_ids())
    if cmd == "fill":
        sys.exit(_main_fill(sys.argv[2]))
    sys.stderr.write("usage: fetchngs_helpers.py ids | fill <cache>\n")
    sys.exit(2)
