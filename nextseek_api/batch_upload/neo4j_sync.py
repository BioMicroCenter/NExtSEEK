"""Stage 6: NEO4J — Bulk MERGE with retry for nodes and relationships."""
from __future__ import annotations

import json
import logging

try:
    import orjson
    def _json_loads(s): return orjson.loads(s)
except ImportError:
    _json_loads = json.loads
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import psutil
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .config import Neo4jConfig
from .models import (
    DerivedFromRelRow,
    DirectionComputation,
    InInvestigationRelRow,
    InStudyRelRow,
    InputRowModel,
    InsertableSample,
    InvestigationNodeRow,
    Metrics,
    NodeRow,
    OfTypeRelRow,
    RowOutcome,
    SampleTypeNodeRow,
    StudyNodeRow,
)

log = logging.getLogger(__name__)

_SOP_URL_RE = re.compile(r"/sops/(\d+)")

# ── retry decorator ───────────────────────────────────────────────────────


def _is_transient_error(e: Exception) -> bool:
    """Check if a Neo4j error is transient and retryable."""
    try:
        from neo4j.exceptions import TransientError, ServiceUnavailable, SessionExpired
        return isinstance(e, (TransientError, ServiceUnavailable, SessionExpired))
    except ImportError:
        return False


def _retry(fn: Callable, attempts: int = 3, backoff_base: float = 0.5):
    """Retry with exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts or not _is_transient_error(e):
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            jitter = random.uniform(0, delay * 0.3)
            log.warning(
                "Neo4j transient error (attempt %d/%d), retrying in %.1fs: %s",
                attempt, attempts, delay + jitter, e,
            )
            time.sleep(delay + jitter)


# ── constraint setup ──────────────────────────────────────────────────────


def ensure_constraints(driver, database_name: str) -> None:
    """Create uniqueness constraints if they don't exist."""
    constraints = [
        "CREATE CONSTRAINT sample_id_unique IF NOT EXISTS FOR (s:Sample) REQUIRE s.id IS UNIQUE",
        "CREATE CONSTRAINT sample_uuid_unique IF NOT EXISTS FOR (s:Sample) REQUIRE s.uuid IS UNIQUE",
        "CREATE CONSTRAINT sample_type_title_unique IF NOT EXISTS FOR (st:SampleType) REQUIRE st.title IS UNIQUE",
        "CREATE CONSTRAINT study_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE",
        "CREATE CONSTRAINT investigation_id_unique IF NOT EXISTS FOR (inv:Investigation) REQUIRE inv.id IS UNIQUE",
    ]
    for cypher in constraints:
        try:
            driver.execute_query(cypher, database_=database_name)
        except Exception:
            log.warning("Could not create constraint: %s", cypher, exc_info=True)


# ── bulk merge operations ─────────────────────────────────────────────────


def bulk_merge_nodes(
    driver, db_name: str, node_rows: List[NodeRow], chunk_size: int = 10_000
) -> Tuple[int, int]:
    """MERGE Sample nodes in chunks. Returns (created, matched)."""
    created_total = 0
    matched_total = 0
    cypher = """
    UNWIND $rows AS row
    MERGE (s:Sample {uuid: row.sample_uuid})
    SET s.id = row.sample_id, s.type = row.sample_type, s += row.properties
    """

    for i in range(0, len(node_rows), chunk_size):
        chunk = node_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]
        t0 = time.perf_counter()

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        elapsed = time.perf_counter() - t0
        log.info(
            "Neo4j bulk_merge_nodes chunk %d-%d: %.1fs",
            i, i + len(chunk), elapsed,
        )
        # Counters from summary
        summary = result.summary if hasattr(result, "summary") else None
        if summary and hasattr(summary, "counters"):
            created_total += getattr(summary.counters, "nodes_created", 0)

    matched_total = len(node_rows) - created_total
    return created_total, matched_total


def bulk_merge_sample_type_nodes(
    driver, db_name: str, st_rows: List[SampleTypeNodeRow], chunk_size: int = 10_000
) -> int:
    """MERGE SampleType nodes. Returns count of created nodes."""
    created = 0
    cypher = """
    UNWIND $rows AS row
    MERGE (st:SampleType {title: row.title})
    ON CREATE SET st.id = row.id
    ON MATCH SET st.id = coalesce(row.id, st.id)
    """

    for i in range(0, len(st_rows), chunk_size):
        chunk = st_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        summary = result.summary if hasattr(result, "summary") else None
        if summary and hasattr(summary, "counters"):
            created += getattr(summary.counters, "nodes_created", 0)

    return created


def bulk_merge_relationships(
    driver, db_name: str, derived_from_rows: List[DerivedFromRelRow], chunk_size: int = 20_000
) -> int:
    """MERGE DERIVED_FROM relationships. Returns count processed."""
    total = 0
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Sample {uuid: row.child_uuid})
    MATCH (p:Sample {uuid: row.parent_uuid})
    MERGE (c)-[r:DERIVED_FROM]->(p)
    SET r.protocol_id = row.protocol_id, r.protocol_title = row.protocol_title,
        r.internal_assay_id = row.internal_assay_id, r.internal_assay_title = row.internal_assay_title,
        r.child_id = row.child_id, r.parent_id = row.parent_id
    RETURN count(r) AS processed
    """

    for i in range(0, len(derived_from_rows), chunk_size):
        chunk = derived_from_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        if result.records:
            total += result.records[0]["processed"]

    return total


def bulk_merge_of_type_relationships(
    driver, db_name: str, of_type_rows: List[OfTypeRelRow], chunk_size: int = 20_000
) -> int:
    """MERGE OF_TYPE relationships. Returns count processed."""
    total = 0
    cypher = """
    UNWIND $rows AS row
    MATCH (s:Sample {uuid: row.sample_uuid})
    MATCH (st:SampleType {id: row.sample_type_id})
    MERGE (s)-[r:OF_TYPE]->(st)
    RETURN count(r) AS processed
    """

    for i in range(0, len(of_type_rows), chunk_size):
        chunk = of_type_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        if result.records:
            total += result.records[0]["processed"]

    return total


def bulk_merge_in_study_relationships(
    driver, db_name: str, in_study_rows: List[InStudyRelRow], chunk_size: int = 20_000
) -> int:
    """MERGE (Sample)-[:IN_STUDY]->(Study). Returns count processed."""
    total = 0
    cypher = """
    UNWIND $rows AS row
    MATCH (s:Sample {uuid: row.sample_uuid})
    MATCH (st:Study {id: row.study_id})
    MERGE (s)-[r:IN_STUDY]->(st)
    RETURN count(r) AS processed
    """
    for i in range(0, len(in_study_rows), chunk_size):
        chunk = in_study_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        if result.records:
            total += result.records[0]["processed"]

    return total


def build_study_node_payloads(
    study_ids: Set[int],
    sql_conn: Connection,
) -> Tuple[List[StudyNodeRow], List[InvestigationNodeRow], List[InInvestigationRelRow]]:
    """Fetch study and investigation data from MySQL, build Neo4j payloads."""
    if not study_ids:
        return [], [], []

    study_list = sorted(study_ids)
    study_rows_out: List[StudyNodeRow] = []
    inv_ids_needed: Set[int] = set()
    study_to_inv: Dict[int, int] = {}

    for chunk_start in range(0, len(study_list), 1000):
        chunk = study_list[chunk_start : chunk_start + 1000]
        params = {f"s{i}": s for i, s in enumerate(chunk)}
        placeholders = ", ".join(f":s{i}" for i in range(len(chunk)))
        result = sql_conn.execute(
            text(f"SELECT id, title, description, investigation_id FROM studies WHERE id IN ({placeholders})"),
            params,
        )
        for sid, title, description, inv_id in result.fetchall():
            if title:
                study_rows_out.append(StudyNodeRow(id=sid, title=title, description=description or ""))
                if inv_id:
                    inv_ids_needed.add(inv_id)
                    study_to_inv[sid] = inv_id

    inv_rows_out: List[InvestigationNodeRow] = []
    if inv_ids_needed:
        inv_list = sorted(inv_ids_needed)
        for chunk_start in range(0, len(inv_list), 1000):
            chunk = inv_list[chunk_start : chunk_start + 1000]
            params = {f"i{i}": inv for i, inv in enumerate(chunk)}
            placeholders = ", ".join(f":i{i}" for i in range(len(chunk)))
            result = sql_conn.execute(
                text(f"SELECT id, title, description FROM investigations WHERE id IN ({placeholders})"),
                params,
            )
            for iid, title, description in result.fetchall():
                if title:
                    inv_rows_out.append(InvestigationNodeRow(id=iid, title=title, description=description or ""))

    inv_rel_rows = [
        InInvestigationRelRow(study_id=sid, investigation_id=inv_id)
        for sid, inv_id in study_to_inv.items()
    ]

    return study_rows_out, inv_rows_out, inv_rel_rows


def bulk_merge_study_nodes(
    driver, db_name: str, study_rows: List[StudyNodeRow], chunk_size: int = 10_000
) -> int:
    """MERGE Study nodes. Returns count created."""
    created = 0
    cypher = """
    UNWIND $rows AS row
    MERGE (st:Study {id: row.id})
    SET st.title = row.title, st.description = row.description
    """
    for i in range(0, len(study_rows), chunk_size):
        chunk = study_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        summary = result.summary if hasattr(result, "summary") else None
        if summary and hasattr(summary, "counters"):
            created += getattr(summary.counters, "nodes_created", 0)
    return created


def bulk_merge_investigation_nodes(
    driver, db_name: str, inv_rows: List[InvestigationNodeRow], chunk_size: int = 10_000
) -> int:
    """MERGE Investigation nodes. Returns count created."""
    created = 0
    cypher = """
    UNWIND $rows AS row
    MERGE (inv:Investigation {id: row.id})
    SET inv.title = row.title, inv.description = row.description
    """
    for i in range(0, len(inv_rows), chunk_size):
        chunk = inv_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        summary = result.summary if hasattr(result, "summary") else None
        if summary and hasattr(summary, "counters"):
            created += getattr(summary.counters, "nodes_created", 0)
    return created


def bulk_merge_in_investigation_relationships(
    driver, db_name: str, rel_rows: List[InInvestigationRelRow], chunk_size: int = 20_000
) -> int:
    """MERGE (Study)-[:IN_INVESTIGATION]->(Investigation). Returns count."""
    total = 0
    cypher = """
    UNWIND $rows AS row
    MATCH (s:Study {id: row.study_id})
    MATCH (inv:Investigation {id: row.investigation_id})
    MERGE (s)-[r:IN_INVESTIGATION]->(inv)
    RETURN count(r) AS processed
    """
    for i in range(0, len(rel_rows), chunk_size):
        chunk = rel_rows[i : i + chunk_size]
        rows_data = [r.model_dump() for r in chunk]

        def _run(data=rows_data):
            return driver.execute_query(cypher, {"rows": data}, database_=db_name)

        result = _retry(_run)
        if result.records:
            total += result.records[0]["processed"]
    return total


# ── delete stale relationships ────────────────────────────────────────────


def delete_derived_from_for_uuids(
    driver, db_name: str, uuids: List[str], chunk_size: int = 10_000
) -> int:
    """Delete all DERIVED_FROM relationships where child is one of the given UUIDs.

    Returns count of deleted relationships.
    """
    if not uuids:
        return 0

    deleted_total = 0
    cypher = """
    UNWIND $uuids AS uuid
    MATCH (c:Sample {uuid: uuid})-[r:DERIVED_FROM]->()
    DELETE r
    RETURN count(r) AS deleted
    """

    for i in range(0, len(uuids), chunk_size):
        chunk = uuids[i : i + chunk_size]

        def _run(data=chunk):
            return driver.execute_query(cypher, {"uuids": data}, database_=db_name)

        result = _retry(_run)
        if result.records:
            deleted_total += result.records[0]["deleted"]

    return deleted_total


def refresh_assays_for_uuids(
    uuids: List[str],
    outcomes: Dict[str, RowOutcome],
    sql_conn: Connection,
) -> Dict[str, Set[int]]:
    """Query actual assay_assets from MySQL for parent-changed samples.

    Instead of using the spreadsheet-derived assays_by_uid, this queries
    the post-smart-merge state from the assay_assets table.

    Returns {uuid: set(assay_ids)}.
    """
    if not uuids:
        return {}

    # Build sample_id -> uuid mapping from outcomes
    sid_to_uuid: Dict[int, str] = {}
    for uid in uuids:
        outcome = outcomes.get(uid)
        if outcome and outcome.sample_id is not None:
            sid_to_uuid[outcome.sample_id] = uid

    if not sid_to_uuid:
        return {}

    # Chunked IN query on assay_assets
    result_map: Dict[str, Set[int]] = {uid: set() for uid in uuids}
    sid_list = list(sid_to_uuid.keys())

    for chunk_start in range(0, len(sid_list), 1000):
        chunk = sid_list[chunk_start : chunk_start + 1000]
        params = {f"a{i}": sid for i, sid in enumerate(chunk)}
        placeholders = ", ".join(f":a{i}" for i in range(len(chunk)))
        sql = text(
            f"SELECT asset_id, assay_id FROM assay_assets "
            f"WHERE asset_id IN ({placeholders}) AND asset_type = 'Sample'"
        )
        rows = sql_conn.execute(sql, params).fetchall()
        for asset_id, assay_id in rows:
            uid = sid_to_uuid.get(asset_id)
            if uid:
                result_map[uid].add(assay_id)

    return result_map


# ── payload building ──────────────────────────────────────────────────────


def build_payloads(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
) -> Tuple[List[NodeRow], List[OfTypeRelRow]]:
    """Build Neo4j node and OF_TYPE payloads from successful outcomes."""
    node_rows: List[NodeRow] = []
    of_type_rows: List[OfTypeRelRow] = []
    # Build uid -> input model lookup
    uid_to_model = {m.UID: m for m in input_models}

    for uid, outcome in outcomes.items():
        if outcome.sample_id is None:
            continue
        model = uid_to_model.get(uid)
        if not model:
            continue

        # Parse properties from json_metadata
        try:
            props = _json_loads(model.json_metadata)
            if not isinstance(props, dict):
                props = {}
        except (json.JSONDecodeError, TypeError):
            props = {}

        node_rows.append(NodeRow(
            sample_id=outcome.sample_id,
            sample_uuid=uid,
            sample_type=model.SampleType,
            properties=props,
        ))

    return node_rows, of_type_rows


def build_in_study_payloads(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
) -> Tuple[List[InStudyRelRow], int]:
    """Build IN_STUDY payloads. Returns (rows, warning_count).

    Includes all outcomes with sample_id (success + skipped duplicates).
    Logs warning and increments count for rows missing study_id.
    """
    uid_to_model = {m.UID: m for m in input_models}
    rows: List[InStudyRelRow] = []
    warnings = 0
    for uid, outcome in outcomes.items():
        if outcome.sample_id is None:
            continue
        model = uid_to_model.get(uid)
        if not model:
            continue
        if model.study_id is None:
            warnings += 1
            log.warning("IN_STUDY: skipping UID=%s — no study_id provided", uid)
            continue
        rows.append(InStudyRelRow(sample_uuid=uid, study_id=model.study_id))
    return rows, warnings


def build_of_type_payloads(
    outcomes: Dict[str, RowOutcome],
    insertable_samples: List[InsertableSample],
) -> List[OfTypeRelRow]:
    """Build OF_TYPE payloads using InsertableSample.sample_type_id.

    Includes all outcomes with sample_id (success + skipped duplicates).
    """
    uuid_to_insertable = {s.uuid: s for s in insertable_samples}
    rows: List[OfTypeRelRow] = []
    for uid, outcome in outcomes.items():
        if outcome.sample_id is None:
            continue
        insertable = uuid_to_insertable.get(uid)
        if not insertable:
            continue
        rows.append(OfTypeRelRow(
            sample_id=outcome.sample_id,
            sample_uuid=uid,
            sample_type_id=insertable.sample_type_id,
        ))
    return rows


def build_derived_from_payloads_from_db(
    parent_child_rels: Dict[str, Set[str]],
    sql_conn: Connection,
    assays_by_uid: Dict[str, Set[int]],
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
) -> List[DerivedFromRelRow]:
    """Build DERIVED_FROM payloads with protocol and assay context.

    4-step process:
    Step 0: Parent ID lookup
    Step 1: Child metadata (Protocol)
    Step 2: Protocol titles
    Step 3: Shared assays
    """
    if not parent_child_rels:
        return []

    uid_to_model = {m.UID: m for m in input_models}
    provided_sop_by_uid: Dict[str, int] = {}
    provided_assay_title_by_id: Dict[int, str] = {}
    for uid, model in uid_to_model.items():
        if model.sop_id is not None:
            try:
                provided_sop_by_uid[uid] = int(model.sop_id)
            except Exception:
                pass
        if model.assay_titles and model.assay_ids and len(model.assay_titles) == len(model.assay_ids):
            for aid, title in zip(model.assay_ids, model.assay_titles):
                if aid is None:
                    continue
                t = str(title).strip()
                if not t:
                    continue
                if aid in provided_assay_title_by_id and provided_assay_title_by_id[aid] != t:
                    log.warning(
                        "Conflicting assay_titles provided for assay_id=%s; keeping first='%s', ignoring='%s'",
                        aid,
                        provided_assay_title_by_id[aid],
                        t,
                    )
                    continue
                provided_assay_title_by_id[aid] = t

    # Collect all parent and child UUIDs
    all_children = set(parent_child_rels.keys())
    all_parents: Set[str] = set()
    for parents in parent_child_rels.values():
        all_parents.update(parents)

    # Step 0: Parent ID lookup
    parent_uuid_to_id: Dict[str, int] = {}
    parent_list = list(all_parents)
    for chunk_start in range(0, len(parent_list), 1000):
        chunk = parent_list[chunk_start : chunk_start + 1000]
        params = {f"p_{i}": p for i, p in enumerate(chunk)}
        placeholders = ", ".join(f":p_{i}" for i in range(len(chunk)))
        sql = text(f"SELECT uuid, id FROM samples WHERE uuid IN ({placeholders})")
        rows = sql_conn.execute(sql, params).fetchall()
        for uuid, sid in rows:
            parent_uuid_to_id[uuid] = sid

    # Build child uuid -> id from outcomes
    child_uuid_to_id: Dict[str, int] = {}
    for uid in all_children:
        outcome = outcomes.get(uid)
        if outcome and outcome.sample_id:
            child_uuid_to_id[uid] = outcome.sample_id
    child_id_to_uuid: Dict[int, str] = {sid: uid for uid, sid in child_uuid_to_id.items()}

    # Step 1: Child metadata (Protocol extraction)
    child_ids = list(child_uuid_to_id.values())
    child_protocol_map: Dict[int, Optional[int]] = {}
    for chunk_start in range(0, len(child_ids), 1000):
        chunk = child_ids[chunk_start : chunk_start + 1000]
        params = {f"c_{i}": c for i, c in enumerate(chunk)}
        placeholders = ", ".join(f":c_{i}" for i in range(len(chunk)))
        sql = text(f"SELECT id, json_metadata FROM samples WHERE id IN ({placeholders})")
        rows = sql_conn.execute(sql, params).fetchall()
        for sid, jmeta in rows:
            # Prefer user-provided sop_id when available
            sop_id = None
            child_uid = child_id_to_uuid.get(sid)
            if child_uid and child_uid in provided_sop_by_uid:
                sop_id = provided_sop_by_uid.get(child_uid)
                child_protocol_map[sid] = sop_id
                continue

            try:
                meta = _json_loads(jmeta) if jmeta else {}
                protocol_val = meta.get("Protocol") or meta.get("protocol") or ""
                m = _SOP_URL_RE.search(str(protocol_val))
                if m:
                    sop_id = int(m.group(1))
            except (json.JSONDecodeError, TypeError):
                pass
            child_protocol_map[sid] = sop_id

    # Step 2: Protocol titles
    sop_ids = [v for v in child_protocol_map.values() if v is not None]
    sop_titles: Dict[int, str] = {}
    if sop_ids:
        unique_sops = list(set(sop_ids))
        for chunk_start in range(0, len(unique_sops), 1000):
            chunk = unique_sops[chunk_start : chunk_start + 1000]
            params = {f"pr_{i}": p for i, p in enumerate(chunk)}
            placeholders = ", ".join(f":pr_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id, title FROM sops WHERE id IN ({placeholders})")
            rows = sql_conn.execute(sql, params).fetchall()
            for sid, title in rows:
                sop_titles[sid] = title

    # Step 3: Shared assays — find best internal assay per (child, parent) pair
    # Pre-compute all shared assay IDs needed, then bulk-fetch titles
    all_shared_assay_ids: Set[int] = set()
    for child_uid, parent_uids in parent_child_rels.items():
        child_assays = assays_by_uid.get(child_uid, set())
        for parent_uid in parent_uids:
            parent_assays = assays_by_uid.get(parent_uid, set())
            shared = child_assays & parent_assays
            if shared:
                all_shared_assay_ids.add(min(shared))

    # Subtract already-provided titles, bulk-fetch the rest
    assay_ids_to_fetch = all_shared_assay_ids - set(provided_assay_title_by_id.keys())
    assay_titles: Dict[int, str] = {}
    if assay_ids_to_fetch:
        fetch_list = sorted(assay_ids_to_fetch)
        for chunk_start in range(0, len(fetch_list), 1000):
            chunk = fetch_list[chunk_start : chunk_start + 1000]
            params = {f"a_{i}": a for i, a in enumerate(chunk)}
            placeholders = ", ".join(f":a_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id, title FROM assays WHERE id IN ({placeholders})")
            rows = sql_conn.execute(sql, params).fetchall()
            for aid, title in rows:
                assay_titles[aid] = title

    results: List[DerivedFromRelRow] = []
    seen: Set[Tuple[int, int]] = set()

    for child_uid, parent_uids in parent_child_rels.items():
        child_id = child_uuid_to_id.get(child_uid)
        if not child_id:
            continue
        child_assays = assays_by_uid.get(child_uid, set())

        for parent_uid in parent_uids:
            parent_id = parent_uuid_to_id.get(parent_uid)
            if not parent_id:
                continue

            key = (child_id, parent_id)
            if key in seen:
                continue
            seen.add(key)

            protocol_id = child_protocol_map.get(child_id)
            protocol_title = sop_titles.get(protocol_id) if protocol_id else None

            # Find shared assay (lowest ID)
            parent_assays = assays_by_uid.get(parent_uid, set())
            shared = child_assays & parent_assays
            internal_assay_id = min(shared) if shared else None

            internal_assay_title = None
            if internal_assay_id:
                # Prefer user-provided assay_titles when available
                if internal_assay_id in provided_assay_title_by_id:
                    internal_assay_title = provided_assay_title_by_id.get(internal_assay_id)
                else:
                    internal_assay_title = assay_titles.get(internal_assay_id)

            results.append(DerivedFromRelRow(
                child_id=child_id,
                child_uuid=child_uid,
                parent_id=parent_id,
                parent_uuid=parent_uid,
                protocol_id=protocol_id,
                protocol_title=protocol_title,
                internal_assay_id=internal_assay_id,
                internal_assay_title=internal_assay_title,
            ))

    return results


# ── main upload orchestrator ──────────────────────────────────────────────


def upload_all(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
    direction_computation: DirectionComputation,
    sql_conn: Connection,
    neo4j_config: Neo4jConfig,
    insertable_samples: Optional[List[InsertableSample]] = None,
) -> Metrics:
    """Full Neo4j upload: constraints -> Sample nodes -> SampleType nodes ->
    DERIVED_FROM -> OF_TYPE -> IN_STUDY.

    Returns Metrics with all counters.
    """
    if not neo4j_config.NEO4J_UPLOAD_ENABLED:
        log.info("Neo4j upload disabled, skipping")
        return Metrics()

    try:
        from neo4j import GraphDatabase
    except ImportError:
        log.warning("neo4j driver not installed, skipping")
        return Metrics()

    t0 = time.perf_counter()
    metrics = Metrics()
    db_name = neo4j_config.NEO4J_DB

    # ── Phase 1: All MySQL fetches (while sql_conn is fresh) ─────────────

    node_rows, _ = build_payloads(outcomes, input_models)
    metrics.nodes_input = len(node_rows)

    derived_from_rows = build_derived_from_payloads_from_db(
        direction_computation.parents_of,
        sql_conn,
        direction_computation.assays_by_uid,
        outcomes,
        input_models,
    )
    metrics.rels_input = len(derived_from_rows)
    metrics.eligible_children = len(direction_computation.parents_of)

    # Handle parent-changed samples (re-resolve DERIVED_FROM with fresh assays)
    parent_changed_uuids = [
        uid for uid, outcome in outcomes.items()
        if outcome.parent_changed and outcome.sample_id is not None
    ]
    if parent_changed_uuids:
        refreshed_assays = refresh_assays_for_uuids(parent_changed_uuids, outcomes, sql_conn)
        updated_assays_by_uid = dict(direction_computation.assays_by_uid)
        updated_assays_by_uid.update(refreshed_assays)
        derived_from_rows = build_derived_from_payloads_from_db(
            direction_computation.parents_of,
            sql_conn,
            updated_assays_by_uid,
            outcomes,
            input_models,
        )
        metrics.rels_input = len(derived_from_rows)

    st_map: Dict[str, Optional[int]] = {}
    uid_to_model = {m.UID: m for m in input_models}
    for uid, outcome in outcomes.items():
        if outcome.sample_id is not None:
            model = uid_to_model.get(uid)
            if model and model.SampleType not in st_map:
                st_map[model.SampleType] = None
    st_node_rows = [SampleTypeNodeRow(title=t, id=None) for t in st_map]

    of_type_rows = build_of_type_payloads(outcomes, insertable_samples or [])

    in_study_rows, in_study_warn_count = build_in_study_payloads(outcomes, input_models)

    study_ids = {row.study_id for row in in_study_rows}
    study_node_rows, inv_node_rows, inv_rel_rows = (
        build_study_node_payloads(study_ids, sql_conn) if study_ids else ([], [], [])
    )

    # ── Phase 2: All Neo4j operations (no more MySQL I/O) ───────────────

    driver = GraphDatabase.driver(
        neo4j_config.URI,
        auth=(neo4j_config.NEO4J_USER, neo4j_config.PASSWORD),
    )

    try:
        # 1. Ensure constraints
        ensure_constraints(driver, db_name)

        # 2. MERGE Sample nodes
        if node_rows:
            created, matched = bulk_merge_nodes(
                driver, db_name, node_rows, neo4j_config.NEO4J_NODE_CHUNK
            )
            metrics.nodes_created = created
            metrics.nodes_matched = matched

        # 3. MERGE SampleType nodes
        if st_node_rows:
            st_created = bulk_merge_sample_type_nodes(
                driver, db_name, st_node_rows, neo4j_config.NEO4J_NODE_CHUNK
            )
            metrics.sample_type_nodes_created = st_created

        # 4. Delete stale DERIVED_FROM for parent-changed samples
        if parent_changed_uuids:
            deleted_count = delete_derived_from_for_uuids(driver, db_name, parent_changed_uuids)
            log.info("Neo4j: deleted %d stale DERIVED_FROM for %d parent-changed samples",
                     deleted_count, len(parent_changed_uuids))

        # 5. MERGE DERIVED_FROM
        if derived_from_rows:
            df_count = bulk_merge_relationships(
                driver, db_name, derived_from_rows, neo4j_config.NEO4J_REL_CHUNK
            )
            metrics.derived_from_rels_created = df_count

        # 6. MERGE OF_TYPE
        if of_type_rows:
            ot_count = bulk_merge_of_type_relationships(
                driver, db_name, of_type_rows, neo4j_config.NEO4J_REL_CHUNK
            )
            metrics.of_type_rels_created = ot_count

        # 7. MERGE Study + Investigation nodes
        if study_node_rows:
            study_created = bulk_merge_study_nodes(driver, db_name, study_node_rows, neo4j_config.NEO4J_NODE_CHUNK)
            metrics.study_nodes_created = study_created
        if inv_node_rows:
            inv_created = bulk_merge_investigation_nodes(driver, db_name, inv_node_rows, neo4j_config.NEO4J_NODE_CHUNK)
            metrics.investigation_nodes_created = inv_created
        if inv_rel_rows:
            inv_rel_count = bulk_merge_in_investigation_relationships(driver, db_name, inv_rel_rows, neo4j_config.NEO4J_REL_CHUNK)
            metrics.in_investigation_rels_created = inv_rel_count

        # 8. MERGE IN_STUDY
        if in_study_rows:
            is_count = bulk_merge_in_study_relationships(
                driver, db_name, in_study_rows, neo4j_config.NEO4J_REL_CHUNK
            )
            metrics.in_study_rels_created = is_count
        metrics.in_study_warnings = in_study_warn_count

    finally:
        driver.close()

    metrics.elapsed_ms_total = (time.perf_counter() - t0) * 1000
    log.info("Neo4j upload completed in %.0f ms", metrics.elapsed_ms_total)
    return metrics
