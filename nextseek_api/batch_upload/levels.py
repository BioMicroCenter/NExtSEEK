"""Stage 2.5: LEVELS — Topological level assignment for parent-child insertion ordering."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from .insert import load_existing_samples
from .models import InputRowModel

log = logging.getLogger(__name__)


@dataclass
class LevelAssignment:
    """Result of topological level computation."""

    levels: Dict[str, int]              # uid -> topo level (0, 1, 2, ...)
    max_level: int                      # highest level number (-1 if empty)
    cycle_uids: Set[str]               # UIDs in cycles (deferred to final pass)
    orphan_uids: Set[str]              # UIDs with unresolvable parents (level 0 + warning)
    external_parents: Dict[str, int]   # parent_uid -> sample_id (from DB lookup)
    preexisting_uids: Dict[str, int]   # spreadsheet UIDs already in DB (uuid -> sample_id)


def compute_levels(
    rows: List[InputRowModel],
    parents_of: Dict[str, Set[str]],
    children_of: Dict[str, Set[str]],
    cycles: List[List[str]],
    conn_factory: Callable,
) -> LevelAssignment:
    """Compute topological insertion levels for all rows.

    Uses Kahn's algorithm (BFS-based topological sort) to assign each sample
    a level such that all parents are at strictly lower levels.

    Level 0: samples with no in-batch parents (roots, external-parent children, orphans).
    Level N: samples whose highest-level in-batch parent is at level N-1.
    Cycle UIDs: excluded from levels, deferred to a final pass.
    """
    if not rows:
        return LevelAssignment(
            levels={}, max_level=-1, cycle_uids=set(),
            orphan_uids=set(), external_parents={},
            preexisting_uids={},
        )

    batch_uids = {r.UID for r in rows if r.UID is not None}

    # Flatten cycle UIDs
    cycle_uid_set: Set[str] = set()
    for cycle in cycles:
        cycle_uid_set.update(cycle)

    # Find all referenced parent UIDs that are not in the current batch
    all_parent_uids: Set[str] = set()
    for parent_set in parents_of.values():
        all_parent_uids.update(parent_set)
    external_parent_uids = all_parent_uids - batch_uids

    # Query DB once for external parents AND pre-existing spreadsheet UIDs
    all_uids_to_check = external_parent_uids | batch_uids
    with conn_factory() as conn:
        all_existing = load_existing_samples(list(all_uids_to_check), conn)

    # Split results: external parents vs spreadsheet UIDs already in DB
    external_parents: Dict[str, int] = {
        uid: sid for uid, sid in all_existing.items() if uid in external_parent_uids
    }
    preexisting_uids: Dict[str, int] = {
        uid: sid for uid, sid in all_existing.items() if uid in batch_uids
    }

    # For each non-cycle UID, compute effective in-batch parents
    # (parents that are in the batch AND not in a cycle — these are the
    #  dependencies that must be satisfied before this UID can be inserted)
    orphan_uids: Set[str] = set()
    effective_parents: Dict[str, Set[str]] = {}  # uid -> set of in-batch parent uids

    for uid in batch_uids:
        if uid in cycle_uid_set:
            continue
        parents = parents_of.get(uid, set())
        in_batch = set()
        for p in parents:
            if p in batch_uids and p not in cycle_uid_set:
                in_batch.add(p)
            elif p not in batch_uids and p not in external_parents:
                # Parent is not in batch and not in DB — orphan
                orphan_uids.add(uid)
        effective_parents[uid] = in_batch

    # Kahn's algorithm: BFS level assignment
    levels: Dict[str, int] = {}
    # In-degree: count of remaining effective parents
    in_degree: Dict[str, int] = {}
    for uid in batch_uids:
        if uid in cycle_uid_set:
            continue
        in_degree[uid] = len(effective_parents.get(uid, set()))

    # Seed queue with zero in-degree UIDs (sorted for determinism)
    queue: deque[str] = deque()
    for uid in sorted(in_degree.keys()):
        if in_degree[uid] == 0:
            levels[uid] = 0
            queue.append(uid)

    max_level = 0 if queue else -1

    while queue:
        uid = queue.popleft()
        current_level = levels[uid]
        # Process children sorted for determinism
        for child in sorted(children_of.get(uid, set())):
            if child in cycle_uid_set or child not in batch_uids:
                continue
            if child not in in_degree:
                continue
            # This parent is satisfied — decrement child's in-degree
            in_degree[child] -= 1
            # Child's level is at least one more than this parent's level
            candidate = current_level + 1
            levels[child] = max(levels.get(child, 0), candidate)
            if in_degree[child] == 0:
                queue.append(child)
                max_level = max(max_level, levels[child])

    return LevelAssignment(
        levels=levels,
        max_level=max_level,
        cycle_uids=cycle_uid_set,
        orphan_uids=orphan_uids,
        external_parents=external_parents,
        preexisting_uids=preexisting_uids,
    )


def cascade_failures(
    failed_uids: Set[str],
    children_of: Dict[str, Set[str]],
    eligible_uids: Set[str],
) -> Dict[str, str]:
    """Compute all descendants of failed UIDs that must also fail.

    Uses BFS from failed_uids through children_of edges.
    Only cascades within eligible_uids (e.g., UIDs at the current level).
    Cites the immediate parent, not the root ancestor.

    Returns:
        uid -> reason string (e.g., "parent NHP-260225MIT-1 failed to insert")
    """
    if not failed_uids or not children_of:
        return {}

    cascade_result: Dict[str, str] = {}
    queue: deque[tuple[str, str]] = deque()  # (child_uid, failed_parent_uid)

    # Seed: direct children of failed UIDs that are eligible
    for uid in failed_uids:
        for child in children_of.get(uid, set()):
            if child in eligible_uids and child not in failed_uids:
                queue.append((child, uid))

    visited: Set[str] = set(failed_uids)
    while queue:
        child, failed_parent = queue.popleft()
        if child in visited:
            continue
        visited.add(child)
        cascade_result[child] = f"parent {failed_parent} failed to insert"
        # Continue cascading to grandchildren
        for grandchild in children_of.get(child, set()):
            if grandchild in eligible_uids and grandchild not in visited:
                queue.append((grandchild, child))

    return cascade_result
