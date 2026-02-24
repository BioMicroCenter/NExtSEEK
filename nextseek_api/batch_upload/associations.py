"""Project-sample and assay-asset linking (SQL operations 4 and 5)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)

# (assay_id, asset_id, asset_type, direction, relationship_type_id, version)
AssayAssetRecord = Tuple[int, int, str, int, Optional[int], Optional[int]]


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
    records: List[AssayAssetRecord],
    conn: Connection,
) -> int:
    """Idempotently insert into assay_assets.

    Record tuple is (assay_id, asset_id, asset_type, direction, relationship_type_id, version).
    Version is forced to 1 for all inserted rows.
    Only inserts rows that do not already exist with the same identifying fields.
    Returns number of new rows inserted.
    """
    if not records:
        return 0

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Group records by assay_id to keep IN clauses small and leverage index
    grouped: Dict[int, List[AssayAssetRecord]] = {}
    for rec in records:
        grouped.setdefault(int(rec[0]), []).append(rec)

    inserted_total = 0

    for assay_id, group in grouped.items():
        # Fetch existing rows for this assay_id
        asset_ids = list({int(r[1]) for r in group})
        existing_keys: Set[Tuple[int, int]] = set()  # (assay_id, asset_id)

        for chunk_start in range(0, len(asset_ids), 1000):
            chunk = asset_ids[chunk_start : chunk_start + 1000]
            params = {"aid": assay_id}
            params.update({f"sid_{i}": sid for i, sid in enumerate(chunk)})
            placeholders = ", ".join(f":sid_{i}" for i in range(len(chunk)))
            sql = text(
                f"SELECT assay_id, asset_id FROM assay_assets "
                f"WHERE assay_id = :aid AND asset_id IN ({placeholders}) "
                f"AND asset_type = 'Sample'"
            )
            rows = conn.execute(sql, params).fetchall()
            existing_keys.update((int(r[0]), int(r[1])) for r in rows)

        # Filter to new records only
        new_records = [rec for rec in group if (int(rec[0]), int(rec[1])) not in existing_keys]
        if not new_records:
            continue

        # Insert new records in chunks
        for chunk_start in range(0, len(new_records), 1000):
            chunk = new_records[chunk_start : chunk_start + 1000]
            values_parts = []
            params = {}
            for i, rec in enumerate(chunk):
                _assay_id, asset_id, asset_type, direction, rel_type_id, _version = rec
                values_parts.append(
                    f"(:aid_{i}, :sid_{i}, :ver_{i}, :cat_{i}, :cau_{i}, "
                    f":rtid_{i}, :atype_{i}, :dir_{i})"
                )
                params[f"aid_{i}"] = int(_assay_id)
                params[f"sid_{i}"] = int(asset_id)
                params[f"ver_{i}"] = 1  # force version to 1
                params[f"cat_{i}"] = now
                params[f"cau_{i}"] = now
                params[f"rtid_{i}"] = None if rel_type_id is None else int(rel_type_id)
                params[f"atype_{i}"] = str(asset_type) if asset_type else "Sample"
                params[f"dir_{i}"] = int(direction) if direction is not None else 0

            sql = text(
                "INSERT INTO assay_assets "
                "(assay_id, asset_id, version, created_at, updated_at, "
                "relationship_type_id, asset_type, direction) "
                f"VALUES {', '.join(values_parts)}"
            )
            conn.execute(sql, params)
            inserted_total += len(chunk)

    if inserted_total:
        log.info("Created %d assay-asset links", inserted_total)
    return inserted_total
