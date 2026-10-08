"""Gate G: check a graph_sync build against MySQL (the design, section 6; the plan, task G5; checks 9 to 11: the sync
design, section 13 and CI-9). Read-only.

``gate_g`` returns ``{"checks": [{"name", "expected", "actual", "pass"}, ...], "pass": bool, "stats": {...}}``. A
check either counts violations (expected 0) or compares a graph number with its MySQL number; ``detail``, when
present, carries a few examples. Each name starts with the gate G check it belongs to:

1. ``lineage``: every DERIVED_FROM pair MySQL's parent tokens declare exists between the two Sample nodes. An
   undeclared pair between two Sample nodes fails; one touching an OrphanSample is only counted. A declared pair
   carried by two edges fails too.
2. ``scope``: per project, the Samples whose ``project_ids`` hold it equal the distinct ``projects_samples``
   count; every Sample carries ``project_ids``; for the random samples, ``project_ids`` equals the MySQL list; and
   per project the Samples with an IN_PROJECT to it are those whose ``project_ids`` hold it, with no IN_PROJECT
   outside a sample's ``project_ids`` (``in_project_edges_differ``, ``in_project_edges_extra``).
3. ``catalog``: every property key on a ``T_X`` node, system keys excluded, is the title of an Attribute on
   SampleType X: over the random samples, and in one aggregate per type label.
4. ``samples``: the Sample count and the OF_TYPE count equal MySQL's sample count; every Sample has exactly one
   ``T_`` label and one OF_TYPE, and the label is its SampleType's; every Sample's ``type`` is its SampleType's
   title.
5. ``attributes``: the Attribute nodes with an ``id`` are ``sample_attributes`` by id, titles byte-exact.
6. ``scope`` (people): for every person with a membership, the samples the graph shows them equal the SQL
   ``EXISTS projects_samples`` count. The named accounts are resolved by graph_search's own scope resolver and
   counted with the endpoint's Cypher predicate. No membership read while MySQL holds samples fails (it would
   compare nothing).
7. ``metadata``: for the random samples, the node's properties minus system keys equal the projection of the
   sample's ``json_metadata`` (canonical JSON; a date is ``{"$date": "<ISO date>"}``, so a date stored as a
   string does not pass for one). Their ``uuid``, ``type``, ``title`` and ``search_text`` equal the projection's too.
8. ``schema``, ``catalog``, ``graphmeta``: every v1.1 and v1.3 constraint and index exists and every index is ONLINE;
   the catalog builds with no label collision in MySQL or the graph; no SampleType lacks ``id`` or ``label``; one
   GraphMeta node, at the writer's schema version; and no label or relationship type outside the contract's sets for the
   graph (``nextseek_graph.schema``; every ``T_`` label is allowed).
9. ``lineage.labels``: every declared DERIVED_FROM between two Sample nodes is compared with batch upload's label
   rule fed from MySQL (``labels.edge_labels``) and classified (``labels.classify``). It fails on an edge whose
   endpoints share an assay the rule resolves and whose three singular assay fields are all null: the gap that got
   past this gate and gate E. It also fails when the lineage endpoints carry assay links and the assay map read
   empty. A label that differs from the rule (``changed``, ``cleared``) and one lacking only the
   plural lists (``plural_missing``) are reported, never failed (R14). An undeclared edge is check 1's.
10. ``samples``: no node carries a ``T_`` label without ``:Sample`` (an OrphanSample keeps none: the deletion rule).
11. ``samples.parent_lists``: for the random samples, ``parent_titles`` and ``parent_title_hashes`` equal batch
    upload's rule over the sample's parent tokens (``projection.parent_lists``), a UID parent named by its stored
    identity; a node without them fails.
12. ``studies``: the Study layer follows SEEK. No two Study nodes share a ``seek_study_id`` (always enforced).
    Enforced only where the box's switch ``NEXTSEEK_GRAPH_SYNC_STUDY_LINKS`` says ``follow``, and reported
    otherwise: no split pair and no merge candidate is left, every SEEK study has a node, every SEEK-keyed node
    carries SEEK's title, description and investigation, and every Sample's IN_STUDY equals SEEK's studies (a paper
    sample's links to studies of its paper's own investigation, and samples SEEK places in no study, excepted).
    Always reported: the switch, id collisions, SEEK-keyed nodes SEEK lacks, samples kept with no SEEK study,
    OrphanSample links and paper samples with the links withheld from them.
13. ``assays`` (graph schema 1.3): the Assay ids are ``internal_assays``' ids; RUN_IN equals the rows the mapping
    and ``assays.study_id`` give; ACCEPTED_BY and GENERATES equal the parsed catalog; for the random samples, each
    one's whole set of INPUT_TO and OUTPUT_OF (type, Assay, SEEK ids) equals the role rule over its declared lineage,
    so a stale extra edge fails as a missing one does; and the graph's count of INPUT_TO and OUTPUT_OF equals the role
    rule over every declared pair, so an edge left unwritten on a sample the draw missed fails too.
14. ``small``: the small tables follow SEEK: the Project nodes (id and title), the Investigation nodes (id, title and
    their projects through IN_PROJECT) and every MEMBER_OF (person, project, has_left). An Investigation node SEEK
    lacks fails, unless a Study still holds it (``investigations_not_in_seek_held``, reported): a Study of a SEEK study
    that still exists, or a graph-only paper. A membership or an investigation link naming a project SEEK lacks is not
    compared, since no writer can link it; the stats count it.

Every MySQL side joins ``samples`` and counts distinct sample ids: SEEK's link tables hold rows for samples that are
gone and rows repeated (``projects_samples``, ``assay_assets``), which would otherwise read as drift. A check whose
input read returns nothing while MySQL holds rows fails rather than comparing nothing with nothing.

Sized for about 1.08M samples: every full-graph read returns a few rows (the two scope checks share one scan of
``project_ids`` grouped by value), the key aggregate runs one type label per transaction, and the lineage check
streams the edges, a page of child ids per read transaction (``writer.read_sample_pages``), against a set of encoded
MySQL pairs. Check 9 streams them once more, paged the same way, with their seven label properties and classifies
each as it arrives; it holds one assay-id tuple per lineage endpoint (equal tuples shared)
and one resolved protocol per child that names one. The sampled checks read the random samples plus the
strata (the constants above): at most about 6,600 samples on a graph of 110 types, 15 projects and a busy week.
Check 12 reads every Study node once and streams every Sample's IN_STUDY in keyset pages against SEEK's ordered links.
Check 13 reads the assay layer's rows (hundreds), for the random samples their declared lineage from the pairs check 1
already holds and their edges by id, and packs the role rule over every declared pair 8 bytes a role to count it.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from django.conf import settings
from django.db import connections

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.graph_search.scope import ScopeUnavailable, resolve_scope
from nextseek_api.graph_sync import assays as assay_rules
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import labels, run, sources, study_links, study_merge, writer
from nextseek_api.graph_sync.projection import SYSTEM_KEYS, label_for, parent_lists, project_sample
from nextseek_api.graph_sync.writer import _one, _records, _run
from nextseek_graph import schema

log = logging.getLogger(__name__)

SAMPLE_SIZE = 1_000
# Beside the random samples, checks 2, 3, 7 and 11 also read up to STRATUM_SIZE samples of every sample type and of
# every project (drawn with their own generator, so a seed's random draw is unchanged), and every sample created or
# updated in the last RECENT_DAYS days, newest first, at most RECENT_CAP: a small type, a small project or a new
# upload is then always inspected, where a draw of 1,000 in about a million would see a 43-sample upload one night
# in 25.
STRATUM_SIZE = 5
RECENT_DAYS = 7
RECENT_CAP = 5_000
# The sample properties check 7 compares with the projection beside the metadata (projection.project_sample).
IDENTITY_KEYS = ("uuid", "type", "title", "search_text")
# The gating accounts of the merged dataset (design, section 4): a TCGA member and a non-member control.
GATE_ACCOUNTS = ("tcgamember", "user")
EXAMPLES = 10
SAMPLED_BATCH = 1_000
LABEL_RULE_CACHE = 100_000   # distinct (child assays, parent assays, protocol) inputs whose labels check 9 keeps

# The names gate G expects, from the contract's 1.1 groups (nextseek_graph/schema.py).
EXPECTED_CONSTRAINTS = tuple(name for name, _label, _prop in (*schema.UNIQUE_CONSTRAINTS_V11,
                                                                *schema.UNIQUE_CONSTRAINTS_V13))
EXPECTED_INDEXES = (tuple(name for name, _label, _prop in (*schema.RANGE_INDEXES_V11, *schema.RANGE_INDEXES_V13))
                    + (schema.FULLTEXT_INDEX,))
# "Protocol" and "protocol" both end in this as JSON keys. A child's metadata without it names no protocol, which
# spares parsing most children's metadata a second time.
_PROTOCOL_KEY_TAIL = 'rotocol"'

# --- statements ----------------------------------------------------------------------------------

# Every DERIVED_FROM between two Sample nodes, read a page of child ids at a time (writer.read_sample_pages).
LINEAGE_PAIRS = ("MATCH (c:Sample) WHERE {page}\n"
                 "MATCH (c)-[:DERIVED_FROM]->(p:Sample) RETURN c.id AS child, p.id AS parent")
LINEAGE_ON_ORPHANS = "MATCH (:OrphanSample)-[e:DERIVED_FROM]-() RETURN count(DISTINCT e) AS n"
# The same edges with their seven label properties, null when absent.
LINEAGE_LABELS = ("MATCH (c:Sample) WHERE {page}\n"
                  "MATCH (c)-[e:DERIVED_FROM]->(p:Sample)\n"
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
# Every Sample whose ``type`` property is not the title of the SampleType it has OF_TYPE to (exhaustive).
TYPE_TITLE_DIFFERS = """
MATCH (s:Sample)-[:OF_TYPE]->(t:SampleType)
WHERE s.type IS NULL OR t.title IS NULL OR s.type <> t.title
RETURN count(s) AS n
"""
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
# The census: every label and relationship type the database lists, and whether a node or relationship still carries
# one it lists (a name can outlive the last thing that carried it). Only an unexpected name is looked up.
LABELS_LISTED = "CALL db.labels() YIELD label RETURN collect(label) AS names"
RELATIONSHIP_TYPES_LISTED = ("CALL db.relationshipTypes() YIELD relationshipType "
                             "RETURN collect(relationshipType) AS names")
# Cypher 25 dynamic labels and types stop at the first hit (two db hits); a WHERE on labels(n) or type(r) scans them all.
LABEL_CARRIED = "CYPHER 25 MATCH (n:$($name)) RETURN 1 AS found LIMIT 1"
RELATIONSHIP_TYPE_CARRIED = "CYPHER 25 MATCH ()-[r:$($name)]->() RETURN 1 AS found LIMIT 1"
# The names a graph may carry, from the contract (every T_ label besides): 1.1's and 1.3's groups (1.2 adds no label
# or type). 1.3's are allowed on a 1.2 graph too, so the census never fails a box mid-rollout.
EXPECTED_LABELS = schema.LABELS_V11 | schema.LABELS_V13
EXPECTED_RELATIONSHIP_TYPES = frozenset(schema.RELATIONSHIPS_V11) | frozenset(schema.RELATIONSHIPS_V13)
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
# Check 13: the assay layer (graph schema 1.3).
ASSAY_IDS = "MATCH (a:Assay) RETURN a.id AS id"
RUN_IN_ROWS = """
MATCH (a:Assay)-[r:RUN_IN]->(st:Study)
RETURN a.id AS assay_id, st.seek_study_id AS study_id, r.seek_assay_ids AS seek_assay_ids
"""
CATALOG_EDGE_ROWS = """
MATCH (t:SampleType)-[r:ACCEPTED_BY]->(a:Assay)
RETURN 'ACCEPTED_BY' AS type, t.title AS code, a.id AS assay_id, r.required AS required, r.group AS group_index
UNION ALL
MATCH (a:Assay)-[r:GENERATES]->(t:SampleType)
RETURN 'GENERATES' AS type, t.title AS code, a.id AS assay_id, null AS required, r.group AS group_index
"""
SAMPLED_ASSAY_EDGES = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})-[r:INPUT_TO|OUTPUT_OF]->(a:Assay)
RETURN s.id AS id, type(r) AS type, a.id AS assay_id, r.seek_assay_ids AS seek_assay_ids
"""
# Every INPUT_TO and OUTPUT_OF, from the relationship count store (no label in the pattern, so no node is read).
SAMPLE_ASSAY_EDGE_COUNT = """
CALL () { MATCH ()-[r:INPUT_TO]->() RETURN count(r) AS inputs }
CALL () { MATCH ()-[r:OUTPUT_OF]->() RETURN count(r) AS outputs }
RETURN inputs + outputs AS n
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
    strata: dict = field(default_factory=dict)     # how many samples each stratum drew (stats.sample_strata)


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


def _draw(pools: dict, seen: Counter, key, row: dict, rng: random.Random) -> None:
    """Reservoir-sample ``row`` into ``pools[key]``, at most ``STRATUM_SIZE`` rows per key."""
    seen[key] += 1
    pool = pools.setdefault(key, [])
    if len(pool) < STRATUM_SIZE:
        pool.append(row)
    else:
        slot = rng.randrange(seen[key])
        if slot < STRATUM_SIZE:
            pool[slot] = row


def _scan_mysql(chunk: int, sample_size: int, rng: random.Random, protocol_of=None, *,
                strata_rng: random.Random | None = None, recent_ids=frozenset()) -> _MySQLSide:
    """One pass over MySQL's samples: the count, the ids, the declared lineage, each child's resolved protocol (with
    ``protocol_of``) and a reservoir sample. With ``strata_rng``, ``sampled`` also takes up to ``STRATUM_SIZE``
    samples per sample type and per project, and every row whose id is in ``recent_ids``; ``strata`` counts each."""
    side = _MySQLSide(projects=sources.sample_projects())
    uuid_index = sources.uuid_to_ids()
    by_type: dict = {}
    by_project: dict = {}
    seen_types: Counter = Counter()
    seen_projects: Counter = Counter()
    recent: dict = {}
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
            if strata_rng is not None:
                _draw(by_type, seen_types, row["sample_type_id"], row, strata_rng)
                for project_id in side.projects.get(row["id"], ()):
                    _draw(by_project, seen_projects, project_id, row, strata_rng)
            if row["id"] in recent_ids:
                recent[row["id"]] = row
    chosen = {row["id"]: row for row in side.sampled}
    side.strata = {"random": len(chosen),
                   "per_type": sum(len(pool) for pool in by_type.values()),
                   "per_project": sum(len(pool) for pool in by_project.values()),
                   "recent": len(recent), "recent_ids_read": len(recent_ids)}
    for pools in (by_type, by_project):
        for pool in pools.values():
            chosen.update((row["id"], row) for row in pool)
    chosen.update(recent)
    side.sampled = sorted(chosen.values(), key=lambda r: r["id"])
    side.strata["compared"] = len(side.sampled)
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
        # One page's, built here so a retried read starts clean. A pair's edges share their child, so a second edge
        # of a declared pair comes in the page of its first.
        edges = extra = doubled = 0
        examples, doubled_examples, found = [], [], set()
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
                if code not in found:
                    found.add(code)
                else:   # a second edge for a declared pair
                    doubled += 1
                    if len(doubled_examples) < EXAMPLES:
                        doubled_examples.append([child, parent])
            else:
                extra += 1
                if len(examples) < EXAMPLES:
                    examples.append([child, parent])
        return edges, extra, examples, found, doubled, doubled_examples

    remaining = set(declared)
    edges = extra = doubled = 0
    extra_examples, doubled_examples = [], []
    for page_edges, page_extra, examples, found, page_doubled, page_doubled_examples in writer.read_sample_pages(
            driver, db, LINEAGE_PAIRS, compare, name="gate G: lineage read"):
        edges += page_edges
        extra += page_extra
        doubled += page_doubled
        remaining -= found
        extra_examples.extend(examples[:EXAMPLES - len(extra_examples)])
        doubled_examples.extend(page_doubled_examples[:EXAMPLES - len(doubled_examples)])
    on_orphans = _one(_read(driver, db, LINEAGE_ON_ORPHANS), "n")
    stats.update(lineage_declared_pairs=len(declared), lineage_edges_between_samples=edges,
                 lineage_edges_touching_orphans=on_orphans)
    missing = [list(run.decode_pair(code)) for code in heapq.nsmallest(EXAMPLES, remaining)]
    _check(checks, "1.lineage.declared_pairs_missing", 0, len(remaining), detail=missing)
    _check(checks, "1.lineage.undeclared_pairs_between_samples", 0, extra, detail=extra_examples)
    _check(checks, "1.lineage.duplicate_edges", 0, doubled, detail=doubled_examples)
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
        if not schema.is_type_label(label):
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
    _check(checks, "4.samples.type_differs_from_sample_type", 0, _one(_read(driver, db, TYPE_TITLE_DIFFERS), "n"))


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


def _check_people(driver, db, groups: list, accounts, checks: list, stats: dict, samples: int = 0) -> None:
    people: dict[int, set] = defaultdict(set)
    for membership in sources.memberships():
        people[int(membership["person_id"])].add(int(membership["project_id"]))
    # No membership read while MySQL holds samples would compare nothing with nothing and pass.
    _check(checks, "6.scope.people_compared", "at least 1", len(people), passed=bool(people) or samples == 0)
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
        for name in ("7.metadata.sampled_mismatched", "7.metadata.sampled_identity_mismatched"):
            _check(checks, name, 0, "not compared: the catalog does not build", passed=False, detail=catalog_error)
        return
    mysql_hash, graph_hash = hashlib.sha256(), hashlib.sha256()
    compared, wrong, wrong_identity = 0, [], []
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
        identity = [key for key in IDENTITY_KEYS if proj.props.get(key) != node["props"].get(key)]
        if identity:
            wrong_identity.append({"id": row["id"], "keys": identity})
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
    _check(checks, "7.metadata.sampled_identity_mismatched", 0, len(wrong_identity),
           detail=wrong_identity[:EXAMPLES])


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
    _check_census(driver, db, checks)


def _carried_but_unexpected(driver, db, listed: str, carried: str, expected) -> list[str]:
    """The names ``listed`` returns that ``expected(name)`` rejects and that something still carries."""
    names = _one(_read(driver, db, listed), "names", []) or []
    return [name for name in sorted(n for n in names if not expected(n))
            if _records(_read(driver, db, carried, {"name": name}))]


def _check_census(driver, db, checks: list) -> None:
    """No label or relationship type the contract does not name (a restore or an old writer can bring back what a
    cleanup removed)."""
    labels_found = _carried_but_unexpected(driver, db, LABELS_LISTED, LABEL_CARRIED,
                                           lambda n: n in EXPECTED_LABELS or schema.is_type_label(n))
    types_found = _carried_but_unexpected(driver, db, RELATIONSHIP_TYPES_LISTED, RELATIONSHIP_TYPE_CARRIED,
                                          lambda n: n in EXPECTED_RELATIONSHIP_TYPES)
    _check(checks, "8.schema.unknown_labels", 0, len(labels_found), detail=labels_found[:EXAMPLES])
    _check(checks, "8.schema.unknown_relationship_types", 0, len(types_found), detail=types_found[:EXAMPLES])


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
    refresh_examples: list = field(default_factory=list)    # renamed and protocol_filled: the next sync writes them

    def add(self, page: "_LabelTally") -> None:
        """Merge one page's tally: the counts summed, the examples kept in read order up to ``EXAMPLES``."""
        self.edges += page.edges
        self.classes.update(page.classes)
        self.by_property.update(page.by_property)
        self.unlabelled += page.unlabelled
        self.new_without_assay += page.new_without_assay
        for name in ("unlabelled_examples", "differ_examples", "refresh_examples"):
            kept = getattr(self, name)
            kept.extend(getattr(page, name)[:EXAMPLES - len(kept)])


def _check_labels(driver, db, mysql: _MySQLSide, assays: dict, assay_map: dict, checks: list, stats: dict) -> None:
    declared, protocols = mysql.lineage, mysql.protocols
    rules: dict = {}   # the rule's labels per input, kept across pages: a cache, it counts nothing

    def classify(result):
        tally = _LabelTally()  # one page's, built here so a retried read starts clean
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
            elif kind in labels.REFRESH_CLASSES and len(tally.refresh_examples) < EXAMPLES:
                keys = labels.differences(stored, rule)
                tally.refresh_examples.append({"pair": [child, parent], "class": kind,
                                               "stored": {k: stored.get(k) for k in keys},
                                               "rule": {k: rule[k] for k in keys}})
        return tally

    tally = _LabelTally()
    for page in writer.read_sample_pages(driver, db, LINEAGE_LABELS, classify, name="gate G: lineage labels read"):
        tally.add(page)
    by_property = dict(sorted(tally.by_property.items()))
    changed, cleared = tally.classes[labels.CHANGED], tally.classes[labels.CLEARED]
    stats["lineage_labels"] = {"edges_compared": tally.edges,
                               "classes": {kind: tally.classes[kind] for kind in labels.CLASSES},
                               "new_without_assay": tally.new_without_assay, "by_property": by_property}
    # With assay links on the endpoints but no assay map, the rule resolves no assay and every label reads as
    # cleared: reported, never failed, so the check would pass on an empty read of SEEK's assays.
    map_read = bool(assay_map) or not assays
    _check(checks, "9.lineage.assay_map_read", True, map_read,
           detail=None if map_read else "lineage endpoints carry assay links but the assay map read empty")
    _check(checks, "9.lineage.labels", 0, tally.unlabelled, detail=tally.unlabelled_examples)
    differ = None
    if changed or cleared:
        differ = {"changed": changed, "cleared": cleared, "by_property": by_property,
                  "examples": tally.differ_examples}
    _check(checks, "9.lineage.labels_differ_from_rule", "any", changed + cleared, passed=True, detail=differ)
    _check(checks, "9.lineage.labels_plural_missing", "any", tally.classes[labels.PLURAL_MISSING], passed=True)
    refresh = tally.classes[labels.RENAMED] + tally.classes[labels.PROTOCOL_FILLED]
    _check(checks, "9.lineage.labels_refresh_pending", "any", refresh, passed=True,
           detail={"examples": tally.refresh_examples} if refresh else None)


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


# --- family 12: studies (the studies release) ------------------------------------------------------------------------

STUDY_CHECKS_ENFORCED_WHEN_FOLLOWING = ("split_pairs", "merge_candidates", "nodes_differ_from_seek",
                                        "seek_studies_without_node", "in_study_missing", "in_study_extra")


def _example(bucket: list, value) -> None:
    if len(bucket) < EXAMPLES:
        bucket.append(value)


def _edge_key(rel: str, assay_id, seek_ids) -> tuple:
    return rel, assay_id, tuple(sorted(seek_ids or ()))


def _check_assays(driver, db, st, mysql: _MySQLSide, assays: dict, sampled: dict, checks: list, stats: dict) -> None:
    graph_ids = {r["id"] for r in _records(_read(driver, db, ASSAY_IDS))}
    missing = sorted(set(st.ids) - graph_ids)
    extra = sorted(graph_ids - set(st.ids), key=_sort_key)
    _check(checks, "13.assays.ids", 0, len(missing) + len(extra),
           detail={"missing_in_graph": missing[:EXAMPLES], "not_in_mysql": extra[:EXAMPLES]})

    want = {(r["assay_id"], r["study_id"], tuple(r["seek_assay_ids"])) for r in st.runs}
    got = {(r["assay_id"], r["study_id"], tuple(sorted(r["seek_assay_ids"] or ())))
           for r in _records(_read(driver, db, RUN_IN_ROWS))}
    _check(checks, "13.assays.run_in", 0, len(want ^ got),
           detail={"missing_in_graph": sorted(want - got, key=repr)[:EXAMPLES],
                   "not_in_mysql": sorted(got - want, key=repr)[:EXAMPLES]})

    want = ({("ACCEPTED_BY", r["code"], r["assay_id"], r["required"], r["group"]) for r in st.catalog.accepted_by}
            | {("GENERATES", r["code"], r["assay_id"], None, r["group"]) for r in st.catalog.generates})
    got = {(r["type"], r["code"], r["assay_id"], r["required"], r["group_index"])
           for r in _records(_read(driver, db, CATALOG_EDGE_ROWS))}
    _check(checks, "13.assays.catalog_edges", 0, len(want ^ got),
           detail={"missing_in_graph": sorted(want - got, key=repr)[:EXAMPLES],
                   "not_in_mysql": sorted(got - want, key=repr)[:EXAMPLES]})

    ids = sorted(i for i in sampled if _is_id(i))   # a sampled sample with no node fails check 7
    wanted = set(ids)
    pairs = [pair for pair in map(run.decode_pair, mysql.lineage) if pair[0] in wanted or pair[1] in wanted]
    roles = assay_rules.roles_for_pairs(pairs, assays, st.internal_by_seek)
    expected = {row["id"]: ({_edge_key("INPUT_TO", e["assay_id"], e["seek_assay_ids"]) for e in row["inputs"]}
                            | {_edge_key("OUTPUT_OF", e["assay_id"], e["seek_assay_ids"]) for e in row["outputs"]})
                for row in assay_rules.sample_edge_rows({i: roles.get(i, {}) for i in ids})}
    found: dict = {i: set() for i in ids}
    for start in range(0, len(ids), SAMPLED_BATCH):
        for r in _records(_read(driver, db, SAMPLED_ASSAY_EDGES, {"ids": ids[start:start + SAMPLED_BATCH]})):
            found.setdefault(r["id"], set()).add(_edge_key(r["type"], r["assay_id"], r["seek_assay_ids"]))
    wrong = [{"id": i, "missing": sorted(expected[i] - found[i]), "extra": sorted(found[i] - expected[i])}
             for i in ids if expected[i] != found[i]]
    stats["assays"] = {"assays": len(st.ids), "run_in_rows": len(st.runs), "sampled_compared": len(ids),
                       "sampled_with_edges": sum(1 for i in ids if expected[i])}
    _check(checks, "13.assays.sampled_sample_edges", 0, len(wrong), detail=wrong[:EXAMPLES])

    roles = run.RoleCodes(st.internal_by_seek)
    for code in mysql.lineage:
        child, parent = run.decode_pair(code)
        roles.add_edge(child, parent, assays.get(child), assays.get(parent))
    want_edges = sum(len(sample_roles) for _, sample_roles in roles.by_sample())
    got_edges = _one(_read(driver, db, SAMPLE_ASSAY_EDGE_COUNT), "n")
    stats["assays"]["sample_edges_expected"] = want_edges
    _check(checks, "13.assays.sample_edge_count", want_edges, got_edges)


def _check_studies(driver, db, checks: list, stats: dict) -> None:
    """Family 12: the Study layer follows SEEK (docs/neo4j-schema.md, v1.2 "Study nodes and IN_STUDY"). The duplicate
    check always expects 0; the checks of ``STUDY_CHECKS_ENFORCED_WHEN_FOLLOWING`` expect 0 only where the box's
    switch says ``follow`` and report otherwise, so a box rebuilt before its merge keeps a green drift check; the
    rest always report. Reads only."""
    follow = study_links.follows_seek()
    index = study_merge.read_index(driver, db)
    selections = [study_merge.classify(index, x) for x in study_merge.study_ids(index)]
    duplicates = writer.seek_study_id_duplicates(driver, db)
    split = [s.study_id for s in selections if s.kind in study_merge.SPLIT_KINDS and s.legacy is not None
             and s.legacy.in_study > 0 and s.seek_keyed is not None and s.seek_keyed.in_study > 0]
    candidates = [s.study_id for s in selections if s.kind in study_merge.ACTING]
    collisions = [s.study_id for s in selections if s.kind == study_merge.ID_COLLISION]
    differ, not_in_seek, not_in_seek_empty = [], [], []
    keyed = {node.seek_study_id for node in index.nodes if _is_id(node.seek_study_id)}
    without_node = sorted(x for x in index.seek_studies if x not in keyed)
    for node in index.nodes:
        key = node.seek_study_id
        if not _is_id(key):
            continue
        seek = index.seek_studies.get(key)
        if seek is None:
            # writer.delete_gone_seek_study_nodes deletes the empty ones every run: only IN_INVESTIGATION and RUN_IN
            # left on a node with no `id` (cypher._SEEK_STUDY_NODE_EMPTY).
            empty = (node.id is None and node.in_study == 0
                     and all(t == "RUN_IN" for t in node.other_relationships))
            (not_in_seek_empty if empty else not_in_seek).append(key)
            continue
        wanted_inv = [] if seek.get("investigation_id") is None else [seek["investigation_id"]]
        if (node.title != seek.get("title")
                or (node.props.get("description") or None) != (seek.get("description") or None)
                or sorted(i.get("id") for i in node.investigations) != wanted_inv):
            differ.append(key)
    missing = extra = kept = paper = withheld = unknown = 0
    examples = {"missing": [], "extra": [], "kept": [], "paper": []}
    scope = writer.paper_scope(index.seek_studies.values(), index.seek_investigations.values())
    for found in study_links.diff_in_study(driver, db, scope=scope):
        if found.paper:
            paper += 1
            withheld += len(found.withheld)
            unknown += found.investigation_unknown
            _example(examples["paper"], found.sample_id)
        if found.add:
            missing += len(found.add)
            _example(examples["missing"], [found.sample_id, list(found.add)])
        if found.remove:
            extra += len(found.remove)
            _example(examples["extra"], [found.sample_id, [link["seek_study_id"] for link in found.remove]])
        if found.no_seek_study:
            kept += 1
            _example(examples["kept"], found.sample_id)
    orphan = writer.orphan_in_study(driver, db)

    def gated(name, actual, detail=None):
        if follow:
            _check(checks, f"12.studies.{name}", 0, actual, detail=detail)
        else:
            _check(checks, f"12.studies.{name}", "any", actual, passed=True, detail=detail)

    def reported(name, actual, detail=None):
        _check(checks, f"12.studies.{name}", "any", actual, passed=True, detail=detail)

    reported("switch", study_links.switch_value())
    _check(checks, "12.studies.seek_study_id_duplicates", 0, len(duplicates), detail=duplicates[:EXAMPLES])
    gated("split_pairs", len(split), split[:EXAMPLES])
    gated("merge_candidates", len(candidates), candidates[:EXAMPLES])
    reported("id_collisions", len(collisions), collisions[:EXAMPLES])
    gated("nodes_differ_from_seek", len(differ), differ[:EXAMPLES])
    reported("nodes_not_in_seek", len(not_in_seek), not_in_seek[:EXAMPLES])
    _check(checks, "12.studies.nodes_not_in_seek_empty", 0, len(not_in_seek_empty),
           detail=not_in_seek_empty[:EXAMPLES])
    gated("seek_studies_without_node", len(without_node), without_node[:EXAMPLES])
    gated("in_study_missing", missing, examples["missing"])
    gated("in_study_extra", extra, examples["extra"])
    reported("no_seek_study_kept", kept, examples["kept"])
    reported("orphan_in_study", orphan)
    reported("paper_samples", paper, {"withheld_links": withheld, "investigation_unknown": unknown,
                                      "examples": examples["paper"]})
    stats["studies"] = {"switch": study_links.switch_value(), "split_pairs": len(split),
                        "merge_candidates": len(candidates), "id_collisions": len(collisions),
                        "seek_studies_without_node": len(without_node), "in_study_missing": missing,
                        "in_study_extra": extra, "paper_samples": paper, "withheld_links": withheld,
                        "paper_investigation_unknown": unknown}


# --- family 14: the small tables, and check 2's IN_PROJECT edges ----------------------------------------------------

def _check_in_project_edges(driver, db, checks: list, stats: dict) -> None:
    """Check 2 on the edges: per project, the Samples linked to it by IN_PROJECT equal the distinct ``projects_samples``
    count check 2 already compared with the ``project_ids`` property (``stats["projects"]``); and no IN_PROJECT links a
    Sample to a project its ``project_ids`` does not name. A property right and an edge dropped (a Project node
    missing when the sample was written) read as current everywhere else."""
    mysql = {int(p): counts["mysql"] for p, counts in (stats.get("projects") or {}).items()}
    graph = {r["id"]: int(r["n"]) for r in _records(_read(driver, db, q.IN_PROJECT_DEGREES)) if _is_id(r["id"])}
    differ = [{"project_id": p, "mysql": mysql.get(p, 0), "graph": graph.get(p, 0)}
              for p in sorted(set(mysql) | set(graph)) if mysql.get(p, 0) != graph.get(p, 0)]
    _check(checks, "2.scope.in_project_edges_differ", 0, len(differ), detail=differ[:EXAMPLES])
    _check(checks, "2.scope.in_project_edges_extra", 0, _one(_read(driver, db, q.IN_PROJECT_EXTRA), "n"))


def _check_small_tables(driver, db, checks: list, stats: dict) -> None:
    """Family 14: the Project, Investigation, Person and MEMBER_OF nodes and edges equal SEEK's tables. Every check
    expects 0 and lists up to ``EXAMPLES``. An Investigation SEEK lacks that a Study still holds is kept by the small
    tables and reported apart; only a Study of a SEEK study that still exists, or a graph-only paper (no
    ``seek_study_id``), holds one. A ``group_memberships`` or
    ``investigations_projects`` row naming a project SEEK's ``projects`` lacks is left out, as the writers leave it
    (they MATCH the Project node), and counted in the stats: SEEK data to fix, which no sync can clear."""
    seek_projects = {int(p["id"]): p.get("title") for p in sources.projects()}
    graph_projects = {r["id"]: r["title"] for r in _records(_read(driver, db, q.GRAPH_PROJECTS)) if _is_id(r["id"])}
    projects_differ = [p for p in sorted(set(seek_projects) | set(graph_projects))
                       if p not in seek_projects or p not in graph_projects or seek_projects[p] != graph_projects[p]]
    links: dict[int, set] = {}
    links_dropped = 0
    for row in sources.investigation_projects():
        if int(row["project_id"]) not in seek_projects:
            links_dropped += 1
            continue
        links.setdefault(int(row["investigation_id"]), set()).add(int(row["project_id"]))
    seek_invs = {int(i["id"]): (i.get("title"), sorted(links.get(int(i["id"]), ()))) for i in sources.investigations()}
    study_ids = sorted({int(s["id"]) for s in sources.studies()})
    graph_rows = [r for r in _records(_read(driver, db, q.GRAPH_INVESTIGATIONS, {"study_ids": study_ids}))
                  if _is_id(r["id"])]
    graph_invs = {r["id"]: (r["title"], sorted(r["project_ids"] or [])) for r in graph_rows}
    invs_differ = [i for i in sorted(seek_invs) if graph_invs.get(i) != seek_invs[i]]
    gone = [r["id"] for r in graph_rows if r["id"] not in seek_invs and not r["held"]]
    held = [r["id"] for r in graph_rows if r["id"] not in seek_invs and r["held"]]
    seek_members: dict[tuple[int, int], bool] = {}
    members_dropped = 0
    for m in sources.memberships():
        if int(m["project_id"]) not in seek_projects:
            members_dropped += 1
            continue
        seek_members[(int(m["person_id"]), int(m["project_id"]))] = bool(m["has_left"])
    graph_members = {(r["person_id"], r["project_id"]): bool(r["has_left"])
                     for r in _records(_read(driver, db, q.GRAPH_MEMBER_OF))}
    members_differ = sorted((pair for pair in set(seek_members) | set(graph_members)
                             if seek_members.get(pair) != graph_members.get(pair)),
                            key=lambda pair: tuple(_sort_key(v) for v in pair))
    _check(checks, "14.small.projects_differ", 0, len(projects_differ), detail=projects_differ[:EXAMPLES])
    _check(checks, "14.small.investigations_differ", 0, len(invs_differ), detail=invs_differ[:EXAMPLES])
    _check(checks, "14.small.investigations_not_in_seek", 0, len(gone), detail=gone[:EXAMPLES])
    _check(checks, "14.small.investigations_not_in_seek_held", "any", len(held), passed=True, detail=held[:EXAMPLES])
    _check(checks, "14.small.member_of_differs", 0, len(members_differ),
           detail=[list(m) for m in members_differ[:EXAMPLES]])
    stats["small"] = {"projects": len(seek_projects), "investigations": len(seek_invs),
                      "memberships": len(seek_members), "investigations_not_in_seek_held": len(held),
                      "memberships_project_not_in_seek": members_dropped,
                      "investigation_links_project_not_in_seek": links_dropped}


# --- the gate ------------------------------------------------------------------------------------

def gate_g(driver, db, sample_size: int = SAMPLE_SIZE, *, seed: int | None = None, accounts=GATE_ACCOUNTS,
           chunk: int = writer.SAMPLE_CHUNK) -> dict:
    """Run the gate G check families (module docstring) against MySQL; reads only.

    ``sample_size`` random samples (a reservoir over the MySQL scan, drawn with ``seed``, which is reported), and the
    strata beside them (``STRATUM_SIZE`` per sample type and per project, and every sample of the last
    ``RECENT_DAYS`` days), feed checks 2, 3, 7 and 11. ``accounts`` are the SEEK logins check 6 resolves by name.
    ``chunk`` is the MySQL page size.
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
    assay_state = run.read_assays()
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=RECENT_DAYS)
    recent_ids = frozenset(sources.recent_sample_ids(since, RECENT_CAP))
    mysql = _timed(timings, "mysql_scan", _scan_mysql, chunk, sample_size, random.Random(seed),
                   _protocol_reader(sops), strata_rng=random.Random(seed + 1), recent_ids=recent_ids)
    stats.update(mysql_samples=mysql.count, sampled_ids=[row["id"] for row in mysql.sampled],
                 sample_strata=mysql.strata)

    sampled = _timed(timings, "sampled_nodes", _sampled_nodes, driver, db, stats["sampled_ids"])
    groups = _timed(timings, "project_id_groups", _project_groups, driver, db)
    audit_rows = _records(_timed(timings, "type_label_audit", _read, driver, db, TYPE_LABEL_AUDIT))
    audit = dict(audit_rows[0]) if audit_rows else {}
    graph_catalog = _records(_read(driver, db, GRAPH_CATALOG))

    _timed(timings, "1.lineage", _check_lineage, driver, db, mysql, checks, stats)
    _timed(timings, "2.scope", _check_scope, mysql, groups, sampled, checks, stats)
    _timed(timings, "2.scope_edges", _check_in_project_edges, driver, db, checks, stats)
    _timed(timings, "3.catalog", _check_catalog, driver, db, graph_catalog, audit, sampled, checks, stats)
    _timed(timings, "4.samples", _check_samples, driver, db, mysql, audit, checks)
    _timed(timings, "5.attributes", _check_attributes, driver, db, checks, stats)
    _timed(timings, "6.people", _check_people, driver, db, groups, accounts, checks, stats, mysql.count)
    _timed(timings, "7.metadata", _check_metadata, cat, catalog_error, mysql, sampled, checks, stats)
    _timed(timings, "8.schema", _check_schema, driver, db, types, catalog_error, checks)
    assays = _timed(timings, "lineage_assays", _endpoint_assays, mysql.lineage)
    _timed(timings, "9.lineage", _check_labels, driver, db, mysql, assays, assay_map, checks, stats)
    _timed(timings, "10.samples", _check_type_labels, driver, db, checks)
    _timed(timings, "11.samples", _check_parent_lists, mysql, sampled, checks, stats)
    _timed(timings, "12.studies", _check_studies, driver, db, checks, stats)
    _timed(timings, "13.assays", _check_assays, driver, db, assay_state, mysql, assays, sampled, checks, stats)
    _timed(timings, "14.small", _check_small_tables, driver, db, checks, stats)
    return {"checks": checks, "pass": all(c["pass"] for c in checks), "stats": stats}
