#!/usr/bin/env python3
"""Fixed, non-interpolated helper STAGED to Luria and invoked from run.sh:

  python3 fetchngs_helpers.py ids [r1_col]                -> write ids.csv from samplesheet.csv
  python3 fetchngs_helpers.py fill <cache> [r1_col r2_col] -> fill the read columns from <cache>/fastq

The read COLUMN NAMES are arguments because they are not always fastq_1/fastq_2.
Several pipelines rename them — ampliseq uses forwardReads/reverseReads, bacass R1/R2,
detaxizer short_reads_fastq_1/2 — and the emitter renames the columns before the sheet
is staged. Hardcoding fastq_1 here meant the fetch downloaded the reads, wrote them to
a column the sheet did not have, and the write-back (extrasaction="ignore") silently
dropped them. The run then started with empty inputs. Defaults preserve the old
behaviour for the pipelines that do use the standard names.

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


def needs_fetch_accessions(rows: list[dict], r1_col: str = "fastq_1") -> list[str]:
    """Bare-id accessions of rows whose read column is empty, deduped, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        if (row.get(r1_col) or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if acc and _ACC_RE.match(acc) and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def _paths_for(cache: str, acc: str) -> tuple[str | None, str]:
    """Resolve the fetched fastq(s) for an accession from the cache.

    nf-core/fetchngs names outputs <experiment>_<run>_{1,2}.fastq.gz (e.g.
    SRX6818190_SRR10085181_1.fastq.gz), so the run accession is a SUFFIX, not the
    whole filename. Match it as a suffix, falling back to a bare <acc>… name. `acc`
    is pre-validated ^[A-Za-z0-9]+$, so it carries no glob metacharacters.
    """
    fq = Path(cache) / "fastq"

    def _first(*patterns: str) -> str | None:
        for pat in patterns:
            hits = sorted(fq.glob(pat))
            if hits:
                return str(hits[0])
        return None

    r1 = _first(f"*_{acc}_1.fastq.gz", f"{acc}_1.fastq.gz")
    if r1:
        r2 = _first(f"*_{acc}_2.fastq.gz", f"{acc}_2.fastq.gz")
        return r1, (r2 or "")
    se = _first(f"*_{acc}.fastq.gz", f"{acc}.fastq.gz")
    if se:
        return se, ""
    return None, ""


def fill_rows(rows: list[dict], cache: str, r1_col: str = "fastq_1",
              r2_col: str = "fastq_2") -> tuple[list[dict], list[str]]:
    """Fill the read columns for blank SRR rows from the cache; return (rows, missing)."""
    missing: list[str] = []
    for row in rows:
        if (row.get(r1_col) or "").strip():
            continue
        acc = (row.get("accession") or "").strip()
        if not (acc and _ACC_RE.match(acc)):
            continue
        f1, f2 = _paths_for(cache, acc)
        if f1 is None:
            missing.append(acc)
            continue
        row[r1_col] = f1
        if r2_col:
            row[r2_col] = f2
    return rows, missing


def _main_ids(r1_col: str = "fastq_1", sheet: str = "samplesheet.csv",
              ids: str = "ids.csv") -> int:
    with open(sheet, newline="") as fh:
        rows = list(csv.DictReader(fh))
    accs = needs_fetch_accessions(rows, r1_col)
    Path(ids).write_text("".join(a + "\n" for a in accs), encoding="utf-8")
    return 0


def _main_fill(cache: str, r1_col: str = "fastq_1", r2_col: str = "fastq_2",
               sheet: str = "samplesheet.csv") -> int:
    with open(sheet, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames or []
    # Fail loudly rather than writing into a column the sheet does not have: the
    # write-back below uses extrasaction="ignore", so an unknown key vanishes and the
    # pipeline starts with empty reads after a successful, expensive download.
    if r1_col not in fields:
        sys.stderr.write(
            f"fetchngs fill: read column {r1_col!r} is not in the samplesheet header "
            f"{fields}; refusing to fill\n")
        return 1
    rows, missing = fill_rows(rows, cache, r1_col, r2_col if r2_col in fields else "")
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
        sys.exit(_main_ids(*(sys.argv[2:3] or ["fastq_1"])))
    if cmd == "fill":
        sys.exit(_main_fill(sys.argv[2], *(sys.argv[3:5] or ["fastq_1", "fastq_2"])))
    sys.stderr.write(
        "usage: fetchngs_helpers.py ids [r1_col] | fill <cache> [r1_col r2_col]\n")
    sys.exit(2)
