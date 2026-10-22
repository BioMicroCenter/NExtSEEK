#!/usr/bin/env python3
"""Measure what graph schema 1.3's assay layer will hold on this box, before its first full sync at 1.3. Read-only.

Spec: docs/superpowers/specs/2026-09-25-graph-assay-nodes-design.md, section 9. It must run on the image the box has
today, so it is copied into the app container rather than built into it:

    docker cp scripts/graph_search/measure_assay_nodes.py nextseek:/app/scripts/graph_search/
    docker compose exec -T nextseek /app/.venv/bin/python scripts/graph_search/measure_assay_nodes.py \
        --instance <local|dev|prod> --out /app/logs/assay_nodes_<instance>.json </dev/null

Both MySQL sessions are set READ ONLY before the first read; the graph is read with read routing and read statements
only. It writes one JSON file and prints a summary on stderr. The role rule (spec 5.3) is written out here rather than
imported, because nextseek_api/graph_sync/assays.py is not in the image it runs on;
nextseek_api/tests/test_graph_sync_assays.py pins the two to each other.

What it reports:
- internal_assays: rows, and titles held by more than one id;
- assay_context: rows, rows with no internal_assay_id, internal ids with more than one row, rows naming an unknown id;
- the mapping: rows, bad rows, SEEK assays mapped to more than one internal assay, SEEK assays with no study;
- unmapped SEEK assays with members: how many, their members, the largest;
- the role rule over the graph's DERIVED_FROM between Sample nodes: the expected INPUT_TO and OUTPUT_OF edges, the
  samples that get one, the members with no role, and the mapped SEEK assays none of whose members gets one;
- the DERIVED_FROM degree of Sample nodes, which sets targeted.PARTNER_REWRITE_MAX;
- the largest membership of one SEEK assay and of one internal assay, which sets targeted.ASSAY_REWRITE_MAX.

Exit status: 0 with the file written; 2 when it cannot start (a database that is not MySQL, no Neo4j URI).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from array import array
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT_TO, OUTPUT_OF = "INPUT_TO", "OUTPUT_OF"
TOP = 20
INSTANCES = ("local", "dev", "prod")

LINEAGE = "MATCH (c:Sample)-[:DERIVED_FROM]->(p:Sample) RETURN c.id AS child, p.id AS parent"
DEGREES = """
MATCH (s:Sample)
WITH COUNT { (s)-[:DERIVED_FROM]-(:Sample) } AS d
RETURN count(*) AS samples, max(d) AS max, percentileDisc(d, 0.5) AS p50, percentileDisc(d, 0.99) AS p99,
       percentileDisc(d, 0.999) AS p999, sum(CASE WHEN d > 1000 THEN 1 ELSE 0 END) AS over_1000,
       sum(CASE WHEN d > 10000 THEN 1 ELSE 0 END) AS over_10000
"""
TOP_DEGREES = """
MATCH (s:Sample)
WITH s, COUNT { (s)-[:DERIVED_FROM]-(:Sample) } AS d
ORDER BY d DESC LIMIT 10
RETURN s.id AS id, s.type AS type, d AS degree
"""


class Refusal(RuntimeError):
    """The run cannot start; nothing was read."""


# --- the pure part -------------------------------------------------------------------------------------------------

def valid_mapping(pairs, internal_ids, seek_ids):
    """SEEK assay id to its sorted internal assay ids, and the rows dropped (spec 4.2)."""
    by_seek: dict[int, set[int]] = {}
    problems = {"without_internal_assay": [], "unknown_internal_assay": [], "unknown_seek_assay": []}
    for seek_id, internal_id in pairs:
        if internal_id is None:
            problems["without_internal_assay"].append(seek_id)
        elif internal_id not in internal_ids:
            problems["unknown_internal_assay"].append([seek_id, internal_id])
        elif seek_id not in seek_ids:
            problems["unknown_seek_assay"].append([seek_id, internal_id])
        else:
            by_seek.setdefault(seek_id, set()).add(internal_id)
    return {s: tuple(sorted(ids)) for s, ids in sorted(by_seek.items())}, problems


def roles_of_pair(child, parent, child_assays, parent_assays, internal_by_seek):
    """Spec 5.3 for one DERIVED_FROM pair: (sample, relationship type, internal assay id, SEEK assay id) per role."""
    if child == parent:
        return
    for seek_id in sorted(set(child_assays or ()) & set(parent_assays or ())):
        for internal_id in internal_by_seek.get(seek_id, ()):
            yield child, OUTPUT_OF, internal_id, seek_id
            yield parent, INPUT_TO, internal_id, seek_id


def role_rule(pairs, assays_by_sample, internal_by_seek) -> dict:
    """Sample id to {(relationship type, internal assay id): set of SEEK assay ids}."""
    roles: dict = {}
    for child, parent in pairs:
        for sample_id, rel, internal_id, seek_id in roles_of_pair(
                child, parent, assays_by_sample.get(child), assays_by_sample.get(parent), internal_by_seek):
            roles.setdefault(sample_id, {}).setdefault((rel, internal_id), set()).add(seek_id)
    return roles


class RoleTally:
    """The role rule over a stream of pairs, kept as packed codes of 8 bytes each, so a million-edge graph fits."""

    def __init__(self, assays_by_sample: dict, internal_by_seek: dict):
        self.assays, self.by_seek = assays_by_sample, internal_by_seek
        self.edge_codes = array("q")     # sample << 32 | internal id << 1 | 1 for OUTPUT_OF
        self.member_codes = array("q")   # sample << 32 | SEEK id: a membership that has a role
        self.pairs = 0

    def add(self, child, parent) -> None:
        if not (isinstance(child, int) and isinstance(parent, int)):
            return
        self.pairs += 1
        for sample_id, rel, internal_id, seek_id in roles_of_pair(
                child, parent, self.assays.get(child), self.assays.get(parent), self.by_seek):
            self.edge_codes.append((sample_id << 32) | (internal_id << 1) | (rel == OUTPUT_OF))
            self.member_codes.append((sample_id << 32) | seek_id)

    def finish(self) -> dict:
        edges = sorted(set(self.edge_codes))
        with_role = set(self.member_codes)
        self.edge_codes, self.member_codes = array("q"), array("q")
        without_role: Counter = Counter()
        with_any_role: set[int] = {code & 0xFFFFFFFF for code in with_role}
        for sample_id, seek_ids in self.assays.items():
            for seek_id in seek_ids:
                if seek_id in self.by_seek and ((sample_id << 32) | seek_id) not in with_role:
                    without_role[seek_id] += 1
        never = {s: n for s, n in sorted(without_role.items()) if s not in with_any_role}
        return {
            "pairs": self.pairs,
            "expected_sample_edges": len(edges),
            "input_to_edges": sum(1 for code in edges if not code & 1),
            "output_of_edges": sum(1 for code in edges if code & 1),
            "samples_with_edges": len({code >> 32 for code in edges}),
            "members_without_role": sum(without_role.values()),
            "members_without_role_by_seek_assay": dict(without_role.most_common(TOP)),
            "mapped_seek_assays_without_any_role": never,
        }


def lineage_tally(assays_by_sample: dict, internal_by_seek: dict):
    """The result transformer of the lineage read: a fresh ``RoleTally`` over every record it is handed. The driver
    runs it again when a transient error retries the read, so a retry starts over instead of counting the pairs of the
    try it cut short twice."""
    def transform(result) -> RoleTally:
        tally = RoleTally(assays_by_sample, internal_by_seek)
        for record in result:
            tally.add(record["child"], record["parent"])
        return tally
    return transform


def titles_held_twice(internal) -> dict:
    """Each title more than one internal assay holds, to their ids. Keyed by the title as text, so a NULL title sits
    beside the others when the result is written as sorted JSON."""
    titles: dict = {}
    for internal_id, title in internal:
        titles.setdefault(str(title), []).append(internal_id)
    return {t: ids for t, ids in titles.items() if len(ids) > 1}


def _round_up(value: int, step: int) -> int:
    return int(math.ceil(max(value, 0) / step) * step)


def suggest_limits(degree_p999: int, largest_seek_membership: int) -> dict:
    """The rules W11 applies: a partner above production's 99.9th degree percentile is handed to the loop (at least
    1,000, at most 10,000); a member rewrite covers production's largest SEEK assay (at least 50,000, at most
    250,000, above which a full sync is the cheaper repair)."""
    return {"PARTNER_REWRITE_MAX": min(10_000, max(1_000, _round_up(degree_p999, 1_000))),
            "ASSAY_REWRITE_MAX": min(250_000, max(50_000, _round_up(largest_seek_membership, 10_000)))}


# --- the reads -----------------------------------------------------------------------------------------------------

def _text(value):
    return bytes(value).decode("utf-8") if isinstance(value, (bytes, bytearray)) else value


def _rows(conn, sql, params=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, params or [])
        while True:
            batch = cursor.fetchmany(10_000)
            if not batch:
                return
            yield from batch


def _read_only(connections, aliases) -> None:
    for alias in dict.fromkeys(aliases):
        conn = connections[alias]
        if conn.vendor != "mysql":
            raise Refusal(f"database {alias!r} is {conn.vendor}, not MySQL")
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")


def _has_table(conn, table) -> bool:
    with conn.cursor() as cursor:
        return table in conn.introspection.table_names(cursor)


def measure(connections, settings, driver, db) -> dict:
    """Every number of the module docstring. Reads only."""
    from neo4j import RoutingControl
    from nextseek_api.services.context_catalog import _rows_from_cursor

    seek, dmac = connections[settings.SEEK_DATABASE], connections[settings.NEXTSEEK_DATABASE]
    _read_only(connections, (settings.SEEK_DATABASE, settings.NEXTSEEK_DATABASE))
    out: dict = {}

    internal = [(int(i), _text(t)) for i, t in _rows(dmac, "SELECT id, internal_assay_title FROM internal_assays")] \
        if _has_table(dmac, "internal_assays") else []
    out["internal_assays"] = {"rows": len(internal), "titles_held_twice": titles_held_twice(internal)}
    internal_ids = {i for i, _ in internal}

    context = []
    if _has_table(dmac, "assay_context"):
        with dmac.cursor() as cursor:
            cursor.execute("SELECT * FROM assay_context ORDER BY id")
            context = _rows_from_cursor(cursor)
    by_internal = Counter(r.get("internal_assay_id") for r in context if r.get("internal_assay_id") is not None)
    out["assay_context"] = {
        "rows": len(context),
        "rows_without_internal_assay": sum(1 for r in context if r.get("internal_assay_id") is None),
        "internal_ids_with_several_rows": {int(i): n for i, n in by_internal.items() if n > 1},
        "rows_naming_an_unknown_internal_assay": sorted(
            r.get("id") for r in context
            if r.get("internal_assay_id") is not None and int(r["internal_assay_id"]) not in internal_ids)}

    study_of = {int(a): (int(s) if s is not None else None) for a, s in _rows(seek, "SELECT id, study_id FROM assays")}
    pairs = [(int(a), int(i) if i is not None else None) for a, i in _rows(
        dmac, "SELECT DISTINCT assay_id, internal_assay_id FROM assays_internal_assays WHERE assay_id IS NOT NULL")] \
        if _has_table(dmac, "assays_internal_assays") else []
    by_seek, problems = valid_mapping(pairs, internal_ids, set(study_of))
    out["mapping"] = {"rows": len(pairs), "mapped_seek_assays": len(by_seek),
                      "seek_assays_mapped_to_several": {s: list(ids) for s, ids in by_seek.items() if len(ids) > 1},
                      "seek_assays_without_study": sum(1 for s in study_of.values() if s is None),
                      "mapped_seek_assays_without_study": sum(1 for s in by_seek if study_of.get(s) is None),
                      "bad_rows": problems}

    assays_by_sample: dict[int, tuple] = {}
    shared: dict[tuple, tuple] = {}
    members: Counter = Counter()
    current, found = None, set()
    for sample_id, seek_id in _rows(seek, "SELECT asset_id, assay_id FROM assay_assets WHERE asset_type = %s "
                                          "AND asset_id IS NOT NULL AND assay_id IS NOT NULL ORDER BY asset_id",
                                    ["Sample"]):
        sample_id, seek_id = int(sample_id), int(seek_id)
        if sample_id != current:
            if current is not None:
                key = tuple(sorted(found))
                assays_by_sample[current] = shared.setdefault(key, key)
            current, found = sample_id, set()
        found.add(seek_id)
    if current is not None:
        key = tuple(sorted(found))
        assays_by_sample[current] = shared.setdefault(key, key)
    for seek_ids in assays_by_sample.values():
        members.update(seek_ids)
    unmapped = {s: n for s, n in members.items() if s not in by_seek}
    out["unmapped_seek_assays_with_members"] = {
        "count": len(unmapped), "members": sum(unmapped.values()),
        "largest": dict(Counter(unmapped).most_common(TOP))}
    per_internal: Counter = Counter()
    for seek_ids in assays_by_sample.values():
        per_internal.update({i for s in seek_ids for i in by_seek.get(s, ())})
    out["largest_membership"] = {
        "seek_assay": dict(members.most_common(5)), "internal_assay": dict(per_internal.most_common(5))}

    tally = driver.execute_query(LINEAGE, {}, database_=db, routing_=RoutingControl.READ,
                                 result_transformer_=lineage_tally(assays_by_sample, by_seek))
    out["roles"] = tally.finish()

    degree = driver.execute_query(DEGREES, {}, database_=db, routing_=RoutingControl.READ).records
    out["degree"] = dict(degree[0]) if degree else {}
    top = driver.execute_query(TOP_DEGREES, {}, database_=db, routing_=RoutingControl.READ).records
    out["degree"]["largest"] = [dict(r) for r in top]
    out["suggested"] = suggest_limits(int(out["degree"].get("p999") or 0),
                                      max(members.values(), default=0))
    return out


def main(argv=None) -> int:
    """Parse the arguments, set up Django, measure, write the JSON file. READ ONLY throughout."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--instance", choices=INSTANCES, required=True)
    parser.add_argument("--out", required=True, help="the JSON file to write")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
    # An app image that warms the nf-core schema cache at startup does it in a thread django.setup() starts: outbound
    # requests and a log line. A read-only measurement wants neither.
    os.environ.setdefault("NEXTSEEK_SKIP_SCHEMA_WARM", "1")
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections
    from neo4j import GraphDatabase

    config = getattr(settings, "NEO4J_DATABASE", None) or {}
    if not config.get("URI"):
        print("measure_assay_nodes: settings.NEO4J_DATABASE names no URI", file=sys.stderr)
        return 2
    try:
        with GraphDatabase.driver(config["URI"], auth=config["AUTH"]) as driver:
            result = measure(connections, settings, driver, config["NAME"])
    except Refusal as exc:
        print(f"measure_assay_nodes: refused: {exc}", file=sys.stderr)
        return 2
    result = {"instance": args.instance, "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **result}
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    roles = result["roles"]
    print(f"measure_assay_nodes: {args.instance}: {result['internal_assays']['rows']} internal assays, "
          f"{roles['expected_sample_edges']} sample edges on {roles['samples_with_edges']} samples, "
          f"{roles['members_without_role']} members without a role, degree max {result['degree'].get('max')}, "
          f"suggested {result['suggested']}; written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
