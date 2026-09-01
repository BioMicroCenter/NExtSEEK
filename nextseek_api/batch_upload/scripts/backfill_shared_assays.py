"""One-time backfill: populate internal_assay_ids/titles on DERIVED_FROM edges (#118).

Until the fix in neo4j_sync.build_derived_from_payloads_from_db, an edge kept only
the LOWEST internal_assay_id among the assays its two endpoints shared and discarded
the rest with no record. On production that lost a second assay on ~3,645 edges, and
the discarded one was frequently the more specific: Cell Isolation lost to Flow
Cytometry on 998 edges, Bacterial Extraction to DNA Extraction on 387.

The graph cannot repair itself. What was dropped exists only in
seek_production.assay_assets, so the shared set is recomputed from SQL here and
written back onto the edge.

Additive and conservative:
  * internal_assay_id / internal_assay_title are NEVER touched. Every existing
    consumer (entity_tree, the download workbook, chat_nextseek context,
    seek/views.py) reads those and must not move.
  * Only edges whose endpoints share MORE THAN ONE assay are written. An edge with
    a single shared assay is already fully described by the singular fields, and
    consumers fall back to them when the list is absent.

TWO TRAPS, both of which have cost real time on this codebase before:

  1. The correct Django alias is `seek` (seek_production). The `default` alias is
     `dmac`, whose assay_assets table EXISTS but is EMPTY, so querying it returns a
     confident and entirely wrong answer -- previously "0% of edges share an assay"
     for both a test set and its control. If a control and a test set agree
     exactly, suspect the connection, not the data.
  2. Neo4j's password is NEO4J_PASSWORD in the environment. docker-compose.yml
     still names an old literal; cypher-shell with it fails mid-session while the
     bolt driver keeps working.

Usage:
    # always first, writes nothing:
    python nextseek_api/batch_upload/scripts/backfill_shared_assays.py --dry-run

    # after reviewing the ledger AND with a restore-tested Neo4j backup in hand:
    python nextseek_api/batch_upload/scripts/backfill_shared_assays.py --apply
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from collections import defaultdict
from typing import Dict, List, Tuple

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
django.setup()

from django.conf import settings  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from nextseek_api.assay_registration.graph import (  # noqa: E402
    RECOMPUTE_CYPHER as _WRITE,
    assays_by_sample,
    resolve_internal,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BATCH_SIZE = 1000

#: Edges that carry an assay at all. child_id/parent_id are seek sample ids, which
#: is what assay_assets keys on.
_FETCH_EDGES = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
WHERE r.internal_assay_title IS NOT NULL
  AND r.child_id IS NOT NULL AND r.parent_id IS NOT NULL
RETURN r.child_id AS child_id, r.parent_id AS parent_id,
       r.internal_assay_id AS current_id, r.internal_assay_title AS current_title
"""


def plan(driver, db_name: str) -> List[dict]:
    """Every edge needing a list, with the winner it currently reports."""
    records, _s, _k = driver.execute_query(_FETCH_EDGES, database_=db_name)
    edges = [dict(r) for r in records]
    log.info("edges carrying an assay: %d", len(edges))

    sample_ids = {int(e["child_id"]) for e in edges} | {int(e["parent_id"]) for e in edges}
    by_sample = assays_by_sample(sample_ids)
    if not by_sample:
        raise SystemExit(
            "assay_assets returned nothing for any sample. Refusing to continue: "
            "this is the signature of querying the empty dmac copy instead of "
            "seek_production (trap 1)."
        )
    every_assay = {a for s in by_sample.values() for a in s}
    mapping = resolve_internal(every_assay)
    log.info("samples %d | assays %d | resolved %d", len(by_sample), len(every_assay), len(mapping))

    rows: List[dict] = []
    for e in edges:
        shared = by_sample.get(int(e["child_id"]), set()) & by_sample.get(int(e["parent_id"]), set())
        resolved = {}
        for a in shared:
            hit = mapping.get(a)
            if hit:
                resolved.setdefault(hit[0], hit[1])
        if len(resolved) <= 1:
            continue  # already fully described by the singular fields
        ids = sorted(resolved)
        rows.append({
            "child_id": int(e["child_id"]),
            "parent_id": int(e["parent_id"]),
            "ids": ids,
            "titles": [resolved[i] for i in ids],
            "current_id": e["current_id"],
            "current_title": e["current_title"],
            "dropped": [resolved[i] for i in ids if i != e["current_id"]],
        })
    return rows


def write_ledger(rows: List[dict], path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["child_id", "parent_id", "kept_id", "kept_title",
                    "all_ids", "all_titles", "recovered_titles"])
        for r in rows:
            w.writerow([r["child_id"], r["parent_id"], r["current_id"], r["current_title"],
                        ";".join(map(str, r["ids"])), ";".join(r["titles"]),
                        ";".join(r["dropped"])])
    log.info("ledger written: %s (%d rows)", path, len(rows))


def summarise(rows: List[dict]) -> None:
    by_count: Dict[int, int] = defaultdict(int)
    pairs: Dict[Tuple[str, str], int] = defaultdict(int)
    for r in rows:
        by_count[len(r["ids"])] += 1
        for d in r["dropped"]:
            pairs[(d, r["current_title"] or "")] += 1
    log.info("edges to update: %d", len(rows))
    for n in sorted(by_count):
        log.info("  %d shared assays: %d edges", n, by_count[n])
    log.info("largest recoveries (dropped -> kept):")
    for (dropped, kept), n in sorted(pairs.items(), key=lambda kv: -kv[1])[:10]:
        log.info("  %6d  %r recovered, %r still the winner", n, dropped, kept)


def apply(driver, db_name: str, rows: List[dict]) -> int:
    """Single statement. See _WRITE for why this is not batched."""
    edges = {
        f"{r['child_id']}_{r['parent_id']}": {"ids": r["ids"], "titles": r["titles"]}
        for r in rows
    }
    log.info("writing %d edges in one pass ...", len(edges))
    recs, _s, _k = driver.execute_query(_WRITE, {"edges": edges}, database_=db_name)
    return recs[0]["written"] if recs else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="plan and write the ledger only")
    g.add_argument("--apply", action="store_true", help="write to the graph")
    ap.add_argument("--ledger", default="shared_assay_backfill.csv")
    args = ap.parse_args()

    neo = settings.NEO4J_DATABASE
    with GraphDatabase.driver(neo["URI"], auth=neo["AUTH"]) as driver:
        rows = plan(driver, neo["NAME"])
        summarise(rows)
        write_ledger(rows, args.ledger)
        if args.dry_run:
            log.info("DRY RUN: nothing was written.")
            return
        log.info("applying to %s ...", neo["URI"])
        written = apply(driver, neo["NAME"], rows)
        log.info("edges written: %d (planned %d)", written, len(rows))
        if written != len(rows):
            log.warning("written != planned; re-run --dry-run and compare against the ledger")


if __name__ == "__main__":
    main()
