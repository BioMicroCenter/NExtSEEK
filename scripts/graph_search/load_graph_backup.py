#!/usr/bin/env python3
"""Load a graph backup (``neo4j.cypher.gz`` in the ``dump_neo4j.py`` format) into the lane's empty Neo4j.

    scripts/graph_search/lane.sh python scripts/graph_search/load_graph_backup.py \\
        /gswork/seeds/local-2026-09-14/neo4j.cypher.gz [--batch 5000] [--stats FILE] [--json]

The technique is the seed loader's (``startup/steps/seed.py``): node CREATEs are grouped by label set and
relationships by type, and each group is sent as parameterized ``UNWIND ... CREATE`` batches. Every statement is
classified with the seed loader's own regexes and map-literal parser, imported from it. Unlike the seed loader, the file
is streamed one statement at a time and a batch is sent as soon as it is full, so memory is bounded by ``--batch``
rows, not by the file.

The file holds every node statement, then its import index, then every relationship statement, then its cleanup; the
loader re-issues the index and the cleanup itself. After the load it checks the graph against what it read: the node
count per label and the relationship count per type must equal the file's, and, when ``--stats`` names an export-stats
file (by default ``neo4j.export-stats.json`` beside the backup, if present), the counts that export recorded.

Refusals (exit 2, nothing written): the graph is not empty (plain CREATE is not idempotent), or the Neo4j host is the
live stack's (``neo4j``). Exit 1 when the loaded counts differ from what was expected; 0 otherwise.

Connection: ``NEXTSEEK_NEO4J_HOST`` and ``NEXTSEEK_NEO4J_PASSWORD``, which ``lane.sh`` sets, in the form
``dmac.settings`` builds ``NEO4J_DATABASE`` from them.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter

from neo4j import GraphDatabase

from startup.steps.seed import _IMPORT_INDEX, _NODE_RE, _REL_RE, _cypher_map_to_dict

IMPORT_LABEL = "_ImportRef"
LIVE_NEO4J_HOSTS = frozenset({"neo4j"})
PROGRESS_EVERY = 50  # batches between progress lines

CREATE_IMPORT_INDEX = f"CREATE INDEX {_IMPORT_INDEX} IF NOT EXISTS FOR (n:{IMPORT_LABEL}) ON (n._exportId)"
DROP_IMPORT_INDEX = f"DROP INDEX {_IMPORT_INDEX} IF EXISTS"
STRIP_IMPORT_REF = (f"MATCH (n:{IMPORT_LABEL}) WITH n LIMIT $lim "
                    f"REMOVE n:{IMPORT_LABEL}, n._exportId RETURN count(n) AS n")
NODE_COUNT = "MATCH (n) RETURN count(n) AS n"
LABEL_COUNTS = "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n"
REL_TYPE_COUNTS = "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n"


class Refusal(RuntimeError):
    """The load must not start; nothing was written."""


def create_nodes(labels: str) -> str:
    """The node statement for one label set (``":Sample:_ImportRef"``; the regex allows only identifier labels)."""
    return f"UNWIND $rows AS r CREATE (n{labels}) SET n = r RETURN count(n) AS n"


def create_rels(rel_type: str) -> str:
    """The relationship statement for one type (the regex allows only identifier types)."""
    return (f"UNWIND $rows AS r "
            f"MATCH (a:{IMPORT_LABEL} {{_exportId: r.a}}) "
            f"MATCH (b:{IMPORT_LABEL} {{_exportId: r.b}}) "
            f"CREATE (a)-[rel:`{rel_type}`]->(b) SET rel = r.props RETURN count(rel) AS n")


def classify(line: str):
    """``("node", labels, props)``, ``("rel", type, row)``, ``("skip", None, None)`` or raise ValueError.

    The same rules as ``startup.steps.seed.parse_neo4j_cypher_dump``: an unrecognized statement fails the load.
    """
    s = line.strip()
    if not s:
        return "skip", None, None
    if s.startswith("CREATE (n"):
        m = _NODE_RE.match(s)
        if not m:
            raise ValueError(f"unparseable node statement: {s[:100]}")
        return "node", m.group(1), _cypher_map_to_dict(m.group(2))
    if s.startswith(f"MATCH (a:{IMPORT_LABEL}"):
        m = _REL_RE.match(s)
        if not m:
            raise ValueError(f"unparseable relationship statement: {s[:100]}")
        props = _cypher_map_to_dict(m.group(4).strip()) if m.group(4) else {}
        return "rel", m.group(3), {"a": int(m.group(1)), "b": int(m.group(2)), "props": props}
    if s.startswith(("CREATE INDEX", "DROP INDEX")) or s.startswith(f"MATCH (n:{IMPORT_LABEL}) REMOVE"):
        return "skip", None, None  # import scaffolding, re-issued by the loader
    raise ValueError(f"unrecognized cypher statement: {s[:100]}")


def iter_statements(path: str):
    """Yield the file's lines one at a time. ``newline="\\n"`` keeps a carriage return inside a string value,
    exactly as the seed loader's ``split("\\n")`` does."""
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as fh:
        for line in fh:
            yield line.rstrip("\n")


class Loader:
    def __init__(self, driver, database: str, batch: int, log=None):
        self.driver = driver
        self.database = database
        self.batch = batch
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.node_buffers: dict[str, list] = {}
        self.rel_buffers: dict[str, list] = {}
        self.nodes_read: Counter = Counter()      # label set to statements read
        self.rels_read: Counter = Counter()       # type to statements read
        self.nodes_created = 0
        self.rels_created = 0
        self.batches = 0
        self.rel_phase = False

    def _write(self, query: str, rows: list) -> int:
        def work(tx):
            return tx.run(query, rows=rows).single()["n"]
        with self.driver.session(database=self.database) as session:
            n = session.execute_write(work)
        self.batches += 1
        if self.batches % PROGRESS_EVERY == 0:
            self.log(f"load_graph_backup: {self.batches} batches, {self.nodes_created} nodes, "
                     f"{self.rels_created} relationships")
        return int(n)

    def _flush_nodes(self, labels: str) -> None:
        rows = self.node_buffers.pop(labels, [])
        if rows:
            n = self._write(create_nodes(labels), rows)
            if n != len(rows):
                raise RuntimeError(f"{labels}: created {n} nodes for {len(rows)} statements")
            self.nodes_created += n

    def _flush_rels(self, rel_type: str) -> None:
        rows = self.rel_buffers.pop(rel_type, [])
        if rows:
            n = self._write(create_rels(rel_type), rows)
            if n != len(rows):
                raise RuntimeError(f"{rel_type}: created {n} relationships for {len(rows)} statements; "
                                   "an endpoint's _exportId is missing")
            self.rels_created += n

    def _start_rel_phase(self) -> None:
        for labels in list(self.node_buffers):
            self._flush_nodes(labels)
        with self.driver.session(database=self.database) as session:
            session.run(CREATE_IMPORT_INDEX).consume()
            session.run("CALL db.awaitIndexes(600)").consume()
        self.rel_phase = True
        self.log(f"load_graph_backup: {self.nodes_created} nodes created; import index online")

    def add(self, kind: str, key, row) -> None:
        if kind == "node":
            if self.rel_phase:
                raise ValueError("a node statement follows the relationship statements; this loader needs every "
                                 "node first, as dump_neo4j.py writes them")
            self.nodes_read[key] += 1
            buf = self.node_buffers.setdefault(key, [])
            buf.append(row)
            if len(buf) >= self.batch:
                self._flush_nodes(key)
        elif kind == "rel":
            if not self.rel_phase:
                self._start_rel_phase()
            self.rels_read[key] += 1
            buf = self.rel_buffers.setdefault(key, [])
            buf.append(row)
            if len(buf) >= self.batch:
                self._flush_rels(key)

    def finish(self) -> int:
        """Flush what is left, strip the import label and property, drop the import index. Returns nodes stripped."""
        if not self.rel_phase:
            self._start_rel_phase()
        for rel_type in list(self.rel_buffers):
            self._flush_rels(rel_type)
        stripped = 0
        with self.driver.session(database=self.database) as session:
            while True:
                n = session.execute_write(lambda tx: tx.run(STRIP_IMPORT_REF, lim=self.batch).single()["n"])
                if not n:
                    break
                stripped += n
            session.run(DROP_IMPORT_INDEX).consume()
        return stripped


def expected_label_counts(nodes_read: Counter) -> dict[str, int]:
    """Node count per label once the import label is stripped (a node counts once for each of its labels)."""
    counts: Counter = Counter()
    for labels, n in nodes_read.items():
        for label in labels.split(":"):
            if label and label != IMPORT_LABEL:
                counts[label] += n
    return dict(sorted(counts.items()))


def read_graph_counts(driver, database: str) -> tuple[dict, dict]:
    with driver.session(database=database) as session:
        labels = {r["label"]: int(r["n"]) for r in session.run(LABEL_COUNTS)}
        rels = {r["type"]: int(r["n"]) for r in session.run(REL_TYPE_COUNTS)}
    return dict(sorted(labels.items())), dict(sorted(rels.items()))


def compare(name: str, expected: dict, actual: dict, checks: list) -> None:
    checks.append({"name": name, "expected": expected, "actual": actual, "pass": expected == actual})


def connection_from_env(allow_live: bool) -> tuple[str, tuple[str, str]]:
    host = os.environ.get("NEXTSEEK_NEO4J_HOST")
    password = os.environ.get("NEXTSEEK_NEO4J_PASSWORD")
    if not host or not password:
        raise Refusal("NEXTSEEK_NEO4J_HOST and NEXTSEEK_NEO4J_PASSWORD must be set (lane.sh sets both)")
    if host.split(":")[0].lower() in LIVE_NEO4J_HOSTS and not allow_live:
        raise Refusal(f"host {host!r} is the live stack's Neo4j; pass --i-mean-the-live-graph to load into it")
    return "neo4j://" + host, ("neo4j", password)


def default_stats_path(backup: str) -> str | None:
    candidate = os.path.join(os.path.dirname(os.path.abspath(backup)), "neo4j.export-stats.json")
    return candidate if os.path.exists(candidate) else None


def load(driver, database: str, backup: str, batch: int, stats_path: str | None) -> dict:
    with driver.session(database=database) as session:
        present = session.run(NODE_COUNT).single()["n"]
    if present:
        raise Refusal(f"the graph holds {present} nodes; the backup uses plain CREATE and needs an empty graph")

    started = time.monotonic()
    loader = Loader(driver, database, batch)
    statements = 0
    for line in iter_statements(backup):
        kind, key, row = classify(line)
        if kind != "skip":
            statements += 1
            loader.add(kind, key, row)
    stripped = loader.finish()
    seconds = round(time.monotonic() - started, 1)

    graph_labels, graph_rels = read_graph_counts(driver, database)
    checks: list = []
    file_labels = expected_label_counts(loader.nodes_read)
    file_rels = dict(sorted(loader.rels_read.items()))
    compare("nodes_per_label_equal_file", file_labels, graph_labels, checks)
    compare("relationships_per_type_equal_file", file_rels, graph_rels, checks)
    compare("nodes_created_equal_file", sum(loader.nodes_read.values()), loader.nodes_created, checks)
    compare("import_label_stripped", loader.nodes_created, stripped, checks)
    if stats_path:
        with open(stats_path, encoding="utf-8") as fh:
            stats = json.load(fh)
        compare("nodes_per_label_equal_export", dict(sorted(stats["exported_nodes_by_label"].items())),
                graph_labels, checks)
        compare("relationships_per_type_equal_export", dict(sorted(stats["exported_rels_by_type"].items())),
                graph_rels, checks)
    return {"backup": backup, "stats_file": stats_path, "batch": batch, "statements": statements,
            "nodes": loader.nodes_created, "relationships": loader.rels_created, "batches": loader.batches, "seconds": seconds,
            "nodes_by_label": graph_labels, "relationships_by_type": graph_rels,
            "checks": checks, "pass": all(c["pass"] for c in checks)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("backup", help="the neo4j.cypher.gz backup")
    parser.add_argument("--batch", type=int, default=5000, help="rows per UNWIND transaction (default 5000)")
    parser.add_argument("--stats", help="an export-stats JSON to check against (default: the one beside the backup)")
    parser.add_argument("--database", default="neo4j", help="the database to load into (default neo4j)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON on stdout")
    parser.add_argument("--i-mean-the-live-graph", action="store_true",
                        help="allow the live stack's Neo4j host (neo4j)")
    args = parser.parse_args(argv)
    if args.batch <= 0:
        parser.error("--batch must be positive")
    stats_path = args.stats or default_stats_path(args.backup)
    try:
        uri, auth = connection_from_env(args.i_mean_the_live_graph)
        with GraphDatabase.driver(uri, auth=auth) as driver:
            report = load(driver, args.database, args.backup, args.batch, stats_path)
    except Refusal as exc:
        print(f"load_graph_backup refused: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            print(f"{'PASS' if check['pass'] else 'FAIL'}  {check['name']}")
        print(f"{report['nodes']} nodes, {report['relationships']} relationships in {report['seconds']} s")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
