"""Orphan parent resolution -- discover and resolve orphaned parent references.

A sample whose Parent field named a sample nobody had uploaded yet keeps that name in its metadata, and its graph
node keeps it in ``parent_titles`` and ``parent_title_hashes`` (both projection-owned now: the sync design, section 5
E6, R4). When the parent finally arrives, ``discover_orphans`` finds those children through the hash list and
``resolve_orphans`` replaces the name with the parent's UID in MariaDB.

The DERIVED_FROM edge is no longer written here. ``resolve_orphans`` reports the children it rewrote, its caller
enqueues one ``samples`` outbox row for each once the rewrite has committed
(``tasks.py::resolve_orphans_task``), and the graph sync writes the edge and its labels from MySQL: one code path for
every graph write (the sync design, sections 7 and 8; C-14).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

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


from .helpers import collect_parent_tokens
from .identity import hash_identity

log = logging.getLogger(__name__)

_DISCOVER_CYPHER = """
MATCH (child:Sample)
WHERE any(h IN child.parent_title_hashes WHERE h IN $new_identity_hashes)
RETURN child.id AS id, child.uuid AS uuid, child.parent_titles AS parent_titles
"""


def discover_orphans(
    driver: Any,
    database: str,
    identity_map: Dict[str, str],
) -> List[dict]:
    """Query Neo4j for samples whose parent_title_hashes intersect with hashed identity_map keys.

    Args:
        driver: Neo4j driver instance.
        database: Neo4j database name.
        identity_map: ``{identity: UID}`` from newly uploaded samples.

    Returns:
        List of dicts, each containing:
        - ``id``: Neo4j node ``id`` property (the SEEK sample PK).
        - ``uuid``: the sample UID string.
        - ``parent_titles``: full list of parent title strings from the node.
        - ``matched_tokens``: ``{identity: uid}`` subset that matched (exact-case).
    """
    if not identity_map:
        return []

    new_identity_hashes = [
        h for h in (hash_identity(k) for k in identity_map.keys()) if h
    ]
    if not new_identity_hashes:
        return []

    result = driver.execute_query(
        _DISCOVER_CYPHER,
        {"new_identity_hashes": new_identity_hashes},
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
        len(identity_map),
    )
    return orphans


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------

_FETCH_METADATA_SQL = text(
    "SELECT json_metadata FROM samples WHERE id = :sample_id"
)

_UPDATE_METADATA_SQL = text(
    "UPDATE samples SET json_metadata = :meta, updated_at = NOW() WHERE id = :sample_id"
)


def resolve_orphans(
    orphans: List[dict],
    sql_conn: Any,
) -> dict:
    """Resolve orphan parent references: replace the matched identity token with the parent's UID in MariaDB.

    For each candidate orphan, checks if the matched identity token is still present
    in the Parent field. If already resolved (token replaced with UID), skips silently.

    Does NOT modify parent_titles -- it is permanent metadata -- and writes nothing to Neo4j.

    Returns:
        ``{"resolved": int, "sample_ids": [...]}``. The ids are the children this call rewrote, in the order it
        rewrote them: the caller enqueues them for the graph sync once the transaction has committed.
    """
    resolved_ids: List[int] = []

    for orphan in orphans:
        sample_id = orphan["id"]
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
        parent_parts = collect_parent_tokens(meta)

        # Replace matched tokens with UIDs
        any_replaced = False
        for token, uid in matched_tokens.items():
            if token in parent_parts:
                parent_parts = [uid if p == token else p for p in parent_parts]
                any_replaced = True

        if any_replaced:
            # Update Parent field in json_metadata
            parent_key = "Parent" if "Parent" in meta else "parent"
            # Deduplicate while preserving order
            seen_wb: set = set()
            deduped: list = []
            for t in parent_parts:
                if t not in seen_wb:
                    seen_wb.add(t)
                    deduped.append(t)
            meta[parent_key] = ";".join(deduped)
            sql_conn.execute(
                _UPDATE_METADATA_SQL,
                {"meta": _json_dumps(meta), "sample_id": sample_id},
            )
            resolved_ids.append(sample_id)

    log.info(
        "Orphan resolution: %d samples resolved; their lineage is the graph sync's to write",
        len(resolved_ids),
    )
    return {"resolved": len(resolved_ids), "sample_ids": resolved_ids}
