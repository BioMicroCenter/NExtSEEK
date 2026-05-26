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

from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
SEED_DIR = SCRIPT_DIR.parent
ENV_FILE = SCRIPT_DIR / "dump-source.env"


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
    if hasattr(val, "isoformat"):
        # Neo4j temporal types serialize as ISO 8601 strings; cypher's
        # datetime() coerces them back at import time.
        return f'datetime("{val.isoformat()}")'
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

    driver = GraphDatabase.driver(uri, auth=(user, password))
    lines: list[str] = []

    with driver.session(database=database) as session:
        print("Exporting nodes (tagging with :_ImportRef + _exportId for round-trip)...")
        # Defensive: exclude any source nodes that already carry our temp label
        # (would happen only if a prior bad import wasn't cleaned up).
        result = session.run("MATCH (n) WHERE NOT n:_ImportRef RETURN n, labels(n) as lbls, id(n) as nid")
        node_count = 0
        for record in result:
            node = record["n"]
            labels = ":".join(record["lbls"])
            nid = record["nid"]
            props = dict(node)
            props["_exportId"] = nid
            lines.append(f"CREATE (n{node_count}:{labels}:_ImportRef {_props_str(props)});")
            node_count += 1
        print(f"  {node_count} nodes")

        # Build a label-scoped index BEFORE the relationship MATCHes so each
        # WHERE _exportId = N lookup is O(1) instead of O(node_count). Without
        # the index, 600k relationship CREATEs become unusably slow.
        lines.append(
            "CREATE INDEX _import_ref_eid_idx IF NOT EXISTS FOR (n:_ImportRef) ON (n._exportId);"
        )

        print("Exporting relationships...")
        rel_count = 0
        result = session.run(
            "MATCH (a)-[r]->(b) RETURN id(a) as aid, id(b) as bid, type(r) as rtype, properties(r) as rprops"
        )
        for record in result:
            aid, bid = record["aid"], record["bid"]
            rtype = record["rtype"]
            rprops = record["rprops"]
            rel_props_suffix = (" " + _props_str(rprops)) if rprops else ""
            lines.append(
                f"MATCH (a:_ImportRef {{`_exportId`: {aid}}}) "
                f"MATCH (b:_ImportRef {{`_exportId`: {bid}}}) "
                f"CREATE (a)-[:{rtype}{rel_props_suffix}]->(b);"
            )
            rel_count += 1
        print(f"  {rel_count} relationships")

    driver.close()

    # Cleanup: remove the temporary label, the _exportId property, and the
    # index. Runs AFTER all relationships are wired so the index is still
    # available during the bulk MATCH phase.
    lines.append("MATCH (n:_ImportRef) REMOVE n:_ImportRef, n._exportId;")
    lines.append("DROP INDEX _import_ref_eid_idx IF EXISTS;")

    out = SEED_DIR / "neo4j.cypher.gz"
    with gzip.open(out, "wt") as f:
        f.write("\n".join(lines))
    print(f"wrote {out} ({len(lines)} statements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
