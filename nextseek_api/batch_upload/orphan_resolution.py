"""Orphan parent resolution -- discover and resolve orphaned parent references."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

    def _json_dumps(obj):
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    import json

    _json_loads = json.loads

    def _json_dumps(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


log = logging.getLogger(__name__)

_UID_RE = re.compile(r"^([AD]\.)?[A-Z]{3,}-\d{6}[A-Z]{2,5}-\d+(-PUB\d*)?$")

_DISCOVER_CYPHER = """
MATCH (child:Sample)
WHERE any(name IN child.parent_titles WHERE name IN $new_identities)
RETURN child.id AS id, child.uuid AS uuid, child.parent_titles AS parent_titles
"""


def discover_orphans(
    driver: Any,
    database: str,
    identity_map: Dict[str, str],
) -> List[dict]:
    """Query Neo4j for samples whose parent_titles intersect with identity_map keys.

    Args:
        driver: Neo4j driver instance.
        database: Neo4j database name.
        identity_map: ``{identity: UID}`` from newly uploaded samples.

    Returns:
        List of dicts, each containing:
        - ``id``: Neo4j node ``id`` property (the SEEK sample PK).
        - ``uuid``: the sample UID string.
        - ``parent_titles``: full list of parent title strings from the node.
        - ``matched_tokens``: ``{identity: uid}`` subset that matched.
    """
    if not identity_map:
        return []

    new_identities = list(identity_map.keys())

    result = driver.execute_query(
        _DISCOVER_CYPHER,
        {"new_identities": new_identities},
        database_=database,
    )

    orphans: List[dict] = []
    for record in result.records:
        data = record.data()
        parent_titles = data.get("parent_titles") or []

        matched: Dict[str, str] = {}
        for title in parent_titles:
            if title in identity_map:
                matched[title] = identity_map[title]

        if matched:
            orphans.append(
                {
                    "id": data["id"],
                    "uuid": data["uuid"],
                    "parent_titles": parent_titles,
                    "matched_tokens": matched,
                }
            )

    log.info(
        "Orphan discovery: %d candidates found for %d new identities",
        len(orphans),
        len(new_identities),
    )
    return orphans


# ---------------------------------------------------------------------------
# Resolve helpers & constants
# ---------------------------------------------------------------------------

_SOP_URL_RE = re.compile(r"/sops/(\d+)")

_FETCH_METADATA_SQL = text(
    "SELECT json_metadata FROM samples WHERE id = :sample_id"
)

_UPDATE_METADATA_SQL = text(
    "UPDATE samples SET json_metadata = :meta, updated_at = NOW() WHERE id = :sample_id"
)

_DERIVED_FROM_CYPHER = """
UNWIND $rows AS row
MATCH (c:Sample {uuid: row.child_uuid})
MATCH (p:Sample {uuid: row.parent_uuid})
MERGE (c)-[r:DERIVED_FROM]->(p)
SET r.protocol_id = row.protocol_id,
    r.protocol_title = row.protocol_title,
    r.internal_assay_id = row.internal_assay_id,
    r.internal_assay_title = row.internal_assay_title,
    r.child_id = row.child_id,
    r.parent_id = row.parent_id
"""


def _extract_protocol(meta: dict, sql_conn: Any) -> Tuple[Optional[int], Optional[str]]:
    """Extract protocol_id and protocol_title from sample metadata."""
    protocol_str = meta.get("Protocol") or meta.get("protocol") or ""
    m = _SOP_URL_RE.search(str(protocol_str))
    if not m:
        return None, None
    protocol_id = int(m.group(1))
    row = sql_conn.execute(
        text("SELECT title FROM sops WHERE id = :id"), {"id": protocol_id}
    ).fetchone()
    return protocol_id, (row[0] if row else None)


def resolve_orphans(
    orphans: List[dict],
    parent_info: Dict[str, dict],
    sql_conn: Any,
    neo4j_driver: Any,
    neo4j_database: str,
) -> dict:
    """Resolve orphan parent references: update MariaDB Parent field + create DERIVED_FROM edges.

    For each candidate orphan, checks if the matched identity token is still present
    in the Parent field. If already resolved (token replaced with UID), skips silently.

    Does NOT modify parent_titles — it is permanent metadata.

    Returns:
        {"resolved": int, "edges_created": int}
    """
    resolved = 0
    edge_rows: List[dict] = []

    for orphan in orphans:
        sample_id = orphan["id"]
        child_uuid = orphan["uuid"]
        matched_tokens: Dict[str, str] = orphan.get("matched_tokens", {})

        if not matched_tokens:
            continue

        # Fetch current json_metadata from MariaDB
        row = sql_conn.execute(
            _FETCH_METADATA_SQL, {"sample_id": sample_id}
        ).fetchone()
        if not row or not row[0]:
            continue

        meta = _json_loads(row[0])
        parent_field = meta.get("Parent") or meta.get("parent") or ""
        parent_parts = [p.strip() for p in parent_field.split(";")]

        # Replace matched tokens with UIDs
        any_replaced = False
        for token, uid in matched_tokens.items():
            if token in parent_parts:
                parent_parts = [uid if p == token else p for p in parent_parts]
                any_replaced = True

                # Build DERIVED_FROM edge payload
                p_info = parent_info.get(uid, {})
                protocol_id, protocol_title = _extract_protocol(meta, sql_conn)
                edge_rows.append({
                    "child_uuid": child_uuid,
                    "parent_uuid": p_info.get("uuid", uid),
                    "protocol_id": protocol_id,
                    "protocol_title": protocol_title,
                    "internal_assay_id": None,
                    "internal_assay_title": None,
                    "child_id": sample_id,
                    "parent_id": p_info.get("sample_id"),
                })

        if any_replaced:
            # Update Parent field in json_metadata
            parent_key = "Parent" if "Parent" in meta else "parent"
            meta[parent_key] = ";".join(parent_parts)
            sql_conn.execute(
                _UPDATE_METADATA_SQL,
                {"meta": _json_dumps(meta), "sample_id": sample_id},
            )
            resolved += 1

    # Batch-create DERIVED_FROM edges in Neo4j
    edges_created = 0
    if edge_rows:
        neo4j_driver.execute_query(
            _DERIVED_FROM_CYPHER,
            {"rows": edge_rows},
            database_=neo4j_database,
        )
        edges_created = len(edge_rows)

    log.info(
        "Orphan resolution: %d samples resolved, %d edges created",
        resolved,
        edges_created,
    )
    return {"resolved": resolved, "edges_created": edges_created}
