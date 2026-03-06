"""One-time backfill: populate parent_titles on Neo4j Sample nodes.

For each sample with a Parent field in json_metadata:
- UID tokens  -> look up parent's Name/File_PrimaryData from in-memory cache
- Non-UID tokens (unresolved names) -> use as-is (the token IS the identity)

Usage:
    cd /opt/NExtSEEK && .venv/bin/python nextseek_api/batch_upload/scripts/backfill_parent_titles.py
"""
from __future__ import annotations

import json
import logging
import os
import re

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

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)

_UID_RE = re.compile(r"^([AD]\.)?[A-Z]{3,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?$")
_PARENT_SPLIT_RE = re.compile(r";")  # semicolons only — Names may contain spaces/commas
_FILE_BASED_PREFIXES = ("D.", "A.")
_FILE_PRIMARY_FIELDS = (
    "File_PrimaryData",
    "File_PrimartyData",
    "File_PrimaryData_Forward",
    "File_PrimartyData_Forward",
    "File_PrimaryData_Reverse",
    "File_PrimartyData_Reverse",
)
BATCH_SIZE = 1000


def _extract_identity(meta: dict, sample_type_hint: str) -> str | None:
    """Extract Name or File_PrimaryData from parsed json_metadata.

    Mirrors neo4j_sync._extract_identity_from_meta logic.
    """
    if any(sample_type_hint.startswith(p) for p in _FILE_BASED_PREFIXES):
        for field in _FILE_PRIMARY_FIELDS:
            val = meta.get(field)
            if val and str(val).strip():
                return str(val).strip()
        return None
    name = meta.get("Name") or meta.get("name")
    if name is not None:
        s = str(name).strip()
        return s if s else None
    return None


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
        # Infer sample type prefix from UUID for file-based check
        prefix = uuid_val.split("-")[0] if "-" in uuid_val else ""
        identity = _extract_identity(meta, prefix)
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
        parent_str = meta.get("Parent") or meta.get("parent") or ""
        if not parent_str or not isinstance(parent_str, str):
            continue
        tokens = [t.strip() for t in _PARENT_SPLIT_RE.split(parent_str) if t.strip()]
        if not tokens:
            continue

        titles: list[str] = []
        for token in tokens:
            if _UID_RE.match(token):
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
            updates.append({"uuid": uuid_val, "parent_titles": titles})

    log.info("Will update %d Sample nodes with parent_titles", len(updates))

    # 4. Batch-update Neo4j
    updated_total = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i : i + BATCH_SIZE]
        driver.execute_query(
            """
            UNWIND $rows AS row
            MATCH (s:Sample {uuid: row.uuid})
            SET s.parent_titles = row.parent_titles
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
