"""Stream the whole graph to stdout in startup/seed/regenerate/dump_neo4j.py's exact statement format.

Runs inside a box's nextseek container (its python and neo4j driver; a read-only session), sent by ../fetch.sh.
Differences from dump_neo4j.py: writes each line as it goes and reads credentials from the app container's env;
constraint and index DDL go to stderr as `DDL <statement>` lines. _temporal_fn/_escape/_props_str must stay
identical to dump_neo4j.py's (startup/tests/test_seed.py compares them) so startup/steps/seed.py reads both.
"""
import datetime
import os
import sys

import neo4j.time as neo4j_time
from neo4j import GraphDatabase


def _temporal_fn(val):
    if isinstance(val, (neo4j_time.Duration,)):
        return "duration"
    if isinstance(val, (neo4j_time.DateTime, datetime.datetime)):
        return "datetime" if val.tzinfo is not None else "localdatetime"
    if isinstance(val, (neo4j_time.Date, datetime.date)):
        return "date"
    if isinstance(val, (neo4j_time.Time, datetime.time)):
        return "time" if val.tzinfo is not None else "localtime"
    return None


def _escape(val):
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
        iso = val.iso_format() if fn == "duration" else val.isoformat()
        return f'{fn}("{iso}")'
    return '"' + str(val).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _props_str(props):
    if not props:
        return ""
    return "{" + ", ".join(f"`{k}`: {_escape(v)}" for k, v in props.items()) + "}"


def main():
    uri = "neo4j://" + os.environ["NEXTSEEK_NEO4J_HOST"]
    drv = GraphDatabase.driver(uri, auth=("neo4j", os.environ["NEXTSEEK_NEO4J_PASSWORD"]))
    out = sys.stdout
    with drv.session(database="neo4j", default_access_mode="READ") as s:
        before = s.run("MATCH (n) RETURN count(n) AS n").single()["n"], s.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
        print(f"before nodes={before[0]} rels={before[1]}", file=sys.stderr, flush=True)
        # The server ends any transaction over its time limit, so page by internal id: one short read
        # transaction per PAGE ids (NodeByIdSeek), nodes first, then each page's outgoing relationships.
        PAGE = 20000
        maxid = s.run("MATCH (n) RETURN max(id(n)) AS m").single()["m"] or 0
        print(f"max id {maxid}", file=sys.stderr, flush=True)
        n = 0
        for lo in range(0, maxid + 1, PAGE):
            for rec in s.run("UNWIND range($lo, $hi) AS i MATCH (n) WHERE id(n) = i AND NOT n:_ImportRef "
                             "RETURN n, labels(n) AS lbls, id(n) AS nid", lo=lo, hi=min(lo + PAGE - 1, maxid)):
                props = dict(rec["n"])
                props["_exportId"] = rec["nid"]
                out.write(f"CREATE (n{n}:{':'.join(rec['lbls'])}:_ImportRef {_props_str(props)});\n")
                n += 1
        print(f"nodes {n}", file=sys.stderr, flush=True)
        out.write("CREATE INDEX _import_ref_eid_idx IF NOT EXISTS FOR (n:_ImportRef) ON (n._exportId);\n")
        r = 0
        for lo in range(0, maxid + 1, PAGE):
            for rec in s.run("UNWIND range($lo, $hi) AS i MATCH (a)-[r]->(b) WHERE id(a) = i "
                             "RETURN id(a) AS aid, id(b) AS bid, type(r) AS rtype, properties(r) AS rprops",
                             lo=lo, hi=min(lo + PAGE - 1, maxid)):
                suffix = (" " + _props_str(rec["rprops"])) if rec["rprops"] else ""
                out.write(
                    f"MATCH (a:_ImportRef {{`_exportId`: {rec['aid']}}}) "
                    f"MATCH (b:_ImportRef {{`_exportId`: {rec['bid']}}}) "
                    f"CREATE (a)-[:{rec['rtype']}{suffix}]->(b);\n"
                )
                r += 1
        out.write("MATCH (n:_ImportRef) REMOVE n:_ImportRef, n._exportId;\n")
        out.write("DROP INDEX _import_ref_eid_idx IF EXISTS;")
        after = s.run("MATCH (n) RETURN count(n) AS n").single()["n"], s.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
        print(f"exported nodes={n} rels={r}; after nodes={after[0]} rels={after[1]}", file=sys.stderr, flush=True)
        for row in s.run("SHOW CONSTRAINTS YIELD name, createStatement RETURN name, createStatement ORDER BY name"):
            print("DDL", row["createStatement"], file=sys.stderr)
        for row in s.run("SHOW INDEXES YIELD name, type, owningConstraint, createStatement WHERE owningConstraint IS NULL AND type <> 'LOOKUP' RETURN name, createStatement ORDER BY name"):
            print("DDL", row["createStatement"], file=sys.stderr)
    drv.close()


if __name__ == "__main__":  # `python -c` runs as __main__; a test import does not connect
    main()
