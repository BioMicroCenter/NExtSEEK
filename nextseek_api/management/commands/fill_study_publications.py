"""Turn the DOIs sitting in study-description prose into study attributes.

Two phases, deliberately separated by a human:

    uv run manage.py fill_study_publications --extract --out review.tsv
    # curator edits the `approve` column
    uv run manage.py fill_study_publications --apply review.tsv

Nothing is written to any database by --extract. --apply writes only rows whose
`approve` column is exactly "yes" (case-insensitive), so an unreviewed file
writes nothing.

See docs/2026-08-21-publication-links-design.md, "Backfill".
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
from seek.publications import _rows, ensure_study_publication_columns
from seek.publications_graph import try_sync_study_publications

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


def curator_doi(text: str) -> str | None:
    """The DOI in a hand-entered value, or None.

    Curators paste whatever the publisher shows them — usually a
    https://doi.org/... URL rather than a bare DOI — so this runs the same
    extractor used on descriptions. That also means hand-entered values inherit
    the rules that reject truncated prefixes and supplementary sub-DOIs.
    """
    for candidate in extract_publication_candidates(text):
        if candidate.kind == "doi":
            return candidate.value
    return None


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


def parse_review_file(path: str) -> list[dict]:
    """Rows the curator approved. Anything not exactly "yes" is ignored."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [r for r in reader if (r.get("approve") or "").strip().lower() == "yes"]


def _write_study(study_id: int, doi: str, pmid) -> None:
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            "UPDATE studies SET doi = %s, pmid = %s WHERE id = %s",
            [doi or None, int(pmid) if pmid else None, int(study_id)],
        )


class Command(BaseCommand):
    help = "Extract DOIs from study descriptions for review, then fill studies.doi/pmid."

    def add_arguments(self, parser):
        parser.add_argument("--extract", action="store_true",
                            help="Write a review file. Touches no database.")
        parser.add_argument("--apply", metavar="FILE",
                            help="Fill studies.doi/pmid from a reviewed file.")
        parser.add_argument("--out", default="study_publication_review.tsv",
                            help="Where --extract writes its review file.")
        parser.add_argument("--offline", action="store_true",
                            help="Skip Crossref/NCBI; extract identifiers only.")
        parser.add_argument("--cache", default="study_publication_cache.json",
                            help="Resolution cache path.")

    def handle(self, *args, **options):
        if bool(options["extract"]) == bool(options["apply"]):
            raise CommandError("pass exactly one of --extract or --apply")
        if options["extract"]:
            self._extract(options)
        else:
            self._apply(options)

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
            "you accept, checking title_similarity, then rerun with --apply."
        )

    def _apply(self, options):
        approved = parse_review_file(options["apply"])
        if not approved:
            self.stdout.write("No rows approved — nothing to do.")
            return

        added = ensure_study_publication_columns()
        if added:
            self.stdout.write(f"added columns to studies: {added}")

        for row in approved:
            raw = (row.get("normalized_doi") or "").strip()
            if not raw:
                raise CommandError(f"study {row['study_id']}: approved row has no DOI")
            # Curators paste whatever the publisher shows them — usually a
            # https://doi.org/... URL. Normalize to a bare DOI so the column holds
            # one shape, and so the same rules that reject supplementary sub-DOIs
            # apply to hand-entered values too.
            doi = curator_doi(raw)
            if doi is None:
                raise CommandError(
                    f"study {row['study_id']}: {raw!r} is not a usable DOI "
                    "(truncated, or a supplementary-file sub-DOI)"
                )
            _write_study(int(row["study_id"]), doi, row.get("pmid"))

        synced = try_sync_study_publications()
        self.stdout.write(
            f"filled {len(approved)} studies; graph sync "
            f"{'ok' if synced else 'deferred'}"
        )
        if not synced:
            self.stdout.write("Run `manage.py sync_study_publications` to repair the graph.")
