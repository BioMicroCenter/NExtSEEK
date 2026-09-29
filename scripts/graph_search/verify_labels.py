#!/usr/bin/env python3
"""Verify the DERIVED_FROM label rule against the dev box's TCGA labels and the local production labels (plan task V1).

    scripts/graph_search/lane.sh python scripts/graph_search/verify_labels.py [--local-host HOST ...]
        [--dev-dump FILE] [--local-dump FILE] [--relabel-rows FILE] [--out-dir DIR] [--chunk N]

Read-only. It computes the labels of every DERIVED_FROM pair the merged MySQL declares with the one rule graph_sync
writes (`graph_sync/labels.py`, fed by `graph_sync/sources.py`) and compares them with two graph dumps in the
`dump_neo4j.py` format, each streamed one statement at a time with the lane's parser (`load_graph_backup.py`'s reader,
`startup/steps/seed.py`'s regexes and map parser):

(a) TCGA, against the dev box's dump: the edges whose two endpoints are both TCGA samples (ids 389,935 to 1,308,453
    by default), on the three singular fields only. The merge renumbered SEEK and internal assay ids, so the dev
    values go through `dmac.gs_remap` first: `assay_id` through kind `assay`, `internal_assay_id` through kind
    `internal_assay`. Keying internal assays by title instead (the local titles are unique) is reported beside it,
    and so are the 2026-09-15 relabel's rows when `--relabel-rows` names them (by default when the file is there).
    The plural lists are counted apart and are no part of the pass condition: the dev box's mix two id spaces.
(b) production, against the local graph's dump (ids unchanged): every edge between two production samples, every
    label property, each edge sorted by `labels.classify` and counted per class, per property and per kind.

Memory: the MySQL pass holds the uuid index (`samples.uuid` has no MySQL index, so a by-page lookup would scan the
table per query) and one page of samples; each declared pair is kept as one 64-bit key and one 64-bit digest of its
labels (`label_check.LabelIndex`). The uuid index is dropped before the dumps are read, and a dump pass keeps only its
export-id to sample-id map.

The protocol rule reads a `/sops/<id>` URL as a local SOP only on this instance's hosts
(`batch_upload/helpers.py::_local_url_hosts`), and the lane's hosts are not production's. `--local-host` adds a host
for this run (it extends `ALLOWED_HOSTS` inside this process, nothing else); the report counts the children, and the
pairs, whose protocol depends on an added host.

Nothing is written to any database or graph: both MySQL sessions are set `READ ONLY` before the first read, and no
Neo4j is opened. Output: `report.json` and `report.md` in `--out-dir` (default `$GS_RUN_DIR/labels`).

Exit status: 0 when (a) matches every edge (by default 1,213,093 of 1,213,093) on the singular fields; 1 when it does
not; 2 when the run cannot start (a dump missing, `gs_remap` absent, a database that is not the lane's MySQL).
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import re
import resource
import sys
import time
from array import array
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import django

django.setup()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402

from load_graph_backup import iter_statements  # noqa: E402  (this folder is on sys.path)
from nextseek_api.batch_upload.helpers import parse_protocol_value  # noqa: E402
from nextseek_api.graph_sync import label_check, labels, sources  # noqa: E402
from startup.steps.seed import _NODE_RE, _REL_RE, _cypher_map_to_dict  # noqa: E402

logging.getLogger().setLevel(logging.INFO)
logging.getLogger("seek").setLevel(logging.INFO)

TCGA_MIN, TCGA_MAX = 389_935, 1_308_453
EXPECTED_TCGA_EDGES = 1_213_093
EXPORT_MAP_LIMIT = 50_000_000   # export ids below this go in a flat array, any above it in a dict
PAGES_PER_LOG = 40
EDGES_PER_LOG = 500_000

# A node's `_exportId` and `id`, read without parsing the whole map. A string value that happens to contain the same
# text makes a second match, and the node is then parsed in full.
_EXPORT_ID_RE = re.compile(r"(?:\{|, )`_exportId`: (\d+)(?=[,}])")
_ID_RE = re.compile(r"(?:\{|, )`id`: (-?\d+)(?=[,}])")

MAPPING = ("dmac.gs_remap: assay_id through kind 'assay', internal_assay_id through kind 'internal_assay' "
           "(never across); internal_assay_title compared as stored")


class Refusal(RuntimeError):
    """The run cannot start; nothing was read."""


def max_rss_mib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def log(message: str) -> None:
    print(f"verify_labels: {message} (max rss {max_rss_mib()} MiB)", file=sys.stderr, flush=True)


def _text(value):
    return bytes(value).decode("utf-8") if isinstance(value, (bytes, bytearray)) else value


class Scope:
    """Which population a pair belongs to: `tcga` (both ends TCGA), `production` (neither) or `straddling`."""

    def __init__(self, low: int, high: int):
        self.low, self.high = low, high

    def is_tcga(self, sample_id: int) -> bool:
        return self.low <= sample_id <= self.high

    def of(self, child: int, parent: int) -> str:
        child_tcga, parent_tcga = self.is_tcga(child), self.is_tcga(parent)
        if child_tcga and parent_tcga:
            return "tcga"
        return "straddling" if child_tcga or parent_tcga else "production"

    def tcga(self, child: int, parent: int) -> bool:
        return self.of(child, parent) == "tcga"

    def production(self, child: int, parent: int) -> bool:
        return self.of(child, parent) == "production"


# --- MySQL (read only) -------------------------------------------------------------------------------------------

def read_only_sessions() -> list[str]:
    """Set each MySQL session this run reads through READ ONLY, so any write would fail at the server."""
    aliases = list(dict.fromkeys((settings.SEEK_DATABASE, settings.NEXTSEEK_DATABASE)))
    for alias in aliases:
        conn = connections[alias]
        if conn.vendor != "mysql":
            raise Refusal(f"database {alias!r} is {conn.vendor}, not MySQL: run this through lane.sh python")
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
    return aliases


def read_remaps() -> dict[str, dict[int, int]]:
    """`dmac.gs_remap` kinds `assay` and `internal_assay`: dev id to local id."""
    if not sources.table_exists(settings.NEXTSEEK_DATABASE, "gs_remap"):
        raise Refusal("dmac.gs_remap is absent: it exists only in the lane's merged MySQL")
    remaps: dict[str, dict[int, int]] = {"assay": {}, "internal_assay": {}}
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
        cursor.execute("SELECT kind, old_id, new_id FROM gs_remap WHERE kind IN (%s, %s)", list(remaps))
        for kind, old_id, new_id in cursor.fetchall():
            remaps[_text(kind)][int(old_id)] = int(new_id)
    return remaps


def internal_ids_by_title() -> tuple[dict[str, int], list[str]]:
    """Local internal assay title (byte-exact) to its id, for the titles held once; and the titles held twice."""
    by_title: dict[str, list[int]] = {}
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
        cursor.execute("SELECT id, internal_assay_title FROM internal_assays ORDER BY id")
        for ia_id, title in cursor.fetchall():
            title = _text(title)
            if title is not None:
                by_title.setdefault(title, []).append(int(ia_id))
    unique = {title: ids[0] for title, ids in by_title.items() if len(ids) == 1}
    return unique, sorted(title for title, ids in by_title.items() if len(ids) > 1)


def protocol_shape(value) -> str:
    """Which of the house rule's formats a child's `Protocol` value takes."""
    ref = parse_protocol_value(value)
    if ref.sop_id is not None:
        return "local_sop_url"
    if ref.title is not None:
        return "title"
    return "external_url" if ref.external_url is not None else "none"


def on_added_host(value, added_hosts: set[str]) -> bool:
    text = str(value or "").strip()
    if not added_hosts or "://" not in text:
        return False
    try:
        return (urlsplit(text).hostname or "").lower() in added_hosts
    except ValueError:
        return False


def build_index(scope: Scope, chunk: int, added_hosts: set[str]) -> tuple[label_check.LabelIndex, dict]:
    """Every declared pair of the merged MySQL with its computed labels, and what the pass counted."""
    started = time.monotonic()
    assay_map = sources.resolved_assay_map()
    sops = sources.sops_map()
    sop_index = labels.sop_title_index(sops)
    uuid_index = sources.uuid_to_ids()
    log(f"maps read: {len(assay_map)} assays, {len(sops)} SOPs, {len(uuid_index)} uuids")

    index = label_check.LabelIndex()
    pairs_by_scope: Counter = Counter()
    tcga_shared: Counter = Counter()
    children: dict[str, Counter] = {"tcga": Counter(), "production": Counter()}
    pairs_via_added_host: Counter = Counter()
    samples = pages = 0
    for page in sources.iter_samples(chunk):
        samples += len(page)
        pages += 1
        pairs = list(dict.fromkeys(sources.declared_lineage(page, uuid_index)))
        if pages % PAGES_PER_LOG == 0:
            log(f"MySQL pass: {samples} samples, {len(index)} pairs")
        if not pairs:
            continue
        child_ids = {child for child, _ in pairs}
        assays = sources.sample_assay_ids_for(child_ids | {parent for _, parent in pairs})
        protocol: dict[int, tuple] = {}
        via_added: set[int] = set()
        for row in page:
            if row["id"] not in child_ids:
                continue
            value = labels.protocol_value_of(row["json_metadata"])
            protocol[row["id"]] = labels.resolve_protocol(value, sops, sop_index)
            shape = protocol_shape(value)
            tally = children["tcga" if scope.is_tcga(row["id"]) else "production"]
            tally["children"] += 1
            tally[shape] += 1
            if protocol[row["id"]] != (None, None):
                tally["resolved"] += 1
            if shape == "local_sop_url" and on_added_host(value, added_hosts):
                tally["local_sop_url_on_added_host"] += 1
                via_added.add(row["id"])
        for child, parent in pairs:
            child_assays, parent_assays = assays.get(child, ()), assays.get(parent, ())
            index.add(child, parent, labels.edge_labels(child_assays, parent_assays, assay_map, protocol[child]))
            where = scope.of(child, parent)
            pairs_by_scope[where] += 1
            if where == "tcga":
                tcga_shared[len(set(child_assays) & set(parent_assays))] += 1
            if child in via_added:
                pairs_via_added_host[where] += 1
    del uuid_index
    gc.collect()
    index.freeze()
    log(f"MySQL pass done: {samples} samples, {len(index)} pairs, {index.distinct} distinct label maps")
    return index, {
        "samples": samples, "pairs": dict(sorted(pairs_by_scope.items())), "pairs_total": len(index),
        "repeated_pairs": index.duplicates, "distinct_label_maps": index.distinct,
        "tcga_shared_assays": {str(n): count for n, count in sorted(tcga_shared.items())},
        "children_protocol": {side: dict(sorted(tally.items())) for side, tally in children.items()},
        "pairs_with_protocol_via_added_host": dict(sorted(pairs_via_added_host.items())),
        "resolved_assay_map": len(assay_map), "sops": len(sops),
        "seconds": round(time.monotonic() - started, 1),
    }


# --- the dumps ---------------------------------------------------------------------------------------------------

def node_ids(map_text: str) -> tuple[int, int | None]:
    """A Sample node statement's `_exportId` and `id` (None when it has none)."""
    exports, ids = _EXPORT_ID_RE.findall(map_text), _ID_RE.findall(map_text)
    if len(exports) == 1 and len(ids) == 1:
        return int(exports[0]), int(ids[0])
    props = _cypher_map_to_dict(map_text)
    sample_id = props.get("id")
    has_id = isinstance(sample_id, (int, float)) and not isinstance(sample_id, bool)
    return int(props["_exportId"]), (int(sample_id) if has_id else None)


def parse_props(raw: str) -> dict:
    return _cypher_map_to_dict(raw.strip()) if raw else {}


class DumpEdges:
    """The DERIVED_FROM edges of one dump between two Sample nodes, as `(child id, parent id, raw property map)`.

    Every node statement precedes the relationship statements (the `dump_neo4j.py` order), so a sample's id is
    known before any edge names it. An unrecognised statement fails the run, as the lane's loader fails the load.
    """

    def __init__(self, path: Path):
        self.path = path
        self.counts: Counter = Counter()

    def __iter__(self):
        flat, wide = array("i"), {}

        def sample_of(export_id: int) -> int | None:
            if export_id < len(flat):
                value = flat[export_id]
                return value if value >= 0 else None
            return wide.get(export_id)

        for line in iter_statements(str(self.path)):
            statement = line.strip()
            if not statement:
                continue
            if statement.startswith("CREATE (n"):
                match = _NODE_RE.match(statement)
                if not match:
                    raise ValueError(f"unparseable node statement: {statement[:100]}")
                self.counts["nodes"] += 1
                if "Sample" not in match.group(1).split(":"):
                    continue
                export_id, sample_id = node_ids(match.group(2))
                if sample_id is None:
                    self.counts["sample_nodes_without_id"] += 1
                    continue
                self.counts["sample_nodes"] += 1
                if export_id < EXPORT_MAP_LIMIT:
                    if export_id >= len(flat):
                        flat.extend(array("i", [-1]) * (export_id + 1 - len(flat)))
                    flat[export_id] = sample_id
                else:
                    wide[export_id] = sample_id
            elif statement.startswith("MATCH (a:_ImportRef"):
                match = _REL_RE.match(statement)
                if not match:
                    raise ValueError(f"unparseable relationship statement: {statement[:100]}")
                self.counts["relationships"] += 1
                if match.group(3) != "DERIVED_FROM":
                    continue
                self.counts["derived_from"] += 1
                child, parent = sample_of(int(match.group(1))), sample_of(int(match.group(2)))
                if child is None or parent is None:
                    self.counts["derived_from_not_between_samples"] += 1
                    continue
                if self.counts["derived_from"] % EDGES_PER_LOG == 0:
                    log(f"{self.path.name}: {self.counts['derived_from']} DERIVED_FROM statements read")
                yield child, parent, match.group(4) or ""
            elif statement.startswith(("CREATE INDEX", "DROP INDEX", "MATCH (n:_ImportRef) REMOVE")):
                continue
            else:
                raise ValueError(f"unrecognized cypher statement: {statement[:100]}")

    def check_endpoint_ids(self, child: int, parent: int, props: dict) -> None:
        """Count an edge whose own `child_id`/`parent_id` properties name other samples than its endpoints."""
        if props.get("child_id", child) != child or props.get("parent_id", parent) != parent:
            self.counts["endpoint_id_properties_disagree"] += 1


def verify_tcga(index, scope, dev_dump: Path, remaps, ids_by_title, expected: int, cap: int) -> dict:
    """Check (a): the dev box's TCGA edges on the singular fields, through gs_remap and, beside it, by title."""
    started = time.monotonic()
    by_remap = label_check.SingularCheck(index, expected=expected, example_cap=cap)
    by_title = label_check.SingularCheck(index, expected=expected, example_cap=cap)
    edges = DumpEdges(dev_dump)
    edges_by_scope: Counter = Counter()
    for child, parent, raw in edges:
        where = scope.of(child, parent)
        edges_by_scope[where] += 1
        if where != "tcga":
            continue
        props = parse_props(raw)
        edges.check_endpoint_ids(child, parent, props)
        stored = label_check.stored_labels(props)
        remapped = label_check.remap_ids(stored, assay_ids=remaps["assay"], internal_ids=remaps["internal_assay"])
        by_remap.edge(child, parent, remapped, plural=label_check.plural_shape(stored))
        by_title.edge(child, parent, label_check.key_internal_by_title(remapped, ids_by_title))
    result = by_remap.close(in_scope=scope.tcga)
    title_result = by_title.close(in_scope=scope.tcga)
    log(f"(a) done: {result['matched']} of {result['total']} match")
    return {"result": result, "title_keyed": title_result,
            "dev_dump": {"path": str(dev_dump), **dict(sorted(edges.counts.items())),
                         "derived_from_by_scope": dict(sorted(edges_by_scope.items()))},
            "seconds": round(time.monotonic() - started, 1)}


def verify_relabel_rows(index, scope, rows_path: Path, expected: int, cap: int) -> dict:
    """The 2026-09-15 relabel's rows (dev labels already renumbered) on the singular fields, as a cross-check."""
    check = label_check.SingularCheck(index, expected=expected, example_cap=cap)
    with open(rows_path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                check.edge(row["c"], row["p"], {"assay_id": row["a"], "internal_assay_id": row["i"],
                                                "internal_assay_title": row["t"]})
    return {"path": str(rows_path), "result": check.close(in_scope=scope.tcga)}


def verify_production(index, scope, local_dump: Path, cap: int) -> dict:
    """Check (b): the local graph's production edges, every property, per `labels.classify` class."""
    started = time.monotonic()
    check = label_check.ClassCheck(index, example_cap=cap)
    edges = DumpEdges(local_dump)
    edges_by_scope: Counter = Counter()
    stored_state: Counter = Counter()
    plural: Counter = Counter()
    for child, parent, raw in edges:
        where = scope.of(child, parent)
        edges_by_scope[where] += 1
        if where != "production":
            continue
        props = parse_props(raw)
        edges.check_endpoint_ids(child, parent, props)
        stored = label_check.stored_labels(props)
        labelled = any(stored.get(key) is not None for key in labels.SINGULAR_ASSAY_KEYS)
        stored_state["labelled" if labelled else "unlabelled"] += 1
        if labelled:
            plural[label_check.plural_shape(stored)] += 1
        if "assay_title" in props:
            stored_state["legacy_assay_title"] += 1
        if any(stored.get(key) is not None for key in labels.PROTOCOL_KEYS):
            stored_state["with_protocol"] += 1
        check.edge(child, parent, stored)
    result = check.close(in_scope=scope.production)
    log(f"(b) done: {result['compared']} edges classified")
    return {"result": result, "stored": dict(sorted(stored_state.items())),
            "labelled_plural_shapes": dict(sorted(plural.items())),
            "local_dump": {"path": str(local_dump), **dict(sorted(edges.counts.items())),
                           "derived_from_by_scope": dict(sorted(edges_by_scope.items()))},
            "seconds": round(time.monotonic() - started, 1)}


# --- the report --------------------------------------------------------------------------------------------------

def _n(value) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def _table(header: tuple[str, ...], rows) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_n(cell) for cell in row) + " |" for row in rows]
    return lines + [""]


def render_md(report: dict) -> str:
    a, b, mysql = report["a"], report["b"], report["mysql"]
    res, title, rows = a["result"], a["title_keyed"], a.get("relabel_rows")
    br = b["result"]
    out = ["# DERIVED_FROM label verification (plan task V1)", "",
           f"Generated {report['generated_at']} in {report['seconds']} s. Read-only: both MySQL sessions READ ONLY, "
           "no Neo4j opened, nothing written to any table or graph. The rule is `graph_sync/labels.py` fed by "
           "`graph_sync/sources.py`, over the merged MySQL.", "",
           f"Declared pairs: {_n(mysql['pairs_total'])} "
           f"({', '.join(f'{k} {_n(v)}' for k, v in mysql['pairs'].items())}); "
           f"{_n(mysql['distinct_label_maps'])} distinct label maps.", "",
           "## (a) TCGA against the dev box: the three singular fields", "",
           f"**{'PASS' if res['passed'] else 'FAIL'}: {_n(res['matched'])} of {_n(res['total'])} edges match** on "
           f"`assay_id`, `internal_assay_id` and `internal_assay_title` (expected {_n(res['expected'])}).", "",
           f"Mapping used: {a['mapping']} ({_n(a['remap_rows']['assay'])} and "
           f"{_n(a['remap_rows']['internal_assay'])} rows).", ""]
    out += _table(("matched", "differing", "graph only", "rule only", "duplicates"),
                  [(res["matched"], res["differing"], res["graph_only"], res["rule_only"], res["duplicates"])])
    out += _table(("differing field", "edges"), res["differing_by_key"].items())
    out += ["Cross-checks (not part of the pass):", "",
            f"- internal assays keyed by title ({_n(a['titles_unique'])} unique local titles): "
            f"{_n(title['matched'])} of {_n(title['total'])} match.",
            (f"- the 2026-09-15 relabel rows: {_n(rows['result']['matched'])} of {_n(rows['result']['total'])} match."
             if rows else "- the 2026-09-15 relabel rows: not given."),
            f"- pairs by number of shared assays: {a['expectations']['tcga_shared_assays']}; exactly one on every "
            f"pair: {a['expectations']['every_pair_shares_exactly_one_assay']}.",
            f"- protocol labels: {_n(res['stored_protocol'])} stored on the dev box, {_n(res['computed_protocol'])} "
            "computed.", "",
            "Plural lists as the dev box stores them (reported apart, never part of the pass):", ""]
    out += _table(("internal_assay_ids holds", "edges"), res["plural_shapes"].items())
    out += ["## (b) production against the local graph: every property", "",
            f"{_n(br['compared'])} edges classified; stored: "
            f"{', '.join(f'{k} {_n(v)}' for k, v in b['stored'].items())}.", ""]
    out += _table(("class", "edges", "of them unlabelled when read"),
                  [(cls, br["classes"][cls], br["by_stored"]["unlabelled"].get(cls, 0)) for cls in labels.CLASSES])
    out += [f"`new` edges by what the rule would write: {br['new']}.",
            f"Labelled edges' plural lists: {b['labelled_plural_shapes']}.",
            f"Graph-only edges (in the graph, not declared by MySQL): {br['graph_only']}; "
            f"rule-only pairs (declared, not in the graph): {br['rule_only']}; duplicates: {_n(br['duplicates'])}.", "",
            "Per property (`absent`: nothing stored, the rule has a value; `absent_empty`: the rule has an empty "
            "list; `cleared`: the rule would remove it; `changed`: the rule would replace it):", ""]
    out += _table(("class", "property", "kind", "edges"),
                  [(cls, key, kind, n) for cls, per in br["per_property"].items()
                   for key, kinds in per.items() for kind, n in kinds.items()])
    prod = mysql["children_protocol"].get("production", {})
    out += [f"Protocol values of production children: {prod}.",
            f"Local hosts added for the protocol rule: {report['rule']['local_hosts_added'] or 'none'}; pairs whose "
            f"protocol depends on one: {mysql['pairs_with_protocol_via_added_host']}.", "",
            f"Examples, capped, are in `report.json` (`a.result.examples`, `b.result.examples`). Exit status "
            f"{report['exit_status']}.", ""]
    return "\n".join(out)


def parse_args(argv):
    run_dir = Path(os.environ.get("GS_RUN_DIR", "/gswork/runs"))
    seeds = run_dir.parent / "seeds"
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dev-dump", type=Path, default=seeds / "devbox-2026-09-14" / "neo4j.cypher.gz")
    parser.add_argument("--local-dump", type=Path, default=seeds / "local-2026-09-14" / "neo4j.cypher.gz")
    parser.add_argument("--relabel-rows", type=Path, default=run_dir / "relabel" / "relabel-rows.jsonl",
                        help="the 2026-09-15 relabel rows, a cross-check for (a); skipped when absent")
    parser.add_argument("--out-dir", type=Path, default=run_dir / "labels")
    parser.add_argument("--local-host", action="append", default=[],
                        help="a host whose /sops/<id> URLs the protocol rule reads as local (repeatable)")
    parser.add_argument("--chunk", type=int, default=5000, help="samples per keyset page (default 5000)")
    parser.add_argument("--tcga-min", type=int, default=TCGA_MIN)
    parser.add_argument("--tcga-max", type=int, default=TCGA_MAX)
    parser.add_argument("--expect-tcga-edges", type=int, default=EXPECTED_TCGA_EDGES)
    parser.add_argument("--examples", type=int, default=20, help="examples kept per kind (default 20)")
    args = parser.parse_args(argv)
    if args.chunk <= 0:
        parser.error("--chunk must be positive")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    added_hosts = sorted({host.strip().lower() for host in args.local_host if host.strip()})
    try:
        for path in (args.dev_dump, args.local_dump):
            if not path.is_file():
                raise Refusal(f"{path} does not exist")
        aliases = read_only_sessions()
        if added_hosts:
            settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, *added_hosts]
        remaps = read_remaps()
        ids_by_title, ambiguous_titles = internal_ids_by_title()
    except Refusal as exc:
        print(f"verify_labels refused: {exc}", file=sys.stderr)
        return 2

    scope = Scope(args.tcga_min, args.tcga_max)
    index, mysql = build_index(scope, args.chunk, set(added_hosts))
    connections.close_all()

    a = verify_tcga(index, scope, args.dev_dump, remaps, ids_by_title, args.expect_tcga_edges, args.examples)
    a["mapping"] = MAPPING
    a["remap_rows"] = {kind: len(table) for kind, table in remaps.items()}
    a["titles_unique"], a["titles_ambiguous"] = len(ids_by_title), ambiguous_titles
    shared = mysql["tcga_shared_assays"]
    a["expectations"] = {
        "tcga_shared_assays": shared,
        "every_pair_shares_exactly_one_assay": set(shared) == {"1"},
        "no_protocol_label_either_side": a["result"]["stored_protocol"] == 0 == a["result"]["computed_protocol"],
    }
    if args.relabel_rows and args.relabel_rows.is_file():
        a["relabel_rows"] = verify_relabel_rows(index, scope, args.relabel_rows, args.expect_tcga_edges,
                                                args.examples)
    b = verify_production(index, scope, args.local_dump, args.examples)

    passed = a["result"]["passed"]
    report = {
        "task": "V1", "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": round(time.monotonic() - started, 1), "max_rss_mib": max_rss_mib(),
        "exit_status": 0 if passed else 1, "a_passed": passed,
        "inputs": {"mysql_aliases": aliases, "dev_dump": str(args.dev_dump), "local_dump": str(args.local_dump),
                   "tcga_ids": [args.tcga_min, args.tcga_max]},
        "rule": {"local_hosts_added": added_hosts, "allowed_hosts": list(settings.ALLOWED_HOSTS)},
        "mysql": mysql, "a": a, "b": b,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    (args.out_dir / "report.md").write_text(render_md(report), encoding="utf-8")
    res = a["result"]
    print(f"(a) {'PASS' if passed else 'FAIL'}: {res['matched']} of {res['total']} TCGA edges match on the singular "
          f"fields (expected {res['expected']}); (b) {b['result']['classes']}; report in {args.out_dir}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
