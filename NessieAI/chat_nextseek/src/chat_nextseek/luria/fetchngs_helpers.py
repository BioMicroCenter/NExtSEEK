#!/usr/bin/env python3
"""Fixed, non-interpolated helper STAGED to Luria and invoked from run.sh:

  python3 fetchngs_helpers.py ids           -> write ids.csv from samplesheet.csv
  python3 fetchngs_helpers.py fill <cache>  -> fill fastq_1/2 in samplesheet.csv from <cache>/fastq
  python3 fetchngs_helpers.py cached <cache> -> exit 0 if fill can resolve every id in ids.csv

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


def _cached_runs(cache: str) -> dict[tuple[str, ...], dict[str, str]]:
    """Group <cache>/fastq/*.fastq.gz by run: the name's id tokens -> {read: path}.

    nf-core/fetchngs names outputs <experiment>_<run>_{1,2}.fastq.gz, or
    <experiment>_<run>.fastq.gz for single-end (e.g. SRX6818190_SRR10085181_1.fastq.gz);
    a bare <run>_1.fastq.gz is accepted too. The read is "1", "2" or "" (single-end).
    A name whose id tokens are not all bare ids is ignored.
    """
    runs: dict[tuple[str, ...], dict[str, str]] = {}
    for path in sorted((Path(cache) / "fastq").glob("*.fastq.gz")):
        tokens = path.name[: -len(".fastq.gz")].split("_")
        read = tokens.pop() if len(tokens) > 1 and tokens[-1] in ("1", "2") else ""
        if all(_ACC_RE.match(t) for t in tokens):
            runs.setdefault(tuple(tokens), {})[read] = str(path)
    return runs


def _runs_for(runs: dict[tuple[str, ...], dict[str, str]], acc: str) -> list[tuple[str, str]]:
    """(fastq_1, fastq_2) of every cached run whose name carries `acc` as a WHOLE id token.

    A run accession (SRR/ERR/DRR) names one run; an experiment accession (SRX/ERX/DRX)
    names every run fetched for it, so it can resolve to several. Whole-token matching
    keeps SRR1 from taking SRR10's files. A run with only a read 2 does not count.
    """
    out: list[tuple[str, str]] = []
    for ids, reads in runs.items():
        if acc not in ids:
            continue
        if "1" in reads:
            out.append((reads["1"], reads.get("2", "")))
        elif "" in reads:
            out.append((reads[""], ""))
    return out


def uncached_accessions(accs: list[str], cache: str) -> list[str]:
    """Accessions that fill could NOT resolve from the cache, in order.

    run.sh uses this (`cached`) to decide whether to run fetchngs, so "cached" means
    exactly what fill can resolve. It cannot tell that an experiment is missing some of
    its runs (an interrupted earlier download); it sees only what is on disk.
    """
    runs = _cached_runs(cache)
    return [a for a in accs if not (_ACC_RE.match(a) and _runs_for(runs, a))]


def fill_rows(rows: list[dict], cache: str) -> tuple[list[dict], list[str]]:
    """Fill fastq_1/fastq_2 for blank accession rows from the cache; return (rows, missing_accessions).

    A row whose accession resolves to several runs (an experiment with more than one run)
    becomes one row per run, all with the same `sample`: nf-core pipelines merge rows that
    share a sample, so no run is dropped. Rows with a fastq_1 already, or no bare-id
    accession, pass through unchanged.
    """
    runs = _cached_runs(cache)
    out: list[dict] = []
    missing: list[str] = []
    for row in rows:
        acc = (row.get("accession") or "").strip()
        if (row.get("fastq_1") or "").strip() or not (acc and _ACC_RE.match(acc)):
            out.append(row)
            continue
        found = _runs_for(runs, acc)
        if not found:
            missing.append(acc)
            out.append(row)
            continue
        for f1, f2 in found:
            out.append({**row, "fastq_1": f1, "fastq_2": f2})
    return out, missing


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


def _main_cached(cache: str, ids: str = "ids.csv") -> int:
    """Exit 0 when fill can resolve every accession in ids.csv from the cache, else 1."""
    accs = [line.strip() for line in Path(ids).read_text(encoding="utf-8").splitlines() if line.strip()]
    return 1 if uncached_accessions(accs, cache) else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ids":
        sys.exit(_main_ids())
    if cmd == "fill":
        sys.exit(_main_fill(sys.argv[2]))
    if cmd == "cached":
        sys.exit(_main_cached(sys.argv[2]))
    sys.stderr.write("usage: fetchngs_helpers.py ids | fill <cache> | cached <cache>\n")
    sys.exit(2)
