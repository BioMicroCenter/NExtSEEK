"""Write graph schema v1.1 to Neo4j (docs/neo4j-schema.md, section "v1.1"; the design, section 6).

Every function takes ``(driver, db, ...)``, sends statements from ``cypher.py`` through ``driver.execute_query`` with
bound parameters, retries transient errors with batch upload's ``_retry``, and returns a counts dict (the index budget
returns the names of its indexes). Nothing here reads MySQL: ``run.py`` (G5) reads the sources, projects the rows and
calls these in the design's order:

    find_ghosts > delete_ghosts > relabel_orphans > archive_and_drop_child_of > ensure_constraints_v11 >
    write_sample_types > write_attributes > write_projects > write_people_and_memberships >
    write_investigation_projects > write_samples (per chunk) > write_missing_lineage >
    archive_and_drop_undeclared_derived_from > write_seek_studies > write_attribute_counts >
    write_sample_type_counts > ensure_index_budget > ensure_fulltext > await_indexes > write_graphmeta

Writes fail loudly: a schema statement that Neo4j refuses raises, and so does a catalog that would clash with the
graph. Shortfalls the graph can explain (a type, project or endpoint node that is missing) are counted, not raised,
so the caller can decide; gate G checks the result.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from typing import Iterable, Iterator

from neo4j import RoutingControl

from nextseek_api.batch_upload.neo4j_sync import _retry
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.catalog import role_for
from nextseek_api.graph_sync.projection import SampleProjection, label_for

log = logging.getLogger(__name__)

# Written to GraphMeta.schema_version; bumped with docs/neo4j-schema.md in the same commit.
SCHEMA_VERSION = "1.1"

SAMPLE_CHUNK = 5_000          # samples per write transaction (the design's default)
REL_CHUNK = 10_000            # relationship rows per write transaction
CHILD_OF_DELETE_BATCH = 50_000
DERIVED_FROM_DELETE_BATCH = 10_000
DERIVED_FROM_ARCHIVE_HEADER = "child_id\tparent_id\tchild_uuid\tparent_uuid\tprops\n"

# The index budget (design, "Technical defaults").
INDEX_MIN_SAMPLES = 1_000
INDEX_MAX_VALUE_CHARS = 4_000
INDEXED_WITH_ANY_VALUE = frozenset({"float", "integer", "date"})
NEVER_INDEXED_ROLES = frozenset({"lineage", "file"})


# --- plumbing ------------------------------------------------------------------------------------

def _run(driver, db, query, params=None, *, read=False, transformer=None):
    kwargs = {"database_": db}
    if read:
        kwargs["routing_"] = RoutingControl.READ
    if transformer is not None:
        kwargs["result_transformer_"] = transformer
    return _retry(lambda: driver.execute_query(query, params or {}, **kwargs))


def _records(result) -> list:
    return list(getattr(result, "records", None) or [])


def _one(result, key, default=0):
    """``key`` of the first record, or ``default`` when there is none or it is null."""
    records = _records(result)
    if not records:
        return default
    value = records[0][key]
    return default if value is None else value


def _counter(result, name) -> int:
    counters = getattr(getattr(result, "summary", None), "counters", None)
    return int(getattr(counters, name, 0) or 0)


def _batches(items: Iterable, size: int) -> Iterator[list]:
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _sorted_ids(ids) -> list:
    """Ids sorted even when legacy nodes mix ints with other types."""
    return sorted(ids, key=lambda v: (type(v).__name__, v))


# --- ghosts, orphans and CHILD_OF ----------------------------------------------------------------

def find_ghosts(driver, db, mysql_ids: set[int], mysql_uuids: set[str]) -> dict:
    """Classify the Sample nodes that the sync cannot simply overwrite. Read-only.

    - ``ghost_element_ids``: nodes whose ``id`` is on more than one node, is a MySQL sample id, and whose ``uuid`` is
      not in MySQL (the duplicates beside a live sample; deleting them lets ``Sample.id`` be unique).
    - ``orphan_ids``: every other Sample id not in MySQL, a duplicated one included (its nodes are all graph-only, so
      they are kept as orphans rather than deleted).
    - ``unresolved_duplicate_ids``: ids that would still sit on two nodes after the ghosts go (more than one node's
      uuid is in MySQL). The ``Sample.id`` constraint cannot be created until they are resolved.
    - ``idless_element_ids``: Sample nodes with no ``id``, which no MERGE can reach.
    """
    def scan(result):
        total, missing = 0, set()
        for record in result:
            total += 1
            sample_id = record["id"]
            if sample_id is not None and sample_id not in mysql_ids:
                missing.add(sample_id)
        return total, missing

    total, not_in_mysql = _run(driver, db, q.SAMPLE_IDS, read=True, transformer=scan)
    duplicate_ids = [r["id"] for r in _records(_run(driver, db, q.DUPLICATE_SAMPLE_IDS, read=True))]
    ghosts, unresolved = [], []
    if duplicate_ids:
        nodes_by_id = defaultdict(list)
        for record in _records(_run(driver, db, q.NODES_FOR_IDS, {"ids": duplicate_ids}, read=True)):
            nodes_by_id[record["id"]].append(record)
        for sample_id in duplicate_ids:
            if sample_id not in mysql_ids:
                continue
            nodes = nodes_by_id.get(sample_id, [])
            ghosts.extend(n["element_id"] for n in nodes if n["uuid"] not in mysql_uuids)
            if sum(1 for n in nodes if n["uuid"] in mysql_uuids) > 1:
                unresolved.append(sample_id)
    idless = [r["element_id"] for r in _records(_run(driver, db, q.SAMPLES_WITHOUT_ID, read=True))]
    return {"ghost_element_ids": sorted(ghosts), "orphan_ids": _sorted_ids(not_in_mysql),
            "unresolved_duplicate_ids": _sorted_ids(unresolved), "idless_element_ids": idless,
            "duplicate_ids": len(duplicate_ids), "sample_nodes": total}


def delete_ghosts(driver, db, element_ids) -> dict:
    """DETACH DELETE the Sample nodes with these element ids (from ``find_ghosts``)."""
    deleted = 0
    for batch in _batches(element_ids, REL_CHUNK):
        deleted += _one(_run(driver, db, q.DELETE_GHOSTS, {"element_ids": batch}), "n")
    return {"ghosts_deleted": deleted}


def relabel_orphans(driver, db, ids, element_ids=()) -> dict:
    """Swap ``:Sample`` for ``:OrphanSample`` on graph-only nodes, keeping their properties and edges.

    ``ids`` are Sample ids not in MySQL; ``element_ids`` are Sample nodes with no id at all.
    """
    relabeled = 0
    for batch in _batches(ids, REL_CHUNK):
        relabeled += _one(_run(driver, db, q.RELABEL_ORPHANS, {"ids": batch}), "n")
    for batch in _batches(element_ids, REL_CHUNK):
        relabeled += _one(_run(driver, db, q.RELABEL_ORPHANS_BY_ELEMENT_ID, {"element_ids": batch}), "n")
    return {"orphans_relabeled": relabeled}


def archive_and_drop_child_of(driver, db, out_path: str, declared_pairs: set[tuple[str, str]]) -> dict:
    """Archive every CHILD_OF pair to a TSV, then delete CHILD_OF in batches of 50,000.

    The file has a header and one row per distinct (child uuid, parent uuid) pair; ``declared`` is ``true`` when the
    pair is in ``declared_pairs`` (what MySQL's parent tokens declare) and ``false`` otherwise. It is written to a
    ``.partial`` file and renamed into place before the first delete, so a failed write deletes nothing. A graph with
    no CHILD_OF writes no file, which keeps the archive an earlier run made.
    """
    if not _one(_run(driver, db, q.CHILD_OF_COUNT, read=True), "n"):
        return {"child_of_pairs": 0, "child_of_undeclared": 0, "child_of_deleted": 0, "archive_path": None}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    partial = out_path + ".partial"

    def write(result):
        pairs = undeclared = 0
        with open(partial, "w", encoding="utf-8", newline="") as fh:
            fh.write("child_uuid\tparent_uuid\tdeclared\n")
            for record in result:
                child, parent = record["child_uuid"] or "", record["parent_uuid"] or ""
                declared = (child, parent) in declared_pairs
                fh.write(f"{child}\t{parent}\t{'true' if declared else 'false'}\n")
                pairs += 1
                undeclared += not declared
        return pairs, undeclared

    pairs, undeclared = _run(driver, db, q.CHILD_OF_PAIRS, read=True, transformer=write)
    os.replace(partial, out_path)
    log.info("CHILD_OF: archived %d pairs (%d undeclared) to %s", pairs, undeclared, out_path)
    deleted = 0
    while True:
        n = _one(_run(driver, db, q.DELETE_CHILD_OF_BATCH, {"batch": CHILD_OF_DELETE_BATCH}), "deleted")
        if not n:
            break
        deleted += n
    return {"child_of_pairs": pairs, "child_of_undeclared": undeclared, "child_of_deleted": deleted,
            "archive_path": out_path}


# --- constraints and indexes ---------------------------------------------------------------------

def ensure_constraints_v11(driver, db) -> dict:
    """Drop v1.0's unique ``Sample.uuid``, then create every v1.1 constraint and index. A refusal raises."""
    statements = [*q.DROP_V10_CONSTRAINTS, *q.CONSTRAINTS_V11]
    for statement in statements:
        _run(driver, db, statement)
    return {"schema_statements": len(statements)}


def _qualifies(key, entry: dict, bench_keys) -> bool:
    title = entry["title"]
    if (entry.get("role") or role_for(title)) in NEVER_INDEXED_ROLES:
        return False
    # A range-indexed value over about 8 KB fails the whole write; a failed cast keeps its raw string, so this
    # applies to numeric and date attributes too.
    if int(entry.get("max_len") or 0) > INDEX_MAX_VALUE_CHARS:
        return False
    if key in bench_keys or (entry.get("sample_type"), title) in bench_keys:
        return True
    count = int(entry.get("sample_count") or 0)
    if entry.get("value_type") in INDEXED_WITH_ANY_VALUE:
        return count > 0
    return count >= INDEX_MIN_SAMPLES


def ensure_index_budget(driver, db, census: dict, bench_keys=frozenset()) -> list[str]:
    """Create one range index per qualifying (type label, attribute title) pair; drop ``gs_`` indexes that no longer do.

    ``census`` maps any key (G5 uses the attribute key ``"<type id>:<title>"``) to an entry with ``sample_type`` (the
    type title) or ``label``, ``title``, ``value_type``, ``sample_count`` (samples holding a value) and ``max_len``
    (the longest stored value, in characters), and optionally ``role`` (else ``catalog.role_for(title)``).

    A pair qualifies when its role is not ``lineage`` or ``file`` and no value is longer than 4,000 characters, and
    either its ``value_type`` is float, integer or date and it has values, or it is a string on at least 1,000
    samples, or it is a benchmark key. ``bench_keys`` holds census keys or (sample type title, attribute title)
    pairs. Returns the budget's index names, sorted. Index population runs in the background: ``await_indexes``.
    """
    wanted: dict[str, str] = {}
    for key, entry in census.items():
        if not _qualifies(key, entry, bench_keys):
            continue
        label = entry.get("label") or label_for(entry["sample_type"])
        name, statement = q.budget_index(label, entry["title"])
        wanted[name] = statement
    existing = [r["name"] for r in _records(_run(driver, db, q.GS_INDEX_NAMES, read=True))]
    for name in sorted(set(existing) - set(wanted)):
        _run(driver, db, q.drop_index(name))
    for name in sorted(wanted):
        _run(driver, db, wanted[name])
    return sorted(wanted)


def ensure_fulltext(driver, db) -> dict:
    """Create the ``sample_search_text`` fulltext index on ``Sample.search_text``."""
    _run(driver, db, q.FULLTEXT)
    return {"fulltext_index": q.FULLTEXT_INDEX}


def await_indexes(driver, db, timeout_s: float = 3600, poll_s: float = 5) -> dict:
    """Wait until every index is ONLINE. Raises RuntimeError naming any FAILED index, TimeoutError on the deadline."""
    deadline = time.monotonic() + timeout_s
    while True:
        rows = _records(_run(driver, db, q.INDEX_STATES, read=True))
        failed = sorted(r["name"] for r in rows if r["state"] == "FAILED")
        if failed:
            raise RuntimeError(f"indexes FAILED: {', '.join(failed)}")
        pending = sorted(r["name"] for r in rows if r["state"] != "ONLINE")
        if not pending:
            return {"indexes_online": len(rows)}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{len(pending)} indexes not ONLINE after {timeout_s} s: {', '.join(pending[:10])}")
        time.sleep(poll_s)


# --- the catalog ---------------------------------------------------------------------------------

def write_sample_types(driver, db, rows: list[dict]) -> dict:
    """MERGE every SampleType on ``id`` and replace its property map with the row (``catalog.build_sample_types``).

    An id-less node from v1.0 first takes the id of the type with its title. A node that holds a title under a
    different id raises ValueError before anything is written. SampleType nodes left without a MySQL id are
    reported, not deleted. ``sample_count`` and ``attribute_count`` are wiped by the replace: write them afterwards
    with ``write_sample_type_counts``.
    """
    rows = list(rows)
    keys = [{"id": int(r["id"]), "title": r["title"]} for r in rows]
    conflicts = _records(_run(driver, db, q.SAMPLE_TYPE_TITLE_CONFLICTS, {"rows": keys}, read=True))
    if conflicts:
        detail = "; ".join(f"{c['title']!r} is id {c['graph_id']} in the graph, {c['mysql_id']} in MySQL"
                           for c in conflicts)
        raise ValueError(f"SampleType titles are held under other ids: {detail}")
    _run(driver, db, q.BACKFILL_SAMPLE_TYPE_ID, {"rows": keys})
    for batch in _batches(rows, SAMPLE_CHUNK):
        _run(driver, db, q.MERGE_SAMPLE_TYPES, {"rows": batch})
    leftover = _records(_run(driver, db, q.SAMPLE_TYPES_NOT_IN, {"ids": [k["id"] for k in keys]}, read=True))
    graph_only = sorted((r["title"] for r in leftover), key=str)
    if graph_only:
        log.warning("SampleType nodes with no MySQL sample type: %s", graph_only)
    return {"sample_types_written": len(rows), "graph_only_sample_types": graph_only}


def write_attributes(driver, db, rows: list[dict]) -> dict:
    """Replace the Attribute catalog: delete every Attribute whose ``key`` is not in ``rows``, then MERGE each on
    ``key``, replace its properties and link it from its SampleType.

    Deleting first means a renamed attribute's old node gives up its ``id`` before the new key takes it. Raises
    ValueError on an empty catalog (it would delete every Attribute) and on a repeated key. ``sample_count`` is wiped
    by the replace: write it afterwards with ``write_attribute_counts``.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("no Attribute rows; refusing to delete the whole catalog")
    repeated = sorted(k for k, n in Counter(r["key"] for r in rows).items() if n > 1)
    if repeated:
        raise ValueError(f"Attribute keys repeat: {repeated}")
    _run(driver, db, q.DELETE_GONE_ATTRIBUTES, {"keys": [r["key"] for r in rows]})
    linked = 0
    for batch in _batches(rows, SAMPLE_CHUNK):
        linked += _one(_run(driver, db, q.MERGE_ATTRIBUTES, {"rows": batch}), "linked")
    return {"attributes_written": len(rows), "attributes_without_type": len(rows) - linked}


def write_attribute_counts(driver, db, counts: dict[str, int]) -> dict:
    """Set ``Attribute.sample_count`` from the census (attribute key to samples holding a value); 0 for the rest."""
    rows = [{"key": key, "count": int(n)} for key, n in counts.items()]
    set_count = 0
    for batch in _batches(rows, REL_CHUNK):
        set_count += _one(_run(driver, db, q.SET_ATTRIBUTE_COUNTS, {"rows": batch}), "n")
    zeroed = _one(_run(driver, db, q.ZERO_ATTRIBUTE_COUNTS, {"keys": list(counts)}), "n")
    return {"attribute_counts_set": set_count, "attribute_counts_zeroed": zeroed}


def write_sample_type_counts(driver, db) -> dict:
    """Set ``SampleType.sample_count`` (Samples with OF_TYPE to it) and ``attribute_count`` from the graph."""
    return {"sample_type_counts_set": _one(_run(driver, db, q.SET_SAMPLE_TYPE_COUNTS), "n")}


# --- projects, people, investigations ------------------------------------------------------------

def write_projects(driver, db, rows: list[dict]) -> dict:
    """Replace the Project nodes with ``rows`` (``id``, ``title``); a project gone from MySQL is deleted."""
    clean = [{k: v for k, v in r.items() if v is not None} for r in rows]
    if not clean:
        raise ValueError("no Project rows; refusing to delete every Project")
    _run(driver, db, q.DELETE_GONE_PROJECTS, {"ids": [r["id"] for r in clean]})
    _run(driver, db, q.MERGE_PROJECTS, {"rows": clean})
    return {"projects_written": len(clean)}


def write_people_and_memberships(driver, db, rows: list[dict]) -> dict:
    """Replace every MEMBER_OF from ``sources.memberships()`` rows (``person_id``, ``project_id``, ``has_left``,
    ``time_left_at``). Person nodes carry ``id`` only; a person with no membership left is deleted."""
    rows = list(rows)
    ids = sorted({int(r["person_id"]) for r in rows})
    _run(driver, db, q.DELETE_MEMBER_OF)
    _run(driver, db, q.DELETE_GONE_PEOPLE, {"ids": ids})
    _run(driver, db, q.MERGE_PEOPLE, {"ids": ids})
    linked = 0
    for batch in _batches(rows, REL_CHUNK):
        linked += _one(_run(driver, db, q.MERGE_MEMBER_OF, {"rows": batch}), "linked")
    return {"people_written": len(ids), "memberships_written": linked, "memberships_dropped": len(rows) - linked}


def write_investigation_projects(driver, db, investigations: list[dict], links: list[dict]) -> dict:
    """MERGE every SEEK Investigation on ``id`` and replace every ``(:Investigation)-[:IN_PROJECT]->(:Project)``.

    ``Investigation.project_id`` (read by ``services/sampletype_connections.py``) is the investigation's lowest linked
    project id, and absent when it has none.
    """
    project_of: dict[int, int] = {}
    link_rows, seen = [], set()
    for link in links:
        pair = (int(link["investigation_id"]), int(link["project_id"]))
        if pair in seen:
            continue
        seen.add(pair)
        link_rows.append({"investigation_id": pair[0], "project_id": pair[1]})
        project_of[pair[0]] = min(project_of.get(pair[0], pair[1]), pair[1])
    rows = [{"id": int(i["id"]), "title": i.get("title"), "description": i.get("description"),
             "project_id": project_of.get(int(i["id"]))} for i in investigations]
    for batch in _batches(rows, REL_CHUNK):
        _run(driver, db, q.MERGE_INVESTIGATIONS, {"rows": batch})
    _run(driver, db, q.DELETE_INVESTIGATION_IN_PROJECT)
    linked = 0
    for batch in _batches(link_rows, REL_CHUNK):
        linked += _one(_run(driver, db, q.MERGE_INVESTIGATION_IN_PROJECT, {"rows": batch}), "linked")
    return {"investigations_written": len(rows), "investigation_links": linked,
            "investigation_links_dropped": len(link_rows) - linked}


# --- samples, lineage, studies -------------------------------------------------------------------

def write_samples(driver, db, projections: list[SampleProjection], chunk: int = SAMPLE_CHUNK) -> dict:
    """Write projected samples, one ``WRITE_SAMPLES`` transaction per ``chunk``.

    Each node's property map is replaced by the projection's ``props`` plus ``synced_at``; its ``T_`` label is set
    and any other ``T_`` label removed; OF_TYPE and IN_PROJECT are rebuilt. ``untyped`` counts samples whose
    SampleType node is missing, ``in_project_missing`` project links whose Project node is.
    """
    written = typed = linked = expected = failures = 0
    for batch in _batches(projections, chunk):
        rows = [{"id": p.id, "label": p.label, "sample_type_id": p.sample_type_id, "props": p.props}
                for p in batch]
        result = _run(driver, db, q.WRITE_SAMPLES, {"rows": rows})
        written += _one(result, "written")
        typed += _one(result, "typed")
        linked += _one(result, "linked")
        expected += sum(len(p.props.get("project_ids") or ()) for p in batch)
        failures += sum(len(p.cast_failures) for p in batch)
    return {"samples_written": written, "of_type": typed, "untyped": written - typed, "in_project": linked,
            "in_project_expected": expected, "in_project_missing": expected - linked, "cast_failures": failures}


def write_missing_lineage(driver, db, pairs: Iterable[tuple[int, int]], chunk: int = REL_CHUNK) -> dict:
    """MERGE a DERIVED_FROM edge for every declared (child id, parent id) pair; existing edges keep their properties.

    A new edge records ``child_id`` and ``parent_id`` in the form existing edges use (sample ids, or uuids when the
    first existing edge has a string ``child_id``). ``lineage_dropped`` counts pairs with an endpoint missing from
    the graph. ``pairs`` may be any iterable; repeats within a chunk are sent once.
    """
    sent = matched = created = 0
    by_uuid = None
    for batch in _batches(pairs, chunk):
        if by_uuid is None:
            form = _one(_run(driver, db, q.DERIVED_FROM_ID_FORM, read=True), "child_id", default=None)
            by_uuid = isinstance(form, str)
        rows = [list(pair) for pair in dict.fromkeys((int(c), int(p)) for c, p in batch)]
        result = _run(driver, db, q.WRITE_MISSING_LINEAGE, {"rows": rows, "by_uuid": by_uuid})
        sent += len(rows)
        matched += _one(result, "matched")
        created += _counter(result, "relationships_created")
    return {"lineage_pairs": sent, "lineage_matched": matched, "lineage_created": created,
            "lineage_dropped": sent - matched}


def _tsv_field(value) -> str:
    """One archive field on one line: None is empty; backslash, tab, CR and LF are escaped as ``\\\\ \\t \\r \\n``."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def _props_json(props) -> str:
    """An edge's properties as JSON with sorted keys; non-ASCII escaped, temporal values as ISO strings."""
    return json.dumps(dict(props or {}), sort_keys=True, ensure_ascii=True, default=str)


def _is_declared(declared_pairs, child, parent) -> bool:
    try:
        return (child, parent) in declared_pairs
    except TypeError:  # an unhashable legacy id is never a declared pair
        return False


def archive_and_drop_undeclared_derived_from(driver, db, out_path: str, declared_pairs) -> dict:
    """Archive to a TSV, then delete, every DERIVED_FROM between two Sample nodes that MySQL does not declare.

    ``declared_pairs`` answers ``(child id, parent id) in declared_pairs`` for the pairs MySQL's parent tokens
    declare (``run.DeclaredIdPairs``, or a set of tuples). Every DERIVED_FROM between two Sample nodes is streamed
    once; an undeclared edge (a pair a later Parent edit left stale, a self-loop, a token that no longer resolves)
    becomes one row: ``child_id``, ``parent_id``, ``child_uuid``, ``parent_uuid`` and ``props``, the edge's
    properties as JSON (uuids escaped by ``_tsv_field``). The file is written to a ``.partial`` path and renamed
    into place before the first delete, so a failed write deletes nothing. The edges are then deleted by element id
    in batches, each delete matching only an edge between two Sample nodes, so an edge touching an OrphanSample is
    never deleted. With nothing undeclared no file is written (an earlier archive is kept) and nothing is deleted.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    partial = out_path + ".partial"

    def archive(result):
        edges, undeclared = 0, []  # built here, so a retried read starts clean
        with open(partial, "w", encoding="utf-8", newline="") as fh:
            fh.write(DERIVED_FROM_ARCHIVE_HEADER)
            for record in result:
                edges += 1
                child, parent = record["child_id"], record["parent_id"]
                if _is_declared(declared_pairs, child, parent):
                    continue
                fh.write("\t".join((_tsv_field(child), _tsv_field(parent), _tsv_field(record["child_uuid"]),
                                    _tsv_field(record["parent_uuid"]), _props_json(record["props"]))) + "\n")
                undeclared.append(record["element_id"])
        return edges, undeclared

    try:
        edges, element_ids = _run(driver, db, q.DERIVED_FROM_BETWEEN_SAMPLES, read=True, transformer=archive)
    except BaseException:
        if os.path.exists(partial):
            os.remove(partial)
        raise
    if not element_ids:
        os.remove(partial)
        return {"derived_from_between_samples": edges, "derived_from_undeclared": 0, "derived_from_deleted": 0,
                "derived_from_archive_path": None}
    os.replace(partial, out_path)
    log.info("DERIVED_FROM: archived %d undeclared of %d edges between samples to %s", len(element_ids), edges,
             out_path)
    deleted = 0
    for batch in _batches(element_ids, DERIVED_FROM_DELETE_BATCH):
        deleted += _one(_run(driver, db, q.DELETE_UNDECLARED_DERIVED_FROM, {"element_ids": batch}), "deleted")
    if deleted != len(element_ids):
        log.warning("DERIVED_FROM: %d undeclared edges archived but %d deleted", len(element_ids), deleted)
    return {"derived_from_between_samples": edges, "derived_from_undeclared": len(element_ids),
            "derived_from_deleted": deleted, "derived_from_archive_path": out_path}


def write_seek_studies(driver, db, links: list[dict]) -> dict:
    """Place samples in their SEEK studies, for samples that are in no paper-level Study.

    ``links`` are ``sources.seek_study_links()`` rows (``sample_id``, ``study_id``, ``study_title``,
    ``investigation_id``). A SEEK study becomes a Study node MERGEd on ``seek_study_id`` (no ``id``: the paper-level
    Study nodes own graph-local ids), linked to its Investigation; each eligible sample gets IN_STUDY to it. A sample
    already in a paper-level Study (one with no ``seek_study_id``) is left as it is.
    """
    in_paper = {r["id"] for r in _records(_run(driver, db, q.SAMPLES_IN_PAPER_STUDIES, read=True))}
    studies: dict[int, dict] = {}
    edges: list[tuple[int, int]] = []
    skipped = set()
    for link in links:
        sample_id, study_id = int(link["sample_id"]), int(link["study_id"])
        if sample_id in in_paper:
            skipped.add(sample_id)
            continue
        studies.setdefault(study_id, {"study_id": study_id, "title": link.get("study_title"),
                                      "investigation_id": link.get("investigation_id")})
        edges.append((sample_id, study_id))
    for batch in _batches([studies[k] for k in sorted(studies)], REL_CHUNK):
        _run(driver, db, q.MERGE_SEEK_STUDIES, {"rows": batch})
    linked = 0
    for batch in _batches(edges, REL_CHUNK):
        rows = [{"sample_id": s, "study_id": st} for s, st in batch]
        linked += _one(_run(driver, db, q.MERGE_SEEK_IN_STUDY, {"rows": rows}), "linked")
    return {"seek_studies": len(studies), "in_study_written": linked, "in_study_dropped": len(edges) - linked,
            "samples_skipped_in_paper_study": len(skipped)}


# --- GraphMeta -----------------------------------------------------------------------------------

def write_graphmeta(driver, db, catalog_hash: str) -> dict:
    """Stamp the single GraphMeta node with the schema version, the catalog hash and ``synced_at``."""
    _run(driver, db, q.WRITE_GRAPHMETA, {"schema_version": SCHEMA_VERSION, "catalog_hash": catalog_hash})
    return {"schema_version": SCHEMA_VERSION, "catalog_hash": catalog_hash}
