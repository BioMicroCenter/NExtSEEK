"""Dump the Neo4j graph to a portable cypher file. Maintainer-only.

Requires dump-source.env in this directory with NEO4J_URI / NEO4J_USER /
NEO4J_PASSWORD / NEO4J_DATABASE. Writes neo4j.cypher.gz to ../.

Round-trip strategy: Neo4j's internal id() values are NOT preserved across
export → import — when the target DB executes CREATE statements it assigns
fresh sequential internal ids (0, 1, 2, ...). The source's id() values
(which may have gaps from deletions, e.g., 0..50000 then 52740..52790) only
overlap with target ids by coincidence. Any relationship MATCH WHERE id(x)=N
fails silently for N outside the target's assigned range, and CREATE on an
empty MATCH is a no-op with no error.

This script avoids that trap by tagging every node with two pieces of
import-only metadata:

  1. A temporary `:_ImportRef` label (so we can build a label-scoped index)
  2. An `_exportId` property holding the source's id() value

Relationships then MATCH on `(:_ImportRef {_exportId: N})` instead of the
meaningless `WHERE id(n) = N`. A final cleanup pass strips the label, the
property, and the index after everything is wired up — leaving the target
DB indistinguishable from the source (modulo Neo4j-internal ids).
"""
from __future__ import annotations

import gzip
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "error: python-dotenv not installed. "
        "Run: uv sync --project startup --group maintainer",
        file=sys.stderr,
    )
    sys.exit(2)

import datetime

import neo4j.time as neo4j_time
from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
SEED_DIR = SCRIPT_DIR.parent
ENV_FILE = SCRIPT_DIR / "dump-source.env"
PAGE = 20000  # internal ids per read transaction


def _temporal_fn(val: object) -> str | None:
    """The Cypher function for a temporal value, or None. Every Sample has had a DateTime `synced_at` since
    graph schema 1.1, and Date attributes are common; writing them all as datetime() turned dates into datetimes."""
    if isinstance(val, (neo4j_time.Duration,)):
        return "duration"
    if isinstance(val, (neo4j_time.DateTime, datetime.datetime)):
        return "datetime" if val.tzinfo is not None else "localdatetime"
    if isinstance(val, (neo4j_time.Date, datetime.date)):
        return "date"
    if isinstance(val, (neo4j_time.Time, datetime.time)):
        return "time" if val.tzinfo is not None else "localtime"
    return None


def _escape(val: object) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return (
            '"'
            + val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
            + '"'
        )
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return "[" + ", ".join(_escape(v) for v in val) + "]"
    fn = _temporal_fn(val)
    if fn:
        # A temporal becomes the Cypher call that rebuilds the same type from its ISO 8601 text;
        # startup/steps/seed.py reads exactly these six calls back (Temporal, driver_value).
        iso = val.iso_format() if fn == "duration" else val.isoformat()
        return f'{fn}("{iso}")'
    # Last-resort fallback: quote whatever str() produces. Loses type fidelity
    # but won't corrupt the file with unescaped specials.
    return '"' + str(val).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _props_str(props: dict) -> str:
    if not props:
        return ""
    return "{" + ", ".join(f"`{k}`: {_escape(v)}" for k, v in props.items()) + "}"


def main() -> int:
    if not ENV_FILE.exists():
        print(
            f"error: {ENV_FILE} missing.\n"
            "This command is maintainer-only — it requires dev DB credentials.\n"
            "Copy dump-source.env.example to dump-source.env and fill in real values.",
            file=sys.stderr,
        )
        return 2

    load_dotenv(ENV_FILE)
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USER"]
    password = os.environ["NEO4J_PASSWORD"]
    database = os.environ["NEO4J_DATABASE"]

    driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")

    # Streamed into a temp file beside the seed and moved into place only on success, so a failed run
    # never truncates the committed seed and a ~1M-node graph never sits in memory as one list.
    out = SEED_DIR / "neo4j.cypher.gz"
    tmp = out.with_name(f"{out.name}.tmp.{os.getpid()}")
    statements = 0
    try:
        with gzip.open(tmp, "wt") as f, driver.session(database=database, default_access_mode="READ") as session:

            def emit(statement: str) -> None:
                nonlocal statements
                f.write(("\n" if statements else "") + statement)
                statements += 1

            # Read in pages of internal ids, one short read transaction each: dev and production end any
            # transaction over the server's time limit (db.transaction.timeout), which one whole-graph
            # MATCH outlasts. Each page is a NodeByIdSeek, so the total cost stays one pass.
            max_id = session.run("MATCH (n) RETURN max(id(n)) AS m").single()["m"]
            pages = range(0, (max_id if max_id is not None else -1) + 1, PAGE)

            print("Exporting nodes (tagging with :_ImportRef + _exportId for round-trip)...")
            node_count = 0
            for lo in pages:
                # Defensive: exclude any source nodes that already carry our temp label
                # (would happen only if a prior bad import wasn't cleaned up).
                for record in session.run(
                    "UNWIND range($lo, $hi) AS i MATCH (n) WHERE id(n) = i AND NOT n:_ImportRef "
                    "RETURN n, labels(n) as lbls, id(n) as nid", lo=lo, hi=min(lo + PAGE - 1, max_id),
                ):
                    labels = ":".join(record["lbls"])
                    props = dict(record["n"])
                    props["_exportId"] = record["nid"]
                    emit(f"CREATE (n{node_count}:{labels}:_ImportRef {_props_str(props)});")
                    node_count += 1
            print(f"  {node_count} nodes")

            # Build a label-scoped index BEFORE the relationship MATCHes so each
            # WHERE _exportId = N lookup is O(1) instead of O(node_count). Without
            # the index, 600k relationship CREATEs become unusably slow.
            emit("CREATE INDEX _import_ref_eid_idx IF NOT EXISTS FOR (n:_ImportRef) ON (n._exportId);")

            print("Exporting relationships...")
            rel_count = 0
            for lo in pages:
                for record in session.run(
                    "UNWIND range($lo, $hi) AS i MATCH (a)-[r]->(b) WHERE id(a) = i "
                    "RETURN id(a) as aid, id(b) as bid, type(r) as rtype, properties(r) as rprops",
                    lo=lo, hi=min(lo + PAGE - 1, max_id),
                ):
                    rprops = record["rprops"]
                    rel_props_suffix = (" " + _props_str(rprops)) if rprops else ""
                    emit(
                        f"MATCH (a:_ImportRef {{`_exportId`: {record['aid']}}}) "
                        f"MATCH (b:_ImportRef {{`_exportId`: {record['bid']}}}) "
                        f"CREATE (a)-[:{record['rtype']}{rel_props_suffix}]->(b);"
                    )
                    rel_count += 1
            print(f"  {rel_count} relationships")

            # Cleanup: remove the temporary label, the _exportId property, and the
            # index. Runs AFTER all relationships are wired so the index is still
            # available during the bulk MATCH phase.
            emit("MATCH (n:_ImportRef) REMOVE n:_ImportRef, n._exportId;")
            emit("DROP INDEX _import_ref_eid_idx IF EXISTS;")
        tmp.replace(out)
    finally:
        driver.close()
        tmp.unlink(missing_ok=True)
    print(f"wrote {out} ({statements} statements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
