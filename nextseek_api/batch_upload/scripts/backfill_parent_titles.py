"""One-time backfill: populate parent_titles AND parent_title_hashes on Neo4j Sample nodes.

For each sample with a Parent field in json_metadata:
- UID tokens  -> look up parent's Name/File_PrimaryData from in-memory cache
- Non-UID tokens (unresolved names) -> use as-is (the token IS the identity)

Both parent_titles (raw strings) and parent_title_hashes (hash_identity()
digests) are written in lockstep to satisfy the orphan-resolution writer
contract.

Usage:
    cd /opt/NExtSEEK && .venv/bin/python nextseek_api/batch_upload/scripts/backfill_parent_titles.py
"""
from __future__ import annotations

import json
import logging
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
django.setup()

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

except ImportError:
    _json_loads = json.loads

from neo4j import GraphDatabase
from sqlalchemy import text

from nextseek_api.batch_upload.config import Neo4jConfig
from nextseek_api.batch_upload.db_engine import get_connection
from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens, split_parent_field
from nextseek_api.batch_upload.identity import extract_identity, hash_identity

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)

BATCH_SIZE = 1000


def backfill() -> None:
    """Populate parent_titles on all existing Sample nodes in Neo4j."""
    config = Neo4jConfig.from_django_settings()
    if not config.NEO4J_UPLOAD_ENABLED:
        log.error(
            "Neo4j is not configured or missing keys: %s",
            ", ".join(config.MISSING_KEYS),
        )
        return

    driver = GraphDatabase.driver(config.URI, auth=(config.NEO4J_USER, config.PASSWORD))

    # 1. Fetch all samples with json_metadata from MariaDB
    with get_connection() as conn:
        log.info("Fetching all samples from MariaDB...")
        all_rows = conn.execute(
            text("SELECT uuid, json_metadata FROM samples WHERE json_metadata IS NOT NULL")
        ).fetchall()
        log.info("Fetched %d samples", len(all_rows))

    # 2. Build UID -> (parsed_meta, identity) cache
    uid_to_meta: dict[str, dict] = {}
    uid_to_identity: dict[str, str] = {}

    for uuid_val, jmeta in all_rows:
        try:
            meta = _json_loads(jmeta)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        uid_to_meta[uuid_val] = meta
        identity = extract_identity(meta, uid=uuid_val)
        if identity:
            uid_to_identity[uuid_val] = identity

    log.info(
        "Built identity cache: %d/%d samples have identities",
        len(uid_to_identity),
        len(all_rows),
    )

    # 3. Build parent_titles for each sample that has a Parent field
    updates: list[dict] = []

    for uuid_val, meta in uid_to_meta.items():
        tokens = collect_parent_tokens(meta)
        if not tokens:
            continue
        if not tokens:
            continue

        titles: list[str] = []
        for token in tokens:
            if UID_RE.match(token):
                identity = uid_to_identity.get(token)
                if identity:
                    titles.append(identity)
                else:
                    log.debug(
                        "Could not resolve identity for parent UID %s (child %s)",
                        token,
                        uuid_val,
                    )
            else:
                # Non-UID token is already a human-readable identity
                titles.append(token)

        if titles:
            hashes = [h for h in (hash_identity(t) for t in titles) if h]
            updates.append({
                "uuid": uuid_val,
                "parent_titles": titles,
                "parent_title_hashes": hashes,
            })

    log.info("Will update %d Sample nodes with parent_titles", len(updates))

    # 4. Batch-update Neo4j
    updated_total = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]
        driver.execute_query(
            """
            UNWIND $rows AS row
            MATCH (s:Sample {uuid: row.uuid})
            SET s.parent_titles = row.parent_titles,
                s.parent_title_hashes = row.parent_title_hashes
            """,
            {"rows": batch},
            database_=config.NEO4J_DB,
        )
        updated_total = min(i + BATCH_SIZE, len(updates))
        log.info("Updated %d/%d Sample nodes", updated_total, len(updates))

    driver.close()
    log.info("Backfill complete: %d nodes updated.", len(updates))


if __name__ == "__main__":
    backfill()
