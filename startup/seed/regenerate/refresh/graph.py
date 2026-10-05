"""Graph side of the seed refresh. Run with the startup venv (neo4j driver):
  uv run --project <wt>/startup --group maintainer python graph.py <cmd> ...

  load FILE.gz          stream a dump_neo4j-format file into the (empty) target, UNWIND batches, like seed.py
  snap OUT.json         counts per label set and relationship type, constraints, indexes
  kill OUT.json         DETACH DELETE the kill sets read from the MySQL work schema (batched transactions)
  parity OUT.json       graph ids vs MySQL rows (Sample, Study, Investigation, Project, Person)
  diff A.json B.json    before/after/deleted

Target bolt port from env PORT (default 17687), password seedlib.NEO4J_PW. MySQL container from env C.
"""
import gzip
import json
import os
import re
import sys
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # the repo root, for startup.steps.seed
from startup.steps.seed import _NODE_RE, _REL_RE, Temporal, _cypher_map_to_dict, driver_value  # noqa: E402

import seedlib as s  # noqa: E402

_OFFSET = re.compile(r"(Z|[+-]\d\d:\d\d(:\d\d)?)$")


def legacy(v):
    """The raw dev export was written by the OLD escaper, which wrote every temporal as datetime(iso).
    Recover the source type from the ISO text: no 'T' = a Date, no offset = a LocalDateTime."""
    if isinstance(v, Temporal) and v.fn == "datetime":
        if "T" not in v.iso:
            v = Temporal("date", v.iso)
        elif not _OFFSET.search(v.iso):
            v = Temporal("localdatetime", v.iso)
    if isinstance(v, list):
        return [legacy(x) for x in v]
    return driver_value(v)


def props(raw):
    return {k: legacy(v) for k, v in _cypher_map_to_dict(raw).items()}

PORT = os.environ.get("PORT", "17687")
B = 5000


def driver():
    return GraphDatabase.driver(f"bolt://localhost:{PORT}", auth=("neo4j", s.NEO4J_PW), notifications_min_severity="OFF")


def load(path):
    drv = driver()
    nodes = rels = 0
    with drv.session() as ses:
        def flush_nodes(labels, rows):
            ses.execute_write(lambda tx: tx.run(f"UNWIND $rows AS r CREATE (n{labels}) SET n = r", rows=rows).consume())

        def flush_rels(rtype, rows):
            ses.execute_write(lambda tx: tx.run(
                "UNWIND $rows AS r MATCH (a:_ImportRef {_exportId: r.a}) MATCH (b:_ImportRef {_exportId: r.b}) "
                f"CREATE (a)-[rel:`{rtype}`]->(b) SET rel = r.props", rows=rows).consume())

        nbuf, rbuf, indexed = {}, {}, False
        with gzip.open(path, "rt") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("CREATE (n"):
                    m = _NODE_RE.match(line)
                    if not m:
                        raise ValueError(f"unparseable node: {line[:120]}")
                    buf = nbuf.setdefault(m.group(1), [])
                    buf.append(props(m.group(2)))
                    nodes += 1
                    if len(buf) >= B:
                        flush_nodes(m.group(1), buf); nbuf[m.group(1)] = []
                elif line.startswith("MATCH (a:_ImportRef"):
                    if not indexed:
                        for lb, buf in nbuf.items():
                            if buf:
                                flush_nodes(lb, buf)
                        nbuf = {}
                        ses.run("CREATE INDEX _import_ref_eid_idx IF NOT EXISTS FOR (n:_ImportRef) ON (n._exportId)").consume()
                        ses.run("CALL db.awaitIndexes(600)").consume()
                        indexed = True
                        print(f"nodes loaded: {nodes}", flush=True)
                    m = _REL_RE.match(line)
                    if not m:
                        raise ValueError(f"unparseable rel: {line[:120]}")
                    buf = rbuf.setdefault(m.group(3), [])
                    buf.append({"a": int(m.group(1)), "b": int(m.group(2)),
                                "props": props(m.group(4).strip()) if m.group(4) else {}})
                    rels += 1
                    if len(buf) >= B:
                        flush_rels(m.group(3), buf); rbuf[m.group(3)] = []
                    if rels % 500000 == 0:
                        print(f"rels loaded: {rels}", flush=True)
                elif line.startswith(("CREATE INDEX", "DROP INDEX", "MATCH (n:_ImportRef) REMOVE")):
                    continue
                else:
                    raise ValueError(f"unrecognized: {line[:120]}")
        for lb, buf in nbuf.items():
            if buf:
                flush_nodes(lb, buf)
        for rt, buf in rbuf.items():
            if buf:
                flush_rels(rt, buf)
        while ses.run("MATCH (n:_ImportRef) WITH n LIMIT $l REMOVE n:_ImportRef, n._exportId RETURN count(n) AS c", l=B).single()["c"]:
            pass
        ses.run("DROP INDEX _import_ref_eid_idx IF EXISTS").consume()
    drv.close()
    print(f"loaded nodes={nodes} rels={rels}")


def ddl(path):
    """Replay dev's constraint and index DDL (createStatement lines captured by the export) on the throwaway."""
    drv = driver()
    with drv.session() as ses:
        for line in open(path):
            if line.startswith("DDL "):
                ses.run(line[4:].strip()).consume()
        ses.run("CALL db.awaitIndexes(1800)").consume()
    drv.close()


def snap(out):
    drv = driver()
    with drv.session() as ses:
        res = {
            "labels": {"|".join(sorted(r["l"])): r["c"] for r in ses.run("MATCH (n) RETURN labels(n) AS l, count(*) AS c")},
            "rels": {r["t"]: r["c"] for r in ses.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")},
            "constraints": sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in ses.run(
                "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties")),
            "indexes": sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in ses.run(
                "SHOW INDEXES YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties")),
        }
        res["nodes"] = sum(res["labels"].values())
        res["relationships"] = sum(res["rels"].values())
    drv.close()
    json.dump(res, open(out, "w"), indent=1, sort_keys=True)
    print(f"{out}: nodes={res['nodes']} rels={res['relationships']} constraints={len(res['constraints'])} indexes={len(res['indexes'])}")


def ids(table):
    exists = s.scalar(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{s.WORK}' AND table_name='k__s__{table}'")
    return [int(r[0]) for r in s.rows(f"SELECT id FROM {s.WORK}.k__s__{table}")] if exists == "1" else []


def kill(out):
    k = {t: ids(t) for t in ("samples", "studies", "investigations", "projects", "people", "sample_types")}
    drv = driver()
    log = {}
    with drv.session() as ses:
        def batched(label, idset, name):
            """One scan of the label to Python, then DETACH DELETE by element id in batches of 2000.
            label None: idset already holds element ids."""
            if label is None:
                eids = sorted(idset)
            else:
                eids = [r["e"] for r in ses.run(f"MATCH (n:{label}) RETURN elementId(n) AS e, n.id AS id") if r["id"] in idset]
            n_nodes = n_rels = 0
            for i in range(0, len(eids), 2000):
                chunk = eids[i:i + 2000]
                r = ses.execute_write(lambda tx: tx.run(
                    "UNWIND $e AS e MATCH (n) WHERE elementId(n) = e "
                    "OPTIONAL MATCH (n)-[r]-() WITH n, count(r) AS deg DETACH DELETE n RETURN count(n) AS n, sum(deg) AS r",
                    e=chunk).single())
                n_nodes += r["n"]; n_rels += r["r"] or 0
            log[name] = {"nodes": n_nodes, "rels_touching": n_rels}
            print(f"{name}: nodes={n_nodes} rels={n_rels}", flush=True)

        # graph-side extras, computed BEFORE anything is deleted so the study links are still there
        kst = set(k["studies"]); kinv = set(k["investigations"]); kproj = set(k["projects"]); ksmp = set(k["samples"])
        dead_studies = [r["e"] for r in ses.run(
            "MATCH (st:Study) OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(i:Investigation) "
            "WITH st, collect(i.id) AS invs WHERE st.seek_study_id IN $kst OR (st.seek_study_id IS NULL AND st.id IN $kst AND size(invs) = 0) "
            "OR (size(invs) > 0 AND all(x IN invs WHERE x IN $kinv)) RETURN elementId(st) AS e",
            kst=list(kst), kinv=list(kinv))]
        extra = [r["id"] for r in ses.run(
            "MATCH (n)-[:IN_STUDY]->(st:Study) WHERE (n:Sample OR n:OrphanSample) WITH n, collect(elementId(st)) AS sts "
            "WHERE all(x IN sts WHERE x IN $dead) AND NOT n.id IN $ksmp RETURN n.id AS id", dead=dead_studies, ksmp=list(ksmp))]
        extra += [r["id"] for r in ses.run(
            "MATCH (n:Sample) WHERE size(coalesce(n.project_ids, [])) > 0 AND all(p IN n.project_ids WHERE p IN $kproj) "
            "AND NOT n.id IN $ksmp RETURN n.id AS id", kproj=list(kproj), ksmp=list(ksmp))]
        log["graph_only_extra_samples"] = sorted(set(extra))
        print(f"graph-only extra samples (in killed studies/projects, not in the MySQL kill set): {len(set(extra))}", flush=True)
        batched("Sample", ksmp | set(extra), "Sample")
        batched("OrphanSample", ksmp | set(extra), "OrphanSample")
        batched(None, set(dead_studies), "Study")
        batched("Investigation", kinv, "Investigation")
        batched("Project", kproj, "Project")
        batched("Person", set(k["people"]), "Person")
        # a removed sample type: its Attribute nodes (keyed "<sample_type_id>:<title>") and the SampleType node
        kst_types = set(k["sample_types"])
        attrs = [r["e"] for r in ses.run("MATCH (a:Attribute) WHERE a.sample_type_id IN $t RETURN elementId(a) AS e",
                                         t=list(kst_types))]
        batched(None, set(attrs), "Attribute")
        batched("SampleType", kst_types, "SampleType")
    drv.close()
    json.dump(log, open(out, "w"), indent=1)


def parity(out):
    my = {
        "Sample": set(int(r[0]) for r in s.rows("SELECT id FROM seek_production.samples")),
        "Investigation": set(int(r[0]) for r in s.rows("SELECT id FROM seek_production.investigations")),
        "Project": set(int(r[0]) for r in s.rows("SELECT id FROM seek_production.projects")),
        "Person": set(int(r[0]) for r in s.rows("SELECT id FROM seek_production.people")),
        "Study": set(int(r[0]) for r in s.rows("SELECT id FROM seek_production.studies")),
    }
    drv = driver()
    res = {}
    with drv.session() as ses:
        for label in ("Sample", "Investigation", "Project", "Person"):
            g = [r["id"] for r in ses.run(f"MATCH (n:{label}) RETURN n.id AS id")]
            missing = sorted(x for x in g if x not in my[label])
            res[label] = {"graph": len(g), "not_in_mysql": len(missing), "examples": missing[:20]}
        st = [r["sid"] for r in ses.run("MATCH (n:Study) WHERE n.seek_study_id IS NOT NULL RETURN n.seek_study_id AS sid")]
        res["Study.seek_study_id"] = {"graph": len(st), "not_in_mysql": len([x for x in st if x not in my["Study"]])}
        res["Study.no_investigation"] = ses.run("MATCH (n:Study) WHERE NOT (n)-[:IN_INVESTIGATION]->() RETURN count(n) AS c").single()["c"]
        res["OrphanSample"] = ses.run("MATCH (n:OrphanSample) RETURN count(n) AS c").single()["c"]
        res["Sample_without_OF_TYPE"] = ses.run("MATCH (n:Sample) WHERE NOT (n)-[:OF_TYPE]->() RETURN count(n) AS c").single()["c"]
    drv.close()
    json.dump(res, open(out, "w"), indent=1)
    print(json.dumps(res, indent=1)[:3000])


def diff(a, b):
    A, Bj = json.load(open(a)), json.load(open(b))
    print("constraints identical:", A["constraints"] == Bj["constraints"], " indexes identical:", A["indexes"] == Bj["indexes"])
    for kind in ("labels", "rels"):
        for k in sorted(set(A[kind]) | set(Bj[kind])):
            x, y = A[kind].get(k, 0), Bj[kind].get(k, 0)
            if x != y:
                print(f"{kind[:-1]:6s} {k:40s} {x:>10} {y:>10} {x - y:>10}")
    print(f"total nodes {A['nodes']} -> {Bj['nodes']} ({A['nodes'] - Bj['nodes']}), rels {A['relationships']} -> {Bj['relationships']} ({A['relationships'] - Bj['relationships']})")


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"load": lambda: load(sys.argv[2]), "snap": lambda: snap(sys.argv[2]), "kill": lambda: kill(sys.argv[2]),
     "parity": lambda: parity(sys.argv[2]), "ddl": lambda: ddl(sys.argv[2]), "diff": lambda: diff(sys.argv[2], sys.argv[3])}[cmd]()
