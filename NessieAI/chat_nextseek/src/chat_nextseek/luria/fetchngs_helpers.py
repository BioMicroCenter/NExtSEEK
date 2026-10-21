#!/usr/bin/env python3
"""Fixed, non-interpolated helper STAGED to Luria and invoked from run.sh:

  python3 fetchngs_helpers.py ids           -> write ids.csv from samplesheet.csv
  python3 fetchngs_helpers.py fill <cache>  -> fill fastq_1/2 in samplesheet.csv from <cache>/fastq
  python3 fetchngs_helpers.py cached <cache> -> exit 0 if fill can resolve every id in ids.csv
  python3 fetchngs_helpers.py claim <cache> <job_id>   -> mark ids.csv's accessions in use by the job
  python3 fetchngs_helpers.py release <cache> <job_id> -> drop the job's claims (run.sh's exit trap)

Because it is staged verbatim (never string-formatted with run-specific values), it
carries no shell-injection surface. Accessions are validated as bare ids before any
path is constructed, so a malformed accession cannot cause path traversal.
"""
from __future__ import annotations

import csv
import fcntl
import os
import re
import sys
import time
from contextlib import contextmanager
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


def _listed_runs(cache: str, acc: str) -> list[str] | None:
    """Run accessions fetchngs listed for `acc`, or None when it left no usable list.

    fetchngs writes <outdir>/metadata/<acc>.runinfo_ftp.tsv for every id it was given,
    before downloading, with one row per run in a `run_accession` column. `acc` must be
    pre-validated ^[A-Za-z0-9]+$, so the path cannot leave the metadata directory.
    """
    path = Path(cache) / "metadata" / f"{acc}.runinfo_ftp.tsv"
    try:
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if "run_accession" not in (reader.fieldnames or []):
                return None
            listed = [(r.get("run_accession") or "").strip() for r in reader]
    except OSError:
        return None
    return [r for r in listed if _ACC_RE.match(r)] or None


def _resolve(runs: dict[tuple[str, ...], dict[str, str]], cache: str, acc: str) -> list[tuple[str, str]]:
    """The cached runs for `acc`, or [] when any run fetchngs listed for it has no files.

    Without the list an experiment whose earlier download was interrupted would resolve
    to the runs that landed, and the pipeline would run on part of the sample's reads.
    """
    found = _runs_for(runs, acc)
    listed = _listed_runs(cache, acc) if found else None
    if listed and any(not _runs_for(runs, r) for r in listed):
        return []
    return found


def uncached_accessions(accs: list[str], cache: str) -> list[str]:
    """Accessions that fill could NOT resolve from the cache, in order.

    run.sh uses this (`cached`) to decide whether to run fetchngs, so "cached" means
    exactly what fill can resolve, run list included.
    """
    runs = _cached_runs(cache)
    return [a for a in accs if not (_ACC_RE.match(a) and _resolve(runs, cache, a))]


# --- download claims: what the weekly cleanup (bin/nessie_cleanup.py) reads ---------------
# A run claims its accessions BEFORE it checks the cache and releases them when it exits, both
# under <cache>/nessie_status/cleanup.lock, which the cleanup also holds while it deletes
# downloads. So the cleanup can never delete a FASTQ that a starting run has just decided to
# reuse. cleanup.py parses these files: change a format here and you change it there too.

STATUS_DIR = "nessie_status"
LOCK_NAME = "cleanup.lock"
_JOB_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(now: float | None = None) -> str:
    return time.strftime(_TIME_FMT, time.gmtime(time.time() if now is None else now))


def _check_job(job_id: str) -> str:
    if not job_id or not _JOB_RE.match(job_id):
        raise ValueError(f"invalid job id {job_id!r}")
    return job_id


@contextmanager
def _cache_lock(cache: str):
    status = Path(cache) / STATUS_DIR
    status.mkdir(parents=True, exist_ok=True)
    with open(status / LOCK_NAME, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield status
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _touch_last_used(status: Path, acc: str, now: float | None) -> None:
    folder = status / "last_used"
    folder.mkdir(exist_ok=True)
    (folder / f"{acc}.txt").write_text(f"last_used_at: {_iso(now)}\n", encoding="utf-8")


def claim(cache: str, job_id: str, accs: list[str], *, run_dir: str = "",
          now: float | None = None) -> list[str]:
    """Mark `accs` in use by `job_id` and refresh their last_used; return the bare ids claimed."""
    _check_job(job_id)
    accs = [a for a in accs if _ACC_RE.match(a)]
    with _cache_lock(cache) as status:
        for acc in accs:
            folder = status / "in_use" / acc
            folder.mkdir(parents=True, exist_ok=True)
            (folder / job_id).write_text(f"claimed_at: {_iso(now)}\nrun_dir: {run_dir}\n",
                                         encoding="utf-8")
            _touch_last_used(status, acc, now)
    return accs


def release(cache: str, job_id: str, *, now: float | None = None) -> list[str]:
    """Drop every claim `job_id` holds, refreshing last_used for each; return the ids released.

    Refreshing here, not only in claim, starts the cleanup's 30-day clock when a run stops
    using a download rather than when it started."""
    _check_job(job_id)
    if not (Path(cache) / STATUS_DIR / "in_use").is_dir():
        return []
    released: list[str] = []
    with _cache_lock(cache) as status:
        for folder in sorted((status / "in_use").iterdir()):
            entry = folder / job_id
            if not entry.is_file():
                continue
            entry.unlink()
            released.append(folder.name)
            _touch_last_used(status, folder.name, now)
            try:
                folder.rmdir()  # succeeds only when no other job still holds this accession
            except OSError:
                pass
    return released


def fill_rows(rows: list[dict], cache: str) -> tuple[list[dict], list[str]]:
    """Fill fastq_1/fastq_2 for blank accession rows from the cache; return (rows, missing_accessions).

    A row whose accession resolves to several runs (an experiment with more than one run)
    becomes one row per run, all with the same `sample`: nf-core pipelines merge rows that
    share a sample, so no run is dropped. An accession with a run that fetchngs listed but
    did not deliver is missing. Rows with a fastq_1 already, or no bare-id accession, pass
    through unchanged.
    """
    runs = _cached_runs(cache)
    out: list[dict] = []
    missing: list[str] = []
    for row in rows:
        acc = (row.get("accession") or "").strip()
        if (row.get("fastq_1") or "").strip() or not (acc and _ACC_RE.match(acc)):
            out.append(row)
            continue
        found = _resolve(runs, cache, acc)
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
        sys.stderr.write(f"fetchngs fill: no complete fastq set in {cache}/fastq for {missing} "
                         f"(runs expected per accession: {cache}/metadata/<acc>.runinfo_ftp.tsv)\n")
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


def _main_claim(cache: str, job_id: str, ids: str = "ids.csv") -> int:
    accs = [line.strip() for line in Path(ids).read_text(encoding="utf-8").splitlines() if line.strip()]
    claim(cache, job_id, accs, run_dir=os.getcwd())
    return 0


def _main_release(cache: str, job_id: str) -> int:
    release(cache, job_id)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ids":
        sys.exit(_main_ids())
    if cmd == "fill":
        sys.exit(_main_fill(sys.argv[2]))
    if cmd == "cached":
        sys.exit(_main_cached(sys.argv[2]))
    if cmd == "claim" and len(sys.argv) == 4:
        sys.exit(_main_claim(sys.argv[2], sys.argv[3]))
    if cmd == "release" and len(sys.argv) == 4:
        sys.exit(_main_release(sys.argv[2], sys.argv[3]))
    sys.stderr.write("usage: fetchngs_helpers.py ids | fill <cache> | cached <cache> | "
                     "claim <cache> <job_id> | release <cache> <job_id>\n")
    sys.exit(2)
