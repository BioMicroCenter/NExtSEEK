"""graph_search parity, gate E: graph_search's id sets against advanced_search's, per query and scope.

Run it in the lane (scripts/graph_search/README.md) once the v1.1 graph is built and the merged MySQL is loaded:

    scripts/graph_search/lane.sh python scripts/graph_search/parity.py [--only NAME ...] [--scopes KEY ...] [--resume]

Scopes: the superuser; every distinct project set a person holds through group_memberships joined to work_groups
(former members included, as the scope resolver reads it); and the seed accounts ``tcgamember`` and ``user``, resolved
by ``graph_search.scope.resolve_scope``. Accounts whose project sets coincide are computed once and reported under
every name.

A ``compat`` query is one of three forms (``sides``): ``body`` alone goes to both engines; ``body`` and ``graph_body``
give advanced_search's view the first and graph_search the second (the Sample Search page's query text, which
advanced_search parses inside ``filter_searchText`` and graph_search in ``extensions.query``); ``engine: "FILTERING"``
with ``filters`` runs the Simple box's own path (``SampleSearchMixin.searchAdvanced`` with searchType FILTERING, what
``/seek/samples/searching/`` ran) against graph_search's ``body``. Declared difference 2 excuses only the first form.

For each ``compat`` query in queries.json and each scope:

- G, graph_search: ``service.all_ids`` (the ids statement, every match) and ``service.search`` (the endpoint's page and
  count statements, for its ``total`` and timings). Both run in the endpoint's READ session shape.
- A, advanced_search: its own engine. Its WHERE clause is captured from ``seek/sample/queries.py`` (the statement
  ``_sqlQuery_select_records_filters_advanced`` builds, split into UID and text searches exactly as the view splits
  them) and run as an id-only ``SELECT A.id``. When that match count is at most ``--threshold`` (50,000), the view
  ``SampleAdvancedSearchViewSet.create`` is called with the scope's projects handed to it in place of the SEEK REST
  lookup (``resolve_seek_auth`` and ``SeekDB`` are patched): page 1 with ``page_size=1000`` as a client pages, then,
  when there is more than one page, the whole result in one call with only the pagination helper patched out, which
  is checked against page 1. Above the threshold, a shape with no Python stage (no text term) takes the id-only SQL
  as its A side; a shape with one is reported as too broad to compare.

Every difference is classified against the design's declared differences (docs/superpowers/specs/
2026-09-14-graph-search-poc-design.md, section 7) and given a probable cause. ``graph_only`` queries record totals and
timings only. A read-only check sends ``CREATE (:Probe)`` through the endpoint's READ session, both as the plan writes
it and through the service's own transaction function, and each must be refused.

Output in ``--out-dir`` (default ``$GS_RUN_DIR/E6``): ``parity.json``, ``parity.md`` and ``progress.jsonl``. Each
compat pair is logged before it starts and when it ends, so ``--resume`` reuses finished pairs and reports a pair that
was running when the process died (the lane's memory cap, for one) as killed instead of retrying it.

Exit status: 0 when gate E passes (no undeclared difference, every compat pair compared, the write refused both
ways), 1 when it fails.
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import json
import logging
import os
import re
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

import django

django.setup()

import orjson  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from neo4j import READ_ACCESS, RoutingControl  # noqa: E402
from neo4j.exceptions import Neo4jError  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

from nextseek_api.batch_upload.helpers import UID_RE  # noqa: E402
from nextseek_api.graph_search import service  # noqa: E402
from nextseek_api.graph_search.query import split_terms  # noqa: E402
from nextseek_api.graph_search.scope import Scope, resolve_scope  # noqa: E402
from nextseek_api.models import GraphSearchRequest, SampleAdvancedSearchRequest  # noqa: E402
from nextseek_api.services import samples as advanced  # noqa: E402
from nextseek_api.services.graph_search import _neo4j  # noqa: E402
from seek.dbtable_sample import DBtable_sample  # noqa: E402
from seek.dbtable_sampleattribute import DBtable_sampleattribute  # noqa: E402

# Importing seek's URLconf runs logging.basicConfig at DEBUG (seek/CLAUDE.md), which would print every Bolt message
# and every engine statement; the harness logs its own progress.
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("seek").setLevel(logging.INFO)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_QUERIES = REPO / "scripts" / "graph_search" / "queries.json"
NAMED_ACCOUNTS = ("tcgamember", "user")
ADMIN_KEY = "admin"
VIEW_PAGE_SIZE = 1000
ADVANCED_PATH = "/nextseek_api/samples/advanced_search/"

# The design's declared differences (section 7). Only 2 can change an id set; the others concern row order, the
# envelope, paging, authentication and highlighting, which an id-set comparison does not see.
DECLARED = {
    1: "rows are in global id order; a mixed UID-plus-text search keeps footer and sampleTypes",
    2: ("PubMed syntax inside filter_searchText (parentheses, NOT, term[TYPE]) is not parsed; the string is one term "
        "(the same text in extensions.query is)"),
    3: "sampleTypes is computed after every filter",
    4: "an out-of-range page returns an empty page, not every row",
    5: "a caller with no SEEK person is 403 even with Basic credentials; Token authentication is not offered",
    6: "no highlight HTML",
    7: ("the Simple box's path judges each row after a sample that passes the rule without holding the attribute by "
        "the rule's result on the row before it (seek/sample/queries.py _filterSamples skips its index)"),
}
# advanced_search's PubMed parser splits a string on these (seek/search.py, Search.__validateExpression and
# __parseKeyword); graph_search keeps such a string as one term.
_PUBMED_RE = re.compile(r"[()\[\]]|\s(?:AND|OR|NOT)\s", re.IGNORECASE)
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{256,}")


class HarnessError(RuntimeError):
    """The harness could not obtain one side of a pair."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _log(message: str) -> None:
    print(f"{_now()} {message}", file=sys.stderr, flush=True)


def _memory() -> dict:
    """This process's peak RSS and its container's cgroup memory, in MiB."""
    out = {"rss_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    for name in ("memory.current", "memory.peak"):
        try:
            out[name.replace(".", "_") + "_mb"] = round(int(Path("/sys/fs/cgroup", name).read_text()) / 1048576, 1)
        except (OSError, ValueError):
            pass
    return out


_DEVNULL = open(os.devnull, "w")


def _quiet():
    """advanced_search's engine prints its SQL and counts to stdout; keep them out of the harness's output."""
    return contextlib.redirect_stdout(_DEVNULL)


# ---------------------------------------------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------------------------------------------

def scope_key(scope: Scope) -> str:
    if scope.is_admin:
        return ADMIN_KEY
    return "projects:" + (",".join(str(p) for p in scope.project_ids) or "none")


def person_project_sets() -> dict[tuple[int, ...], int]:
    """Every distinct project set a person holds, with the number of people holding it."""
    sql = ("SELECT gm.person_id, wg.project_id FROM group_memberships gm "
           "JOIN work_groups wg ON wg.id = gm.work_group_id WHERE wg.project_id IS NOT NULL")
    by_person: dict[int, set[int]] = {}
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute(sql)
        for person_id, project_id in cur.fetchall():
            by_person.setdefault(int(person_id), set()).add(int(project_id))
    counts: dict[tuple[int, ...], int] = {}
    for projects in by_person.values():
        key = tuple(sorted(projects))
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_scopes() -> list[dict]:
    """The superuser, each distinct person project set, and the named accounts, deduplicated by project set."""
    entries: dict[str, dict] = {ADMIN_KEY: {"key": ADMIN_KEY, "scope": Scope(True, None, ()), "persons": None,
                                            "names": ["superuser"]}}
    for projects, persons in sorted(person_project_sets().items(), key=lambda kv: (-kv[1], kv[0])):
        scope = Scope(False, None, projects)
        entries[scope_key(scope)] = {"key": scope_key(scope), "scope": scope, "persons": persons, "names": []}
    for login in NAMED_ACCOUNTS:
        scope = resolve_scope(SimpleNamespace(username=login, is_superuser=False))
        key = scope_key(scope)
        entry = entries.setdefault(key, {"key": key, "scope": scope, "persons": None, "names": []})
        entry["names"].append(login)
        entry.setdefault("accounts", []).append({"login": login, "person_id": scope.person_id})
    return list(entries.values())


# ---------------------------------------------------------------------------------------------------------------
# G side
# ---------------------------------------------------------------------------------------------------------------

def graph_meta(driver, db: str) -> dict:
    def one(text: str):
        records = driver.execute_query(text, routing_=RoutingControl.READ, database_=db).records
        return records[0] if records else None

    meta = one("CYPHER 25 MATCH (g:GraphMeta) RETURN g.schema_version AS schema_version, "
               "g.catalog_hash AS catalog_hash, toString(g.synced_at) AS synced_at")
    samples = one("CYPHER 25 MATCH (s:Sample) RETURN count(s) AS n")
    return {**(dict(meta) if meta else {}), "samples": samples["n"] if samples else None}


def g_side(body: dict, scope: Scope, driver, db: str, timeout: float) -> dict:
    req = GraphSearchRequest.model_validate(body)
    filters = service.db_filters(req)
    start = time.perf_counter()
    ids = service.all_ids(req, scope, driver=driver, db=db, filters=filters, timeout=timeout)
    ids_ms = _ms(start)
    start = time.perf_counter()
    page = service.search(req, scope, 1, 100, driver=driver, db=db, filters=filters)
    search_ms = _ms(start)
    return {"ids": ids, "total": page["total"], "ids_ms": ids_ms, "search_ms": search_ms,
            "timings": page["timings"], "sample_types": page["sample_types"]}


# ---------------------------------------------------------------------------------------------------------------
# A side
# ---------------------------------------------------------------------------------------------------------------

FILTERING = "FILTERING"


def sides(q: dict) -> tuple[dict, dict | None, dict | None]:
    """What each engine is given: (graph_search's body, advanced_search's view body or None, FILTERING filters or
    None)."""
    if q.get("engine") == FILTERING:
        return q["body"], None, q["filters"]
    return q.get("graph_body") or q["body"], q["body"], None


def filtering_filters(spec: dict, resolve=None) -> dict:
    """The GET parameters the Simple box sent to /seek/samples/searching/, the sample type title resolved to its id."""
    resolve = resolve or advanced.resolve_sampletype_to_seek_id
    type_id = resolve(spec["sampletype"])
    if type_id is None or not str(type_id).isdigit():
        raise HarnessError(f"sample type {spec['sampletype']!r} does not resolve on this instance")
    return {"sampletype_id": int(type_id), "attribute": spec["attribute"], "filter_rule": spec["filter_rule"],
            "filter_valueFrom": spec["filter_valueFrom"], "filter_valueTo": spec["filter_valueTo"]}


def filtering_statements(filters: dict, scope: Scope) -> tuple[list[dict], bool]:
    """The id-only statement the Simple box's engine path runs, and whether it has a Python stage (a chosen attribute
    whose rule is not No Filter, or a keyword)."""
    dbs = DBtable_sample()
    with _quiet():
        msg, status, fd = dbs._parseSearchFilters(filters, FILTERING, 0)
        if status == 0:
            raise HarnessError(f"advanced_search refuses the filters: {msg}")
        if not scope.is_admin:
            fd["scoped_project_ids"] = [str(p) for p in scope.project_ids]
        where, params = dbs._sqlQuery_select_records_filters_advanced(fd)
    sql = "SELECT A.id" + dbs._sqlQuery_select_records_from(fd["project_id"]) + where
    python_stage = (filters["attribute"] != "none" and filters["filter_rule"] != "No Filter") or bool(
        fd.get("searchText"))
    return [{"search_type": FILTERING, "sql": sql, "params": list(params)}], python_stage


def filtering_kept(rows: list[tuple[int, dict]], passes: list, attribute: str, slip: bool) -> list[int]:
    """The ids ``_filterSamples`` keeps: a row that passes the rule and holds the attribute with a non-null value.

    ``slip=True`` is its loop exactly (seek/sample/queries.py): a row that passes without a value ``continue``s
    before ``index += 1``, so every later row reads the rule's result of the row before it (declared difference 7).
    ``slip=False`` is the loop without that slip.
    """
    kept, index = [], 0
    for sample_id, meta in rows:
        if passes[index]:
            if meta.get(attribute) is None:
                if slip:
                    continue
            else:
                kept.append(sample_id)
        index += 1
    return kept


def a_side_filtering(filters: dict, scope: Scope) -> dict:
    """advanced_search's answer on the Simple box's path, in the scope ``runSampleSearch`` gives a caller.

    Also ``aligned``: the same rows judged by the engine's own rule functions without ``_filterSamples``' index slip,
    and a note when the engine's answer is not what ``filtering_kept`` predicts for it.
    """
    start = time.perf_counter()
    scoped = None if scope.is_admin else [str(p) for p in scope.project_ids]
    with _quiet():
        raw = DBtable_sample().searchAdvanced(None, dict(filters), FILTERING, 0, scoped_project_ids=scoped)
    data = orjson.loads(raw)
    if data.get("status") != 1:
        raise HarnessError(f"advanced_search's FILTERING path answered {data.get('msg')!r}")
    ids = [int(row["id"]) for row in data.get("rows") or []]
    notes = [] if len(ids) == len(set(ids)) else [f"{len(ids) - len(set(ids))} duplicate row ids"]
    aligned = ids
    attribute = str(filters["attribute"]).strip()
    if filters["attribute"] != "none" and filters["filter_rule"] != "No Filter":
        dbs = DBtable_sample()
        with _quiet():
            _msg, _status, fd = dbs._parseSearchFilters(dict(filters), FILTERING, 0)
            if scoped is not None:
                fd["scoped_project_ids"] = scoped
            engine_rows = dbs._retrieveRecords_advanced(None, fd)["rows"]
            rows = [(int(r["id"]), json.loads(r["json_metadata"])) for r in engine_rows]
            passes = DBtable_sampleattribute().filterValues(
                [meta.get(attribute) for _, meta in rows], filters["sampletype_id"], filters["attribute"],
                filters["filter_rule"], filters["filter_valueFrom"], filters["filter_valueTo"])
        aligned = filtering_kept(rows, passes, attribute, slip=False)
        if filtering_kept(rows, passes, attribute, slip=True) != ids:
            notes.append("the engine's rows are not what filtering_kept predicts for them")
    return {"ids": ids, "aligned": aligned, "total": int(data.get("total") or 0), "ms": _ms(start), "calls": 1,
            "notes": notes}


def advanced_filters(body: dict) -> dict:
    """The filters advanced_search's view computes for this body."""
    req = SampleAdvancedSearchRequest.model_validate(body)
    return req.to_db_filters(sampletype_resolver=advanced.resolve_sampletype_to_seek_id)


def engine_statements(body: dict, filters: dict, scope: Scope) -> tuple[list[dict], bool]:
    """The id-only statements advanced_search's engine would run, and whether the view has a Python stage.

    The view runs a UID search (``searchType`` UIDs) for UID terms and a text search (Advanced) for the rest, the
    text terms nested for the PubMed parser, and an Advanced search with an empty text when there is no term at all.
    Each WHERE clause, with its bound values, is taken from the engine's own builder, scope included as
    ``searchAdvanced`` adds it.
    """
    terms = split_terms(body.get("filter_searchText"))
    uid_terms = [t for t in terms if UID_RE.match(t)]
    other_terms = [t for t in terms if not UID_RE.match(t)]
    partials = []
    if uid_terms:
        sub = dict(filters)
        sub["filter_searchUIDs"] = "\n".join(uid_terms)
        partials.append(("UIDs", sub))
    if other_terms:
        sub = dict(filters)
        if len(other_terms) >= 2:
            op = " AND " if str(filters.get("searchText_logic") or "OR").upper() == "AND" else " OR "
            sub["filter_searchText"] = advanced._nest_boolean_search_terms(other_terms, op)
        else:
            sub["filter_searchText"] = other_terms[0]
        partials.append(("Advanced", sub))
    if not partials:
        sub = dict(filters)
        sub["filter_searchText"] = ""
        partials.append(("Advanced", sub))

    dbs = DBtable_sample()
    statements = []
    for search_type, sub in partials:
        with _quiet():
            _msg, _status, fd = dbs._parseSearchFilters(sub, search_type, 0)
            if not scope.is_admin:
                fd["scoped_project_ids"] = [str(p) for p in scope.project_ids]
            where, params = dbs._sqlQuery_select_records_filters_advanced(fd)
        sql = "SELECT A.id" + dbs._sqlQuery_select_records_from(fd["project_id"]) + where
        statements.append({"search_type": search_type, "sql": sql, "params": list(params)})
    python_stage = bool(other_terms) or bool(filters.get("attribute_list") and terms)
    return statements, python_stage


def run_statements(statements: list[dict]) -> set[int]:
    ids: set[int] = set()
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        for st in statements:
            if st["params"]:
                cur.execute(st["sql"], st["params"])
            else:
                cur.execute(st["sql"])
            ids.update(int(row[0]) for row in cur.fetchall())
    return ids


def _seekdb_returning(project_ids: tuple[int, ...]):
    """A stand-in for ``seek.seekdb.SeekDB`` whose current user belongs to exactly ``project_ids``."""
    data = [{"id": str(p), "type": "projects"} for p in project_ids]

    class ParitySeekDB:
        def __init__(self, *args, **kwargs):
            pass

        def getCurrentUser(self):
            return {"data": {"relationships": {"projects": {"data": list(data)}}}}

    return ParitySeekDB


_FACTORY = APIRequestFactory()


def call_view(body: dict, scope: Scope, *, page: int | None = None, unpaged: bool = False) -> tuple[int, list[int]]:
    """One call of advanced_search's view as ``scope``; returns the envelope's total and its row ids in order."""
    query = urlencode({"page": page, "page_size": VIEW_PAGE_SIZE}) if page else ""
    request = _FACTORY.post(ADVANCED_PATH + ("?" + query if query else ""), data=json.dumps(body),
                            content_type="application/json")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=scope.is_admin, username="gs-parity")
    request.data = body
    request.query_params = request.GET
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(advanced, "resolve_seek_auth",
                                              return_value=(("gs-parity", "gs-parity"), None)))
        stack.enter_context(mock.patch.object(advanced, "SeekDB", _seekdb_returning(scope.project_ids)))
        if unpaged:
            stack.enter_context(mock.patch.object(advanced, "paginate_rows_in_envelope",
                                                  lambda request, envelope, *args, **kwargs: envelope))
        stack.enter_context(_quiet())
        response = advanced.SampleAdvancedSearchViewSet().create(request)
    if response.status_code != 200:
        raise HarnessError(f"advanced_search answered {response.status_code}: {response.content[:300]!r}")
    data = orjson.loads(response.content)
    ids = [int(row["id"]) for row in data.get("rows") or []]
    total = int(data.get("total") or 0)
    return total, ids


def a_side_view(body: dict, scope: Scope) -> dict:
    """advanced_search's answer through its view: page 1 as a client pages, then the rest in one call."""
    start = time.perf_counter()
    total, page1 = call_view(body, scope, page=1)
    calls, notes = 1, []
    if total <= len(page1):
        ids = page1
        if len(page1) != total:
            notes.append(f"page 1 holds {len(page1)} rows for total {total}")
    else:
        total_all, ids = call_view(body, scope, unpaged=True)
        calls += 1
        if total_all != total:
            notes.append(f"total {total} on page 1, {total_all} unpaged")
        if ids[:len(page1)] != page1:
            notes.append("page 1 is not the head of the unpaged rows")
    if len(ids) != len(set(ids)):
        notes.append(f"{len(ids) - len(set(ids))} duplicate row ids")
    if len(set(ids)) != total:
        notes.append(f"{len(set(ids))} distinct ids for total {total}")
    return {"ids": ids, "total": total, "ms": _ms(start), "calls": calls, "notes": notes}


# ---------------------------------------------------------------------------------------------------------------
# Classifying a difference
# ---------------------------------------------------------------------------------------------------------------

def _declared_for(q: dict) -> int | None:
    """Declared difference 2 covers every difference of a query whose term carries PubMed syntax, when both engines
    are given that body. A query that gives graph_search the text in ``extensions.query``, or runs the Simple box's
    path, must match."""
    if q.get("engine") or q.get("graph_body"):
        return None
    terms = split_terms(q["body"].get("filter_searchText"))
    return 2 if any(_PUBMED_RE.search(t) for t in terms if not UID_RE.match(t)) else None


def evidence_for(ids: list[int], driver, db: str) -> dict[int, dict]:
    """What MySQL and the graph hold for each sample id: type, projects, uuid, metadata and search_text."""
    if not ids:
        return {}
    out: dict[int, dict] = {i: {"mysql": None, "graph": None} for i in ids}
    marks = ", ".join(["%s"] * len(ids))
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute("SELECT A.id, A.uuid, B.title, A.json_metadata FROM samples A "
                    f"LEFT JOIN sample_types B ON B.id = A.sample_type_id WHERE A.id IN ({marks})", ids)
        for sid, uuid, type_title, metadata in cur.fetchall():
            try:
                parsed = json.loads(metadata) if metadata else {}
            except (TypeError, ValueError):
                parsed = None
            out[int(sid)]["mysql"] = {"uuid": uuid, "type": type_title, "metadata": parsed, "projects": []}
        cur.execute(f"SELECT DISTINCT sample_id, project_id FROM projects_samples WHERE sample_id IN ({marks}) "
                    "ORDER BY sample_id, project_id", ids)
        for sid, project_id in cur.fetchall():
            if out[int(sid)]["mysql"] is not None:
                out[int(sid)]["mysql"]["projects"].append(int(project_id))
    records = driver.execute_query(
        "CYPHER 25 CALL () { MATCH (s:Sample) WHERE s.id IN $ids RETURN s "
        "UNION MATCH (s:OrphanSample) WHERE s.id IN $ids RETURN s } "
        "RETURN s.id AS id, labels(s) AS labels, properties(s) AS props",
        {"ids": ids}, routing_=RoutingControl.READ, database_=db).records
    for record in records:
        props = dict(record["props"])
        out[int(record["id"])]["graph"] = {"labels": sorted(record["labels"]), "props": props}
    return out


def probable_cause(side: str, ev: dict, body: dict) -> str:
    """A best guess at why one side has this sample, from what MySQL and the graph hold for it."""
    mysql, graph = ev.get("mysql"), ev.get("graph")
    if mysql is None:
        return "not in MySQL"
    if graph is None:
        return "not in the graph"
    props = graph["props"]
    if "Sample" not in graph["labels"]:
        return "in the graph only as :OrphanSample"
    if props.get("type") != mysql["type"]:
        return "the graph's type differs from MySQL's"
    if sorted(props.get("project_ids") or []) != sorted(set(mysql["projects"])):
        return "project_ids differ from projects_samples"
    terms = split_terms(body.get("filter_searchText"))
    uid_terms = [t for t in terms if UID_RE.match(t)]
    if uid_terms and mysql["uuid"] not in uid_terms:
        if (mysql["uuid"] or "").lower().rstrip(" ") in {t.lower() for t in uid_terms}:
            return "the uuid matches a UID term only under MySQL's case-insensitive, pad-space collation"
    text_terms = [t.lower() for t in terms if not UID_RE.match(t)]
    metadata = mysql["metadata"]
    if not text_terms:
        return "unexplained"
    if metadata is None:
        return "json_metadata does not parse"
    values = {k: v for k, v in metadata.items() if v is not None}
    hits = {k: v for k, v in values.items() if any(t in str(v).lower() for t in text_terms)}
    search_text = str(props.get("search_text") or "").lower()
    in_search_text = any(t in search_text for t in text_terms)
    folded: dict[str, int] = {}
    for key in metadata:
        folded[str(key).strip().lower()] = folded.get(str(key).strip().lower(), 0) + 1
    attribute = body.get("attribute")
    names = [str(a).strip().lower() for a in (attribute if isinstance(attribute, list) else [attribute])
             if a is not None and str(a).strip()]
    if side == "A" and set(hits) <= {"UID"} and hits:
        return "the term matches only the UID value, which G1 leaves out of search_text"
    if any(folded.get(n, 0) > 1 for n in names):
        return "case-variant keys: advanced_search reads the first, graph_search ORs them all"
    if any(not isinstance(v, str) for v in hits.values()):
        return "a non-string value: str() in advanced_search, raw JSON text in the graph"
    if any(_LONG_TOKEN_RE.search(str(v)) for v in hits.values()):
        return "a value token over 255 characters, which the fulltext analyzer splits"
    if str(body.get("filter_matchType", "")).upper() == "EXACT" and any("\n" in str(v) for v in hits.values()):
        return "a multi-line value, split on newlines by the EXACT rule"
    if side == "A" and not hits:
        return "the term matches a key name or JSON escape text, not a value"
    if hits and not in_search_text:
        return "a matching value is missing from search_text"
    if side == "G" and not hits and in_search_text:
        return "search_text holds text that no JSON value holds"
    return "unexplained"


def classify(result: dict, q: dict, driver, db: str, cache: dict, max_diagnose: int,
             declared: int | None) -> list[dict]:
    """One entry per differing id (up to ``max_diagnose`` per side), classified against the declared list."""
    body = q["body"]
    entries = []
    for side, ids in (("A", result["a_only"]), ("G", result["g_only"])):
        chosen = ids[:max_diagnose]
        missing = [i for i in chosen if i not in cache]
        cache.update(evidence_for(missing, driver, db))
        for sid in chosen:
            ev = cache.get(sid, {})
            mysql = ev.get("mysql") or {}
            entries.append({
                "side": side, "id": sid, "uuid": mysql.get("uuid"), "type": mysql.get("type"),
                "declared": declared, "cause": probable_cause(side, ev, body),
            })
    return entries


# ---------------------------------------------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------------------------------------------

def compat_pair(q: dict, entry: dict, args, driver, db: str, cache: dict) -> dict:
    graph_body, body, filtering = sides(q)
    scope = entry["scope"]
    aligned_ids = None
    result: dict = {"query": q["name"], "scope": entry["key"], "status": "compared", "notes": []}
    try:
        g = g_side(graph_body, scope, driver, db, args.timeout)
    except Exception as exc:  # the graph refused or timed out: the pair is unverified
        return {**result, "status": "error", "error": f"G: {type(exc).__name__}: {exc}"}
    result.update({"g_ids_n": len(g["ids"]), "g_total": g["total"], "g_ids_ms": g["ids_ms"],
                   "g_search_ms": g["search_ms"], "g_timings": g["timings"]})
    try:
        if filtering is not None:
            filters = filtering_filters(filtering)
            statements, python_stage = filtering_statements(filters, scope)
        else:
            filters = advanced_filters(body)
            statements, python_stage = engine_statements(body, filters, scope)
        start = time.perf_counter()
        sql_ids = run_statements(statements)
        result.update({"a_sql_count": len(sql_ids), "a_sql_ms": _ms(start), "a_python_stage": python_stage})
        if scope.is_admin:
            result["a_statements"] = statements
        if len(sql_ids) <= args.threshold:
            a = a_side_filtering(filters, scope) if filtering is not None else a_side_view(body, scope)
            a_ids = set(a["ids"])
            if "aligned" in a:
                aligned_ids = set(a["aligned"])
            result.update({"a_method": "view", "a_total": a["total"], "a_ms": a["ms"], "a_calls": a["calls"]})
            result["notes"] += a["notes"]
            if a["notes"]:
                result["status"] = "a_inconsistent"
            if not a_ids <= sql_ids:
                result["notes"].append(f"{len(a_ids - sql_ids)} view rows are outside the captured SQL")
                result["status"] = "a_inconsistent"
        elif not python_stage:
            a_ids = sql_ids
            result.update({"a_method": "sql", "a_total": len(sql_ids), "a_ms": result["a_sql_ms"], "a_calls": 0})
        else:
            result.update({"status": "too_broad", "a_method": None,
                           "error": f"{len(sql_ids)} SQL matches with a Python stage, over {args.threshold}"})
            return result
    except Exception as exc:
        result.update({"status": "error", "error": f"A: {type(exc).__name__}: {exc}"})
        if body is not None:
            try:  # what a client of the view would have been told
                total, _ids = call_view(body, scope, page=1)
                result["a_view_answer"] = f"200, total {total}"
            except Exception as view_exc:
                result["a_view_answer"] = str(view_exc)[:300]
        return result
    finally:
        gc.collect()

    g_ids = set(g["ids"])
    a_only, g_only = sorted(a_ids - g_ids), sorted(g_ids - a_ids)
    result.update({"a_only_n": len(a_only), "g_only_n": len(g_only),
                   "a_only": a_only[:args.max_ids], "g_only": g_only[:args.max_ids]})
    if len(g["ids"]) != len(g_ids):
        result["notes"].append(f"graph_search returned {len(g['ids']) - len(g_ids)} duplicate ids")
    if g["total"] != len(g_ids):
        result["notes"].append(f"count statement total {g['total']} differs from the ids statement's {len(g_ids)}")
    result["total_mismatch"] = g["total"] != len(g_ids) or (result.get("a_total") != len(a_ids))
    declared = _declared_for(q)
    if (a_only or g_only) and aligned_ids is not None and aligned_ids == g_ids:
        declared = 7
        result["notes"].append("without _filterSamples' index slip advanced_search's rows equal graph_search's")
    result["differences"] = classify({"a_only": a_only, "g_only": g_only}, q, driver, db, cache,
                                     args.max_diagnose, declared) if (a_only or g_only) else []
    result["undeclared_n"] = 0 if declared else len(a_only) + len(g_only)
    result["declared_n"] = len(a_only) + len(g_only) if declared else 0
    if g["total"] != len(g_ids):
        result["undeclared_n"] += 1
    return result


def graph_only_pair(q: dict, entry: dict, driver, db: str) -> dict:
    result = {"query": q["name"], "scope": entry["key"]}
    try:
        req = GraphSearchRequest.model_validate(q["body"])
        start = time.perf_counter()
        page = service.search(req, entry["scope"], 1, 100, driver=driver, db=db)
        result.update({"status": "ok", "total": page["total"], "search_ms": _ms(start), "timings": page["timings"],
                       "sample_types": page["sample_types"]})
    except Exception as exc:
        result.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return result


def read_only_check(driver, db: str) -> dict:
    """A write sent through the endpoint's READ session must be refused, whichever way it is sent."""
    attempts = {
        "plan_lambda": lambda session: session.execute_read(lambda tx: tx.run("CREATE (:Probe)")),
        "service_read": lambda session: service._read(session, "CYPHER 25 CREATE (:Probe)", {},
                                                      lambda result: result.consume(), 30),
    }
    out: dict = {}
    for name, attempt in attempts.items():
        with driver.session(database=db, default_access_mode=READ_ACCESS) as session:
            try:
                attempt(session)
                out[name] = {"refused": False}
            except Neo4jError as exc:
                out[name] = {"refused": True, "code": exc.code, "message": (exc.message or "")[:200]}
            except Exception as exc:  # a driver-side refusal still refuses the write
                out[name] = {"refused": True, "code": type(exc).__name__, "message": str(exc)[:200]}
    records = driver.execute_query("CYPHER 25 MATCH (p:Probe) RETURN count(p) AS n",
                                   routing_=RoutingControl.READ, database_=db).records
    out["probe_nodes_after"] = records[0]["n"]
    out["refused"] = all(v["refused"] for k, v in out.items() if isinstance(v, dict)) and out["probe_nodes_after"] == 0
    return out


# ---------------------------------------------------------------------------------------------------------------
# Progress, summary and report
# ---------------------------------------------------------------------------------------------------------------

class Progress:
    """progress.jsonl: a start line before each compat pair and a done line after it, so a kill is visible."""

    def __init__(self, path: Path, resume: bool):
        self.path = path
        self.done: dict[tuple, dict] = {}
        self.killed: set[tuple] = set()
        if resume and path.exists():
            started = set()
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                key = (event["kind"], event["query"], event["scope"])
                if event["event"] == "start":
                    started.add(key)
                else:
                    self.done[key] = event["result"]
            self.killed = started - set(self.done)
        elif path.exists():
            path.unlink()

    def write(self, event: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def summarize(compat: list[dict], graph_only: list[dict], read_only: dict) -> dict:
    compared = [r for r in compat if r["status"] == "compared"]
    unverified = [r for r in compat if r["status"] != "compared"]
    undeclared = sum(r.get("undeclared_n", 0) for r in compared)
    declared = sum(r.get("declared_n", 0) for r in compared)
    return {
        "compat_pairs": len(compat),
        "compared": len(compared),
        "equal": sum(1 for r in compared if not r["a_only_n"] and not r["g_only_n"] and not r["total_mismatch"]),
        "undeclared_differences": undeclared,
        "declared_differences": declared,
        "unverified": len(unverified),
        "unverified_by_status": {s: sum(1 for r in unverified if r["status"] == s)
                                 for s in sorted({r["status"] for r in unverified})},
        "graph_only_pairs": len(graph_only),
        "graph_only_errors": sum(1 for r in graph_only if r["status"] != "ok"),
        "read_only_refused": bool(read_only.get("refused")),
        "gate_pass": undeclared == 0 and not unverified and bool(compat) and bool(read_only.get("refused")),
    }


def _fmt_ms(value) -> str:
    return "" if value is None else f"{value:,.0f}"


def render_md(report: dict) -> str:
    s = report["summary"]
    names = {e["key"]: ", ".join(e["names"]) for e in report["scopes"]}
    lines = [
        "# graph_search parity (gate E)",
        "",
        f"- Generated {report['generated_at']}; queries sha256 `{report['config']['queries_sha256'][:16]}`.",
        f"- Graph: schema {report['graph'].get('schema_version')}, catalog hash "
        f"`{str(report['graph'].get('catalog_hash'))[:16]}`, {report['graph'].get('samples'):,} samples.",
        f"- **Gate E: {'PASS' if s['gate_pass'] else 'FAIL'}.** {s['compared']} of {s['compat_pairs']} compat pairs "
        f"compared, {s['equal']} equal; {s['undeclared_differences']} undeclared and {s['declared_differences']} "
        f"declared differences; {s['unverified']} unverified"
        + (f" {s['unverified_by_status']}" if s["unverified_by_status"] else "")
        + f"; write refused: {s['read_only_refused']}.",
        f"- A side: the view up to {report['config']['threshold']:,} SQL matches, the id-only SQL above that for "
        "shapes with no Python stage.",
        "",
        "## Read-only check",
        "",
        "| Attempt | Refused | Code |",
        "|---|---|---|",
    ]
    for name in ("plan_lambda", "service_read"):
        r = report["read_only"].get(name, {})
        lines.append(f"| {name} | {r.get('refused')} | `{r.get('code', '')}` |")
    lines += [f"\nProbe nodes after: {report['read_only'].get('probe_nodes_after')}.", "", "## Scopes", "",
              "| Scope | People | Names |", "|---|---|---|"]
    for e in report["scopes"]:
        lines.append(f"| `{e['key']}` | {'' if e['persons'] is None else e['persons']} | {', '.join(e['names'])} |")

    lines += ["", "## Compat queries, all scopes", "",
              "| Query | Pairs | Equal | A only | G only | Undeclared | Unverified | Superuser A / G |",
              "|---|---|---|---|---|---|---|---|"]
    by_query: dict[str, list[dict]] = {}
    for r in report["compat"]:
        by_query.setdefault(r["query"], []).append(r)
    for name, rows in by_query.items():
        admin = next((r for r in rows if r["scope"] == ADMIN_KEY), {})
        cmp_rows = [r for r in rows if r["status"] == "compared"]
        lines.append(
            f"| {name} | {len(rows)} | "
            f"{sum(1 for r in cmp_rows if not r['a_only_n'] and not r['g_only_n'] and not r['total_mismatch'])} | "
            f"{sum(r['a_only_n'] for r in cmp_rows)} | {sum(r['g_only_n'] for r in cmp_rows)} | "
            f"{sum(r['undeclared_n'] for r in cmp_rows)} | {len(rows) - len(cmp_rows)} | "
            f"{admin.get('a_total', '')} / {admin.get('g_total', '')} |")

    lines += ["", "## Compat queries, named scopes", "",
              "| Query | Scope | A method | A total | G total | A ms | G ids ms | G page / count ms | Status |",
              "|---|---|---|---|---|---|---|---|---|"]
    named_keys = [e["key"] for e in report["scopes"] if e["names"]]
    for r in report["compat"]:
        if r["scope"] not in named_keys:
            continue
        t = r.get("g_timings") or {}
        lines.append(
            f"| {r['query']} | {names.get(r['scope'], r['scope'])} | {r.get('a_method') or ''} | "
            f"{r.get('a_total', '')} | {r.get('g_total', '')} | {_fmt_ms(r.get('a_ms'))} | "
            f"{_fmt_ms(r.get('g_ids_ms'))} | {_fmt_ms(t.get('cypher_ms'))} / {_fmt_ms(t.get('count_ms'))} | "
            f"{r['status']} |")

    problems = [r for r in report["compat"] if r["status"] != "compared" or r.get("differences") or r.get("notes")]
    lines += ["", "## Differences, notes and unverified pairs", ""]
    if not problems:
        lines.append("None.")
    for r in problems:
        lines.append(f"- **{r['query']}** as `{r['scope']}`: {r['status']}"
                     + (f", A only {r.get('a_only_n')}, G only {r.get('g_only_n')}" if r["status"] == "compared" else "")
                     + (f"; {r['error']}" if r.get("error") else "")
                     + (f"; the view answered {r['a_view_answer']}" if r.get("a_view_answer") else "")
                     + (f"; notes: {'; '.join(r['notes'])}" if r.get("notes") else ""))
        causes: dict[tuple, int] = {}
        for d in r.get("differences") or []:
            k = (d["side"], d["cause"], d["declared"])
            causes[k] = causes.get(k, 0) + 1
        for (side, cause, declared), n in sorted(causes.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - {side} only, {n} diagnosed: {cause} "
                         f"({'declared ' + str(declared) if declared else 'undeclared'})")

    lines += ["", "## Graph-only queries", "", "| Query | Scope | Total | Page ms | Count ms | Status |",
              "|---|---|---|---|---|---|"]
    for r in report["graph_only"]:
        if r["scope"] not in named_keys:
            continue
        t = r.get("timings") or {}
        lines.append(f"| {r['query']} | {names.get(r['scope'], r['scope'])} | {r.get('total', '')} | "
                     f"{_fmt_ms(t.get('cypher_ms'))} | {_fmt_ms(t.get('count_ms'))} | "
                     f"{r['status']}{': ' + r['error'] if r.get('error') else ''} |")
    others = [r for r in report["graph_only"] if r["scope"] not in named_keys]
    if others:
        lines.append(f"\nThe other {len(others)} scope pairs are in parity.json "
                     f"({sum(1 for r in others if r['status'] != 'ok')} errors).")
    lines += ["", "## Declared differences (design section 7)", ""]
    lines += [f"{n}. {text}" for n, text in DECLARED.items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    p.add_argument("--out-dir", type=Path,
                   default=Path(os.environ.get("GS_RUN_DIR", "/gswork/runs")) / "E6")
    p.add_argument("--only", nargs="*", default=None, help="query names to run (default: all)")
    p.add_argument("--scopes", nargs="*", default=None,
                   help="scope keys (admin, projects:2,3) or account names (tcgamember, user); default: all")
    p.add_argument("--threshold", type=int, default=50_000, help="largest SQL match count sent through the view")
    p.add_argument("--timeout", type=float, default=240, help="seconds for graph_search's ids statement")
    p.add_argument("--max-ids", type=int, default=200, help="differing ids kept per side and pair")
    p.add_argument("--max-diagnose", type=int, default=25, help="differing ids diagnosed per side and pair")
    p.add_argument("--resume", action="store_true", help="reuse finished pairs in progress.jsonl")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = args.queries.read_bytes()
    queries = json.loads(raw)
    if args.only:
        unknown = set(args.only) - {q["name"] for q in queries}
        if unknown:
            raise SystemExit(f"unknown query names: {sorted(unknown)}")
        queries = [q for q in queries if q["name"] in args.only]

    driver, db = _neo4j()
    meta = graph_meta(driver, db)
    _log(f"graph {meta}")
    scopes = build_scopes()
    if args.scopes:
        wanted = set(args.scopes)
        scopes = [e for e in scopes if e["key"] in wanted or wanted & set(e["names"])]
    _log(f"{len(scopes)} scopes: {', '.join(e['key'] for e in scopes)}")

    progress = Progress(args.out_dir / "progress.jsonl", args.resume)
    read_only = read_only_check(driver, db)
    _log(f"read-only check: refused={read_only['refused']}")

    compat_queries = [q for q in queries if q["kind"] == "compat"]
    compat: list[dict] = []
    cache: dict = {}
    n_pairs = len(compat_queries) * len(scopes)
    i = 0
    for q in compat_queries:
        for entry in scopes:
            i += 1
            key = ("compat", q["name"], entry["key"])
            if key in progress.done:
                compat.append(progress.done[key])
                continue
            if key in progress.killed:
                result = {"query": q["name"], "scope": entry["key"], "status": "killed", "notes": [],
                          "error": "the process stopped during this pair (the lane's memory cap or a crash); "
                                   "not retried"}
                progress.write({"event": "done", "kind": "compat", "query": q["name"], "scope": entry["key"],
                                "result": result})
                compat.append(result)
                _log(f"[{i}/{n_pairs}] {q['name']} {entry['key']}: killed in an earlier run, not retried")
                continue
            progress.write({"event": "start", "kind": "compat", "query": q["name"], "scope": entry["key"],
                            "at": _now()})
            result = compat_pair(q, entry, args, driver, db, cache)
            result["memory"] = _memory()
            progress.write({"event": "done", "kind": "compat", "query": q["name"], "scope": entry["key"],
                            "result": result})
            compat.append(result)
            _log(f"[{i}/{n_pairs}] {q['name']} {entry['key']}: {result['status']} "
                 f"A={result.get('a_total')} ({result.get('a_method')}, {_fmt_ms(result.get('a_ms'))} ms) "
                 f"G={result.get('g_total')} ({_fmt_ms(result.get('g_ids_ms'))} ms) "
                 f"A-only={result.get('a_only_n')} G-only={result.get('g_only_n')} "
                 f"rss_peak={result['memory'].get('rss_peak_mb')} MiB {result.get('error', '')}")

    graph_only = []
    for q in (q for q in queries if q["kind"] == "graph_only"):
        for entry in scopes:
            result = graph_only_pair(q, entry, driver, db)
            graph_only.append(result)
            _log(f"graph_only {q['name']} {entry['key']}: {result['status']} total={result.get('total')} "
                 f"{result.get('error', '')}")

    report = {
        "generated_at": _now(),
        "config": {"threshold": args.threshold, "timeout": args.timeout, "view_page_size": VIEW_PAGE_SIZE,
                   "queries": str(args.queries), "queries_sha256": hashlib.sha256(raw).hexdigest(),
                   "only": args.only, "scopes": args.scopes},
        "graph": meta,
        "declared": DECLARED,
        "scopes": [{"key": e["key"], "is_admin": e["scope"].is_admin, "project_ids": list(e["scope"].project_ids),
                    "persons": e["persons"], "names": e["names"], "accounts": e.get("accounts", [])}
                   for e in scopes],
        "read_only": read_only,
        "compat": compat,
        "graph_only": graph_only,
        "memory": _memory(),
    }
    report["summary"] = summarize(compat, graph_only, read_only)
    (args.out_dir / "parity.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (args.out_dir / "parity.md").write_text(render_md(report), encoding="utf-8")
    _log(f"summary {report['summary']}")
    return 0 if report["summary"]["gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
