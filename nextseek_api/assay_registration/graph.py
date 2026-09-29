"""Read assay_assets and the internal-assay mapping. Nothing here writes a graph.

This module used to recompute DERIVED_FROM assay labels and write them itself.
One rule owns those labels now, for every writer
(`nextseek_api/graph_sync/labels.py`, the sync spec's section 7.3): a membership
write enqueues a `samples` outbox row and the drain relabels every edge incident
to those samples from the same MySQL sources. Two writers of one derived
property is how the dev box ended up with edges whose singular assay fields were
right and whose plural lists had been written by something else, so
`RECOMPUTE_CYPHER`, `_EDGES_FOR_SAMPLES` and `recompute_for_samples` are gone
rather than kept beside it.

What remains is SQL. assay_assets is still the source of truth: "The graph
cannot repair itself. What was dropped exists only in
seek_production.assay_assets."

THE TRAP, which has cost real time on this codebase before: the correct Django
alias is `seek` (seek_production). The `default` alias is `dmac`, whose
assay_assets table EXISTS but is EMPTY, so querying it returns a confident and
entirely wrong answer.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Set, Tuple

from django.conf import settings
from django.db import connections

log = logging.getLogger(__name__)

#: SQL chunk size for the two helpers below. Named SQL_CHUNK, not CHUNK: resolver.py
#: defines its own CHUNK = 1000 in this same package, and two module constants sharing
#: a name with different values reads like a bug even when it is not.
SQL_CHUNK = 5000


def _seek_cursor():
    """Cursor on seek_production. See the trap in the module docstring."""
    alias = settings.SEEK_DATABASE
    name = settings.DATABASES[alias]["NAME"]
    # Keep this line. It is the operator's live confirmation of the trap during a
    # backfill dry run: the `default` alias is dmac, whose assay_assets table
    # exists and is EMPTY, so querying it returns a confident and entirely wrong
    # answer. Seeing the resolved database name is what catches that.
    log.info("SQL alias %r -> database %r", alias, name)
    return connections[alias].cursor(), name


def assays_by_sample(sample_ids: Set[int]) -> Dict[int, Set[int]]:
    """sample_id -> {assay_id}, from assay_assets."""
    cursor, dbname = _seek_cursor()
    out: Dict[int, Set[int]] = defaultdict(set)
    ids = sorted(sample_ids)
    with cursor:
        for start in range(0, len(ids), SQL_CHUNK):
            chunk = ids[start : start + SQL_CHUNK]
            ph = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT asset_id, assay_id FROM {dbname}.assay_assets "
                f"WHERE asset_type = 'Sample' AND asset_id IN ({ph})",
                chunk,
            )
            for asset_id, assay_id in cursor.fetchall():
                out[int(asset_id)].add(int(assay_id))
    return out


def resolve_internal(assay_ids: Set[int]) -> Dict[int, Tuple[int, str]]:
    """assay_id -> (internal_assay_id, title), junction table first, id fallback."""
    cursor, dbname = _seek_cursor()
    ns = settings.DATABASES[settings.NEXTSEEK_DATABASE]["NAME"]
    mapping: Dict[int, Tuple[int, str]] = {}
    ids = sorted(assay_ids)
    with cursor:
        for start in range(0, len(ids), SQL_CHUNK):
            chunk = ids[start : start + SQL_CHUNK]
            ph = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT aia.assay_id, ia.id, ia.internal_assay_title "
                f"FROM {ns}.assays_internal_assays aia "
                f"JOIN {ns}.internal_assays ia ON ia.id = aia.internal_assay_id "
                f"WHERE aia.assay_id IN ({ph})",
                chunk,
            )
            for assay_id, ia_id, ia_title in cursor.fetchall():
                mapping[int(assay_id)] = (int(ia_id), ia_title or "")
        unresolved = [a for a in ids if a not in mapping]
        for start in range(0, len(unresolved), SQL_CHUNK):
            chunk = unresolved[start : start + SQL_CHUNK]
            ph = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT id, title FROM {dbname}.assays WHERE id IN ({ph})", chunk
            )
            for assay_id, title in cursor.fetchall():
                mapping[int(assay_id)] = (int(assay_id), title or "")
    return mapping
