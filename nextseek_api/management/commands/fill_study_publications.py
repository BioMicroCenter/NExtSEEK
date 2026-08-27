"""Extract the DOIs sitting in study-description prose, for curator review.

    uv run manage.py fill_study_publications --extract --out review.tsv

Nothing is written to any database. The reviewed file is the input a curator
uses to fill the DOI and PMID attributes on the samples themselves.

This command used to have an --apply phase that wrote studies.doi/studies.pmid.
Those columns were retired on 2026-08-26 when DOI and PMID became attributes of
the sample: study-level DOI cannot work on production, where every assay points
at one of 7 project-level studies. The extraction and Crossref/PubMed
verification are useful independently of where the answer is stored, which is
why this half survives.
"""

from __future__ import annotations

import csv
import json
import os
import time
from difflib import SequenceMatcher

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from seek.doi_extract import extract_publication_candidates


def _rows(sql: str, params: list | None = None) -> list[dict]:
    """Run a parameterized query on the SEEK connection, return dict rows.

    Inlined from the retired seek.publications module, which had no other
    surviving caller.
    """
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

REVIEW_COLUMNS = [
    "approve",
    "study_id",
    "study_title",
    "raw_match",
    "normalized_doi",
    "resolved_title",
    "journal",
    "year",
    "pmid",
    "title_similarity",
    "proposed_action",
    "notes",
]

CROSSREF_URL = "https://api.crossref.org/works/{doi}"

#: NCBI's PMC id-converter (/pmc/utils/idconv/) answers 403 as of 2026-08-24, so
#: DOI->PMID and PMC->DOI both go through E-utilities instead, which works
#: unauthenticated. `tool` identifies the caller per NCBI's usage policy; no
#: email is sent.
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_NCBI_TOOL = "nextseek-publication-links"
_HEADERS = {"User-Agent": "NExtSEEK-publication-links/1.0"}

#: NCBI allows 3 requests/second unauthenticated.
_NCBI_DELAY = 0.4

_TIMEOUT = 20


def parse_esearch_pmid(payload: dict) -> int | None:
    """First PMID from an esearch response, or None."""
    ids = (payload.get("esearchresult") or {}).get("idlist") or []
    return int(ids[0]) if ids else None


def parse_esummary_ids(payload: dict, pmc_numeric: str) -> dict:
    """{'doi': ..., 'pmid': ..., 'title': ...} from a PMC esummary response."""
    record = (payload.get("result") or {}).get(pmc_numeric) or {}
    ids = {a.get("idtype"): a.get("value") for a in record.get("articleids", [])}
    pmid = ids.get("pmid")
    return {
        "doi": (ids.get("doi") or "").lower() or None,
        "pmid": int(pmid) if pmid and str(pmid).isdigit() else None,
        "title": record.get("title") or None,
    }


def pmid_for_doi(doi: str) -> int | None:
    """Look up a PMID by DOI through E-utilities. None on any failure."""
    try:
        response = requests.get(
            ESEARCH_URL,
            params={"db": "pubmed", "term": f"{doi}[DOI]", "retmode": "json",
                    "tool": _NCBI_TOOL},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return parse_esearch_pmid(response.json())
    except Exception:
        return None


def ids_for_pmc(pmc_id: str) -> dict:
    """Resolve a PMC id to its DOI/PMID/title. Empty dict on any failure."""
    numeric = pmc_id.upper().replace("PMC", "")
    try:
        response = requests.get(
            ESUMMARY_URL,
            params={"db": "pmc", "id": numeric, "retmode": "json", "tool": _NCBI_TOOL},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        response.raise_for_status()
        return parse_esummary_ids(response.json(), numeric)
    except Exception:
        return {}


def title_similarity(a: str | None, b: str | None) -> float:
    """0.0-1.0 similarity between a study title and a resolved paper title."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _cache_load(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _cache_save(path: str, cache: dict) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2)


def make_resolver(cache: dict, offline: bool):
    """Return resolver(kind, value) -> metadata dict or None.

    Cached on disk so a rerun after a transient failure does not re-fetch what
    already worked.
    """

    def resolve(kind: str, value: str):
        if offline or not value:
            return None
        key = f"{kind}:{value}"
        if key in cache:
            return cache[key]

        meta = None
        try:
            if kind == "pmc":
                found = ids_for_pmc(value)
                time.sleep(_NCBI_DELAY)
                if found.get("doi"):
                    meta = resolve("doi", found["doi"])
                    if meta and not meta.get("pmid"):
                        meta["pmid"] = found.get("pmid")
                elif found.get("pmid"):
                    # No DOI on record, but a PMID is still worth having.
                    meta = {"doi": None, "title": found.get("title"),
                            "journal": None, "year": None, "pmid": found["pmid"]}
            else:
                response = requests.get(
                    CROSSREF_URL.format(doi=value), headers=_HEADERS, timeout=_TIMEOUT
                )
                response.raise_for_status()
                message = response.json()["message"]
                parts = (message.get("issued", {}).get("date-parts") or [[None]])[0]
                meta = {
                    "doi": (message.get("DOI") or value).lower(),
                    "title": (message.get("title") or [None])[0],
                    "journal": (message.get("container-title") or [None])[0],
                    "year": parts[0] if parts else None,
                    "pmid": None,
                }
                meta["pmid"] = pmid_for_doi(meta["doi"])
                time.sleep(_NCBI_DELAY)
        except Exception:
            meta = None

        cache[key] = meta
        return meta

    return resolve


def build_review_rows(studies: list[dict], resolver) -> list[dict]:
    """One review row per publication reference found. Never writes anything."""
    rows: list[dict] = []
    for study in studies:
        for candidate in extract_publication_candidates(study.get("description")):
            row = {c: "" for c in REVIEW_COLUMNS}
            row["study_id"] = str(study["id"])
            row["study_title"] = study.get("title") or ""
            row["raw_match"] = candidate.raw
            row["notes"] = candidate.note

            if candidate.kind == "unresolvable":
                row["proposed_action"] = "manual"
                rows.append(row)
                continue

            if candidate.kind == "doi":
                row["normalized_doi"] = candidate.value

            meta = resolver(candidate.kind, candidate.value)
            if not meta:
                row["proposed_action"] = "unresolved"
                rows.append(row)
                continue

            row["normalized_doi"] = meta.get("doi") or row["normalized_doi"]
            row["resolved_title"] = meta.get("title") or ""
            row["journal"] = meta.get("journal") or ""
            row["year"] = str(meta.get("year") or "")
            row["pmid"] = meta.get("pmid") or ""
            row["title_similarity"] = round(
                title_similarity(study.get("title"), meta.get("title")), 3
            )
            row["proposed_action"] = "fill"
            rows.append(row)
    return rows


class Command(BaseCommand):
    help = "Extract DOIs from study descriptions for curator review."

    def add_arguments(self, parser):
        parser.add_argument("--extract", action="store_true",
                            help="Write a review file. Touches no database.")
        parser.add_argument("--out", default="study_publication_review.tsv",
                            help="Where --extract writes its review file.")
        parser.add_argument("--offline", action="store_true",
                            help="Skip Crossref/NCBI; extract identifiers only.")
        parser.add_argument("--cache", default="study_publication_cache.json",
                            help="Resolution cache path.")

    def handle(self, *args, **options):
        if not options["extract"]:
            raise CommandError("pass --extract")
        self._extract(options)

    def _extract(self, options):
        studies = _rows("SELECT id, title, description FROM studies ORDER BY id")
        cache = _cache_load(options["cache"])
        resolver = make_resolver(cache, options["offline"])
        rows = build_review_rows(studies, resolver)
        _cache_save(options["cache"], cache)

        with open(options["out"], "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

        actions: dict[str, int] = {}
        for row in rows:
            actions[row["proposed_action"]] = actions.get(row["proposed_action"], 0) + 1
        self.stdout.write(
            f"{len(studies)} studies scanned, {len(rows)} candidates written to "
            f"{options['out']}: {actions}"
        )
        self.stdout.write(
            "Nothing was written to the database. Set `approve` to yes on the rows "
            "you accept, checking title_similarity, then use the reviewed file to "
            "fill the DOI and PMID attributes on the samples."
        )
