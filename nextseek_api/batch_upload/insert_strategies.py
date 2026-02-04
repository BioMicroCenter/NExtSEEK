"""INSERT strategies: RETURNING (MariaDB 10.5+) vs fallback SELECT dispatch."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db_engine import CAPABILITIES

log = logging.getLogger(__name__)


def insert_samples(
    rows_payload: List[dict], conn: Connection
) -> List[Tuple[int, str]]:
    """Insert samples and return (sample_id, uuid) pairs.

    Dispatches to RETURNING or fallback strategy based on DB capabilities.
    rows_payload: list of dicts with keys: title, sample_type_id, json_metadata,
                  uuid, contributor_id, first_letter, policy_id
    """
    if not rows_payload:
        return []
    if CAPABILITIES.get("insert_returning"):
        return insert_samples_returning(rows_payload, conn)
    return insert_samples_fallback_select(rows_payload, conn)


def insert_samples_returning(
    rows_payload: List[dict], conn: Connection
) -> List[Tuple[int, str]]:
    """Multi-row INSERT ... RETURNING id, uuid (MariaDB 10.5+)."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    values_parts = []
    params = {}
    for i, row in enumerate(rows_payload):
        values_parts.append(
            f"(:title_{i}, :sample_type_id_{i}, :json_metadata_{i}, :uuid_{i}, "
            f":contributor_id_{i}, :first_letter_{i}, :policy_id_{i}, :created_at_{i}, :updated_at_{i})"
        )
        params[f"title_{i}"] = row["title"]
        params[f"sample_type_id_{i}"] = row["sample_type_id"]
        params[f"json_metadata_{i}"] = row["json_metadata"]
        params[f"uuid_{i}"] = row["uuid"]
        params[f"contributor_id_{i}"] = row["contributor_id"]
        params[f"first_letter_{i}"] = row["first_letter"]
        params[f"policy_id_{i}"] = row["policy_id"]
        params[f"created_at_{i}"] = now
        params[f"updated_at_{i}"] = now

    sql = text(
        "INSERT INTO samples "
        "(title, sample_type_id, json_metadata, uuid, contributor_id, "
        "first_letter, policy_id, created_at, updated_at) "
        f"VALUES {', '.join(values_parts)} "
        "RETURNING id, uuid"
    )
    result = conn.execute(sql, params)
    return [(row[0], row[1]) for row in result.fetchall()]


def insert_samples_fallback_select(
    rows_payload: List[dict], conn: Connection
) -> List[Tuple[int, str]]:
    """Multi-row INSERT followed by SELECT for id, uuid pairs."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Step 1: INSERT
    values_parts = []
    params = {}
    uuids = []
    for i, row in enumerate(rows_payload):
        values_parts.append(
            f"(:title_{i}, :sample_type_id_{i}, :json_metadata_{i}, :uuid_{i}, "
            f":contributor_id_{i}, :first_letter_{i}, :policy_id_{i}, :created_at_{i}, :updated_at_{i})"
        )
        params[f"title_{i}"] = row["title"]
        params[f"sample_type_id_{i}"] = row["sample_type_id"]
        params[f"json_metadata_{i}"] = row["json_metadata"]
        params[f"uuid_{i}"] = row["uuid"]
        params[f"contributor_id_{i}"] = row["contributor_id"]
        params[f"first_letter_{i}"] = row["first_letter"]
        params[f"policy_id_{i}"] = row["policy_id"]
        params[f"created_at_{i}"] = now
        params[f"updated_at_{i}"] = now
        uuids.append(row["uuid"])

    sql = text(
        "INSERT INTO samples "
        "(title, sample_type_id, json_metadata, uuid, contributor_id, "
        "first_letter, policy_id, created_at, updated_at) "
        f"VALUES {', '.join(values_parts)}"
    )
    conn.execute(sql, params)

    # Step 2: SELECT back the ids
    select_params = {f"u_{i}": u for i, u in enumerate(uuids)}
    placeholders = ", ".join(f":u_{i}" for i in range(len(uuids)))
    select_sql = text(f"SELECT id, uuid FROM samples WHERE uuid IN ({placeholders})")
    rows = conn.execute(select_sql, select_params).fetchall()
    return [(r[0], r[1]) for r in rows]


def compute_first_letter(title: str) -> str:
    """Return first character uppercase, default '?'."""
    if title and title.strip():
        return title.strip()[0].upper()
    return "?"
