"""Project-sample and assay-asset linking (SQL operations 4 and 5)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)


def batch_insert_projects_samples(
    project_id: int, sample_ids: List[int], conn: Connection
) -> int:
    """Link samples to a project, idempotently.

    Returns count of newly inserted links.
    """
    if not project_id or not sample_ids:
        return 0

    # Step 1: Find existing links (chunked by 1000)
    existing: Set[int] = set()
    for chunk_start in range(0, len(sample_ids), 1000):
        chunk = sample_ids[chunk_start : chunk_start + 1000]
        params = {"pid": project_id}
        params.update({f"sid_{i}": sid for i, sid in enumerate(chunk)})
        placeholders = ", ".join(f":sid_{i}" for i in range(len(chunk)))
        sql = text(
            f"SELECT sample_id FROM projects_samples "
            f"WHERE project_id = :pid AND sample_id IN ({placeholders})"
        )
        rows = conn.execute(sql, params).fetchall()
        existing.update(r[0] for r in rows)

    # Step 2: Insert new links
    new_ids = [sid for sid in sample_ids if sid not in existing]
    if not new_ids:
        return 0

    values_parts = []
    params = {}
    for i, sid in enumerate(new_ids):
        values_parts.append(f"(:pid_{i}, :sid_{i})")
        params[f"pid_{i}"] = project_id
        params[f"sid_{i}"] = sid

    sql = text(
        f"INSERT INTO projects_samples (project_id, sample_id) "
        f"VALUES {', '.join(values_parts)}"
    )
    conn.execute(sql, params)
    log.info("Linked %d samples to project %d", len(new_ids), project_id)
    return len(new_ids)


def batch_insert_assay_assets(
    assay_records: List[Tuple[str, int, List[int]]],
    direction_by_pair: Dict[Tuple[str, int], int],
    conn: Connection,
) -> int:
    """Link samples to assays as assets, using DAG-computed directions.

    assay_records: list of (uid, sample_id, assay_ids)
    direction_by_pair: (uid, assay_id) -> 0|1

    Returns count of newly inserted links.
    """
    if not assay_records:
        return 0

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Build full list of (assay_id, sample_id, uid) tuples
    all_links: List[Tuple[int, int, str]] = []
    for uid, sample_id, assay_ids in assay_records:
        for aid in assay_ids:
            all_links.append((aid, sample_id, uid))

    if not all_links:
        return 0

    # Step 1: Find existing links (grouped by assay_id, chunked by 1000)
    existing: Set[Tuple[int, int]] = set()  # (assay_id, sample_id)

    # Group by assay_id for efficient querying
    by_assay: Dict[int, List[int]] = {}
    for aid, sid, _uid in all_links:
        by_assay.setdefault(aid, []).append(sid)

    for aid, sids in by_assay.items():
        unique_sids = list(set(sids))
        for chunk_start in range(0, len(unique_sids), 1000):
            chunk = unique_sids[chunk_start : chunk_start + 1000]
            params = {"aid": aid}
            params.update({f"sid_{i}": sid for i, sid in enumerate(chunk)})
            placeholders = ", ".join(f":sid_{i}" for i in range(len(chunk)))
            sql = text(
                f"SELECT assay_id, asset_id FROM assay_assets "
                f"WHERE assay_id = :aid AND asset_id IN ({placeholders}) "
                f"AND asset_type = 'Sample'"
            )
            rows = conn.execute(sql, params).fetchall()
            existing.update((r[0], r[1]) for r in rows)

    # Step 2: Insert new links
    new_links = [(aid, sid, uid) for aid, sid, uid in all_links if (aid, sid) not in existing]
    if not new_links:
        return 0

    values_parts = []
    params = {}
    for i, (aid, sid, uid) in enumerate(new_links):
        direction = direction_by_pair.get((uid, aid), 0)
        values_parts.append(
            f"(:aid_{i}, :sid_{i}, :ver_{i}, :cat_{i}, :cau_{i}, "
            f":rtid_{i}, :atype_{i}, :dir_{i})"
        )
        params[f"aid_{i}"] = aid
        params[f"sid_{i}"] = sid
        params[f"ver_{i}"] = 1
        params[f"cat_{i}"] = now
        params[f"cau_{i}"] = now
        params[f"rtid_{i}"] = None
        params[f"atype_{i}"] = "Sample"
        params[f"dir_{i}"] = direction

    sql = text(
        "INSERT INTO assay_assets "
        "(assay_id, asset_id, version, created_at, updated_at, "
        "relationship_type_id, asset_type, direction) "
        f"VALUES {', '.join(values_parts)}"
    )
    conn.execute(sql, params)
    log.info("Created %d assay-asset links", len(new_links))
    return len(new_links)
