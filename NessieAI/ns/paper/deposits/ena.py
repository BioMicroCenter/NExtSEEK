"""ENA portal filereport: the runs of any SRA/ENA study, experiment, run or BioProject.

One call per accession. Each run is a record; its FASTQs are files (https URLs, size,
md5). Links carry the experiment, sample and study accessions, which fold the run into
its GEO sample or its experiment row (skeleton).
"""
from __future__ import annotations

import json

from NessieAI.ns.paper.dates import parse_date
from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.models import DepositRecord, FileRef

URL = "https://www.ebi.ac.uk/ena/portal/api/filereport"
FIELDS = (
    "run_accession", "experiment_accession", "sample_accession", "secondary_sample_accession",
    "study_accession", "secondary_study_accession", "sample_alias", "sample_title", "experiment_title",
    "library_name", "library_strategy", "library_source", "library_selection", "library_layout",
    "instrument_platform", "instrument_model", "scientific_name", "tax_id",
    "fastq_ftp", "fastq_bytes", "fastq_md5", "first_public",
)
_FILE_FIELDS = frozenset({"fastq_ftp", "fastq_bytes", "fastq_md5"})
_LINK_FIELDS = ("experiment_accession", "sample_accession", "secondary_sample_accession",
                "study_accession", "secondary_study_accession")


def fetch(acc: str, fetcher: Fetcher) -> list[DepositRecord]:
    body = fetcher.get_text(URL, params={"accession": acc, "result": "read_run",
                                         "fields": ",".join(FIELDS), "format": "json"}).strip()
    return [_record(row) for row in (json.loads(body) if body else [])]


def _record(row: dict) -> DepositRecord:
    return DepositRecord(
        repo="ena", record_id=row["run_accession"], kind="run",
        title=row.get("experiment_title") or row.get("sample_title") or "",
        fields={k: str(v) for k, v in row.items() if k not in _FILE_FIELDS and v not in ("", None)},
        files=_fastqs(row),
        links=[row[k] for k in _LINK_FIELDS if row.get(k)],
        submitted=parse_date(row.get("first_public")),
    )


def _fastqs(row: dict) -> list[FileRef]:
    urls = [u for u in (row.get("fastq_ftp") or "").split(";") if u]
    sizes = (row.get("fastq_bytes") or "").split(";")
    md5s = (row.get("fastq_md5") or "").split(";")
    files = []
    for i, url in enumerate(urls):
        size = sizes[i] if i < len(sizes) else ""
        md5 = md5s[i] if i < len(md5s) else ""
        files.append(FileRef(name=url.rsplit("/", 1)[-1], url=url if "://" in url else f"https://{url}",
                             size=int(size) if size.isdigit() else None,
                             checksum=md5 or None, checksum_type="md5" if md5 else None))
    return files
