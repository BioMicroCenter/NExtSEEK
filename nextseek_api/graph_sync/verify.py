"""Gate G: check a graph_sync build against MySQL (the design, section 6; the plan, task G5; checks 9 to 11: the sync
design, section 13 and CI-9). Read-only.

``gate_g`` returns ``{"checks": [{"name", "expected", "actual", "pass"}, ...], "pass": bool, "stats": {...}}``. A
check either counts violations (expected 0) or compares a graph number with its MySQL number; ``detail``, when
present, carries a few examples. Each name starts with the gate G check it belongs to:

1. ``lineage``: every DERIVED_FROM pair MySQL's parent tokens declare exists between the two Sample nodes. An
   undeclared pair between two Sample nodes fails; one touching an OrphanSample is only counted.
2. ``scope``: per project, the Samples whose ``project_ids`` hold it equal the distinct ``projects_samples``
   count; every Sample carries ``project_ids``; for the random samples, ``project_ids`` equals the MySQL list.
3. ``catalog``: every property key on a ``T_X`` node, system keys excluded, is the title of an Attribute on
   SampleType X: over the random samples, and in one aggregate per type label.
4. ``samples``: the Sample count and the OF_TYPE count equal MySQL's sample count; every Sample has exactly one
   ``T_`` label and one OF_TYPE, and the label is its SampleType's.
5. ``attributes``: the Attribute nodes with an ``id`` are ``sample_attributes`` by id, titles byte-exact.
6. ``scope`` (people): for every person with a membership, the samples the graph shows them equal the SQL
   ``EXISTS projects_samples`` count. The named accounts are resolved by graph_search's own scope resolver and
   counted with the endpoint's Cypher predicate.
7. ``metadata``: for the random samples, the node's properties minus system keys equal the projection of the
   sample's ``json_metadata`` (canonical JSON; a date is ``{"$date": "<ISO date>"}``, so a date stored as a
   string does not pass for one).
8. ``schema``, ``catalog``, ``graphmeta``: every v1.1 constraint and index exists and every index is ONLINE; the
   catalog builds with no label collision in MySQL or the graph; no SampleType lacks ``id`` or ``label``; one
   GraphMeta node, at the writer's schema version.
9. ``lineage.labels``: every declared DERIVED_FROM between two Sample nodes is compared with batch upload's label
   rule fed from MySQL (``labels.edge_labels``) and classified (``labels.classify``). It fails on an edge whose
   endpoints share an assay the rule resolves and whose three singular assay fields are all null: the gap that got
   past this gate and gate E. A label that differs from the rule (``changed``, ``cleared``) and one lacking only the
   plural lists (``plural_missing``) are reported, never failed (R14). An undeclared edge is check 1's.
10. ``samples``: no node carries a ``T_`` label without ``:Sample`` (an OrphanSample keeps none: the deletion rule).
11. ``samples.parent_lists``: for the random samples, ``parent_titles`` and ``parent_title_hashes`` equal batch
    upload's rule over the sample's parent tokens (``projection.parent_lists``), a UID parent named by its stored
    identity; a node without them fails.

Sized for about 1.08M samples: every full-graph read returns a few rows (the two scope checks share one scan of
``project_ids`` grouped by value), the key aggregate runs one type label per transaction, and the lineage check
streams the edges against a set of encoded MySQL pairs. Check 9 streams them once more with their seven label
properties and classifies each as it arrives; it holds one assay-id tuple per lineage endpoint (equal tuples shared)
and one resolved protocol per child that names one.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
import random
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from django.conf import settings
from django.db import connections

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.graph_search.scope import ScopeUnavailable, resolve_scope
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import labels, run, sources, writer
from nextseek_api.graph_sync.projection import SYSTEM_KEYS, label_for, parent_lists, project_sample
from nextseek_api.graph_sync.writer import _one, _records, _run

log = logging.getLogger(__name__)

SAMPLE_SIZE = 1_000
# The gating accounts of the merged dataset (design, section 4): a TCGA member and a non-member control.
GATE_ACCOUNTS = ("tcgamember", "user")
EXAMPLES = 10
SAMPLED_BATCH = 1_000
LABEL_RULE_CACHE = 100_000   # distinct (child assays, parent assays, protocol) inputs whose labels check 9 keeps

_NAME_RE = re.compile(r"CREATE (CONSTRAINT|INDEX) (\w+)")
EXPECTED_CONSTRAINTS = tuple(m.group(2) for m in map(_NAME_RE.match, q.CONSTRAINTS_V11)
                             if m and m.group(1) == "CONSTRAINT")
EXPECTED_INDEXES = tuple(m.group(2) for m in map(_NAME_RE.match, q.CONSTRAINTS_V11)
                         if m and m.group(1) == "INDEX") + (q.FULLTEXT_INDEX,)
_LABEL_RE = re.compile(r"T_[A-Za-z0-9_]+")
# "Protocol" and "protocol" both end in this as JSON keys. A child's metadata without it names no protocol, which
# spares parsing most children's metadata a second time.
_PROTOCOL_KEY_TAIL = 'rotocol"'

# --- statements ----------------------------------------------------------------------------------

LINEAGE_PAIRS = "MATCH (c:Sample)-[:DERIVED_FROM]->(p:Sample) RETURN c.id AS child, p.id AS parent"
LINEAGE_ON_ORPHANS = "MATCH (:OrphanSample)-[e:DERIVED_FROM]-() RETURN count(DISTINCT e) AS n"
# The same edges with their seven label properties, null when absent.
LINEAGE_LABELS = ("MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)\n"
                  "RETURN c.id AS child, p.id AS parent, e {"
                  + ", ".join("." + key for key in q.EDGE_LABEL_KEYS) + "} AS stored")
PROJECT_ID_GROUPS = "MATCH (s:Sample) RETURN s.project_ids AS project_ids, count(*) AS n"
# graph_search's scope clause, as the endpoint sends it.
ACCOUNT_SCOPE_COUNT = """
MATCH (s:Sample) WHERE any(p IN s.project_ids WHERE p IN $projects)
RETURN count(s) AS n
"""
GRAPH_CATALOG = """
MATCH (t:SampleType)
RETURN t.id AS id, t.title AS title, t.label AS label,
       [(t)-[:HAS_ATTRIBUTE]->(a:Attribute) | a.title] AS titles
"""
# {label} is a SampleType label checked against the T_ rule before it is formatted in.
TYPE_KEYS = """
MATCH (s:Sample:`{label}`)
UNWIND keys(s) AS k
WITH DISTINCT k WHERE NOT k IN $system
RETURN collect(k) AS keys
"""
SAMPLED_NODES = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})
RETURN s.id AS id, properties(s) AS props, [l IN labels(s) WHERE l STARTS WITH 'T_'] AS type_labels
"""
SAMPLE_COUNT = "MATCH (s:Sample) RETURN count(s) AS n"
OF_TYPE_COUNT = "MATCH (:Sample)-[r:OF_TYPE]->() RETURN count(r) AS n"
TYPE_LABEL_AUDIT = """
MATCH (s:Sample)
WITH [l IN labels(s) WHERE l STARTS WITH 'T_'] AS type_labels,
     [(s)-[:OF_TYPE]->(t:SampleType) | t.label] AS of_type_labels
RETURN count(*) AS samples,
       sum(CASE WHEN size(type_labels) <> 1 THEN 1 ELSE 0 END) AS not_one_type_label,
       sum(CASE WHEN size(of_type_labels) <> 1 THEN 1 ELSE 0 END) AS not_one_of_type,
       sum(CASE WHEN size(type_labels) = 1 AND size(of_type_labels) = 1
                     AND type_labels[0] <> of_type_labels[0] THEN 1 ELSE 0 END) AS label_differs,
       collect(DISTINCT type_labels) AS label_sets
"""
GRAPH_ATTRIBUTE_IDS = """
MATCH (a:Attribute) WHERE a.id IS NOT NULL
RETURN a.id AS id, a.title AS title, a.sample_type_id AS sample_type_id
"""
CONSTRAINT_NAMES = "SHOW CONSTRAINTS YIELD name RETURN name"
LABEL_COLLISIONS = """
MATCH (t:SampleType) WHERE t.label IS NOT NULL
WITH t.label AS label, count(*) AS n WHERE n > 1
RETURN count(*) AS n
"""
SAMPLE_TYPES_WITHOUT_ID_OR_LABEL = "MATCH (t:SampleType) WHERE t.id IS NULL OR t.label IS NULL RETURN count(t) AS n"
GRAPHMETA = "MATCH (m:GraphMeta) RETURN m.schema_version AS schema_version"
# Nodes carrying a type label without :Sample: an OrphanSample that kept one, or a node nothing should have typed.
T_LABEL_WITHOUT_SAMPLE = """
MATCH (n) WHERE NOT n:Sample AND any(l IN labels(n) WHERE l STARTS WITH 'T_')
RETURN count(n) AS n
"""
T_LABEL_WITHOUT_SAMPLE_EXAMPLES = """
MATCH (n) WHERE NOT n:Sample AND any(l IN labels(n) WHERE l STARTS WITH 'T_')
RETURN n.id AS id, labels(n) AS labels
LIMIT $limit
"""

# advanced_search's scope rule in SQL.
_EXISTS_SQL = ("SELECT COUNT(*) FROM samples s WHERE EXISTS (SELECT 1 FROM projects_samples ps "
               "WHERE ps.sample_id = s.id AND ps.project_id IN ({placeholders}))")


# --- seams ---------------------------------------------------------------------------------------

def _sql_scope_count(project_ids) -> int:
    """Samples with a ``projects_samples`` row in any of ``project_ids`` (0 for none)."""
    ids = sorted({int(p) for p in project_ids})
    if not ids:
        return 0
    sql = _EXISTS_SQL.format(placeholders=", ".join(["%s"] * len(ids)))
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, ids)
        return int(cursor.fetchone()[0])


def _account_scope(login: str):
    """graph_search's scope for ``login`` as a non-superuser: its project ids, or None with no SEEK person."""
    try:
        return resolve_scope(SimpleNamespace(username=login, is_superuser=False)).project_ids
    except ScopeUnavailable:
        return None


# --- plumbing ------------------------------------------------------------------------------------

def _read(driver, db, query, params=None, transformer=None):
    return _run(driver, db, query, params, read=True, transformer=transformer)


def _timed(timings: dict, name: str, fn, *args, **kwargs):
    log.info("gate G: %s", name)
    started = time.monotonic()
    out = fn(*args, **kwargs)
    timings[name] = round(time.monotonic() - started, 1)
    return out


def _check(checks: list, name: str, expected, actual, passed=None, detail=None) -> None:
    entry = {"name": name, "expected": expected, "actual": actual,
             "pass": bool(expected == actual if passed is None else passed)}
    if detail:
        entry["detail"] = detail
    checks.append(entry)


def _sort_key(value):
    return (type(value).__name__, value)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plain(value):
    """A stored value as JSON: neo4j temporal types to native, dates tagged so a string cannot pass for one."""
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        value = to_native()
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _metadata(props: dict) -> dict:
    return {k: _plain(v) for k, v in props.items() if k not in SYSTEM_KEYS}


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _metadata_object(raw) -> dict:
    """``json_metadata`` as a dict; unreadable or non-object metadata reads as empty, as batch upload reads it."""
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


# --- the MySQL side ------------------------------------------------------------------------------

@dataclass
class _MySQLSide:
    count: int = 0
    ids: set = field(default_factory=set)
    lineage: set = field(default_factory=set)      # run.encode_pair codes
    sampled: list = field(default_factory=list)    # a uniform random sample of rows, ordered by id
    projects: dict = field(default_factory=dict)   # sample id to its sorted distinct project ids
    protocols: dict = field(default_factory=dict)  # child id to its resolved (protocol id, title), when it has one


def _protocol_reader(sops: dict):
    """``protocol_of(json_metadata)``: a child's resolved ``(protocol_id, protocol_title)``, or None for none.

    Batch upload's rule (``labels.protocol_value_of``, then ``labels.resolve_protocol`` against ``sops``), resolved
    once per distinct value: ``parse_protocol_value`` reads a value as its stripped text, so that text keys the cache.
    """
    by_title = labels.sop_title_index(sops)
    resolved: dict = {}

    def protocol_of(raw):
        if not raw or (isinstance(raw, str) and _PROTOCOL_KEY_TAIL not in raw):
            return None
        value = labels.protocol_value_of(raw)
        text = str(value).strip()
        if not text:
            return None
        if text not in resolved:
            pair = labels.resolve_protocol(value, sops, by_title)
            resolved[text] = None if pair == (None, None) else pair
        return resolved[text]

    return protocol_of


def _scan_mysql(chunk: int, sample_size: int, rng: random.Random, protocol_of=None) -> _MySQLSide:
    """One pass over MySQL's samples: the count, the ids, the declared lineage, each child's resolved protocol (with
    ``protocol_of``) and a reservoir sample."""
    side = _MySQLSide(projects=sources.sample_projects())
    uuid_index = sources.uuid_to_ids()
    for page in sources.iter_samples(chunk=chunk):
        children = set()
        for child, parent in sources.declared_lineage(page, uuid_index):
            side.lineage.add(run.encode_pair(child, parent))
            children.add(child)
        for row in page:
            if protocol_of is not None and row["id"] in children:
                protocol = protocol_of(row["json_metadata"])
                if protocol is not None:
                    side.protocols[row["id"]] = protocol
            side.ids.add(row["id"])
            if side.count < sample_size:
                side.sampled.append(row)
            else:
                slot = rng.randrange(side.count + 1)
                if slot < sample_size:
                    side.sampled[slot] = row
            side.count += 1
    side.sampled.sort(key=lambda r: r["id"])
    return side


def _endpoint_assays(lineage: set) -> dict:
    """Each lineage endpoint's SEEK assay ids (``assay_assets``) as a sorted tuple, one tuple object shared by every
    sample holding the same assays. A sample with no assay link is absent."""
    endpoints = set()
    for code in lineage:
        child, parent = run.decode_pair(code)
        endpoints.add(child)
        endpoints.add(parent)
    shared: dict = {}
    assays = {}
    for sample_id, ids in sources.sample_assay_ids_for(endpoints).items():
        key = tuple(ids)
        assays[sample_id] = shared.setdefault(key, key)
    return assays


# --- graph reads shared by several checks --------------------------------------------------------

def _sampled_nodes(driver, db, ids: list) -> dict:
    nodes = {}
    for start in range(0, len(ids), SAMPLED_BATCH):
        for record in _records(_read(driver, db, SAMPLED_NODES, {"ids": ids[start:start + SAMPLED_BATCH]})):
            nodes[record["id"]] = {"props": dict(record["props"] or {}),
                                   "type_labels": list(record["type_labels"] or [])}
    return nodes


def _project_groups(driver, db) -> list:
    """(project_ids as a tuple, or None when absent or not a list; sample count) per distinct value."""
    groups = []
    for record in _records(_read(driver, db, PROJECT_ID_GROUPS)):
        value = record["project_ids"]
        groups.append((tuple(value) if isinstance(value, (list, tuple)) else None, int(record["n"])))
    return groups


def _group_count(groups: list, scope: frozenset) -> int:
    return sum(n for pids, n in groups if pids and not scope.isdisjoint(pids))


# --- the checks ----------------------------------------------------------------------------------

def _check_lineage(driver, db, mysql: _MySQLSide, checks: list, stats: dict) -> None:
    declared = mysql.lineage

    def compare(result):
        remaining = set(declared)  # built here, so a retried read starts clean
        edges = extra = 0
        examples = []
        for record in result:
            edges += 1
            child, parent = record["child"], record["parent"]
            code = None
            if _is_id(child) and _is_id(parent):
                try:
                    code = run.encode_pair(child, parent)
                except ValueError:
                    code = None
            if code is not None and code in declared:
                remaining.discard(code)
            else:
                extra += 1
                if len(examples) < EXAMPLES:
                    examples.append([child, parent])
        return edges, extra, examples, remaining

    edges, extra, extra_examples, remaining = _read(driver, db, LINEAGE_PAIRS, transformer=compare)
    on_orphans = _one(_read(driver, db, LINEAGE_ON_ORPHANS), "n")
    stats.update(lineage_declared_pairs=len(declared), lineage_edges_between_samples=edges,
                 lineage_edges_touching_orphans=on_orphans)
    missing = [list(run.decode_pair(code)) for code in heapq.nsmallest(EXAMPLES, remaining)]
    _check(checks, "1.lineage.declared_pairs_missing", 0, len(remaining), detail=missing)
    _check(checks, "1.lineage.undeclared_pairs_between_samples", 0, extra, detail=extra_examples)
    _check(checks, "1.lineage.pairs_touching_orphans", "any", on_orphans, passed=True)


def _check_scope(mysql: _MySQLSide, groups: list, sampled: dict, checks: list, stats: dict) -> None:
    graph_counts: Counter = Counter()
    without = 0
    for pids, n in groups:
        if pids is None:
            without += n
            continue
        for pid in set(pids):
            graph_counts[pid] += n
    mysql_counts: Counter = Counter()
    for sample_id, pids in mysql.projects.items():
        if sample_id in mysql.ids:
            for pid in set(pids):
                mysql_counts[pid] += 1
    per_project = {p: {"mysql": mysql_counts[p], "graph": graph_counts[p]}
                   for p in sorted(set(mysql_counts) | set(graph_counts), key=_sort_key)}
    stats["projects"] = {str(p): counts for p, counts in per_project.items()}
    mismatched = [{"project_id": p, **counts} for p, counts in per_project.items()
                  if counts["mysql"] != counts["graph"]]
    _check(checks, "2.scope.projects_with_count_mismatch", 0, len(mismatched), detail=mismatched[:EXAMPLES])
    _check(checks, "2.scope.samples_without_project_ids", 0, without)

    wrong = []
    for row in mysql.sampled:
        node = sampled.get(row["id"])
        if node is None:
            continue  # counted by 7.metadata.sampled_missing_in_graph
        graph = node["props"].get("project_ids")
        expected = sorted(set(mysql.projects.get(row["id"], ())))
        if not isinstance(graph, (list, tuple)) or list(graph) != expected:
            wrong.append({"id": row["id"], "mysql": expected, "graph": graph})
    _check(checks, "2.scope.sampled_project_ids_mismatch", 0, len(wrong), detail=wrong[:EXAMPLES])


def _check_catalog(driver, db, graph_catalog: list, audit: dict, sampled: dict, checks: list, stats: dict) -> None:
    titles_by_label: dict[str, set] = defaultdict(set)
    for record in graph_catalog:
        if record["label"] is not None:
            titles_by_label[record["label"]].update(t for t in (record["titles"] or []) if t is not None)

    unlisted_on_samples = []
    for sample_id in sorted(sampled):
        node = sampled[sample_id]
        if len(node["type_labels"]) != 1:
            continue  # counted by 4.samples.not_exactly_one_type_label
        label = node["type_labels"][0]
        unlisted = sorted(set(node["props"]) - SYSTEM_KEYS - titles_by_label.get(label, set()))
        if unlisted:
            unlisted_on_samples.append({"id": sample_id, "label": label, "keys": unlisted[:EXAMPLES]})
    _check(checks, "3.catalog.sampled_samples_with_unlisted_keys", 0, len(unlisted_on_samples),
           detail=unlisted_on_samples[:EXAMPLES])

    unlisted_by_type: dict[str, list] = {}
    total = 0
    for label in sorted(titles_by_label):
        if not _LABEL_RE.fullmatch(label):
            unlisted_by_type[label] = ["(label outside the T_ rule; not scanned)"]
            continue
        log.info("gate G: keys on %s", label)
        keys = _one(_read(driver, db, TYPE_KEYS.format(label=label), {"system": sorted(SYSTEM_KEYS)}), "keys", [])
        unlisted = sorted(set(keys) - titles_by_label[label])
        if unlisted:
            unlisted_by_type[label] = unlisted[:EXAMPLES]
            total += len(unlisted)
    stats["catalog_unlisted_keys"] = total
    _check(checks, "3.catalog.types_with_unlisted_keys", 0, len(unlisted_by_type),
           detail=dict(list(unlisted_by_type.items())[:EXAMPLES]))

    on_samples = {label for labels in (audit.get("label_sets") or []) for label in labels}
    without_type = sorted(on_samples - set(titles_by_label))
    _check(checks, "3.catalog.type_labels_without_sample_type", 0, len(without_type),
           detail=without_type[:EXAMPLES])


def _check_samples(driver, db, mysql: _MySQLSide, audit: dict, checks: list) -> None:
    _check(checks, "4.samples.graph_count", mysql.count, _one(_read(driver, db, SAMPLE_COUNT), "n"))
    _check(checks, "4.samples.of_type_count", mysql.count, _one(_read(driver, db, OF_TYPE_COUNT), "n"))
    _check(checks, "4.samples.not_exactly_one_type_label", 0, audit.get("not_one_type_label") or 0)
    _check(checks, "4.samples.not_exactly_one_of_type", 0, audit.get("not_one_of_type") or 0)
    _check(checks, "4.samples.type_label_differs_from_sample_type", 0, audit.get("label_differs") or 0)


def _check_attributes(driver, db, checks: list, stats: dict) -> None:
    mysql = {int(a["id"]): a for a in sources.sample_attributes()}
    graph = {r["id"]: r for r in _records(_read(driver, db, GRAPH_ATTRIBUTE_IDS))}
    missing = sorted(set(mysql) - set(graph), key=_sort_key)
    extra = sorted(set(graph) - set(mysql), key=_sort_key)
    differ = [{"id": i, "mysql": [mysql[i]["sample_type_id"], mysql[i]["title"]],
               "graph": [graph[i]["sample_type_id"], graph[i]["title"]]}
              for i in sorted(set(mysql) & set(graph))
              if graph[i]["title"] != mysql[i]["title"] or graph[i]["sample_type_id"] != mysql[i]["sample_type_id"]]
    stats.update(attributes_mysql=len(mysql), attributes_graph_with_id=len(graph))
    _check(checks, "5.attributes.missing_in_graph", 0, len(missing), detail=missing[:EXAMPLES])
    _check(checks, "5.attributes.not_in_mysql", 0, len(extra), detail=extra[:EXAMPLES])
    _check(checks, "5.attributes.title_or_type_differs", 0, len(differ), detail=differ[:EXAMPLES])


def _check_people(driver, db, groups: list, accounts, checks: list, stats: dict) -> None:
    people: dict[int, set] = defaultdict(set)
    for membership in sources.memberships():
        people[int(membership["person_id"])].add(int(membership["project_id"]))
    account_scopes = {login: _account_scope(login) for login in accounts}
    scopes: Counter = Counter(frozenset(pids) for pids in people.values())
    for scope in account_scopes.values():
        if scope is not None:
            scopes[frozenset(scope)] += 0
    sql_counts, mismatched = {}, []
    for scope in sorted(scopes, key=sorted):
        sql_counts[scope] = _sql_scope_count(scope)
        graph_n = _group_count(groups, scope)
        if graph_n != sql_counts[scope]:
            mismatched.append({"projects": sorted(scope), "people": scopes[scope], "mysql": sql_counts[scope],
                               "graph": graph_n})
    stats.update(people_with_membership=len(people), distinct_scopes=len(scopes))
    _check(checks, "6.scope.person_scopes_mismatched", 0, len(mismatched), detail=mismatched[:EXAMPLES])

    for login, scope in account_scopes.items():
        name = f"6.scope.account.{login}"
        if scope is None:
            _check(checks, name, "a SEEK person", "none", passed=False)
            continue
        projects = sorted(scope)
        graph_n = _one(_read(driver, db, ACCOUNT_SCOPE_COUNT, {"projects": projects}), "n") if projects else 0
        _check(checks, name, sql_counts[frozenset(scope)], graph_n, detail={"projects": projects})


def _check_metadata(cat, catalog_error, mysql: _MySQLSide, sampled: dict, checks: list, stats: dict) -> None:
    missing = [row["id"] for row in mysql.sampled if row["id"] not in sampled]
    _check(checks, "7.metadata.sampled_missing_in_graph", 0, len(missing), detail=missing[:EXAMPLES])
    if cat is None:
        _check(checks, "7.metadata.sampled_mismatched", 0, "not compared: the catalog does not build",
               passed=False, detail=catalog_error)
        return
    mysql_hash, graph_hash = hashlib.sha256(), hashlib.sha256()
    compared, wrong = 0, []
    for row in mysql.sampled:
        node = sampled.get(row["id"])
        if node is None:
            continue
        type_id = row["sample_type_id"]
        try:
            proj = project_sample(row, cat.type_titles[type_id], cat.value_types.get(type_id, {}),
                                  mysql.projects.get(row["id"], ()))
        except (KeyError, ValueError, TypeError) as exc:
            wrong.append({"id": row["id"], "error": f"cannot project: {exc}"})
            continue
        want, got = _metadata(proj.props), _metadata(node["props"])
        want_text, got_text = _canonical(want), _canonical(got)
        mysql_hash.update(f"{row['id']}\t{want_text}\n".encode("utf-8"))
        graph_hash.update(f"{row['id']}\t{got_text}\n".encode("utf-8"))
        compared += 1
        if want_text != got_text:
            keys = sorted(k for k in set(want) | set(got)
                          if k not in want or k not in got or _canonical(want[k]) != _canonical(got[k]))
            wrong.append({"id": row["id"], "keys": keys[:EXAMPLES]})
    stats.update(metadata_compared=compared, metadata_hash_mysql=mysql_hash.hexdigest(),
                 metadata_hash_graph=graph_hash.hexdigest())
    _check(checks, "7.metadata.sampled_mismatched", 0, len(wrong), detail=wrong[:EXAMPLES])


def _check_schema(driver, db, types: list, catalog_error, checks: list) -> None:
    constraints = {r["name"] for r in _records(_read(driver, db, CONSTRAINT_NAMES))}
    index_rows = _records(_read(driver, db, q.INDEX_STATES))
    index_names = {r["name"] for r in index_rows}
    missing_constraints = sorted(set(EXPECTED_CONSTRAINTS) - constraints)
    missing_indexes = sorted(set(EXPECTED_INDEXES) - index_names)
    not_online = sorted(f"{r['name']} ({r['state']})" for r in index_rows if r["state"] != "ONLINE")
    mysql_collisions = sorted(label for label, n in Counter(label_for(t["title"]) for t in types
                                                            if t.get("title")).items() if n > 1)
    graph_collisions = _one(_read(driver, db, LABEL_COLLISIONS), "n")
    versions = [r["schema_version"] for r in _records(_read(driver, db, GRAPHMETA))]
    _check(checks, "8.schema.constraints_missing", 0, len(missing_constraints), detail=missing_constraints)
    _check(checks, "8.schema.indexes_missing", 0, len(missing_indexes), detail=missing_indexes)
    _check(checks, "8.schema.indexes_not_online", 0, len(not_online), detail=not_online[:EXAMPLES])
    _check(checks, "8.catalog.builds", True, catalog_error is None, detail=catalog_error)
    _check(checks, "8.catalog.label_collisions", 0, len(mysql_collisions) + graph_collisions,
           detail=mysql_collisions)
    _check(checks, "8.catalog.sample_types_without_id_or_label", 0,
           _one(_read(driver, db, SAMPLE_TYPES_WITHOUT_ID_OR_LABEL), "n"))
    _check(checks, "8.graphmeta.nodes", 1, len(versions))
    _check(checks, "8.graphmeta.schema_version", writer.SCHEMA_VERSION,
           versions[0] if len(versions) == 1 else versions)


@dataclass
class _LabelTally:
    """What check 9 found in one pass over the edges."""
    edges: int = 0                                          # declared edges classified
    classes: Counter = field(default_factory=Counter)       # labels.CLASSES to edges
    by_property: Counter = field(default_factory=Counter)   # label key to changed and cleared edges differing on it
    unlabelled: int = 0                                     # new, and the rule resolves an assay: the failure
    new_without_assay: int = 0                              # new, but the endpoints share no assay the rule resolves
    unlabelled_examples: list = field(default_factory=list)
    differ_examples: list = field(default_factory=list)


def _check_labels(driver, db, mysql: _MySQLSide, assays: dict, assay_map: dict, checks: list, stats: dict) -> None:
    declared, protocols = mysql.lineage, mysql.protocols

    def classify(result):
        tally, rules = _LabelTally(), {}  # built here, so a retried read starts clean
        for record in result:
            child, parent = record["child"], record["parent"]
            if not (_is_id(child) and _is_id(parent)):
                continue
            try:
                code = run.encode_pair(child, parent)
            except ValueError:
                continue
            if code not in declared:
                continue  # an undeclared edge fails check 1
            tally.edges += 1
            inputs = (assays.get(child, ()), assays.get(parent, ()), protocols.get(child))
            rule = rules.get(inputs)
            if rule is None:
                rule = labels.edge_labels(inputs[0], inputs[1], assay_map, inputs[2])
                if len(rules) < LABEL_RULE_CACHE:
                    rules[inputs] = rule
            stored = record["stored"] or {}
            kind = labels.classify(stored, rule)
            tally.classes[kind] += 1
            if kind == labels.NEW:
                if rule["assay_id"] is None:
                    tally.new_without_assay += 1
                    continue
                tally.unlabelled += 1
                if len(tally.unlabelled_examples) < EXAMPLES:
                    tally.unlabelled_examples.append(
                        {"pair": [child, parent], "rule": {k: rule[k] for k in labels.SINGULAR_ASSAY_KEYS}})
            elif kind in (labels.CHANGED, labels.CLEARED):
                keys = labels.differences(stored, rule)
                tally.by_property.update(keys)
                if len(tally.differ_examples) < EXAMPLES:
                    tally.differ_examples.append({"pair": [child, parent], "class": kind,
                                                  "stored": {k: stored.get(k) for k in keys},
                                                  "rule": {k: rule[k] for k in keys}})
        return tally

    tally = _read(driver, db, LINEAGE_LABELS, transformer=classify)
    by_property = dict(sorted(tally.by_property.items()))
    changed, cleared = tally.classes[labels.CHANGED], tally.classes[labels.CLEARED]
    stats["lineage_labels"] = {"edges_compared": tally.edges,
                               "classes": {kind: tally.classes[kind] for kind in labels.CLASSES},
                               "new_without_assay": tally.new_without_assay, "by_property": by_property}
    _check(checks, "9.lineage.labels", 0, tally.unlabelled, detail=tally.unlabelled_examples)
    differ = None
    if changed or cleared:
        differ = {"changed": changed, "cleared": cleared, "by_property": by_property,
                  "examples": tally.differ_examples}
    _check(checks, "9.lineage.labels_differ_from_rule", "any", changed + cleared, passed=True, detail=differ)
    _check(checks, "9.lineage.labels_plural_missing", "any", tally.classes[labels.PLURAL_MISSING], passed=True)


def _check_type_labels(driver, db, checks: list) -> None:
    count = _one(_read(driver, db, T_LABEL_WITHOUT_SAMPLE), "n")
    examples = []
    if count:
        examples = [{"id": r["id"], "labels": sorted(r["labels"] or [])}
                    for r in _records(_read(driver, db, T_LABEL_WITHOUT_SAMPLE_EXAMPLES, {"limit": EXAMPLES}))]
    _check(checks, "10.samples.no_t_label_without_sample", 0, count, detail=examples)


def _check_parent_lists(mysql: _MySQLSide, sampled: dict, checks: list, stats: dict) -> None:
    tokens_by_id = {row["id"]: collect_parent_tokens(_metadata_object(row["json_metadata"]))
                    for row in mysql.sampled if row["id"] in sampled}  # a sample with no node fails check 7
    uids = {token for tokens in tokens_by_id.values() for token in tokens if UID_RE.match(token)}
    identities = sources.parent_identities(uids) if uids else {}
    wrong = []
    for sample_id, tokens in tokens_by_id.items():
        props = sampled[sample_id]["props"]
        keys = [key for key, want in zip(("parent_titles", "parent_title_hashes"), parent_lists(tokens, identities))
                if not isinstance(props.get(key), (list, tuple)) or list(props[key]) != want]
        if keys:
            wrong.append({"id": sample_id, "keys": keys})
    stats["parent_lists_compared"] = len(tokens_by_id)
    _check(checks, "11.samples.parent_lists", 0, len(wrong), detail=wrong[:EXAMPLES])


# --- the gate ------------------------------------------------------------------------------------

def gate_g(driver, db, sample_size: int = SAMPLE_SIZE, *, seed: int | None = None, accounts=GATE_ACCOUNTS,
           chunk: int = writer.SAMPLE_CHUNK) -> dict:
    """Run the eleven gate G checks (module docstring) against MySQL; reads only.

    ``sample_size`` random samples (a reservoir over the MySQL scan, drawn with ``seed``, which is reported) feed
    checks 2, 3, 7 and 11. ``accounts`` are the SEEK logins check 6 resolves by name. ``chunk`` is the MySQL page size.
    """
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive, got {sample_size}")
    if seed is None:
        seed = random.SystemRandom().randrange(1 << 32)
    timings: dict = {}
    stats: dict = {"seed": seed, "sample_size": sample_size, "timings_s": timings}
    checks: list = []

    types = sources.sample_types()
    try:
        cat, catalog_error = run.build_catalog(), None
    except ValueError as exc:
        cat, catalog_error = None, str(exc)
    sops = sources.sops_map()
    assay_map = sources.resolved_assay_map()
    mysql = _timed(timings, "mysql_scan", _scan_mysql, chunk, sample_size, random.Random(seed),
                   _protocol_reader(sops))
    stats.update(mysql_samples=mysql.count, sampled_ids=[row["id"] for row in mysql.sampled])

    sampled = _timed(timings, "sampled_nodes", _sampled_nodes, driver, db, stats["sampled_ids"])
    groups = _timed(timings, "project_id_groups", _project_groups, driver, db)
    audit_rows = _records(_timed(timings, "type_label_audit", _read, driver, db, TYPE_LABEL_AUDIT))
    audit = dict(audit_rows[0]) if audit_rows else {}
    graph_catalog = _records(_read(driver, db, GRAPH_CATALOG))

    _timed(timings, "1.lineage", _check_lineage, driver, db, mysql, checks, stats)
    _timed(timings, "2.scope", _check_scope, mysql, groups, sampled, checks, stats)
    _timed(timings, "3.catalog", _check_catalog, driver, db, graph_catalog, audit, sampled, checks, stats)
    _timed(timings, "4.samples", _check_samples, driver, db, mysql, audit, checks)
    _timed(timings, "5.attributes", _check_attributes, driver, db, checks, stats)
    _timed(timings, "6.people", _check_people, driver, db, groups, accounts, checks, stats)
    _timed(timings, "7.metadata", _check_metadata, cat, catalog_error, mysql, sampled, checks, stats)
    _timed(timings, "8.schema", _check_schema, driver, db, types, catalog_error, checks)
    assays = _timed(timings, "lineage_assays", _endpoint_assays, mysql.lineage)
    _timed(timings, "9.lineage", _check_labels, driver, db, mysql, assays, assay_map, checks, stats)
    _timed(timings, "10.samples", _check_type_labels, driver, db, checks)
    _timed(timings, "11.samples", _check_parent_lists, mysql, sampled, checks, stats)
    return {"checks": checks, "pass": all(c["pass"] for c in checks), "stats": stats}
