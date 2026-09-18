"""Same rows on synthetic data: graph_search's Sample Search operators against advanced_search's own engine.

Run by ``synthetic_parity.sh``, never by hand: it needs an empty, throwaway MySQL and Neo4j on a private network, which
that script starts, memory-capped, and removes. Inside the app image, over an exported tree, this script:

1. creates the SEEK tables advanced_search's engine and graph_search's scope resolver read, from the DDL in
   ``startup/seed/seek_production.sql.gz`` (schema only, no rows), and inserts the synthetic rows below;
2. writes the graph from the same rows with graph_sync's own projection and writer (constraints, the fulltext index,
   the SampleType and Attribute catalog, the samples, DERIVED_FROM, GraphMeta);
3. runs ``parity.py`` over ``synthetic_queries.json`` in every scope (the superuser, each project set a person holds,
   the named accounts): the NOT, AND/OR and tag texts through advanced_search's view and ``extensions.query``, the
   Simple box's Contain, Not Contain, True and False through its FILTERING path and ``extensions.where``;
4. checks the documented residual (an empty-string value) and the parser defects the README lists, each against
   its expected rows;
5. EXPLAINs a bare negation, and times one over ``--scale`` extra samples, for the README's cost note.

Writes ``summary.json`` and ``summary.md`` (plus parity's own ``parity.json`` and ``parity.md``) under ``--out-dir``;
exits 0 only when parity's gate passes and every check holds.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1].parent
SEED = REPO / "startup" / "seed" / "seek_production.sql.gz"
QUERIES = REPO / "scripts" / "graph_search" / "synthetic_queries.json"
TABLES = ("samples", "sample_types", "people", "assay_assets", "assays", "projects_samples", "sample_attributes",
          "sample_attribute_types", "users", "group_memberships", "work_groups")
NOW = "2026-09-18 12:00:00"
DB = "neo4j"

_SETTINGS = '''
import os
from dmac.test_settings import *  # noqa: F401,F403
DATABASES = dict(DATABASES)
DATABASES["seek"] = {
    "ENGINE": "django.db.backends.mysql", "NAME": "seek_production", "USER": "root",
    "PASSWORD": os.environ["GS_SYNTH_MYSQL_PASSWORD"], "HOST": os.environ["GS_SYNTH_MYSQL_HOST"], "PORT": "3306",
    "OPTIONS": {"charset": "utf8mb4"},
}
NEO4J_DATABASE = {"NAME": "neo4j", "URI": "bolt://" + os.environ["GS_SYNTH_NEO4J_HOST"] + ":7687",
                  "AUTH": ("neo4j", os.environ["GS_SYNTH_NEO4J_PASSWORD"])}
'''


def _configure_django() -> None:
    folder = tempfile.mkdtemp(prefix="gs_synth_")
    Path(folder, "gs_synth_settings.py").write_text(_SETTINGS, encoding="utf-8")
    sys.path.insert(0, folder)
    sys.path.insert(0, str(REPO))
    os.environ["DJANGO_SETTINGS_MODULE"] = "gs_synth_settings"
    import django

    django.setup()


def _log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------------------------
# The synthetic data
# ---------------------------------------------------------------------------------------------------------------------

# sample_attribute_types as the seeded schema numbers them. The Simple box offers True and False for type 15
# (seek/dbtable_sampleattribute.py OPERATOR_SETS), whatever that type is called.
ATTRIBUTE_TYPES = [(3, "Real number", "Float"), (4, "Integer", "Integer"), (7, "Text", "Text"),
                   (8, "String", "String"), (15, "ENA custom date", "String"), (16, "Boolean", "Boolean")]
SAMPLE_TYPES = {26: "TIS", 30: "RNA", 31: "D.SEQ", 15: "MUS", 40: "CEL"}
ATTRIBUTES = {  # title -> attribute type id, per sample type, in position order
    "TIS": [("UID", 8), ("Organ", 7), ("Notes", 7), ("Age", 4), ("TumorGrade", 8), ("Viable", 15), ("Checked", 15),
            ("CellCount", 3)],
    "RNA": [("UID", 8), ("Organ", 7), ("RIN", 3), ("Parent", 8), ("Notes", 7)],
    "D.SEQ": [("UID", 8), ("Parent", 8), ("Reads", 7)],
    "MUS": [("UID", 8), ("Sex", 8), ("Strain", 8), ("Stage", 8), ("Notes", 7)],
    "CEL": [("UID", 8), ("Parent", 8), ("Notes", 7)],
}
_ABSENT = object()


def _sample(sample_id, type_title, n, projects, **values):
    """A samples row: every declared key of its type (SEEK writes them all), null unless given; _ABSENT omits it."""
    uid = f"{type_title}-260918SYN-{n}"
    meta = {}
    for title, _ in ATTRIBUTES[type_title]:
        value = uid if title == "UID" else values.get(title)
        if value is not _ABSENT:
            meta[title] = value
    return {"id": sample_id, "uuid": uid, "title": uid, "type": type_title, "meta": meta, "projects": projects}


def synthetic_samples() -> list[dict]:
    s = _sample
    rows = [
        s(1, "TIS", 1, [1], Organ="Lung", Notes="granuloma present", Age=5, TumorGrade="II", Viable=True,
          Checked="yes", CellCount=1200000.0),
        s(2, "TIS", 2, [1], Organ="Left Lung", Notes="no findings", Age=7, Viable="yes", Checked=""),
        s(3, "TIS", 3, [2], Organ="Liver", Notes="granuloma; necrosis", Viable="1", Checked="no"),
        s(4, "TIS", 4, [1, 2], Organ="lung", Notes="left lobe", Viable=" +1 ", Checked=" "),
        s(5, "TIS", 5, [1], Organ="Kidney", Notes="LUNG metastasis", Viable="0"),
        s(6, "TIS", 6, [2], Organ="Liver", Notes="kidney adjacent", Viable=1),
        s(7, "TIS", 7, [1], Organ="Lung lobe", Notes="salt and pepper texture", Viable=0),
        s(8, "TIS", 8, [3], Organ=12, Notes="lung", Viable=1.5),
        s(9, "TIS", 9, [1], Organ="LUNG", Notes="Granuloma", Viable="no"),
        s(10, "TIS", 10, [1], Notes="liver", Viable="TRUE "),
        s(11, "TIS", 11, [2], Organ="Bronchus and lung", Notes="tumor", Viable="0_1"),
        s(12, "TIS", 12, [1], Organ="Spleen", Viable=False),
        s(13, "TIS", 13, [1], Organ="Lung", Notes="stage 2", Viable=2),
        s(14, "TIS", 14, [1], Organ="Lung", Notes="left lobe granuloma", Viable=_ABSENT),
        s(15, "TIS", 15, [2], Organ="Heart", Notes="left lobe granuloma", Viable="01"),
        s(16, "RNA", 1, [1], Organ="Lung", RIN="8.1", Parent="TIS-260918SYN-1", Notes="lung RNA"),
        s(17, "RNA", 2, [2], Organ="Liver", RIN="6", Parent="TIS-260918SYN-3", Notes="granuloma"),
        s(18, "RNA", 3, [1], Organ="lung", RIN="9.5", Parent="TIS-260918SYN-4"),
        s(19, "RNA", 4, [1], Organ="Kidney", Parent="TIS-260918SYN-5", Notes="liver"),
        s(20, "RNA", 5, [2], Organ="Spleen", Parent="TIS-260918SYN-12", Notes="foreign relative"),
        s(21, "D.SEQ", 1, [1], Parent="RNA-260918SYN-1", Reads=12345),
        s(22, "D.SEQ", 2, [2], Parent="RNA-260918SYN-2", Reads="123"),
        s(23, "D.SEQ", 3, [1], Parent="RNA-260918SYN-3", Reads=999),
        s(24, "D.SEQ", 4, [1], Parent="RNA-260918SYN-5", Reads=5),
        s(25, "D.SEQ", 5, [2], Parent="TIS-260918SYN-2", Reads=77),
        s(26, "MUS", 1, [1], Sex="F", Strain="C57BL/6J", Stage="adult", Notes="lung"),
        s(27, "MUS", 2, [3], Sex="M", Strain="BALB/c", Notes="liver"),
    ]
    # An eleven-hop chain below MUS-1: ten CEL samples, then a D.SEQ at the far end.
    parent = "MUS-260918SYN-1"
    for n in range(1, 11):
        rows.append(s(100 + n, "CEL", n, [1], Parent=parent, Notes=f"chain {n}"))
        parent = f"CEL-260918SYN-{n}"
    rows.append(s(111, "D.SEQ", 11, [1], Parent=parent, Reads=1))
    return rows


def lineage_pairs(samples: list[dict]) -> list[tuple[int, int]]:
    by_uuid = {row["uuid"]: row["id"] for row in samples}
    return [(row["id"], by_uuid[row["meta"]["Parent"]]) for row in samples if row["meta"].get("Parent")]


# people, their memberships and the two accounts parity.py resolves by login
PEOPLE = {10: [1], 11: [1, 2], 12: [3]}
ACCOUNTS = {"user": 10, "tcgamember": 11}


# ---------------------------------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------------------------------

def seed_ddl() -> dict[str, str]:
    """``CREATE TABLE`` statements of ``TABLES`` from the seed dump; its rows are never read."""
    wanted = {name: None for name in TABLES}
    pattern = re.compile(r"CREATE TABLE `(\w+)` \(.*?\) ENGINE=[^;]*;", re.S)
    buffer = ""
    with gzip.open(SEED, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("CREATE TABLE `"):
                buffer = line
                continue
            if buffer:
                buffer += line
                if line.startswith(")"):
                    match = pattern.match(buffer)
                    if match and match.group(1) in wanted:
                        wanted[match.group(1)] = match.group(0)
                    buffer = ""
    missing = [name for name, ddl in wanted.items() if ddl is None]
    if missing:
        raise RuntimeError(f"the seed has no DDL for {missing}")
    return wanted


def load_mysql(samples: list[dict]) -> None:
    from django.db import connections

    ddl = seed_ddl()
    with connections["seek"].cursor() as cur:
        for name in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS `{name}`")
            cur.execute(ddl[name])
        cur.executemany("INSERT INTO sample_attribute_types (id, title, base_type, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s)", [(i, t, b, NOW, NOW) for i, t, b in ATTRIBUTE_TYPES])
        cur.executemany("INSERT INTO sample_types (id, title, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                        [(i, t, NOW, NOW) for i, t in SAMPLE_TYPES.items()])
        attr_id = 0
        rows = []
        type_ids = {t: i for i, t in SAMPLE_TYPES.items()}
        for type_title, attrs in ATTRIBUTES.items():
            for pos, (title, attr_type) in enumerate(attrs, start=1):
                attr_id += 1
                rows.append((attr_id, title, attr_type, NOW, NOW, pos, type_ids[type_title], title.lower()))
        cur.executemany("INSERT INTO sample_attributes (id, title, sample_attribute_type_id, created_at, updated_at, "
                        "pos, sample_type_id, original_accessor_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
        cur.executemany("INSERT INTO people (id, first_name) VALUES (%s, %s)", [(p, f"P{p}") for p in PEOPLE])
        cur.executemany("INSERT INTO samples (id, title, sample_type_id, json_metadata, uuid, contributor_id, "
                        "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        [(r["id"], r["title"], type_ids[r["type"]], json.dumps(r["meta"]), r["uuid"], 10, NOW, NOW)
                         for r in samples])
        cur.executemany("INSERT INTO projects_samples (project_id, sample_id) VALUES (%s, %s)",
                        [(p, r["id"]) for r in samples for p in r["projects"]])
        groups = sorted({p for projects in PEOPLE.values() for p in projects})
        cur.executemany("INSERT INTO work_groups (id, name, project_id) VALUES (%s, %s, %s)",
                        [(p, f"group {p}", p) for p in groups])
        cur.executemany("INSERT INTO group_memberships (person_id, work_group_id) VALUES (%s, %s)",
                        [(person, p) for person, projects in PEOPLE.items() for p in projects])
        cur.executemany("INSERT INTO users (login, person_id) VALUES (%s, %s)", list(ACCOUNTS.items()))


def load_graph(driver, samples: list[dict]) -> dict:
    from nextseek_api.graph_sync import catalog, writer
    from nextseek_api.graph_sync.projection import project_sample, value_type_for

    writer.ensure_constraints_v11(driver, DB)
    writer.ensure_fulltext(driver, DB)
    types = [{"id": i, "title": t, "uuid": None, "description": None} for i, t in SAMPLE_TYPES.items()]
    type_rows = catalog.build_sample_types(types, {}, {}, set())
    writer.write_sample_types(driver, DB, type_rows)
    base = {i: {"base_type": b} for i, _, b in ATTRIBUTE_TYPES}
    type_ids = {t: i for i, t in SAMPLE_TYPES.items()}
    attrs, attr_id = [], 0
    for type_title, pairs in ATTRIBUTES.items():
        for pos, (title, attr_type) in enumerate(pairs, start=1):
            attr_id += 1
            attrs.append({"id": attr_id, "sample_type_id": type_ids[type_title], "title": title, "pos": pos,
                          "required": False, "is_title": False, "sample_attribute_type_id": attr_type,
                          "description": None})
    attr_rows = catalog.build_attributes(attrs, base, {}, SAMPLE_TYPES)
    writer.write_attributes(driver, DB, attr_rows)
    value_types = {t: {title: value_type_for(base[a]["base_type"]) for title, a in pairs}
                   for t, pairs in ATTRIBUTES.items()}
    projections = [project_sample({"id": r["id"], "uuid": r["uuid"], "title": r["title"],
                                   "sample_type_id": type_ids[r["type"]], "json_metadata": json.dumps(r["meta"])},
                                  r["type"], value_types[r["type"]], r["projects"]) for r in samples]
    written = writer.write_samples(driver, DB, projections)
    lineage = writer.write_missing_lineage(driver, DB, lineage_pairs(samples))
    writer.await_indexes(driver, DB, timeout_s=300, poll_s=1)
    writer.write_graphmeta(driver, DB, catalog.catalog_hash(type_rows, attr_rows))
    return {**written, **lineage}


def wait_for(what: str, probe, seconds: float = 180) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            probe()
            return
        except Exception as exc:  # not up yet
            if time.monotonic() > deadline:
                raise RuntimeError(f"{what} did not come up: {exc}") from exc
            time.sleep(2)


# ---------------------------------------------------------------------------------------------------------------------
# Checks beyond parity
# ---------------------------------------------------------------------------------------------------------------------

def graph_ids(body: dict, scope, driver) -> set[int]:
    from nextseek_api.graph_search import service
    from nextseek_api.models import GraphSearchRequest

    return set(service.all_ids(GraphSearchRequest.model_validate(body), scope, driver=driver, db=DB))


def residual_checks(parity, driver) -> list[dict]:
    """The README's residual difference (an empty-string value) and its parser defects, each with its expected rows."""
    from nextseek_api.graph_search.scope import Scope

    admin = Scope(True, None, ())
    checks = []

    filters = parity.filtering_filters({"sampletype": "TIS", "attribute": "Checked", "filter_rule": "False",
                                        "filter_valueFrom": "", "filter_valueTo": ""})
    a = set(parity.a_side_filtering(filters, admin)["ids"])
    g = graph_ids({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Checked", "op": "IS FALSE"}]}}, admin, driver)
    checks.append({"check": "residual: an empty-string value is not in the graph (False on Checked)",
                   "a_only": sorted(a - g), "g_only": sorted(g - a), "expected_a_only": [2], "expected_g_only": [],
                   "ok": sorted(a - g) == [2] and not (g - a)})

    filters = parity.filtering_filters({"sampletype": "TIS", "attribute": "Organ", "filter_rule": "Not Contain",
                                        "filter_valueFrom": "Lung", "filter_valueTo": ""})
    a = parity.a_side_filtering(filters, admin)
    g = graph_ids({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "NOT CONTAINS", "value": "Lung"}]}}, admin, driver)
    checks.append({"check": "defect: _filterSamples' index slips after TIS-10 (no Organ), so TIS-13 (Lung) is kept "
                            "and TIS-15 (Heart) dropped; without the slip the rows equal graph_search's",
                   "a_only": sorted(set(a["ids"]) - g), "g_only": sorted(g - set(a["ids"])),
                   "ok": sorted(set(a["ids"]) - g) == [13] and sorted(g - set(a["ids"])) == [15]
                   and set(a["aligned"]) == g and not a["notes"]})

    defects = [
        ("NOT before a group ran as AND", "lung NOT (left lobe)"),
        ("a level that is one group matched a placeholder", "(lung AND granuloma)"),
        ("two groups on one level turned to garbage", "(lung AND granuloma) AND (left lobe)"),
    ]
    for label, text in defects:
        a_total, a_ids = parity.call_view({"filter_searchText": text, "filter_matchType": "PARTIAL"}, admin)
        g = graph_ids({"filter_searchText": "", "extensions": {"query": text}}, admin, driver)
        intended = {
            "lung NOT (left lobe)": graph_ids({"filter_searchText": "", "extensions": {
                "query": "lung NOT left lobe"}}, admin, driver),
            "(lung AND granuloma)": graph_ids({"filter_searchText": "", "extensions": {
                "query": "lung AND granuloma"}}, admin, driver),
            "(lung AND granuloma) AND (left lobe)": graph_ids({"filter_searchText": "", "extensions": {
                "query": "lung AND granuloma AND left lobe"}}, admin, driver),
        }[text]
        as_and = parity.call_view({"filter_searchText": "lung AND (left lobe)", "filter_matchType": "PARTIAL"},
                                  admin)[1] if text == "lung NOT (left lobe)" else None
        ok = g == intended and (set(a_ids) == set(as_and) if as_and is not None else not a_ids)
        checks.append({"check": f"parser defect: {label}", "text": text, "advanced_search": sorted(a_ids),
                       "graph_search": sorted(g), "ok": ok})
    return checks


def explain(driver, body: dict, scope) -> list[str]:
    """The operators of the page statement's plan, top first."""
    from nextseek_api.graph_search import catalog_cache
    from nextseek_api.graph_search.query import build
    from nextseek_api.graph_search.service import db_filters
    from nextseek_api.models import GraphSearchRequest

    req = GraphSearchRequest.model_validate(body)
    built = build(db_filters(req), req.extensions, scope, catalog_cache.get_catalog(driver, DB), 1, 100)
    text = built.page_cypher.replace("CYPHER 25\n", "CYPHER 25 EXPLAIN\n", 1)
    summary = driver.execute_query(text, built.params, database_=DB).summary
    ops = []

    def walk(plan, depth=0):
        ops.append("  " * depth + plan["operatorType"])
        for child in plan.get("children") or []:
            walk(child, depth + 1)

    walk(summary.plan)
    return ops


def scale_timing(driver, n: int) -> dict:
    """A bare negation over ``n`` extra Sample nodes of one type: the rate the README's cost note quotes."""
    if n <= 0:
        return {}
    text = "x" * 40 + " lung tissue section reading " + "y" * 40
    for start in range(0, n, 10000):
        rows = [{"id": 1_000_000 + i, "uuid": f"SCL-260918SYN-{i}", "search_text": f"{text} {i}"}
                for i in range(start, min(n, start + 10000))]
        driver.execute_query("CYPHER 25 UNWIND $rows AS r CREATE (s:Sample:T_SCL {id: r.id, uuid: r.uuid, "
                             "type: 'SCL', project_ids: [1], search_text: r.search_text})", {"rows": rows},
                             database_=DB)
    timings = {}
    for label, statement in (
        ("negation over every Sample", "CYPHER 25 MATCH (s:Sample) WHERE NOT (toLower(s.search_text) CONTAINS "
                                       "$q) RETURN count(s) AS n"),
        ("negation over one type", "CYPHER 25 MATCH (s:Sample) WHERE s.type IN ['SCL'] AND NOT "
                                   "(toLower(s.search_text) CONTAINS $q) RETURN count(s) AS n"),
    ):
        driver.execute_query(statement, {"q": "granuloma"}, database_=DB)  # warm
        start = time.perf_counter()
        records = driver.execute_query(statement, {"q": "granuloma"}, database_=DB).records
        timings[label] = {"rows": records[0]["n"], "ms": round((time.perf_counter() - start) * 1000, 1)}
    return {"extra_samples": n, "timings": timings}


# ---------------------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--scale", type=int, default=200_000, help="extra samples for the negation timing (0 skips it)")
    args = p.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    _configure_django()
    from django.db import connections

    from nextseek_api.services.graph_search import _neo4j

    def mysql_up():
        with connections["seek"].cursor() as cur:
            cur.execute("SELECT 1")

    wait_for("MySQL", mysql_up)
    driver, _db = _neo4j()
    wait_for("Neo4j", lambda: driver.verify_connectivity())

    samples = synthetic_samples()
    load_mysql(samples)
    loaded = load_graph(driver, samples)
    _log(f"loaded {len(samples)} samples: {loaded}")

    import importlib.util

    spec = importlib.util.spec_from_file_location("gs_parity", REPO / "scripts" / "graph_search" / "parity.py")
    parity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parity)
    gate = parity.main(["--queries", str(QUERIES), "--out-dir", str(args.out_dir / "parity")])
    report = json.loads((args.out_dir / "parity" / "parity.json").read_text(encoding="utf-8"))

    from nextseek_api.graph_search.scope import Scope

    checks = residual_checks(parity, driver)
    plans = {
        "bare negation, no type": explain(driver, {"filter_searchText": "", "extensions": {
            "query": "NOT granuloma"}}, Scope(True, None, ())),
        "bare negation, member of project 1": explain(driver, {"filter_searchText": "", "extensions": {
            "query": "NOT granuloma"}}, Scope(False, 10, (1,))),
        "negation under a positive term": explain(driver, {"filter_searchText": "", "extensions": {
            "query": "lung NOT granuloma"}}, Scope(True, None, ())),
    }
    scale = scale_timing(driver, args.scale)

    summary = {
        "graph": loaded,
        "parity": report["summary"],
        "pairs": [{k: r.get(k) for k in ("query", "scope", "status", "a_total", "g_total", "a_only_n", "g_only_n")}
                  for r in report["compat"]],
        "checks": checks,
        "plans": plans,
        "scale": scale,
    }
    ok = gate == 0 and all(c["ok"] for c in checks)
    summary["ok"] = ok
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    lines = [f"# Synthetic parity: {'PASS' if ok else 'FAIL'}", "",
             f"Parity gate: {report['summary']}", "", "| Query | Scope | Status | A | G | A only | G only |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['query']} | {r['scope']} | {r['status']} | {r['a_total']} | {r['g_total']} | "
              f"{r['a_only_n']} | {r['g_only_n']} |" for r in summary["pairs"]]
    lines += ["", "## Checks", ""] + [f"- {'ok' if c['ok'] else 'FAILED'}: {c['check']}: "
                                      f"{json.dumps({k: v for k, v in c.items() if k not in ('check', 'ok')})}"
                                      for c in checks]
    lines += ["", "## Plans", ""] + [f"- {name}: {' > '.join(op.strip() for op in ops)}" for name, ops in plans.items()]
    lines += ["", "## Scale", "", json.dumps(scale)]
    (args.out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"synthetic parity {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
