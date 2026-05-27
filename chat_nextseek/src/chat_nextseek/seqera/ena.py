"""ENA filereport client + accession extraction.

Resolves SRA-style accessions (SRR/SRX/PRJ/GSE/GSM/ERR/ERX/DRR/DRX) to direct
HTTPS FASTQ URLs via the ENA Portal API. Pure stdlib; no third-party deps.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable


_ENA_BASE = "https://www.ebi.ac.uk/ena/portal/api/filereport"
_ACCESSION_PATTERN = re.compile(
    r"\b(?:SRR|SRX|SRP|SRS|ERR|ERX|ERP|ERS|DRR|DRX|DRP|DRS|GSE|GSM|PRJ[A-Z]+)\d+\b"
)
_NEXTSEEK_ACCESSION_FIELDS = {
    "sra_accession", "sra_run", "sra_run_id", "sra_sample", "sra_study",
    "sra_experiment", "sra", "run_accession", "experiment_accession",
    "study_accession", "biosample", "biosample_accession",
    "geo_accession", "geo", "gse", "gsm",
    "ena_accession", "ena_run", "bioproject", "bioproject_accession",
}


@dataclass
class ENARun:
    run_accession: str
    fastq_1: str
    fastq_2: str = ""
    layout: str = ""  # SINGLE | PAIRED


@dataclass
class ENAResolution:
    accession: str
    runs: list[ENARun] = field(default_factory=list)
    missing: bool = False
    reason: str = ""


_RUN_ACCESSION_PREFIXES = ("SRR", "ERR", "DRR")
_EXPERIMENT_ACCESSION_PREFIXES = ("SRX", "ERX", "DRX")


def extract_accessions_from_metadata(metadata: dict | None) -> list[str]:
    """Walk a NExtSEEK metadata dict for accession strings.

    Returns the most specific accessions first: run accessions (SRR/ERR/DRR)
    are preferred over experiment (SRX/ERX/DRX), study (SRP/ERP/DRP),
    sample (SRS/ERS/DRS), GEO (GSE/GSM), and BioProject (PRJ*) — the broad
    ones are only included as a fallback when no run accessions were found.

    This avoids the over-production case where a BioProject in study-level
    metadata expands to hundreds of unrelated runs.
    """
    found: set[str] = set()
    if not isinstance(metadata, dict):
        return []

    def _scan_string(value: str) -> None:
        for m in _ACCESSION_PATTERN.findall(value or ""):
            found.add(m)

    def _walk(node: Any, parent_key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key_lc = (k or "").strip().lower() if isinstance(k, str) else ""
                if key_lc in _NEXTSEEK_ACCESSION_FIELDS and isinstance(v, (str, int)):
                    _scan_string(str(v))
                _walk(v, key_lc)
        elif isinstance(node, list):
            for item in node:
                _walk(item, parent_key)
        elif isinstance(node, str):
            _scan_string(node)

    _walk(metadata)

    # If we found per-run accessions (SRR/ERR/DRR), drop the broad ones —
    # those are study-level pointers that ENA expands to many unrelated runs.
    runs = sorted([a for a in found if a.startswith(_RUN_ACCESSION_PREFIXES)])
    if runs:
        experiments = sorted([a for a in found if a.startswith(_EXPERIMENT_ACCESSION_PREFIXES)])
        return runs + experiments
    return sorted(found)


def _https_prefix(path: str) -> str:
    """ENA returns 'ftp.sra.ebi.ac.uk/...' bare; prepend https://."""
    if not path:
        return ""
    if path.startswith(("http://", "https://", "ftp://")):
        # Force HTTPS even if FTP was returned (Nextflow handles either, but
        # HTTPS works through more compute env egress configs).
        if path.startswith("ftp://"):
            return "https://" + path[len("ftp://"):]
        return path
    return "https://" + path.lstrip("/")


def _http_get_json(url: str, timeout_connect: float = 5.0, timeout_read: float = 15.0,
                   retries: int = 1) -> Any:
    """Minimal GET helper with one retry on 5xx / network errors."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=max(timeout_connect, timeout_read)) as resp:
                if resp.status >= 500:
                    raise urllib.error.HTTPError(url, resp.status, "server error", resp.headers, None)
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else []
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err


def resolve_accession_to_fastqs(accession: str) -> ENAResolution:
    """Resolve a single accession to FASTQ HTTPS URLs via ENA filereport.

    Failures (HTTP error, no rows, missing fastq_ftp) return an ENAResolution
    with `missing=True`, never raise.
    """
    acc = (accession or "").strip()
    if not acc:
        return ENAResolution(accession="", missing=True, reason="empty accession")

    params = urllib.parse.urlencode({
        "accession": acc,
        "result": "read_run",
        "fields": "run_accession,fastq_ftp,read_count,library_layout",
        "format": "json",
    })
    url = f"{_ENA_BASE}?{params}"

    try:
        rows = _http_get_json(url)
    except Exception as e:
        return ENAResolution(accession=acc, missing=True, reason=f"ENA HTTP error: {e!r}")

    if not isinstance(rows, list) or not rows:
        return ENAResolution(accession=acc, missing=True, reason="No rows from ENA filereport")

    runs: list[ENARun] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        run_acc = (row.get("run_accession") or "").strip()
        fastq_ftp = (row.get("fastq_ftp") or "").strip()
        layout = (row.get("library_layout") or "").strip().upper()
        if not fastq_ftp:
            continue
        parts = [p.strip() for p in fastq_ftp.split(";") if p.strip()]
        # ENA returns the second mate at index 1 for paired reads. For single
        # end, only one path. There can also be a "merged" (no _1/_2 suffix)
        # variant first; pick the _1/_2 pair when available.
        fq1, fq2 = "", ""
        mate1 = next((p for p in parts if p.endswith("_1.fastq.gz")), "")
        mate2 = next((p for p in parts if p.endswith("_2.fastq.gz")), "")
        if mate1 and mate2:
            fq1, fq2 = mate1, mate2
        elif mate1:
            fq1 = mate1
        elif parts:
            fq1 = parts[0]
        runs.append(ENARun(
            run_accession=run_acc or acc,
            fastq_1=_https_prefix(fq1),
            fastq_2=_https_prefix(fq2),
            layout=layout,
        ))

    if not runs:
        return ENAResolution(accession=acc, missing=True, reason="No fastq_ftp populated for any run")
    return ENAResolution(accession=acc, runs=runs)


def resolve_accessions(accessions: Iterable[str]) -> list[ENAResolution]:
    """Resolve a list of accessions, preserving order, deduplicating."""
    seen: set[str] = set()
    out: list[ENAResolution] = []
    for acc in accessions or []:
        key = (acc or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(resolve_accession_to_fastqs(key))
    return out
