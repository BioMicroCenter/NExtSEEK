"""Recompute DERIVED_FROM assay labels from assay_assets.

assay_assets is the source of truth and the edge labels are derived from it.
`batch_upload/scripts/backfill_shared_assays.py` puts it plainly: "The graph
cannot repair itself. What was dropped exists only in
seek_production.assay_assets." Registering a membership therefore invalidates
the labels on every DERIVED_FROM edge incident to that sample.

Additive and conservative, exactly as the backfill is:
  * internal_assay_id / internal_assay_title are NEVER touched. Every existing
    consumer (entity_tree, the download workbook, chat_nextseek context,
    seek/views.py) reads those and must not move.
  * Only the plural fields are written.

TWO TRAPS, both of which have cost real time on this codebase before:

  1. The correct Django alias is `seek` (seek_production). The `default` alias
     is `dmac`, whose assay_assets table EXISTS but is EMPTY, so querying it
     returns a confident and entirely wrong answer.
  2. Neo4j's password is NEO4J_PASSWORD in the environment. docker-compose.yml
     still names an old literal.
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

#: ONE pass over the edges, with a server-side map lookup per edge.
#:
#: The obvious form -- UNWIND $rows, then MATCH the edge by child_id/parent_id --
#: is a full DERIVED_FROM scan PER ROW, because this database has no property
#: indexes at all (only the two default LOOKUPs). At 5,000 rows against 500k+
#: edges it does not finish; the first attempt died on
#: TransactionTimedOutClientConfiguration. Matching Sample nodes by uuid or id
#: instead is no better, since those are unindexed label scans.
#:
#: Measured on a 514,067-edge graph, this form is FLAT in batch size:
#: 0.40s for 3 edges, 0.37s for 500, 0.50s for 20,000. That is why the
#: recompute can run inline in a synchronous request.
RECOMPUTE_CYPHER = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
WHERE r.internal_assay_title IS NOT NULL
  AND r.child_id IS NOT NULL AND r.parent_id IS NOT NULL
WITH r, $edges[toString(r.child_id) + "_" + toString(r.parent_id)] AS entry
WHERE entry IS NOT NULL
SET r.internal_assay_ids = entry.ids, r.internal_assay_titles = entry.titles
RETURN count(r) AS written
"""

#: Edges incident to a set of samples, in either direction.
_EDGES_FOR_SAMPLES = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
WHERE r.child_id IS NOT NULL AND r.parent_id IS NOT NULL
  AND (r.child_id IN $sample_ids OR r.parent_id IN $sample_ids)
RETURN r.child_id AS child_id, r.parent_id AS parent_id
"""


def _seek_cursor():
    """Cursor on seek_production. See trap 1 in the module docstring."""
    alias = settings.SEEK_DATABASE
    name = settings.DATABASES[alias]["NAME"]
    # Keep this line. It is the operator's live confirmation of trap 1 during a
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


def recompute_for_samples(sample_ids: Set[int], driver, db_name: str) -> int:
    """Repair the plural assay lists on ALREADY-LABELLED edges incident to these samples.

    It does not label previously-unlabelled edges. `_EDGES_FOR_SAMPLES` selects
    every incident edge, but RECOMPUTE_CYPHER writes only where
    `r.internal_assay_title IS NOT NULL`, so an edge whose singular label is
    NULL is planned and then skipped. That is inherited from the backfill this
    was lifted from, and it is deliberate: writing the plural fields alone on a
    null-singular edge would be invisible anyway, because every plural consumer
    gates on the singular being present (see the outer MATCH in
    services/sampletype_connections.py), so it would create dead data rather
    than a repaired edge. Writing the singular fields instead is a different
    change with four independent consumers.

    Measured on the reference graph: 7,972 of 522,039 DERIVED_FROM edges are in
    that state, 1.53%.

    Returns the number of edges the database reported writing, which may be
    fewer than were planned; the shortfall is logged.
    """
    if not sample_ids:
        return 0

    ids = sorted(int(s) for s in sample_ids)
    with driver.session(database=db_name) as session:
        edges = session.run(_EDGES_FOR_SAMPLES, sample_ids=ids).data()
        if not edges:
            return 0

        endpoints = {int(e["child_id"]) for e in edges}
        endpoints |= {int(e["parent_id"]) for e in edges}

        by_sample = assays_by_sample(endpoints)
        all_assays: Set[int] = set()
        for assays in by_sample.values():
            all_assays |= assays
        internal = resolve_internal(all_assays)

        payload: Dict[str, Dict[str, list]] = {}
        for edge in edges:
            child, parent = int(edge["child_id"]), int(edge["parent_id"])
            shared = by_sample.get(child, set()) & by_sample.get(parent, set())
            resolved = sorted(
                {internal[a] for a in shared if a in internal},
                key=lambda pair: pair[0],
            )
            if not resolved:
                continue
            payload[f"{child}_{parent}"] = {
                "ids": [pair[0] for pair in resolved],
                "titles": [pair[1] for pair in resolved],
            }

        if not payload:
            return 0
        written = int(session.run(RECOMPUTE_CYPHER, edges=payload).single()["written"])
        # Never return a shortfall silently. Planned-but-unwritten edges are the
        # null-singular ones the docstring describes, and a caller that sees only
        # `written` cannot tell a clean run from a partial one. The sibling
        # backfill script warns on exactly this comparison; so does this.
        if written < len(payload):
            log.warning(
                "recompute wrote %d of %d planned edges; %d were planned but not "
                "written, which means their singular internal_assay_title is NULL",
                written, len(payload), len(payload) - written,
            )
        elif written > len(payload):
            # Cannot happen: the write matches only keys present in $edges, so it
            # can touch at most len(payload) edges. Reported rather than ignored
            # because if it ever fires, the query has stopped meaning what this
            # module thinks it means, and a wrong count is how that would first
            # show. Never phrased as a negative shortfall.
            log.error(
                "recompute wrote %d edges but planned only %d; the write query "
                "matched more than its parameter map, which should be impossible",
                written, len(payload),
            )
        else:
            log.info("recomputed assay labels on %d DERIVED_FROM edges", written)
        return written
