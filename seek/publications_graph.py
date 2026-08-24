"""Copy study publication attributes from MySQL into Neo4j.

MySQL is the source of truth. Every study is written, including those with no
DOI — that is what clears a value removed in MySQL rather than leaving a stale
one in the graph.

Two naming facts, both verified against the live instances on 2026-08-24 and
both easy to get wrong silently:

- The graph properties are **DOI and PMID, uppercase**, while the MySQL columns
  are lowercase. Cypher returns null for a wrong-cased property instead of
  raising, so a casing slip looks like "nothing is published".
- The unset value is **'' , not null**. Every study on dev and prod currently
  holds empty strings, so ``IS NOT NULL`` is true for all of them. Test
  emptiness with ``coalesce(st.DOI, '') <> ''``, and write ``''`` rather than
  null so the two sentinels never mix.

The properties exist on all 51 dev studies, 49 of 56 prod studies, and none
locally; SET covers every case. MATCH, never MERGE: a study missing from the
graph is a graph-sync problem to investigate, not a node to invent here.

See docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

import logging

from django.conf import settings
from neo4j import GraphDatabase

from .publications import _rows

log = logging.getLogger(__name__)

STUDY_PROPERTY_CYPHER = """
UNWIND $rows AS row
MATCH (st:Study {id: row.study_id})
SET st.DOI = row.doi, st.PMID = row.pmid
"""


def _driver():
    cfg = settings.NEO4J_DATABASE
    return GraphDatabase.driver(cfg["URI"], auth=cfg["AUTH"])


def _db_name() -> str:
    return settings.NEO4J_DATABASE["NAME"]


def _study_rows() -> list[dict]:
    return _rows("SELECT id, doi, pmid FROM studies ORDER BY id")


def build_study_rows(rows: list[dict]) -> list[dict]:
    """Graph payload: '' for unset, PMID stringified to keep the type stable."""
    return [
        {
            "study_id": r["id"],
            "doi": r.get("doi") or "",
            "pmid": str(r["pmid"]) if r.get("pmid") else "",
        }
        for r in rows
    ]


def sync_study_publications() -> dict:
    """Write DOI/PMID onto every Study node. Idempotent."""
    payload = build_study_rows(_study_rows())
    if payload:
        with _driver() as driver:
            driver.execute_query(
                STUDY_PROPERTY_CYPHER, {"rows": payload}, database_=_db_name()
            )
    return {
        "studies": len(payload),
        "with_doi": sum(1 for r in payload if r["doi"]),
        "with_pmid": sum(1 for r in payload if r["pmid"]),
    }


def try_sync_study_publications() -> bool:
    """Sync, swallowing any failure. Returns False if the graph was not written.

    Curation must not fail because Neo4j is unavailable — the standalone
    management command repairs it later.
    """
    try:
        sync_study_publications()
        return True
    except Exception:
        log.warning("Study publication graph sync deferred", exc_info=True)
        return False
