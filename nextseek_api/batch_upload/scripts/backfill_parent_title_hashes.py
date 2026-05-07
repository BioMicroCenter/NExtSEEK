"""One-time backfill: populate parent_title_hashes on Neo4j Sample nodes.

For each Sample node that already has parent_titles, compute
parent_title_hashes = [hash_identity(t) for t in parent_titles] and write
it to the node. Existing parent_titles are not modified.

Run after the main parent_titles backfill (backfill_parent_titles.py) and
after the new code that maintains both lists at write time has been
deployed. Safe to rerun.

Usage:
    cd /opt/NExtSEEK && .venv/bin/python nextseek_api/batch_upload/scripts/backfill_parent_title_hashes.py
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
django.setup()

from neo4j import GraphDatabase

from nextseek_api.batch_upload.config import Neo4jConfig
from nextseek_api.batch_upload.identity import hash_identity

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)

BATCH_SIZE = 1000

_FETCH_CYPHER = """
MATCH (s:Sample)
WHERE s.parent_titles IS NOT NULL AND size(s.parent_titles) > 0
RETURN s.uuid AS uuid, s.parent_titles AS parent_titles
"""

_WRITE_CYPHER = """
UNWIND $rows AS row
MATCH (s:Sample {uuid: row.uuid})
SET s.parent_title_hashes = row.parent_title_hashes
"""


def compute_hash_updates(rows: Iterable[dict]) -> List[dict]:
    """Build the per-node update payload from fetched (uuid, parent_titles) rows."""
    updates: List[dict] = []
    for row in rows:
        titles = row.get("parent_titles") or []
        if not titles:
            continue
        hashes = [h for h in (hash_identity(t) for t in titles) if h]
        if not hashes:
            continue
        updates.append({"uuid": row["uuid"], "parent_title_hashes": hashes})
    return updates


def write_hash_updates(driver: Any, database: str, updates: List[dict]) -> int:
    """Write parent_title_hashes in batches of BATCH_SIZE. Returns number written."""
    if not updates:
        return 0
    written = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]
        driver.execute_query(_WRITE_CYPHER, {"rows": batch}, database_=database)
        written += len(batch)
        log.info("Updated %d/%d Sample nodes", written, len(updates))
    return written


def backfill() -> None:
    config = Neo4jConfig.from_django_settings()
    if not config.NEO4J_UPLOAD_ENABLED:
        log.error(
            "Neo4j is not configured or missing keys: %s",
            ", ".join(config.MISSING_KEYS),
        )
        return

    driver = GraphDatabase.driver(config.URI, auth=(config.NEO4J_USER, config.PASSWORD))
    try:
        log.info("Fetching Sample nodes with parent_titles from Neo4j...")
        result = driver.execute_query(_FETCH_CYPHER, database_=config.NEO4J_DB)
        rows = [r.data() for r in result.records]
        log.info("Fetched %d candidate samples", len(rows))

        updates = compute_hash_updates(rows)
        log.info("Will update %d Sample nodes with parent_title_hashes", len(updates))

        write_hash_updates(driver, config.NEO4J_DB, updates)
    finally:
        driver.close()
    log.info("Backfill complete.")


if __name__ == "__main__":
    backfill()
