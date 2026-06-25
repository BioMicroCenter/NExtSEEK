"""Seed loader for MySQL + Neo4j gzipped dumps."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from startup.lib.docker_ops import compose_exec, compose_port, DockerOpsError

SEED_FILES: dict[str, str] = {
    "dmac": "startup/seed/dmac.sql.gz",
    "seek_production": "startup/seed/seek_production.sql.gz",
    "neo4j": "startup/seed/neo4j.cypher.gz",
}


def seed_files_present(repo_root: Path) -> list[str]:
    """Return a list of basenames missing from startup/seed/. Empty list = all present."""
    missing: list[str] = []
    for key, rel_path in SEED_FILES.items():
        full = repo_root / rel_path
        if not full.exists():
            missing.append(full.name)
    return missing


def mysql_db_is_populated(database: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if the named MySQL database already has tables."""
    try:
        out = compose_exec(
            service="db",
            command=[
                "mysql",
                "-uroot",
                f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
                "-N",
                "-e",
                f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{database}';",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    try:
        return int(out.strip().splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def neo4j_is_populated(neo4j_password: str, repo_root: Path, env: dict[str, str]) -> bool:
    """Return True if Neo4j has any nodes."""
    try:
        out = compose_exec(
            service="neo4j",
            command=[
                "cypher-shell",
                "-u",
                "neo4j",
                "-p",
                neo4j_password,
                "--format",
                "plain",
                "MATCH (n) RETURN count(n);",
            ],
            project_dir=repo_root,
            env=env,
        )
    except DockerOpsError:
        return False
    for line in out.strip().splitlines():
        token = line.strip()
        if token.isdigit():
            return int(token) > 0
    return False


def load_mysql_dump(
    gz_path: Path, database: str, repo_root: Path, env: dict[str, str]
) -> None:
    """Stream gz_path through gunzip | mysql into the named DB."""
    decompressed = gzip.decompress(gz_path.read_bytes())
    compose_exec(
        service="db",
        command=[
            "mysql",
            "-uroot",
            f"-p{env.get('MYSQL_ROOT_PASSWORD', 'seek_root')}",
            database,
        ],
        project_dir=repo_root,
        env=env,
        stdin=decompressed,
    )


# --- Neo4j cypher-dump parsing (pure; unit-testable without a DB) ---------------
#
# dump_neo4j.py emits the export as ~674k single statements: one
# ``CREATE (nN:Labels:_ImportRef {props});`` per node, then a label-scoped index,
# then one ``MATCH (a:_ImportRef {_exportId: N}) MATCH (b...) CREATE (a)-[:T props]->(b);``
# per relationship, then a cleanup pass. Replaying that statement-by-statement
# through cypher-shell is one transaction (or one parse) per statement and takes
# 30-60 min. We instead parse those statements into rows grouped by node-label-set
# and relationship-type and load each group with a parameterized ``UNWIND`` in
# batches (set-based, ~140 server round-trips total) -> seconds. Same resulting
# graph; the temporary :_ImportRef label + _exportId property are stripped after.

_NODE_RE = re.compile(r"^CREATE \(n\d+((?::[A-Za-z_][A-Za-z0-9_]*)+) (\{.*\})\);?$")
_REL_RE = re.compile(
    r"^MATCH \(a:_ImportRef \{`_exportId`: (\d+)\}\) "
    r"MATCH \(b:_ImportRef \{`_exportId`: (\d+)\}\) "
    r"CREATE \(a\)-\[:([A-Za-z_][A-Za-z0-9_]*)( \{.*\})?\]->\(b\);?$"
)


def _cypher_map_to_dict(s: str) -> dict:
    """Convert a Cypher map literal (incl. surrounding ``{}``) to a dict.

    Walks the string so backtick-quoted keys become JSON keys while backticks
    inside double-quoted string values are left untouched. dump_neo4j.py's
    ``_escape`` emits JSON-compatible values; ``strict=False`` tolerates literal
    control characters (e.g. tabs) inside strings that it does not escape.
    """
    out: list[str] = []
    i = 0
    in_str = False
    while i < len(s):
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "`":
            j = s.index("`", i + 1)
            out.append(json.dumps(s[i + 1:j]))  # backtick-quoted key -> JSON key
            i = j + 1
        else:
            out.append(c)
            i += 1
    return json.loads("".join(out), strict=False)


def parse_neo4j_cypher_dump(text: str) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Parse a neo4j.cypher export into (nodes_by_labelset, rels_by_type).

    nodes_by_labelset: ``":Label1:Label2"`` -> list of property dicts.
    rels_by_type: ``"REL_TYPE"`` -> list of ``{"a": exportId, "b": exportId, "props": {...}}``.
    Index/cleanup scaffolding lines are skipped (the loader re-issues them).
    Raises ValueError on any statement it cannot classify (fail loud, never
    silently drop a node/edge).
    """
    nodes_by_labels: dict[str, list[dict]] = {}
    rels_by_type: dict[str, list[dict]] = {}
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("CREATE (n"):
            m = _NODE_RE.match(s)
            if not m:
                raise ValueError(f"unparseable node statement: {s[:100]}")
            nodes_by_labels.setdefault(m.group(1), []).append(_cypher_map_to_dict(m.group(2)))
        elif s.startswith("MATCH (a:_ImportRef"):
            m = _REL_RE.match(s)
            if not m:
                raise ValueError(f"unparseable relationship statement: {s[:100]}")
            props = _cypher_map_to_dict(m.group(4).strip()) if m.group(4) else {}
            rels_by_type.setdefault(m.group(3), []).append(
                {"a": int(m.group(1)), "b": int(m.group(2)), "props": props}
            )
        elif s.startswith(("CREATE INDEX", "DROP INDEX")) or s.startswith(
            "MATCH (n:_ImportRef) REMOVE"
        ):
            continue  # import scaffolding — re-issued programmatically by the loader
        else:
            raise ValueError(f"unrecognized cypher statement: {s[:100]}")
    return nodes_by_labels, rels_by_type


_IMPORT_INDEX = "_import_ref_eid_idx"


def load_neo4j_dump(
    gz_path: Path,
    neo4j_password: str,
    repo_root: Path,
    env: dict[str, str],
    batch_size: int = 5000,
) -> None:
    """Load the Neo4j seed dump via parameterized UNWIND batches (fast).

    Precondition (enforced by the caller via ``neo4j_is_populated``): the target
    graph is empty. The dump uses plain CREATE, so this is not idempotent on a
    populated DB. Connects to the host-published bolt port (discovered at runtime
    because the port is dynamically allocated).
    """
    from neo4j import GraphDatabase  # lazy import: only needed when actually seeding

    text = gzip.decompress(gz_path.read_bytes()).decode()
    nodes_by_labels, rels_by_type = parse_neo4j_cypher_dump(text)
    bolt_port = compose_port("neo4j", 7687, repo_root, env)
    driver = GraphDatabase.driver(
        f"bolt://localhost:{bolt_port}", auth=("neo4j", neo4j_password)
    )
    try:
        with driver.session() as session:

            def run_batches(rows: list[dict], query: str) -> None:
                for start in range(0, len(rows), batch_size):
                    chunk = rows[start:start + batch_size]
                    session.execute_write(
                        lambda tx, c=chunk, q=query: tx.run(q, rows=c).consume()
                    )

            # 1) nodes (each group has a fixed label string; props as a map param)
            for labels, rows in nodes_by_labels.items():
                run_batches(rows, f"UNWIND $rows AS r CREATE (n{labels}) SET n = r")

            # 2) index on the temp _exportId so the per-rel lookups are O(1)
            session.run(
                f"CREATE INDEX {_IMPORT_INDEX} IF NOT EXISTS "
                "FOR (n:_ImportRef) ON (n._exportId)"
            ).consume()
            session.run("CALL db.awaitIndexes(600)").consume()

            # 3) relationships (rel type can't be a parameter -> one query per type)
            for rtype, rows in rels_by_type.items():
                run_batches(
                    rows,
                    "UNWIND $rows AS r "
                    "MATCH (a:_ImportRef {_exportId: r.a}) "
                    "MATCH (b:_ImportRef {_exportId: r.b}) "
                    f"CREATE (a)-[rel:`{rtype}`]->(b) SET rel = r.props",
                )

            # 4) strip the temporary import scaffolding (batched to bound the tx)
            while session.run(
                "MATCH (n:_ImportRef) WITH n LIMIT $lim "
                "REMOVE n:_ImportRef, n._exportId RETURN count(n) AS c",
                lim=batch_size,
            ).single()["c"]:
                pass
            session.run(f"DROP INDEX {_IMPORT_INDEX} IF EXISTS").consume()
    finally:
        driver.close()
