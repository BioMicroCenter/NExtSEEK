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
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import psutil
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .config import Neo4jConfig
from .errors import ErrorCollector, ErrorType
from .helpers import (
    UID_RE,
    collect_parent_tokens,
    lookup_sop_ids_by_title,
    parse_protocol_value,
    split_parent_field,
)
from .identity import extract_identity, hash_identity
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
        r.assay_id = row.assay_id,
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
    """MERGE (Sample)-[:IN_STUDY]->(Study). Returns count processed.

    The two MATCHes are the silent half of issue #44. `build_in_study_payloads_enriched`
    warns and counts when it cannot work out a study_id at all, but a row that HAS a
    study_id and reaches this query still produces no edge if the Study node does not
    exist: UNWIND + MATCH simply yields no rows for it. The sample is dropped with no
    warning, no counter and no effect on the return value, so the upload reports
    success while the graph quietly lacks the relationship.

    `count(r)` is one r per row that matched both endpoints, so a chunk whose processed
    count is short of its row count dropped exactly that many edges. Log it here; the
    caller turns the shortfall into `Metrics.in_study_rels_dropped` and folds it into
    `in_study_warnings`, and `find_missing_in_study_endpoints` names the culprits.
    """
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
        processed = result.records[0]["processed"] if result.records else 0
        total += processed

        dropped = len(chunk) - processed
        if dropped > 0:
            log.warning(
                "IN_STUDY: %d of %d rows in chunk %d matched no Sample or Study node "
                "and were silently dropped",
                dropped, len(chunk), i // chunk_size,
            )

    return total


def find_missing_in_study_endpoints(
    driver, db_name: str, in_study_rows: List[InStudyRelRow]
) -> Tuple[List[int], List[str]]:
    """Which IN_STUDY endpoints do not exist as nodes. READ-ONLY.

    Names the rows `bulk_merge_in_study_relationships` had to drop, so the shortfall
    it reports is actionable rather than just a number. Deliberately mutation-free:
    creating the missing nodes, or backfilling the edges, is a data decision that
    belongs to an operator, not to a sync that was only asked to merge.

    Returns (missing_study_ids, missing_sample_uuids), both sorted.
    """
    if not in_study_rows:
        return [], []

    study_ids = sorted({r.study_id for r in in_study_rows})
    sample_uuids = sorted({r.sample_uuid for r in in_study_rows})

    missing_studies_cypher = """
    UNWIND $ids AS sid
    OPTIONAL MATCH (st:Study {id: sid})
    WITH sid, st WHERE st IS NULL
    RETURN collect(sid) AS missing
    """
    missing_samples_cypher = """
    UNWIND $uuids AS uid
    OPTIONAL MATCH (s:Sample {uuid: uid})
    WITH uid, s WHERE s IS NULL
    RETURN collect(uid) AS missing
    """

    def _missing(cypher, params):
        def _run():
            return driver.execute_query(cypher, params, database_=db_name)

        result = _retry(_run)
        if not result.records:
            return []
        return list(result.records[0]["missing"] or [])

    return (
        sorted(_missing(missing_studies_cypher, {"ids": study_ids})),
        sorted(_missing(missing_samples_cypher, {"uuids": sample_uuids})),
    )


def build_study_node_payloads(
    study_ids: Set[int],
    sql_conn: Connection,
    fallback_titles: Optional[Dict[int, str]] = None,
) -> Tuple[List[StudyNodeRow], List[InvestigationNodeRow], List[InInvestigationRelRow]]:
    """Fetch study and investigation data from MySQL, build Neo4j payloads.

    Args:
        fallback_titles: Optional mapping of study_id -> title from InputRowModel.
            Used when the DB has no row or an empty title for a study_id.
    """
    if not study_ids:
        return [], [], []

    study_list = sorted(study_ids)
    study_rows_out: List[StudyNodeRow] = []
    inv_ids_needed: Set[int] = set()
    study_to_inv: Dict[int, int] = {}
    found_study_ids: Set[int] = set()

    for chunk_start in range(0, len(study_list), 1000):
        chunk = study_list[chunk_start : chunk_start + 1000]
        params = {f"s{i}": s for i, s in enumerate(chunk)}
        placeholders = ", ".join(f":s{i}" for i in range(len(chunk)))
        result = sql_conn.execute(
            text(f"SELECT id, title, description, investigation_id FROM studies WHERE id IN ({placeholders})"),
            params,
        )
        for sid, title, description, inv_id in result.fetchall():
            effective_title = title
            # If DB title is empty/missing, try fallback
            if not effective_title and fallback_titles and sid in fallback_titles:
                effective_title = fallback_titles[sid]
                log.info("Study id=%d created from fallback title '%s'", sid, effective_title)
            if effective_title:
                found_study_ids.add(sid)
                study_rows_out.append(StudyNodeRow(id=sid, title=effective_title, description=description or ""))
                if inv_id:
                    inv_ids_needed.add(inv_id)
                    study_to_inv[sid] = inv_id

    # Fallback: for study_ids not found in DB at all, use fallback_titles
    if fallback_titles:
        for sid in study_ids:
            if sid not in found_study_ids and sid in fallback_titles:
                title = fallback_titles[sid]
                if title and title.strip():
                    study_rows_out.append(StudyNodeRow(id=sid, title=title.strip(), description=""))
                    log.info("Study id=%d created from fallback title '%s'", sid, title)

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


# ── parent_titles enrichment ──────────────────────────────────────────────


def enrich_parent_titles(
    node_rows: List[NodeRow],
    input_models: List[InputRowModel],
    sql_conn: Optional[Any],
) -> None:
    """Enrich NodeRow objects with parent_titles: the human-readable identities of all parents.

    For each node's Parent field tokens:
    - UID tokens in-batch: look up identity from that model's json_metadata
    - UID tokens external: bulk SQL lookup
    - Non-UID tokens (unresolved names): the token IS the identity

    Mutates node_rows in-place: sets parent_titles field and properties["parent_titles"].
    If no parents, does NOT set properties["parent_titles"].
    """
    # 1. Build uid_to_identity from in-batch models
    uid_to_meta: Dict[str, dict] = {}
    uid_to_sample_type: Dict[str, str] = {}
    for model in input_models:
        if model.UID is None:
            continue
        try:
            meta = _json_loads(model.json_metadata) if model.json_metadata else {}
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        uid_to_meta[model.UID] = meta
        uid_to_sample_type[model.UID] = model.SampleType

    uid_to_identity: Dict[str, Optional[str]] = {}
    for uid, meta in uid_to_meta.items():
        uid_to_identity[uid] = extract_identity(
            meta,
            uid=uid,
            sample_type=uid_to_sample_type[uid],
        )

    # 2. First pass: collect parent tokens, identify external UIDs
    external_uids: Set[str] = set()
    node_parent_tokens: List[List[str]] = []

    for node in node_rows:
        tokens = collect_parent_tokens(node.properties)
        if not tokens:
            node_parent_tokens.append([])
            continue
        node_parent_tokens.append(tokens)
        for token in tokens:
            if UID_RE.match(token) and token not in uid_to_identity:
                external_uids.add(token)

    # 3. Bulk SQL for external UIDs (chunked at 1000)
    if external_uids and sql_conn is not None:
        ext_list = sorted(external_uids)
        for chunk_start in range(0, len(ext_list), 1000):
            chunk = ext_list[chunk_start : chunk_start + 1000]
            params = {f"u{i}": u for i, u in enumerate(chunk)}
            placeholders = ", ".join(f":u{i}" for i in range(len(chunk)))
            sql = text(
                f"SELECT uuid, json_metadata FROM samples WHERE uuid IN ({placeholders})"
            )
            rows = sql_conn.execute(sql, params).fetchall()
            for uuid_val, jmeta in rows:
                try:
                    meta = _json_loads(jmeta) if jmeta else {}
                    if not isinstance(meta, dict):
                        meta = {}
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                uid_to_identity[uuid_val] = extract_identity(meta, uid=uuid_val)

    # 4. Second pass: build parent_titles AND parent_title_hashes for each node
    for i, node in enumerate(node_rows):
        tokens = node_parent_tokens[i]
        if not tokens:
            continue

        titles: List[str] = []
        for token in tokens:
            if UID_RE.match(token):
                # Resolved UID — look up its identity
                identity = uid_to_identity.get(token)
                if identity:
                    titles.append(identity)
                else:
                    log.debug("Could not resolve identity for parent UID %s", token)
            else:
                # Unresolved token — IS the identity
                titles.append(token)

        if not titles:
            continue

        hashes = [h for h in (hash_identity(t) for t in titles) if h]

        node.parent_titles = titles
        node.parent_title_hashes = hashes
        node.properties["parent_titles"] = titles
        node.properties["parent_title_hashes"] = hashes


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


def build_in_study_payloads_enriched(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
    sql_conn: Connection,
) -> Tuple[List[InStudyRelRow], int, Dict[int, str]]:
    """Build IN_STUDY payloads via two routes + collect fallback study titles.

    Route 1: Use InputRowModel.study_id when provided.
    Route 2: For samples without study_id, look up via assay_ids -> assays.study_id.

    Returns:
        (in_study_rows, warning_count, fallback_titles)
        fallback_titles: study_id -> study_title from InputRowModel (for Study node fallback)
    """
    uid_to_model = {m.UID: m for m in input_models}
    seen: Set[Tuple[str, int]] = set()
    rows: List[InStudyRelRow] = []
    warnings = 0
    fallback_titles: Dict[int, str] = {}

    need_assay_lookup: Dict[str, List[int]] = {}

    for uid, outcome in outcomes.items():
        if outcome.sample_id is None:
            continue
        model = uid_to_model.get(uid)
        if not model:
            continue

        if model.study_id is not None:
            key = (uid, model.study_id)
            if key not in seen:
                seen.add(key)
                rows.append(InStudyRelRow(sample_uuid=uid, study_id=model.study_id))
            if model.study_title and model.study_id not in fallback_titles:
                fallback_titles[model.study_id] = model.study_title
        else:
            if model.assay_ids:
                need_assay_lookup[uid] = list(model.assay_ids)
            else:
                warnings += 1
                log.warning("IN_STUDY: skipping UID=%s — no study_id or assay_ids", uid)

    if need_assay_lookup:
        all_assay_ids: Set[int] = set()
        for aids in need_assay_lookup.values():
            all_assay_ids.update(aids)

        assay_to_study: Dict[int, int] = {}
        assay_list = sorted(all_assay_ids)
        for chunk_start in range(0, len(assay_list), 1000):
            chunk = assay_list[chunk_start : chunk_start + 1000]
            params = {f"a_{i}": a for i, a in enumerate(chunk)}
            placeholders = ", ".join(f":a_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id, study_id FROM assays WHERE id IN ({placeholders})")
            result = sql_conn.execute(sql, params).fetchall()
            for aid, sid in result:
                if sid is not None:
                    assay_to_study[aid] = sid

        for uid, assay_ids in need_assay_lookup.items():
            study_ids_for_sample = {assay_to_study[a] for a in assay_ids if a in assay_to_study}
            if not study_ids_for_sample:
                warnings += 1
                log.warning("IN_STUDY: skipping UID=%s — assay lookup returned no study_id", uid)
                continue
            for sid in study_ids_for_sample:
                key = (uid, sid)
                if key not in seen:
                    seen.add(key)
                    rows.append(InStudyRelRow(sample_uuid=uid, study_id=sid))

    return rows, warnings, fallback_titles


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


def build_sample_type_node_payloads(
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
    sql_conn: Connection,
) -> List[SampleTypeNodeRow]:
    """Build SampleType node payloads with real IDs from bulk DB lookup.

    Collects unique SampleType titles from successful outcomes,
    queries the sample_types table for their IDs, and returns
    SampleTypeNodeRow objects with correct integer IDs.
    """
    uid_to_model = {m.UID: m for m in input_models}
    titles: Set[str] = set()
    for uid, outcome in outcomes.items():
        if outcome.sample_id is None:
            continue
        model = uid_to_model.get(uid)
        if model and model.SampleType:
            titles.add(model.SampleType)

    if not titles:
        return []

    # Bulk DB lookup: title -> id
    title_to_id: Dict[str, int] = {}
    title_list = sorted(titles)
    for chunk_start in range(0, len(title_list), 1000):
        chunk = title_list[chunk_start : chunk_start + 1000]
        params = {f"t_{i}": t for i, t in enumerate(chunk)}
        placeholders = ", ".join(f":t_{i}" for i in range(len(chunk)))
        sql = text(f"SELECT id, title FROM sample_types WHERE title IN ({placeholders})")
        rows = sql_conn.execute(sql, params).fetchall()
        for st_id, title in rows:
            title_to_id[title] = st_id

    # Build SampleTypeNodeRow list
    result: List[SampleTypeNodeRow] = []
    for title in sorted(titles):
        st_id = title_to_id.get(title)
        if st_id is None:
            log.warning("SampleType '%s' not found in sample_types table", title)
        result.append(SampleTypeNodeRow(title=title, id=st_id))

    return result


def _resolve_internal_assays(
    assay_ids: Set[int],
    sql_conn: Connection,
) -> Dict[int, Tuple[int, str]]:
    """Resolve assay_ids to (internal_assay_id, internal_assay_title) via junction table.

    Queries assays_internal_assays JOIN internal_assays in the nextseek (dmac) database.
    Returns mapping: assay_id -> (internal_assay_id, internal_assay_title).
    Only assay_ids that have a non-NULL internal_assay mapping are included.
    """
    if not assay_ids:
        return {}

    from django.conf import settings as django_settings
    nextseek_db = django_settings.DATABASES[django_settings.NEXTSEEK_DATABASE]["NAME"]

    result_map: Dict[int, Tuple[int, str]] = {}
    assay_list = sorted(assay_ids)

    for chunk_start in range(0, len(assay_list), 1000):
        chunk = assay_list[chunk_start : chunk_start + 1000]
        params = {f"a_{i}": a for i, a in enumerate(chunk)}
        placeholders = ", ".join(f":a_{i}" for i in range(len(chunk)))
        sql = text(
            f"SELECT ia.id, aia.assay_id, ia.internal_assay_title "
            f"FROM {nextseek_db}.assays_internal_assays aia "
            f"JOIN {nextseek_db}.internal_assays ia ON aia.internal_assay_id = ia.id "
            f"WHERE aia.assay_id IN ({placeholders})"
        )
        rows = sql_conn.execute(sql, params).fetchall()
        for ia_id, assay_id, ia_title in rows:
            # Keep smallest internal_assay_id per assay_id (deterministic for 1:N)
            if assay_id not in result_map or ia_id < result_map[assay_id][0]:
                result_map[assay_id] = (ia_id, ia_title)

    return result_map


# Identical for every row on purpose: validation._group_errors keys on
# (type, message), so embedding the URL here would defeat the grouping and
# reproduce the per-row wall this classification exists to avoid. The URLs
# themselves go to the aggregate log line below.
_EXTERNAL_PROTOCOL_MESSAGE = (
    "Protocol is a URL to a SOP outside this NExtSEEK instance, so the "
    "DERIVED_FROM edge records no local protocol id. Expected, not an error."
)


def _row_index_for(
    uid: Optional[str], uid_to_model: Dict[str, InputRowModel]
) -> int:
    """The sheet row a child came from, or the pipeline's -1 "no row" sentinel."""
    model = uid_to_model.get(uid) if uid else None
    if model is not None and model.original_row_index is not None:
        return model.original_row_index
    return -1


def _report_protocol_problems(
    problems: Dict[int, str],
    child_id_to_uuid: Dict[int, str],
    uid_to_model: Dict[str, InputRowModel],
    error_collector: Optional[ErrorCollector],
) -> None:
    """Surface Protocol values that did not yield a usable SOP.

    ``problems`` maps a child sample_id to its already-composed message, so the
    caller can say precisely what went wrong (an unmatched title, an ambiguous
    one, or an id no ``sops`` row matches) while the row lookup, the collector
    entry and the aggregate log stay in one place.

    The edge is still written — it just carries no usable protocol. That used
    to be a null nobody counted, which is exactly how the whole 4-sheet upload
    path lost its protocols unnoticed. Recorded per row like every other ingest
    problem (the ErrorCollector feeds the summary CSV's ``reason`` column and
    the API's ``errors[]``), and logged once in aggregate.
    """
    for sid, message in sorted(problems.items()):
        uid = child_id_to_uuid.get(sid)
        log.warning("%s", message)
        if error_collector is not None:
            error_collector.add(
                _row_index_for(uid, uid_to_model),
                uid,
                ErrorType.PROTOCOL_UNRESOLVED,
                message,
            )

    log.warning(
        "DERIVED_FROM: %d child sample(s) recorded a Protocol that did not "
        "resolve to a usable SOP",
        len(problems),
    )


def _report_external_protocol_links(
    external_links: Dict[int, str],
    child_id_to_uuid: Dict[int, str],
    uid_to_model: Dict[str, InputRowModel],
    error_collector: Optional[ErrorCollector],
) -> None:
    """Account for Protocol values that link to a SOP hosted elsewhere.

    Deliberately NOT reported as a problem. ``__formatSopUIDLink`` treats an
    http-prefixed Protocol as a legitimate external link, and 1,855 stored
    values are fairdomhub.org URLs; warning on each of them every time a batch
    carrying them is uploaded is how an operator learns to ignore the field,
    and then a genuine unmatched title goes unnoticed. INFO severity, a distinct type, and one shared message so
    the whole set collapses to a single group.
    """
    for sid in sorted(external_links):
        uid = child_id_to_uuid.get(sid)
        if error_collector is not None:
            error_collector.add(
                _row_index_for(uid, uid_to_model),
                uid,
                ErrorType.PROTOCOL_EXTERNAL_LINK,
                _EXTERNAL_PROTOCOL_MESSAGE,
            )

    sample = sorted(set(external_links.values()))[:20]
    log.info(
        "DERIVED_FROM: %d child sample(s) link to an external SOP; no local "
        "protocol recorded for them. Distinct URLs (first 20): %s",
        len(external_links),
        sample,
    )


def build_derived_from_payloads_from_db(
    parent_child_rels: Dict[str, Set[str]],
    sql_conn: Connection,
    assays_by_uid: Dict[str, Set[int]],
    outcomes: Dict[str, RowOutcome],
    input_models: List[InputRowModel],
    error_collector: Optional[ErrorCollector] = None,
) -> List[DerivedFromRelRow]:
    """Build DERIVED_FROM payloads with protocol and assay context.

    5-step process:
    Step 0: Parent ID lookup
    Step 1: Child metadata (Protocol)
    Step 1b: Protocol titles -> sops.id, for the Protocol values that are not
             a local /sops/<id> URL (in production, nearly all of them)
    Step 2: Protocol titles
    Step 3: Shared assays

    ``error_collector`` receives one PROTOCOL_UNRESOLVED entry per child whose
    Protocol names no SOP we can find, so the batch report shows it instead of
    the edge quietly carrying a null.
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
    # sample_id -> the Protocol value that still needs a title lookup
    pending_titles: Dict[int, str] = {}
    # sample_id -> a Protocol pointing at a SOP on another instance
    external_links: Dict[int, str] = {}
    # sample_id -> why this child ended up with no usable protocol
    problems: Dict[int, str] = {}
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
                ref = parse_protocol_value(protocol_val)
                sop_id = ref.sop_id
                if ref.title is not None:
                    pending_titles[sid] = ref.title
                elif ref.external_url is not None:
                    external_links[sid] = ref.external_url
            except (json.JSONDecodeError, TypeError):
                pass
            child_protocol_map[sid] = sop_id

    # Step 1b: resolve Protocol values that carry a SOP TITLE rather than a
    # local /sops/<id> URL. This is the production-majority shape (97,767 of
    # 163,393 samples), and the traditional 4-sheet upload path has no sop_id
    # column at all, so without this every one of its edges lost its protocol.
    if pending_titles:
        resolved_titles, ambiguous_titles = lookup_sop_ids_by_title(
            set(pending_titles.values()), sql_conn
        )
        for sid, title in pending_titles.items():
            resolved_id = resolved_titles.get(title)
            if resolved_id is not None:
                child_protocol_map[sid] = resolved_id
                continue
            who = child_id_to_uuid.get(sid) or sid
            n_matches = ambiguous_titles.get(title)
            if n_matches:
                problems[sid] = (
                    f"Protocol {title!r} matches {n_matches} SOPs; refusing to "
                    f"guess. The DERIVED_FROM edge for {who} carries no protocol."
                )
            else:
                problems[sid] = (
                    f"Protocol {title!r} matches no SOP on this instance (not a "
                    f"local /sops/<id> URL, and no sops.title equals it). The "
                    f"DERIVED_FROM edge for {who} carries no protocol."
                )

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

    # Step 2b: an id that named no sops row. A local /sops/9999, or a sheet
    # sop_id=9999, wrote protocol_id=9999 with protocol_title=None and reported
    # nothing — and that is the exact shape a WRONG id would take, so it is the
    # diagnostic most worth having. The id is still written: nulling it would
    # discard what the sheet said, so the edge is reported, not rewritten.
    for sid, resolved_sop_id in child_protocol_map.items():
        if resolved_sop_id is None or resolved_sop_id in sop_titles:
            continue
        who = child_id_to_uuid.get(sid) or sid
        problems[sid] = (
            f"Protocol resolved to SOP id {resolved_sop_id}, which matches no row "
            f"in sops. The DERIVED_FROM edge for {who} records that id with no "
            f"title."
        )

    if problems:
        _report_protocol_problems(
            problems, child_id_to_uuid, uid_to_model, error_collector
        )
    if external_links:
        _report_external_protocol_links(
            external_links, child_id_to_uuid, uid_to_model, error_collector
        )

    # Step 3: Shared assays — resolve real internal_assay_id via junction table
    # 3a. Collect ALL shared assay_ids across all (child, parent) pairs
    all_shared_assay_ids: Set[int] = set()
    for child_uid, parent_uids in parent_child_rels.items():
        child_assays = assays_by_uid.get(child_uid, set())
        for parent_uid in parent_uids:
            parent_assays = assays_by_uid.get(parent_uid, set())
            shared = child_assays & parent_assays
            all_shared_assay_ids.update(shared)

    # 3b. Primary resolution: assays_internal_assays JOIN internal_assays
    primary_mapping = _resolve_internal_assays(all_shared_assay_ids, sql_conn)

    # 3c. Fallback: for assay_ids NOT resolved via junction table,
    #     use assay_id as internal_assay_id with assays.title as title
    fallback_assay_ids = all_shared_assay_ids - set(primary_mapping.keys())
    fallback_mapping: Dict[int, Tuple[int, str]] = {}
    if fallback_assay_ids:
        fetch_list = sorted(fallback_assay_ids)
        for chunk_start in range(0, len(fetch_list), 1000):
            chunk = fetch_list[chunk_start : chunk_start + 1000]
            params = {f"a_{i}": a for i, a in enumerate(chunk)}
            placeholders = ", ".join(f":a_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id, title FROM assays WHERE id IN ({placeholders})")
            rows = sql_conn.execute(sql, params).fetchall()
            for aid, title in rows:
                fallback_mapping[aid] = (aid, title or "")

    # 3d. Combined mapping: primary overrides fallback
    combined_mapping: Dict[int, Tuple[int, str]] = {}
    combined_mapping.update(fallback_mapping)
    combined_mapping.update(primary_mapping)

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

            # Find shared assays and resolve to real internal_assay_id
            parent_assays = assays_by_uid.get(parent_uid, set())
            shared = child_assays & parent_assays

            internal_assay_id: Optional[int] = None
            internal_assay_title: Optional[str] = None

            assay_id: Optional[int] = None

            if shared:
                # Resolve each shared assay_id to its internal_assay_id,
                # then pick the minimum internal_assay_id
                best_ia_id: Optional[int] = None
                best_ia_title: Optional[str] = None
                best_assay_id: Optional[int] = None
                for assay_id in shared:
                    resolved = combined_mapping.get(assay_id)
                    if resolved is None:
                        continue
                    ia_id, ia_title = resolved
                    if best_ia_id is None or ia_id < best_ia_id:
                        best_ia_id = ia_id
                        best_ia_title = ia_title
                        best_assay_id = assay_id

                assay_id = best_assay_id

                if best_ia_id is not None:
                    internal_assay_id = best_ia_id
                    # User-provided titles override the resolved title only
                    # (internal_assay_id still comes from junction table lookup)
                    user_title = provided_assay_title_by_id.get(best_assay_id)
                    internal_assay_title = user_title if user_title is not None else best_ia_title

            results.append(DerivedFromRelRow(
                child_id=child_id,
                child_uuid=child_uid,
                parent_id=parent_id,
                parent_uuid=parent_uid,
                protocol_id=protocol_id,
                protocol_title=protocol_title,
                assay_id=assay_id,
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
    error_collector: Optional[ErrorCollector] = None,
) -> Metrics:
    """Full Neo4j upload: constraints -> Sample nodes -> SampleType nodes ->
    DERIVED_FROM -> OF_TYPE -> IN_STUDY.

    Returns Metrics with all counters. ``error_collector``, when given, gathers
    the per-row problems this stage finds (unresolvable Protocol values) so
    they reach the batch report; the count also lands in Metrics either way.
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
    enrich_parent_titles(node_rows, input_models, sql_conn)
    metrics.nodes_input = len(node_rows)
    metrics.eligible_children = len(direction_computation.parents_of)

    # Handle parent-changed samples: refresh assays before building DERIVED_FROM
    parent_changed_uuids = [
        uid for uid, outcome in outcomes.items()
        if outcome.parent_changed and outcome.sample_id is not None
    ]
    effective_assays = dict(direction_computation.assays_by_uid)
    if parent_changed_uuids:
        refreshed_assays = refresh_assays_for_uuids(parent_changed_uuids, outcomes, sql_conn)
        effective_assays.update(refreshed_assays)

    # A local collector when the caller supplied none, so the Metrics counter is
    # populated either way; the delta keeps entries from an earlier stage (or an
    # earlier call on the same collector) out of THIS run's count.
    protocol_errors = error_collector if error_collector is not None else ErrorCollector()
    counts_before = protocol_errors.count_by_type()
    unresolved_before = counts_before.get(ErrorType.PROTOCOL_UNRESOLVED, 0)
    external_before = counts_before.get(ErrorType.PROTOCOL_EXTERNAL_LINK, 0)
    derived_from_rows = build_derived_from_payloads_from_db(
        direction_computation.parents_of,
        sql_conn,
        effective_assays,
        outcomes,
        input_models,
        error_collector=protocol_errors,
    )
    counts_after = protocol_errors.count_by_type()
    metrics.protocols_unresolved = (
        counts_after.get(ErrorType.PROTOCOL_UNRESOLVED, 0) - unresolved_before
    )
    metrics.protocols_external_links = (
        counts_after.get(ErrorType.PROTOCOL_EXTERNAL_LINK, 0) - external_before
    )
    metrics.rels_input = len(derived_from_rows)

    st_node_rows = build_sample_type_node_payloads(outcomes, input_models, sql_conn)

    of_type_rows = build_of_type_payloads(outcomes, insertable_samples or [])

    in_study_rows, in_study_warn_count, fallback_study_titles = build_in_study_payloads_enriched(
        outcomes, input_models, sql_conn
    )

    study_ids = {row.study_id for row in in_study_rows}
    study_node_rows, inv_node_rows, inv_rel_rows = (
        build_study_node_payloads(study_ids, sql_conn, fallback_titles=fallback_study_titles)
        if study_ids else ([], [], [])
    )

    # ── Phase 2: All Neo4j operations (no more MySQL I/O) ───────────────

    driver = GraphDatabase.driver(
        neo4j_config.URI,
        auth=(neo4j_config.NEO4J_USER, neo4j_config.PASSWORD),
    )

    try:
        # 1. Ensure constraints
        ensure_constraints(driver, db_name)

        # 1b. Index swap: drop legacy parent_titles index, ensure parent_title_hashes index.
        # Both calls are idempotent; the DROP is a self-healing one-time migration that
        # has no effect once the legacy index is gone.
        try:
            driver.execute_query(
                "DROP INDEX sample_parent_titles IF EXISTS",
                database_=db_name,
            )
        except Exception as exc:
            log.warning("Failed to drop legacy parent_titles index (non-fatal): %s", exc)
        try:
            driver.execute_query(
                "CREATE INDEX sample_parent_title_hashes IF NOT EXISTS "
                "FOR (s:Sample) ON (s.parent_title_hashes)",
                database_=db_name,
            )
        except Exception as exc:
            log.warning("Failed to create parent_title_hashes index (non-fatal): %s", exc)

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

            # #44: a row whose Study node is missing is dropped by the MERGE's MATCH
            # with no error. The shortfall is the only evidence it happened, so make
            # it a counter and name the endpoints that caused it.
            metrics.in_study_rels_attempted = len(in_study_rows)
            dropped = max(0, len(in_study_rows) - is_count)
            metrics.in_study_rels_dropped = dropped
            if dropped:
                in_study_warn_count += dropped
                missing_studies, missing_samples = find_missing_in_study_endpoints(
                    driver, db_name, in_study_rows
                )
                log.warning(
                    "IN_STUDY: %d of %d relationships were dropped — "
                    "%d missing Study node(s) %s, %d missing Sample node(s) %s",
                    dropped, len(in_study_rows),
                    len(missing_studies), missing_studies[:20],
                    len(missing_samples), missing_samples[:20],
                )
        metrics.in_study_warnings = in_study_warn_count

    finally:
        driver.close()

    metrics.elapsed_ms_total = (time.perf_counter() - t0) * 1000
    log.info("Neo4j upload completed in %.0f ms", metrics.elapsed_ms_total)
    return metrics
