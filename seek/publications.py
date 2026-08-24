"""Publication (DOI/PMID) links for samples.

The DOI and PMID are attributes of the study. A sample is published in a paper if
it belongs to a study whose doi or pmid is set — reached in SQL through
assay_assets, and in the graph through IN_STUDY.

All SQL here runs on the SEEK connection, which is where samples, assays and
studies live. See docs/2026-08-21-publication-links-design.md.

Note the deliberate asymmetry with Neo4j: the MySQL columns are lowercase
``doi``/``pmid`` and genuinely NULL when unset, while the graph properties are
uppercase ``DOI``/``PMID`` and an empty string when unset. Do not "tidy" either
side to match the other — see seek/publications_graph.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import connections

#: Column definitions for the two attributes added to seek_production.studies.
#: MySQL 8 has no ADD COLUMN IF NOT EXISTS, so presence is checked first.
STUDY_PUBLICATION_COLUMNS = {
    "doi": "VARCHAR(255) NULL",
    "pmid": "INT NULL",
}


@dataclass(frozen=True)
class PublicationRef:
    study_id: int
    study_title: str | None
    doi: str | None
    pmid: int | None

    @property
    def doi_url(self) -> str | None:
        return f"https://doi.org/{self.doi}" if self.doi else None

    @property
    def pmid_url(self) -> str | None:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else None

    def citation(self) -> str:
        """What to show in one cell. The study title is the paper title here."""
        return self.study_title or self.doi or (str(self.pmid) if self.pmid else "")

    def as_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "study_title": self.study_title,
            "doi": self.doi,
            "pmid": self.pmid,
            "doi_url": self.doi_url,
            "pmid_url": self.pmid_url,
            "citation": self.citation(),
        }


def _rows(sql: str, params: list | None = None) -> list[dict]:
    """Run a parameterized query on the SEEK connection, return dict rows."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _execute(sql: str) -> None:
    """Run a statement that returns no rows, on the SEEK connection."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql)


def ensure_study_publication_columns() -> list[str]:
    """Add doi/pmid to studies if absent. Returns the names actually added.

    A Django migration cannot do this: the entrypoint runs a plain `migrate`,
    which touches only the default database, so a migration against the SEEK
    schema would silently never run. See design finding 13.
    """
    schema = settings.DATABASES[settings.SEEK_DATABASE]["NAME"]
    present = {
        r["COLUMN_NAME"]
        for r in _rows(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'studies' "
            "AND COLUMN_NAME IN ('doi', 'pmid')",
            [schema],
        )
    }
    added = []
    for name, definition in STUDY_PUBLICATION_COLUMNS.items():
        if name in present:
            continue
        _execute(f"ALTER TABLE studies ADD COLUMN {name} {definition}")
        added.append(name)
    return added


_SAMPLE_TO_STUDY_JOIN = """
    FROM assay_assets aa
    JOIN assays a ON a.id = aa.assay_id
    JOIN studies s ON s.id = a.study_id
    WHERE aa.asset_type = 'Sample'
      AND (s.doi IS NOT NULL OR s.pmid IS NOT NULL)
"""


def publications_for_samples(sample_ids) -> dict[int, list[PublicationRef]]:
    """Map each sample id to the published studies it belongs to. One query."""
    ids = list(sample_ids)
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT DISTINCT aa.asset_id AS sample_id, s.id AS study_id,
               s.title AS study_title, s.doi AS doi, s.pmid AS pmid
        {_SAMPLE_TO_STUDY_JOIN}
          AND aa.asset_id IN ({placeholders})
        ORDER BY aa.asset_id, s.id
    """
    grouped: dict[int, list[PublicationRef]] = {}
    for row in _rows(sql, ids):
        grouped.setdefault(row["sample_id"], []).append(
            PublicationRef(row["study_id"], row["study_title"], row["doi"], row["pmid"])
        )
    return grouped


def publications_for_sample(sample_id) -> list[dict]:
    """The published studies one sample belongs to, as template-ready dicts."""
    key = int(sample_id)
    return [ref.as_dict() for ref in publications_for_samples([key]).get(key, [])]


def _doi_in(text: str) -> str | None:
    """The DOI in a user-typed string, or None. Accepts a bare DOI or a doi.org URL."""
    from .doi_extract import extract_publication_candidates

    for candidate in extract_publication_candidates(text):
        if candidate.kind == "doi":
            return candidate.value
    return None


def resolve_study_ids(query: str) -> list[int]:
    """Study ids matching a DOI, a PMID, or an exact title.

    Returns every match rather than erroring on several: a paper spanning two
    studies is legitimate, and the caller wants the union of their samples.
    """
    text = (query or "").strip()
    if not text:
        return []

    doi = _doi_in(text)
    if doi:
        rows = _rows("SELECT id FROM studies WHERE LOWER(doi) = %s", [doi])
    elif text.isdigit():
        rows = _rows("SELECT id FROM studies WHERE pmid = %s", [text])
    else:
        rows = _rows("SELECT id FROM studies WHERE LOWER(title) = LOWER(%s)", [text])
    return [r["id"] for r in rows]


def sample_ids_subquery(study_ids) -> str:
    """Samples belonging to the given studies, as a spliceable subquery.

    The search builder concatenates SQL strings, so ids are forced through int().
    """
    ids = ",".join(str(int(i)) for i in study_ids)
    return (
        "SELECT aa.asset_id FROM assay_assets aa "
        "JOIN assays a ON a.id = aa.assay_id "
        f"WHERE aa.asset_type = 'Sample' AND a.study_id IN ({ids})"
    )


def published_sample_ids_subquery() -> str:
    """Samples belonging to any study that has a DOI or a PMID."""
    return f"SELECT DISTINCT aa.asset_id {_SAMPLE_TO_STUDY_JOIN}"


def publication_where_clause(query, published_only: bool) -> str:
    """A WHERE fragment constraining samples by publication, or "".

    Returns " AND 1=0" when the query names a paper that does not exist — an
    empty result is the honest answer and is easier to reason about than silently
    dropping the filter.
    """
    if query:
        study_ids = resolve_study_ids(query)
        if not study_ids:
            return " AND 1=0"
        return f" AND A.id IN ({sample_ids_subquery(study_ids)})"
    if published_only:
        return f" AND A.id IN ({published_sample_ids_subquery()})"
    return ""


def attach_publications(rows: list[dict]) -> list[dict]:
    """Add a ``publications`` key to each search result row, in place.

    One query for the whole page of results, regardless of page size.
    """
    if not rows:
        return rows
    grouped = publications_for_samples([r["id"] for r in rows])
    for row in rows:
        row["publications"] = [ref.as_dict() for ref in grouped.get(row["id"], [])]
    return rows
