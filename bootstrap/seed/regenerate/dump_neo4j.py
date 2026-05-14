"""Dump the Neo4j graph to a portable cypher file. Maintainer-only.

Requires dump-source.env in this directory with NEO4J_URI / NEO4J_USER /
NEO4J_PASSWORD / NEO4J_DATABASE. Writes neo4j.cypher.gz to ../.
"""
from __future__ import annotations

import gzip
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("error: python-dotenv not installed. Run: uv sync --project bootstrap --group maintainer", file=sys.stderr)
    sys.exit(2)

from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
SEED_DIR = SCRIPT_DIR.parent
ENV_FILE = SCRIPT_DIR / "dump-source.env"


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

    def escape(val: object) -> str:
        if isinstance(val, str):
            return '"' + val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
        if isinstance(val, list):
            return "[" + ", ".join(escape(v) for v in val) + "]"
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)

    def props_str(props: dict) -> str:
        if not props:
            return ""
        return "{" + ", ".join(f"`{k}`: {escape(v)}" for k, v in props.items()) + "}"

    with driver.session(database=database) as session:
        print("Exporting nodes...")
        result = session.run("MATCH (n) RETURN n, labels(n) as lbls, elementId(n) as nid")
        for record in result:
            node = record["n"]
            labels = ":".join(record["lbls"])
            nid = record["nid"]
            lines.append(f"CREATE (n{nid}:{labels} {props_str(dict(node))});")
        print(f"  {len(lines)} nodes")

        print("Exporting relationships...")
        rel_count = 0
        result = session.run(
            "MATCH (a)-[r]->(b) RETURN elementId(a) as aid, elementId(b) as bid, type(r) as rtype, properties(r) as rprops"
        )
        for record in result:
            aid, bid = record["aid"], record["bid"]
            rtype = record["rtype"]
            rprops = record["rprops"]
            p = (" " + props_str(rprops)) if rprops else ""
            lines.append(
                f"MATCH (a) WHERE elementId(a) = '{aid}' MATCH (b) WHERE elementId(b) = '{bid}' CREATE (a)-[:{rtype}{p}]->(b);"
            )
            rel_count += 1
        print(f"  {rel_count} relationships")

    driver.close()

    out = SEED_DIR / "neo4j.cypher.gz"
    with gzip.open(out, "wt") as f:
        f.write("\n".join(lines))
    print(f"wrote {out} ({len(lines)} statements)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
