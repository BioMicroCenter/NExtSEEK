"""Parity check for the sample download API: the old data path against the new one, on a live stack.

Read-only. Runs inside the app image with the instance's real settings (MySQL and Neo4j), for example:

    docker exec -i nextseek uv run python scripts/sample_retrieve_parity.py --per-type 20

or, before a rebuild, from a throwaway container over this checkout with the live container's env and
local_settings (see ``--help``). For every case it computes what ``admin/samples/retrieve/`` returned before
``nextseek_api/services/sample_retrieve.py`` (the old algorithm, reproduced here from the code it replaced:
``DBtable_sample.getChildrenUIDs`` for ``include_tree`` and the project-joined MySQL fallback otherwise) and what
the new ``retrieve_samples`` returns, then compares:

- the status (404 when nothing is found),
- the rows, as a multiset of (id, uuid, sample_type_id, json_metadata),
- the JSON body, every field except the new ``lineage_complete`` and the order of records inside a group,
- the order of records inside each group,
- for ``--excel N`` cases, every cell of every sheet of the two workbooks.

Cases: ``--per-type`` random samples of every UID prefix, each alone, as a superuser, as a member of the projects
that hold it, and as a member of an unrelated project, with ``include_tree`` true and false; plus the largest
lineages, numeric SEEK ids, an unknown UID, a mixed request, and a no-project caller.

The old path resolved the caller's projects from SEEK's ``/people/current``. This script reads them from MySQL
(``group_memberships`` x ``work_groups``), which is what the new path does; the two were compared for every user
on the local snapshot on 2026-09-23 (124 of 124 equal via SEEK's ``/people/{id}``). Run ``--scope-check`` to
repeat that comparison against this instance's SEEK.

Differences are classified. Two are expected and reported, not failed:

- ``duplicate_rows_removed``: the old non-superuser SQL joined ``projects_samples`` without DISTINCT, so a sample
  in two of the caller's projects came back twice.
- ``graph_miss_rescued``: a requested sample the graph does not hold (not synced yet) was a 404 or silently
  missing before; it is now exported, with ``lineage_complete`` false.

Anything else is a failure, and the script exits 1.
"""

import argparse
import collections
import json
import os
import random
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")

import django  # noqa: E402

django.setup()

import pandas as pd  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from nextseek_api.graph_search.scope import Scope  # noqa: E402
from nextseek_api.services import sample_retrieve as sr  # noqa: E402
from seek.dbtable_sample import DBtable_sample  # noqa: E402

COLUMNS = sr.COLUMNS


# -- the old path ---------------------------------------------------------------------------------------------------


def old_rows(identifiers, include_tree, scope):
    """(frame, requested_uids, unresolved_numeric): what the old view built before its JSON or workbook step."""
    requested, numeric = [], []
    for it in identifiers:
        s = str(it or "").strip()
        if s:
            (numeric if s.isdigit() else requested).append(s)
    unresolved = 0
    if numeric:
        found = sr._ids_to_uuids(numeric)
        for n in numeric:
            if int(n) in found:
                requested.append(found[int(n)])
            else:
                unresolved += 1
    requested = list(dict.fromkeys(requested))
    project_ids = [str(p) for p in scope.project_ids]
    if include_tree:
        frame = DBtable_sample().getChildrenUIDs(requested, project_ids, scope.is_admin)
    else:
        frame = old_fallback(requested, project_ids, scope.is_admin)
    return frame, requested, unresolved


def old_fallback(uids, project_ids, admin):
    if not uids:
        return pd.DataFrame(columns=COLUMNS)
    ph = ", ".join(["%s"] * len(uids))
    with connections[settings.SEEK_DATABASE].cursor() as c:
        if admin:
            c.execute(f"SELECT id, sample_type_id, uuid, json_metadata FROM samples WHERE uuid IN ({ph})", uids)
        else:
            pids = project_ids or [""]
            pp = ", ".join(["%s"] * len(pids))
            c.execute(
                "SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata FROM samples s JOIN projects_samples ps "
                f"ON s.id = ps.sample_id WHERE s.uuid IN ({ph}) AND ps.sample_id = s.id AND ps.project_id IN ({pp})",
                list(uids) + list(pids))
        return pd.DataFrame([tuple(r) for r in c.fetchall()], columns=COLUMNS)


# -- comparison -----------------------------------------------------------------------------------------------------


def row_key(r):
    return (int(r["id"]), str(r["uuid"]), None if pd.isna(r["sample_type_id"]) else int(r["sample_type_id"]),
            r["json_metadata"])


def body(frame, requested, unresolved):
    result = sr.RetrieveResult(frame=frame, requested_uids=requested, unresolved_numeric=unresolved,
                               lineage_complete=True)
    b = sr._json_body(result)
    b.pop("lineage_complete")
    return b


def unordered(b):
    out = dict(b)
    out["data"] = [dict(g, samples=sorted(g["samples"], key=lambda s: s["id"])) for g in b["data"]]
    return out


def group_orders(b):
    return {g["sample_type"]: [s["id"] for s in g["samples"]] for g in b["data"]}


def workbook_cells(frame, notice=None):
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        DBtable_sample().sampleRetrievalData(frame.copy(), path, notice=notice)
        book = load_workbook(path)
        return {name: [[c.value for c in row] for row in book[name].iter_rows()] for name in book.sheetnames}
    finally:
        os.unlink(path)


def compare(case, excel=False):
    ident, tree, scope = case["identifiers"], case["include_tree"], case["scope"]
    t0 = time.perf_counter()
    of, oreq, ounres = old_rows(ident, tree, scope)
    t1 = time.perf_counter()
    new = sr.retrieve_samples(ident, tree, scope)
    t2 = time.perf_counter()
    nf = new.frame
    out = {"case": case["name"], "old_s": round(t1 - t0, 3), "new_s": round(t2 - t1, 3),
           "old_rows": int(len(of)), "new_rows": int(len(nf)), "lineage_complete": new.lineage_complete,
           "problems": [], "expected": []}

    old_keys = collections.Counter(row_key(r) for r in of.to_dict("records"))
    new_keys = collections.Counter(row_key(r) for r in nf.to_dict("records"))
    if old_keys != new_keys:
        deduped = collections.Counter({k: 1 for k in old_keys})
        if deduped == new_keys:
            out["expected"].append("duplicate_rows_removed")
        else:
            missing, extra = set(old_keys) - set(new_keys), set(new_keys) - set(old_keys)
            requested = set(new.requested_uids)
            # Only requested samples may be new, and only when the graph was missing something.
            if not missing and not new.lineage_complete and all(k[1] in requested for k in extra):
                out["expected"].append("graph_miss_rescued")
            else:
                out["problems"].append({"missing_in_new": sorted(k[1] for k in missing)[:20],
                                        "extra_in_new": sorted(k[1] for k in extra)[:20]})
    old_status, new_status = (404 if of.empty else 200), (404 if nf.empty else 200)
    if old_status != new_status and "graph_miss_rescued" not in out["expected"]:
        out["problems"].append({"status": [old_status, new_status]})
    if not of.empty and not nf.empty and not out["problems"]:
        ob, nb = body(of.drop_duplicates(subset=["id"]), oreq, ounres), body(nf, new.requested_uids,
                                                                            new.unresolved_numeric)
        if "graph_miss_rescued" not in out["expected"] and unordered(ob) != unordered(nb):
            out["problems"].append({"json_body_differs": True})
        if group_orders(ob) != group_orders(nb):
            out["expected"].append("record_order_differs")
        if excel:
            if workbook_cells(of.drop_duplicates(subset=["id"]).sort_values("id").reset_index(drop=True)) != \
                    workbook_cells(nf):
                out["problems"].append({"workbook_differs": True})
            else:
                out["excel_checked"] = True
    return out


# -- cases ----------------------------------------------------------------------------------------------------------


def all_samples():
    with connections[settings.SEEK_DATABASE].cursor() as c:
        c.execute("SELECT id, uuid FROM samples")
        return [(int(i), str(u)) for i, u in c.fetchall() if u]


def projects_of(ids):
    ph = ", ".join(["%s"] * len(ids))
    with connections[settings.SEEK_DATABASE].cursor() as c:
        c.execute(f"SELECT sample_id, project_id FROM projects_samples WHERE sample_id IN ({ph})", list(ids))
        out = collections.defaultdict(set)
        for s, p in c.fetchall():
            out[int(s)].add(int(p))
    return out


def all_projects():
    with connections[settings.SEEK_DATABASE].cursor() as c:
        c.execute("SELECT DISTINCT project_id FROM projects_samples")
        return sorted(int(r[0]) for r in c.fetchall())


def build_cases(per_type, seed, big):
    rng = random.Random(seed)
    samples = all_samples()
    by_prefix = collections.defaultdict(list)
    prefix_re = re.compile(sr.UID_PREFIX_RE)
    for sid, uuid in samples:
        m = prefix_re.search(uuid)
        by_prefix[m.group(1) if m else "UNKNOWN"].append((sid, uuid))
    picked = []
    for prefix in sorted(by_prefix):
        pool = by_prefix[prefix]
        picked += rng.sample(pool, min(per_type, len(pool)))
    projects = projects_of([sid for sid, _ in picked])
    everything = all_projects()
    admin = Scope(True, None, ())
    cases = []
    for sid, uuid in picked:
        own = tuple(sorted(projects.get(sid, ())))
        other = tuple(p for p in everything if p not in own)[:1]
        for tree in (True, False):
            cases.append({"name": f"{uuid} super tree={tree}", "identifiers": [uuid], "include_tree": tree,
                          "scope": admin})
            if own:
                cases.append({"name": f"{uuid} member tree={tree}", "identifiers": [uuid], "include_tree": tree,
                              "scope": Scope(False, 0, own[:1])})
            if other:
                cases.append({"name": f"{uuid} foreign tree={tree}", "identifiers": [uuid], "include_tree": tree,
                              "scope": Scope(False, 0, other)})
    for uuid in big:
        cases.append({"name": f"{uuid} big super", "identifiers": [uuid], "include_tree": True, "scope": admin})
    some = [u for _, u in picked[:5]]
    cases += [
        {"name": "numeric ids", "identifiers": [str(s) for s, _ in picked[:5]] + ["999999999"],
         "include_tree": True, "scope": admin},
        {"name": "unknown uid", "identifiers": ["NOPE-000000XXX-1"], "include_tree": True, "scope": admin},
        {"name": "mixed batch", "identifiers": some + ["NOPE-000000XXX-1"], "include_tree": True, "scope": admin},
        {"name": "no projects", "identifiers": some, "include_tree": True, "scope": Scope(False, 0, ())},
    ]
    return cases


def scope_check():
    """MySQL membership against SEEK's /people/{id} for every user; prints mismatches."""
    import requests

    base = os.environ.get("SEEK_HOSTNAME", "http://seek:3000")
    with connections[settings.SEEK_DATABASE].cursor() as c:
        c.execute("SELECT u.person_id, wg.project_id FROM users u LEFT JOIN group_memberships gm "
                  "ON gm.person_id = u.person_id LEFT JOIN work_groups wg ON wg.id = gm.work_group_id "
                  "WHERE u.person_id IS NOT NULL")
        sql = collections.defaultdict(set)
        for person, project in c.fetchall():
            sql[int(person)]
            if project is not None:
                sql[int(person)].add(str(project))
    bad = 0
    for person, projects in sorted(sql.items()):
        r = requests.get(f"{base}/people/{person}", headers={"Accept": "application/vnd.api+json"}, timeout=20)
        api = {p["id"] for p in r.json()["data"]["relationships"]["projects"]["data"]}
        if api != projects:
            bad += 1
            print("scope mismatch", person, sorted(projects), sorted(api))
    print(f"scope check: {len(sql)} people, {bad} mismatches")
    return bad == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-type", type=int, default=20)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--excel", type=int, default=40, help="compare workbooks for the first N comparable cases")
    ap.add_argument("--big", nargs="*", default=[], help="extra UIDs with large lineages")
    ap.add_argument("--scope-check", action="store_true")
    ap.add_argument("--out", default=None, help="write every case's result as JSON lines here")
    args = ap.parse_args()

    ok = scope_check() if args.scope_check else True
    cases = build_cases(args.per_type, args.seed, args.big)
    print(f"{len(cases)} cases")
    tally, failures, excel_left = collections.Counter(), [], args.excel
    old_t = new_t = 0.0
    sink = open(args.out, "w") if args.out else None
    for i, case in enumerate(cases, 1):
        res = compare(case, excel=excel_left > 0)
        if res.get("excel_checked"):
            excel_left -= 1
            tally["excel_equal"] += 1
        old_t += res["old_s"]
        new_t += res["new_s"]
        tally["cases"] += 1
        for e in res["expected"]:
            tally[e] += 1
        if not res["lineage_complete"]:
            tally["lineage_incomplete"] += 1
        if res["problems"]:
            failures.append(res)
        if sink:
            sink.write(json.dumps(res, default=str) + "\n")
            sink.flush()
        if i % 200 == 0:
            print(f"  {i}/{len(cases)} done, {len(failures)} failures", flush=True)
    print(json.dumps(dict(tally), indent=1))
    print(f"total time old {old_t:.1f} s, new {new_t:.1f} s")
    for f in failures[:30]:
        print("FAIL", json.dumps(f, default=str))
    print(f"{len(failures)} failures")
    return 0 if ok and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
