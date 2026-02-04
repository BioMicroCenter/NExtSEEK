"""Stage 2: DAG — Direction computation for parent-child-assay relationships."""
from __future__ import annotations

import json
import logging
import re
import sys
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)
except ImportError:
    _json_loads = json.loads

from .models import DirectionComputation, InputRowModel

log = logging.getLogger(__name__)

_PARENT_SPLIT_RE = re.compile(r"[;\,\s]+")
_VALID_PARENT_UID_RE = re.compile(r"^([AD]\.)?[A-Z]{3,}-\d{6}[A-Z]{3}-\d+-PUB\d*$")

# DuckDB fallback threshold (combined rows in assays_df + edges_df)
_DUCKDB_THRESHOLD = 250_000


@lru_cache(maxsize=65536)
def extract_parents(json_metadata_str: str) -> FrozenSet[str]:
    """Extract and validate parent UIDs from a JSON metadata string.

    Cached by the minified JSON string for deduplication.
    """
    try:
        meta = _json_loads(json_metadata_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return frozenset()

    parent_raw = meta.get("Parent") or meta.get("parent") or ""
    if not parent_raw or not isinstance(parent_raw, str):
        return frozenset()

    tokens = _PARENT_SPLIT_RE.split(parent_raw.strip())
    valid = set()
    for token in tokens:
        token = token.strip()
        if token and _VALID_PARENT_UID_RE.match(token):
            valid.add(token)
    return frozenset(valid)


def build_relationships(
    rows: List[InputRowModel],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], Set[Tuple[str, str]]]:
    """Build parent/child/edge indices from rows.

    Uses sys.intern() on all UIDs to deduplicate string objects.

    Returns:
        parents_of: child_uid -> set(parent_uids)
        children_of: parent_uid -> set(child_uids)
        edges: set of (parent_uid, child_uid) tuples
    """
    parents_of: Dict[str, Set[str]] = {}
    children_of: Dict[str, Set[str]] = {}
    edges: Set[Tuple[str, str]] = set()

    for row in rows:
        child = sys.intern(row.UID)
        parent_uids = extract_parents(row.json_metadata)
        if not parent_uids:
            continue
        for p in parent_uids:
            parent = sys.intern(p)
            parents_of.setdefault(child, set()).add(parent)
            children_of.setdefault(parent, set()).add(child)
            edges.add((parent, child))

    return parents_of, children_of, edges


def compute_directions(rows: List[InputRowModel]) -> DirectionComputation:
    """Compute assay directions for all parent-child pairs.

    5-phase algorithm:
    1. Index assays by UID
    2. Build relationships
    3. Build skinny DataFrames
    4. Vectorized join (Pandas or DuckDB)
    5. Build output indices

    Direction semantics: 0 = source (child has assay, parent doesn't),
                         1 = target (both have assay).
    """
    import pandas as pd

    # Phase 1: Index assays by UID
    assays_by_uid: Dict[str, Set[int]] = {}
    for row in rows:
        uid = sys.intern(row.UID)
        if row.assay_ids:
            assays_by_uid[uid] = set(row.assay_ids)

    # Phase 2: Build relationships
    parents_of, _children_of, edges = build_relationships(rows)

    if not edges or not assays_by_uid:
        return DirectionComputation(
            direction_by_pair={},
            parents_of=parents_of,
            assays_by_uid=assays_by_uid,
            child_uids_by_assay={},
            conflicts_by_assay={},
        )

    # Phase 3: Build skinny DataFrames
    assays_records = []
    for uid, aids in assays_by_uid.items():
        for aid in aids:
            assays_records.append({"uid": uid, "assay": aid})

    edges_records = [{"parent": p, "child": c} for p, c in edges]

    total_rows = len(assays_records) + len(edges_records)

    # Phase 4: Vectorized join
    if total_rows >= _DUCKDB_THRESHOLD:
        direction_by_pair, child_uids_by_assay, conflicts_by_assay = _duckdb_path(
            assays_records, edges_records
        )
    else:
        direction_by_pair, child_uids_by_assay, conflicts_by_assay = _pandas_path(
            assays_records, edges_records
        )

    return DirectionComputation(
        direction_by_pair=direction_by_pair,
        parents_of=parents_of,
        assays_by_uid=assays_by_uid,
        child_uids_by_assay=child_uids_by_assay,
        conflicts_by_assay=conflicts_by_assay,
    )


def _pandas_path(
    assays_records: list, edges_records: list
) -> Tuple[Dict[Tuple[str, int], int], Dict[int, Set[str]], Dict[int, Set[str]]]:
    """Pandas-based direction computation for smaller datasets."""
    import pandas as pd

    assays_df = pd.DataFrame(assays_records)
    edges_df = pd.DataFrame(edges_records)

    # Merge: for each edge (parent, child), find assays the child has
    child_assays = edges_df.merge(assays_df, left_on="child", right_on="uid", how="inner")
    # child_assays: parent, child, uid, assay

    if child_assays.empty:
        return {}, {}, {}

    # Check if parent also has that assay
    parent_assays = assays_df.rename(columns={"uid": "parent_uid", "assay": "parent_assay"})
    merged = child_assays.merge(
        parent_assays,
        left_on=["parent", "assay"],
        right_on=["parent_uid", "parent_assay"],
        how="left",
        indicator=True,
    )

    # direction: 1 if both have assay (target), 0 if only child (source)
    merged["direction"] = (merged["_merge"] == "both").astype("int8")

    direction_by_pair: Dict[Tuple[str, int], int] = {}
    child_uids_by_assay: Dict[int, Set[str]] = {}
    conflicts_by_assay: Dict[int, Set[str]] = {}

    for _, row in merged.iterrows():
        uid = row["child"]
        assay_id = int(row["assay"])
        direction = int(row["direction"])
        key = (uid, assay_id)
        direction_by_pair[key] = direction
        child_uids_by_assay.setdefault(assay_id, set()).add(uid)

    return direction_by_pair, child_uids_by_assay, conflicts_by_assay


def _duckdb_path(
    assays_records: list, edges_records: list
) -> Tuple[Dict[Tuple[str, int], int], Dict[int, Set[str]], Dict[int, Set[str]]]:
    """DuckDB-based direction computation for large datasets (>= 250k rows)."""
    try:
        import duckdb
    except ImportError:
        log.warning("DuckDB not available, falling back to pandas for large dataset")
        return _pandas_path(assays_records, edges_records)

    import pandas as pd

    assays_df = pd.DataFrame(assays_records)
    edges_df = pd.DataFrame(edges_records)

    con = duckdb.connect(":memory:")
    con.register("assays_tbl", assays_df)
    con.register("edges_tbl", edges_df)

    result = con.execute("""
        WITH child_assays AS (
            SELECT e.parent, e.child, a.assay
            FROM edges_tbl e
            JOIN assays_tbl a ON e.child = a.uid
        ),
        parent_hits AS (
            SELECT ca.parent, ca.child, ca.assay,
                   CASE WHEN pa.uid IS NOT NULL THEN 1 ELSE 0 END AS direction
            FROM child_assays ca
            LEFT JOIN assays_tbl pa ON ca.parent = pa.uid AND ca.assay = pa.assay
        )
        SELECT child, assay, direction FROM parent_hits
    """).fetchall()

    con.close()

    direction_by_pair: Dict[Tuple[str, int], int] = {}
    child_uids_by_assay: Dict[int, Set[str]] = {}
    conflicts_by_assay: Dict[int, Set[str]] = {}

    for child, assay_id, direction in result:
        key = (child, int(assay_id))
        direction_by_pair[key] = int(direction)
        child_uids_by_assay.setdefault(int(assay_id), set()).add(child)

    return direction_by_pair, child_uids_by_assay, conflicts_by_assay


def detect_cycles(edges: Set[Tuple[str, str]]) -> List[List[str]]:
    """Detect cycles in the parent-child DAG using DFS.

    Returns a list of cycles, each normalized by lexicographic rotation.
    """
    adj: Dict[str, Set[str]] = {}
    for parent, child in edges:
        adj.setdefault(child, set()).add(parent)  # child -> parents (upward)

    all_nodes = set()
    for p, c in edges:
        all_nodes.add(p)
        all_nodes.add(c)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in all_nodes}
    parent_map: Dict[str, Optional[str]] = {n: None for n in all_nodes}
    cycles: List[List[str]] = []
    seen_cycles: Set[Tuple[str, ...]] = set()

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbor in adj.get(node, ()):
            if color[neighbor] == GRAY:
                # Found a cycle — trace it back
                cycle = [neighbor, node]
                cur = node
                while parent_map.get(cur) is not None and parent_map[cur] != neighbor:
                    cur = parent_map[cur]
                    cycle.append(cur)
                # Normalize by lexicographic rotation
                cycle = _normalize_cycle(cycle)
                key = tuple(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    cycles.append(cycle)
            elif color[neighbor] == WHITE:
                parent_map[neighbor] = node
                dfs(neighbor)
        color[node] = BLACK

    for node in sorted(all_nodes):
        if color[node] == WHITE:
            dfs(node)

    return cycles


def _normalize_cycle(cycle: List[str]) -> List[str]:
    """Normalize a cycle by rotating to the lexicographically smallest start."""
    if not cycle:
        return cycle
    min_idx = cycle.index(min(cycle))
    return cycle[min_idx:] + cycle[:min_idx]
