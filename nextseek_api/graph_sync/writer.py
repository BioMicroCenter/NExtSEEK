"""Write graph schema v1.2 to Neo4j (docs/neo4j-schema.md, sections "v1.1" and "v1.2"; the POC design, section 6;
the sync design, sections 6, 7.3 and 9).

Every function takes ``(driver, db, ...)``, sends statements from ``cypher.py`` through ``driver.execute_query`` with
bound parameters, retries transient errors with ``_retry``, and returns a counts dict (the index budget returns the
names of its indexes). Nothing here reads MySQL: ``run.py`` (G5) reads the sources, projects the rows and calls these
in the design's order:

    find_ghosts > delete_ghosts > relabel_orphans > archive_and_drop_child_of > ensure_constraints_v11 >
    write_sample_types > write_attributes > write_projects > write_people_and_memberships >
    write_investigation_projects > write_samples (per chunk) > write_missing_lineage >
    archive_and_drop_undeclared_derived_from > write_seek_studies (the full sync now runs
    study_links.rebuild_in_study instead) > write_attribute_counts >
    write_sample_type_counts > ensure_index_budget > ensure_fulltext > await_indexes > write_graphmeta

Schema 1.2 adds what the by-id syncs need: ``retire_samples`` (the deletion rule), ``edges_incident`` and
``write_edge_labels`` (DERIVED_FROM labels, new ones only unless the operator approves changes),
``archive_and_drop_undeclared_for_children``, ``sample_hashes`` (the ``(id, source_hash)`` stream) and ``graphmeta``.
The studies release adds ``write_seek_study_nodes`` (every SEEK study's node, its investigation first) and
``replace_seek_in_study`` (IN_STUDY follows SEEK, a removal archived to ``in_study_removed.tsv`` first). The studies
tool adds ``share_graph_check``, a read of how a share's samples stand.

Writes fail loudly: a schema statement that Neo4j refuses raises, and so does a catalog that would clash with the
graph. Shortfalls the graph can explain (a type, project or endpoint node that is missing) are counted, not raised,
so the caller can decide; gate G checks the result.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Mapping

from neo4j import RoutingControl
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError

from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync import labels
from nextseek_api.graph_sync.catalog import role_for
from nextseek_api.graph_sync.projection import SampleProjection, label_for
from nextseek_graph import schema

log = logging.getLogger(__name__)

# Written to GraphMeta.schema_version: the contract's, which moves with a docs/neo4j-schema.md section's Versioning
# subsection (nextseek_graph/README.md).
SCHEMA_VERSION = schema.SCHEMA_VERSION

SAMPLE_CHUNK = 5_000          # samples per write transaction (the design's default)
REL_CHUNK = 10_000            # relationship rows per write transaction
HASH_PAGE = 100_000           # (id, source_hash) rows per read page
CHILD_OF_DELETE_BATCH = 50_000
DERIVED_FROM_DELETE_BATCH = 10_000
DERIVED_FROM_ARCHIVE_HEADER = "child_id\tparent_id\tchild_uuid\tparent_uuid\tprops\n"
RETIRED_ARCHIVE_HEADER = "id\tuuid\ttype\tincident_edges\n"
SAMPLE_TYPES_DELETED_FILE = "sample_types_deleted.tsv"
SAMPLE_TYPES_ARCHIVE_HEADER = "id\ttitle\tlabel\tattribute_keys\n"
INVESTIGATIONS_DELETED_FILE = "investigations_deleted.tsv"
INVESTIGATIONS_ARCHIVE_HEADER = "id\ttitle\tproject_ids\n"
IN_STUDY_ARCHIVE_HEADER = "sample_id\tseek_study_id\tstudy_id\tedge_element_id\tpath\n"
# replace_seek_in_study's counts, always all present.
IN_STUDY_COUNTS = ("in_study_rows", "in_study_added", "in_study_removed", "in_study_stale",
                   "in_study_kept_no_seek_study", "in_study_paper_samples", "in_study_withheld",
                   "in_study_paper_links_written", "in_study_paper_investigation_unknown",
                   "in_study_samples_missing", "in_study_studies_missing")

# The seven DERIVED_FROM label properties, always written together (cypher.EDGE_LABEL_KEYS).
EDGE_LABEL_KEYS = q.EDGE_LABEL_KEYS
PLURAL_LABEL_KEYS = schema.DERIVED_FROM_PLURAL_ASSAY_KEYS
GRAPHMETA_KEYS = schema.GRAPHMETA_KEYS

_INT64_MIN = -(2 ** 63)  # below every Sample id, the first keyset bound
_TRANSIENT_ERRORS = (TransientError, ServiceUnavailable, SessionExpired)

# The index budget (design, "Technical defaults").
INDEX_MIN_SAMPLES = 1_000
INDEX_MAX_VALUE_CHARS = 4_000
INDEXED_WITH_ANY_VALUE = frozenset({"float", "integer", "date"})
NEVER_INDEXED_ROLES = frozenset({"lineage", "file"})


# --- plumbing ------------------------------------------------------------------------------------

def _retry(fn: Callable, attempts: int = 3, backoff_base: float = 0.5):
    """Call ``fn``, retrying a transient Neo4j error (a deadlock, a lost connection) with exponential backoff and
    jitter. Any other error, and the last attempt's, is raised."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except _TRANSIENT_ERRORS as exc:
            if attempt == attempts:
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.3)
            log.warning("Neo4j transient error (attempt %d/%d), retrying in %.1f s: %s", attempt, attempts, delay,
                        exc)
            time.sleep(delay)


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


def _first(result) -> dict:
    """The first record as a dict, or {} when there is none."""
    records = _records(result)
    return dict(records[0]) if records else {}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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


def _id_key(value) -> tuple:
    """A sort key for ids that legacy nodes may mix with other types, or leave null."""
    return (type(value).__name__, "" if value is None else value)


def _sorted_ids(ids) -> list:
    """Ids sorted even when legacy nodes mix ints with other types."""
    return sorted(ids, key=_id_key)


def _append_rows(path: str, header: str, lines: list[str]) -> None:
    """Append ``lines`` to the TSV at ``path``, the header first when the file is new or empty, and flush them to
    disk, so an archive is complete before the delete it precedes. A last line a crash cut short is ended first, so
    the new lines never run on from it. Raises OSError when it cannot be written."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a+b") as fh:
        if fh.tell() == 0:
            fh.write(header.encode("utf-8"))
        else:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")
        fh.write("".join(lines).encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


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
    """Turn graph-only nodes graph_sync never wrote into ``:OrphanSample`` (``cypher.ORPHAN_SWAP``, the deletion
    rule's second half): ``:Sample``, every ``T_`` label, OF_TYPE and IN_PROJECT go, ``orphaned_at`` is set, the
    properties and DERIVED_FROM stay.

    ``ids`` are Sample ids not in MySQL; ``element_ids`` are Sample nodes with no id at all. A node carrying
    ``synced_at`` is left as it is: it mirrors a deleted row, and ``retire_samples`` archives and deletes it.
    """
    relabeled = 0
    for batch in _batches(ids, REL_CHUNK):
        relabeled += _one(_run(driver, db, q.RELABEL_ORPHANS, {"ids": batch}), "n")
    for batch in _batches(element_ids, REL_CHUNK):
        relabeled += _one(_run(driver, db, q.RELABEL_ORPHANS_BY_ELEMENT_ID, {"element_ids": batch}), "n")
    return {"orphans_relabeled": relabeled}


def retire_samples(driver, db, ids, archive_path) -> dict:
    """Apply the deletion rule (the sync design, section 9) to Sample ids MySQL no longer holds.

    - A ``:Sample`` graph_sync wrote (it carries ``synced_at``) mirrors a row that is gone: its id, uuid, type and
      incident-edge count are appended to ``archive_path`` (the run's ``retired.tsv``, header
      ``RETIRED_ARCHIVE_HEADER``, fields escaped as in the lineage archive) and flushed, and only then is it DETACH
      DELETEd.
    - A ``:Sample`` graph_sync never wrote becomes an ``:OrphanSample`` through the statement ``relabel_orphans``
      uses (``cypher.ORPHAN_SWAP``).
    - An id with no ``:Sample`` node (retired already, or an existing ``:OrphanSample``, which stays as it is) is
      counted in ``retire_not_found``.

    The caller passes only ids MySQL lacks; nothing here reads MySQL. ``archive_path`` may be None only when no node
    has to be deleted: a delete with nowhere to archive raises ValueError, and an archive that cannot be written
    raises OSError, both before anything is written to the graph.
    """
    wanted = list(dict.fromkeys(ids))
    found = []
    for batch in _batches(wanted, REL_CHUNK):
        found.extend(_records(_run(driver, db, q.RETIRE_CANDIDATES, {"ids": batch}, read=True)))
    synced = [r for r in found if r["synced"]]
    never_synced = [r["element_id"] for r in found if not r["synced"]]
    deleted = orphaned = 0
    if synced:
        if not archive_path:
            raise ValueError(f"{len(synced)} synced samples to retire and no archive path to record them in")
        _append_rows(archive_path, RETIRED_ARCHIVE_HEADER,
                     ["\t".join(_tsv_field(r[k]) for k in ("id", "uuid", "type", "incident_edges")) + "\n"
                      for r in synced])
        for batch in _batches([r["element_id"] for r in synced], REL_CHUNK):
            deleted += _one(_run(driver, db, q.DELETE_RETIRED, {"element_ids": batch}), "n")
        log.info("retire: archived and deleted %d synced samples (%s)", deleted, archive_path)
    for batch in _batches(never_synced, REL_CHUNK):
        orphaned += _one(_run(driver, db, q.RELABEL_ORPHANS_BY_ELEMENT_ID, {"element_ids": batch}), "n")
    return {"retire_requested": len(wanted), "retired_deleted": deleted, "retired_orphaned": orphaned,
            "retire_not_found": len(wanted) - len({r["id"] for r in found}),
            "retired_archive_path": archive_path if synced else None}


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

def write_sample_types(driver, db, rows: list[dict], *, archive_path: str | None = None) -> dict:
    """MERGE every SampleType on ``id`` and replace its property map with the row (``catalog.build_sample_types``).

    An id-less node from v1.0 first takes the id of the type with its title. A node that holds a title under a
    different id raises ValueError before anything is written, unless SEEK no longer has its id and no Sample reaches
    it: with ``archive_path`` every such node (and its Attribute nodes) is appended to that archive, flushed, and
    deleted before the write, so a type deleted and recreated in SEEK under its old title is written. A node left
    without a MySQL id that still holds samples is reported, not deleted (the samples' own retire path handles them).
    ``sample_count`` and ``attribute_count`` are wiped by the replace: write them afterwards with
    ``write_sample_type_counts``.
    """
    rows = list(rows)
    keys = [{"id": int(r["id"]), "title": r["title"]} for r in rows]
    ids = [k["id"] for k in keys]
    conflicts = _records(_run(driver, db, q.SAMPLE_TYPE_TITLE_CONFLICTS, {"rows": keys, "ids": ids}, read=True))
    if conflicts:
        detail = "; ".join(f"{c['title']!r} is id {c['graph_id']} in the graph, {c['mysql_id']} in MySQL"
                           for c in conflicts)
        raise ValueError(f"SampleType titles are held under other ids: {detail}")
    deleted = 0
    if archive_path:
        gone = _records(_run(driver, db, q.SAMPLE_TYPES_GONE, {"ids": ids}, read=True))
        if gone:
            _append_rows(archive_path, SAMPLE_TYPES_ARCHIVE_HEADER,
                         ["\t".join(_tsv_field(v) for v in (g["id"], g["title"], g["label"],
                                                             ",".join(sorted(g["attribute_keys"] or []))))
                          + "\n" for g in gone])
            deleted = _one(_run(driver, db, q.DELETE_SAMPLE_TYPES,
                                {"element_ids": [g["element_id"] for g in gone]}), "deleted")
    _run(driver, db, q.BACKFILL_SAMPLE_TYPE_ID, {"rows": keys})
    for batch in _batches(rows, SAMPLE_CHUNK):
        _run(driver, db, q.MERGE_SAMPLE_TYPES, {"rows": batch})
    leftover = _records(_run(driver, db, q.SAMPLE_TYPES_NOT_IN, {"ids": [k["id"] for k in keys]}, read=True))
    graph_only = sorted((r["title"] for r in leftover), key=str)
    if graph_only:
        log.warning("SampleType nodes with no MySQL sample type that still hold samples: %s", graph_only)
    return {"sample_types_written": len(rows), "graph_only_sample_types": graph_only,
            "sample_types_deleted": deleted}


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


def merge_missing_projects(driver, db, project_ids, project_rows) -> dict:
    """MERGE the Project node of each of ``project_ids`` that has none, from ``project_rows`` (``sources.projects()``),
    before anything links to it: ``WRITE_SAMPLES`` and ``MERGE_INVESTIGATION_IN_PROJECT`` MATCH the Project, so a link
    to a missing one is dropped. Nothing is deleted (the whole-table write owns the deletes). A missing id with no
    ``projects`` row is counted in ``project_ids_not_in_seek`` and written nowhere: SEEK data to fix."""
    wanted = sorted({int(p) for p in project_ids})
    if not wanted:
        return {"projects_written_for_links": 0, "project_ids_not_in_seek": 0}
    present = {r["id"] for r in _records(_run(driver, db, q.PROJECT_IDS_PRESENT, {"ids": wanted}, read=True))}
    rows = {int(r["id"]): r for r in project_rows}
    missing = [p for p in wanted if p not in present]
    new = [{k: v for k, v in rows[p].items() if v is not None} for p in missing if p in rows]
    if new:
        _run(driver, db, q.MERGE_PROJECTS, {"rows": new})
    return {"projects_written_for_links": len(new),
            "project_ids_not_in_seek": sum(1 for p in missing if p not in rows)}


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


def write_investigation_projects(driver, db, investigations: list[dict], links: list[dict], *,
                                 archive_path: str | None = None, seek_study_ids=None) -> dict:
    """MERGE every SEEK Investigation on ``id`` and replace every ``(:Investigation)-[:IN_PROJECT]->(:Project)``.

    ``Investigation.project_id`` is the investigation's lowest linked project id, and absent when it has none (the
    connections endpoint reads the IN_PROJECT links instead). With ``archive_path`` an Investigation node whose id
    SEEK no longer has and that no Study holds is appended to that archive (id, title, project ids), flushed, and
    deleted; one a Study still holds is kept and counted in ``investigations_not_in_seek_held``. A Study holds it
    only while SEEK still has its study (``seek_study_ids``, every SEEK study id, required with ``archive_path``) or
    when it is a graph-only paper (no ``seek_study_id``); the node of a gone SEEK study stays and loses its
    IN_INVESTIGATION. An empty ``investigations`` with nodes to delete raises ValueError before anything is written,
    as ``write_projects`` refuses an empty project list.
    """
    deleted = held = 0
    if archive_path:
        if seek_study_ids is None:
            raise ValueError("deleting Investigation nodes needs SEEK's study ids, to know which Study still holds one")
        ids = sorted({int(i["id"]) for i in investigations})
        study_ids = sorted({int(s) for s in seek_study_ids})
        gone = _records(_run(driver, db, q.INVESTIGATIONS_GONE, {"ids": ids, "study_ids": study_ids}, read=True))
        deletable = [g for g in gone if not g["held"]]
        held = len(gone) - len(deletable)
        if deletable and not ids:
            raise ValueError("no Investigation rows; refusing to delete every Investigation")
        if deletable:
            _append_rows(archive_path, INVESTIGATIONS_ARCHIVE_HEADER,
                         ["\t".join(_tsv_field(v) for v in (g["id"], g["title"],
                                                             ",".join(str(p) for p in sorted(g["project_ids"] or []))))
                          + "\n" for g in deletable])
            deleted = _one(_run(driver, db, q.DELETE_INVESTIGATIONS,
                                {"element_ids": [g["element_id"] for g in deletable], "study_ids": study_ids}),
                           "deleted")
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
            "investigation_links_dropped": len(link_rows) - linked, "investigations_deleted": deleted,
            "investigations_not_in_seek_held": held}


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


def _seen_pairs():
    """A pair seen check for one stream of DERIVED_FROM edges, built per stream so a retried read starts clean: it
    answers whether ``(child, parent)`` came before in that stream, keying the pair as gate G check 1 does (one
    ``run.encode_pair`` code, or the pair itself where the code cannot be made)."""
    from nextseek_api.graph_sync.run import encode_pair   # run imports this module

    seen: set = set()

    def again(child, parent) -> bool:
        try:
            key = encode_pair(child, parent)
        except (TypeError, ValueError):
            key = (child, parent)
        if key in seen:
            return True
        seen.add(key)
        return False

    return again


def _archive_line(record) -> str:
    """One lineage archive row (``DERIVED_FROM_ARCHIVE_HEADER``) for a streamed edge record."""
    return "\t".join((_tsv_field(record["child_id"]), _tsv_field(record["parent_id"]),
                      _tsv_field(record["child_uuid"]), _tsv_field(record["parent_uuid"]),
                      _props_json(record["props"]))) + "\n"


def archive_and_drop_undeclared_derived_from(driver, db, out_path: str, declared_pairs) -> dict:
    """Archive to a TSV, then delete, every DERIVED_FROM between two Sample nodes that MySQL does not declare.

    ``declared_pairs`` answers ``(child id, parent id) in declared_pairs`` for the pairs MySQL's parent tokens
    declare (``run.DeclaredIdPairs``, or a set of tuples). Every DERIVED_FROM between two Sample nodes is streamed
    once; an undeclared edge (a pair a later Parent edit left stale, a self-loop, a token that no longer resolves)
    becomes one row: ``child_id``, ``parent_id``, ``child_uuid``, ``parent_uuid`` and ``props``, the edge's
    properties as JSON (uuids escaped by ``_tsv_field``). A second (or later) edge of a declared pair is archived
    and deleted the same way, counted apart in ``derived_from_doubled``: the first edge the stream gives is kept. The
    file is written to a ``.partial`` path and renamed into place before the first delete, so a failed write deletes
    nothing. The edges are then deleted by element id in batches, each delete matching only an edge between two
    Sample nodes, so an edge touching an OrphanSample is never deleted. With nothing to delete no file is written (an
    earlier archive is kept).
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    partial = out_path + ".partial"

    def archive(result):
        edges, doubled, dropped, again = 0, 0, [], _seen_pairs()  # built here, so a retried read starts clean
        with open(partial, "w", encoding="utf-8", newline="") as fh:
            fh.write(DERIVED_FROM_ARCHIVE_HEADER)
            for record in result:
                edges += 1
                child, parent = record["child_id"], record["parent_id"]
                if _is_declared(declared_pairs, child, parent):
                    if not again(child, parent):
                        continue
                    doubled += 1
                fh.write(_archive_line(record))
                dropped.append(record["element_id"])
        return edges, doubled, dropped

    try:
        edges, doubled, element_ids = _run(driver, db, q.DERIVED_FROM_BETWEEN_SAMPLES, read=True,
                                           transformer=archive)
    except BaseException:
        if os.path.exists(partial):
            os.remove(partial)
        raise
    if not element_ids:
        os.remove(partial)
        return {"derived_from_between_samples": edges, "derived_from_undeclared": 0, "derived_from_doubled": 0,
                "derived_from_deleted": 0, "derived_from_archive_path": None}
    os.replace(partial, out_path)
    log.info("DERIVED_FROM: archived %d undeclared and %d doubled of %d edges between samples to %s",
             len(element_ids) - doubled, doubled, edges, out_path)
    deleted = 0
    for batch in _batches(element_ids, DERIVED_FROM_DELETE_BATCH):
        deleted += _one(_run(driver, db, q.DELETE_UNDECLARED_DERIVED_FROM, {"element_ids": batch}), "deleted")
    if deleted != len(element_ids):
        log.warning("DERIVED_FROM: %d undeclared or doubled edges archived but %d deleted", len(element_ids), deleted)
    return {"derived_from_between_samples": edges, "derived_from_undeclared": len(element_ids) - doubled,
            "derived_from_doubled": doubled, "derived_from_deleted": deleted, "derived_from_archive_path": out_path}


def archive_and_drop_undeclared_for_children(driver, db, child_ids, declared_pairs, archive_path) -> dict:
    """The lineage step of a by-id sync: archive, then delete, every DERIVED_FROM from one of ``child_ids`` to a
    Sample parent that MySQL does not declare.

    ``declared_pairs`` answers ``(child id, parent id) in declared_pairs`` as for
    ``archive_and_drop_undeclared_derived_from``, whose row format this shares, and so does a second edge of a
    declared pair (``derived_from_doubled``; a child's edges all come in its one chunk). Rows are collected per chunk
    of children and appended to ``archive_path`` (header on a new file) and flushed before the first delete, so a run
    that calls this once per chunk keeps one archive. An edge to an OrphanSample is never read or deleted. With
    nothing to delete no file is touched; with something to delete and no ``archive_path`` it raises ValueError, and
    an archive that cannot be written raises OSError, both before any delete.
    """
    def collect(result):
        edges, doubled, lines, element_ids, again = 0, 0, [], [], _seen_pairs()  # so a retried read starts clean
        for record in result:
            edges += 1
            child, parent = record["child_id"], record["parent_id"]
            if _is_declared(declared_pairs, child, parent):
                if not again(child, parent):
                    continue
                doubled += 1
            lines.append(_archive_line(record))
            element_ids.append(record["element_id"])
        return edges, doubled, lines, element_ids

    edges, doubled, lines, element_ids = 0, 0, [], []
    for batch in _batches(dict.fromkeys(child_ids), REL_CHUNK):
        n, batch_doubled, batch_lines, batch_ids = _run(driver, db, q.DERIVED_FROM_OF_CHILDREN, {"ids": batch},
                                                        read=True, transformer=collect)
        edges += n
        doubled += batch_doubled
        lines.extend(batch_lines)
        element_ids.extend(batch_ids)
    if not element_ids:
        return {"derived_from_of_children": edges, "derived_from_undeclared": 0, "derived_from_doubled": 0,
                "derived_from_deleted": 0, "derived_from_archive_path": None}
    if not archive_path:
        raise ValueError(f"{len(element_ids)} undeclared DERIVED_FROM edges and no archive path to record them in")
    _append_rows(archive_path, DERIVED_FROM_ARCHIVE_HEADER, lines)
    deleted = 0
    for batch in _batches(element_ids, DERIVED_FROM_DELETE_BATCH):
        deleted += _one(_run(driver, db, q.DELETE_UNDECLARED_DERIVED_FROM, {"element_ids": batch}), "deleted")
    if deleted != len(element_ids):
        log.warning("DERIVED_FROM: %d undeclared or doubled edges of children archived but %d deleted",
                    len(element_ids), deleted)
    return {"derived_from_of_children": edges, "derived_from_undeclared": len(element_ids) - doubled,
            "derived_from_doubled": doubled, "derived_from_deleted": deleted, "derived_from_archive_path": archive_path}


# --- DERIVED_FROM labels (schema 1.2) -------------------------------------------------------------

def edges_incident(driver, db, ids) -> list[dict]:
    """Every DERIVED_FROM between two Sample nodes with an end among ``ids``, both directions, once each. Read-only.

    Each edge is ``{"child_id", "parent_id", "element_id", "stored"}``, where ``stored`` holds all seven
    ``EDGE_LABEL_KEYS`` (None when absent): what ``labels.classify`` compares, and what an approved
    ``write_edge_labels`` row carries back as ``stored``. Sorted by child id, parent id, element id.
    """
    edges: dict[str, dict] = {}
    for batch in _batches(dict.fromkeys(ids), REL_CHUNK):
        for record in _records(_run(driver, db, q.EDGES_INCIDENT, {"ids": batch}, read=True)):
            stored = record["stored"] or {}
            edges.setdefault(record["element_id"], {
                "child_id": record["child_id"], "parent_id": record["parent_id"], "element_id": record["element_id"],
                "stored": {key: stored.get(key) for key in EDGE_LABEL_KEYS}})
    return sorted(edges.values(), key=lambda e: (_id_key(e["child_id"]), _id_key(e["parent_id"]), e["element_id"]))


def _label_row(row: dict, apply_label_changes: bool) -> dict:
    pair = (row["child_id"], row["parent_id"])
    labels = row.get("labels") or {}
    missing = [key for key in EDGE_LABEL_KEYS if key not in labels]
    if missing:
        raise ValueError(f"DERIVED_FROM {pair}: labels lack {', '.join(missing)}; all seven are written together")
    clean = {key: labels[key] for key in EDGE_LABEL_KEYS}
    for key in PLURAL_LABEL_KEYS:
        if not isinstance(clean[key], (list, tuple)):
            raise ValueError(f"DERIVED_FROM {pair}: {key} must be a list, got {clean[key]!r}")
        clean[key] = list(clean[key])
    out = {"child_id": row["child_id"], "parent_id": row["parent_id"], "labels": clean}
    if apply_label_changes:
        stored = row.get("stored")
        if not isinstance(stored, dict) or any(key not in stored for key in EDGE_LABEL_KEYS):
            raise ValueError(f"DERIVED_FROM {pair}: an approved label change needs the stored values read, "
                             f"all seven keys")
        out["stored"] = {key: stored[key] for key in EDGE_LABEL_KEYS}
    return out


def write_edge_labels(driver, db, rows, *, apply_label_changes: bool = False) -> dict:
    """Write DERIVED_FROM labels: all seven ``EDGE_LABEL_KEYS`` together on each edge, never a subset, and drop the
    legacy ``assay_title`` from each edge written.

    ``rows`` are ``{"child_id", "parent_id", "labels"}``, ``labels`` holding every key of ``EDGE_LABEL_KEYS`` (None
    for a null, lists for the two plural keys; ``labels.edge_labels`` produces them), plus ``stored`` (as
    ``edges_incident`` returns it) when ``apply_label_changes`` is set. Every row is checked before anything is sent:
    a missing key raises ValueError. A pair is sent once (the first row wins).

    By default only new labels are written: the statement's own WHERE passes an edge only when its three singular
    assay fields are all null, so a label written between the caller's read and this write is kept (R14), and a
    stored protocol is kept too (an edge can carry a protocol and no assay label, and R5 forbids replacing a stored
    label without approval: the protocol pair is written only where nothing is stored). With
    ``apply_label_changes`` (the operator's approval) any label is written, but only where all seven stored values
    still equal ``stored``. Returns ``labels_rows`` (pairs sent), ``labels_written`` (edges written),
    ``labels_skipped_labelled`` (default mode: edges already labelled), ``labels_skipped_changed`` (approved mode:
    edges changed since the read) and ``labels_edges_missing`` (pairs with no edge between two Sample nodes).
    """
    payload: dict[tuple, dict] = {}
    for row in rows:
        checked = _label_row(row, apply_label_changes)
        payload.setdefault((row["child_id"], row["parent_id"]), checked)
    statement = q.WRITE_EDGE_LABELS_CHANGED if apply_label_changes else q.WRITE_EDGE_LABELS_NEW
    matched = written = pairs = 0
    for batch in _batches(payload.values(), REL_CHUNK):
        result = _run(driver, db, statement, {"rows": batch})
        matched += _one(result, "matched")
        written += _one(result, "written")
        pairs += _one(result, "pairs")
    skipped = matched - written
    return {"labels_rows": len(payload), "labels_written": written,
            "labels_skipped_labelled": 0 if apply_label_changes else skipped,
            "labels_skipped_changed": skipped if apply_label_changes else 0,
            "labels_edges_missing": len(payload) - pairs}


def write_edge_label_refreshes(driver, db, rows) -> dict:
    """Write the labels of an internal assay renamed under its id or a protocol filled where none was stored
    (``labels.REFRESH_CLASSES``), which need no approval, through the compare-and-set statement, so an edge whose
    seven stored values moved since the read is skipped. ``rows`` are ``{"child_id", "parent_id", "labels",
    "stored"}``, ``stored`` required with all seven keys. Every row is classified again before anything is sent, and a
    row of any other class raises ValueError: this can never write a change of which assay an edge carries. Returns
    ``labels_refresh_rows``, ``labels_refreshed``, ``labels_refresh_skipped_changed`` and
    ``labels_refresh_edges_missing``."""
    payload: dict[tuple, dict] = {}
    for row in rows:
        checked = _label_row(row, True)
        cls = labels.classify(checked["stored"], checked["labels"])
        if cls not in labels.REFRESH_CLASSES:
            raise ValueError(f"DERIVED_FROM {(row['child_id'], row['parent_id'])}: a {cls} label needs the "
                             "operator's approval")
        payload.setdefault((row["child_id"], row["parent_id"]), checked)
    matched = written = pairs = 0
    for batch in _batches(payload.values(), REL_CHUNK):
        result = _run(driver, db, q.WRITE_EDGE_LABELS_CHANGED, {"rows": batch})
        matched += _one(result, "matched")
        written += _one(result, "written")
        pairs += _one(result, "pairs")
    return {"labels_refresh_rows": len(payload), "labels_refreshed": written,
            "labels_refresh_skipped_changed": matched - written, "labels_refresh_edges_missing": len(payload) - pairs}


# --- source hashes -------------------------------------------------------------------------------

def sample_hashes(driver, db) -> Iterator[tuple]:
    """Yield ``(id, source_hash)`` for every Sample node, ordered by id; ``source_hash`` is None on a node written
    before schema 1.2 or by anything but graph_sync. Read-only.

    A generator over keyset pages of ``HASH_PAGE`` rows (``WHERE s.id > $after``), so memory holds one page however
    large the graph; the nightly targeted sync merges it with MySQL's own ordered stream. Only numeric ids are
    streamed: a legacy node with any other id is the full sync's.
    """
    limit, after = HASH_PAGE, _INT64_MIN
    while True:
        rows = _records(_run(driver, db, q.SAMPLE_HASHES_PAGE, {"after": after, "limit": limit}, read=True))
        for row in rows:
            yield row["id"], row["source_hash"]
        if len(rows) < limit:
            return
        after = rows[-1]["id"]


# --- SEEK studies and IN_STUDY (the studies release) ---------------------------------------------------------------

@dataclass(frozen=True)
class SeekTables:
    """SEEK's small tables one call reads once and hands to the writer (which reads no MySQL): ``sources.studies()``,
    ``sources.investigations()``, ``sources.investigation_projects()`` and ``sources.projects()`` rows."""
    studies: tuple = ()
    investigations: tuple = ()
    investigation_projects: tuple = ()
    projects: tuple = ()


@dataclass(frozen=True)
class PaperScope:
    """What the paper-sample rule reads from SEEK: each SEEK study's investigation, each SEEK investigation's title."""
    study_investigation: Mapping[int, int | None]
    investigation_titles: Mapping[int, str | None]


@dataclass(frozen=True)
class PaperSplit:
    """One sample's SEEK studies under the paper-sample rule (``paper_split``)."""
    paper: bool
    own: frozenset | None       # the own investigations; None when they cannot be matched (every study withheld)
    written: tuple              # SEEK's study ids to link, sorted
    withheld: tuple             # SEEK's study ids not linked, sorted


def paper_scope(studies, investigations) -> PaperScope:
    """``PaperScope`` from ``sources.studies()`` and ``sources.investigations()`` rows."""
    return PaperScope(
        study_investigation=MappingProxyType({int(s["id"]): s.get("investigation_id") for s in studies}),
        investigation_titles=MappingProxyType({int(i["id"]): i.get("title") for i in investigations}))


def _folded(title) -> str:
    return str(title or "").strip().casefold()


def paper_split(links, study_ids, scope: PaperScope) -> PaperSplit:
    """Which of SEEK's studies ``study_ids`` a sample with IN_STUDY ``links`` (as ``sample_studies`` returns them) is
    linked to. A sample with a link to a Study that has no ``seek_study_id`` is a paper sample. A paper link's own
    investigation is its Study's one Investigation, when that node's ``id`` is a SEEK investigation and its title
    equals SEEK's title (``str.strip().casefold()`` on both sides, the studies tool's rule). A paper sample is
    not linked to a SEEK study of one of its papers' own investigations, nor to one SEEK files under no
    investigation; its links to other investigations' studies are written. When any of its paper links has no such
    own investigation (no IN_INVESTIGATION, several, an id SEEK lacks, another title), ``own`` is None and every
    study is withheld. A sample that is not a paper sample is linked to every study."""
    wanted = tuple(sorted({int(s) for s in study_ids}))
    papers = [link for link in links if link.get("seek_study_id") is None]
    if not papers:
        return PaperSplit(False, None, wanted, ())
    own: set | None = set()
    for link in papers:
        found = link.get("investigations") or []
        inv_id = found[0].get("id") if len(found) == 1 else None
        if (not _is_int(inv_id) or inv_id not in scope.investigation_titles
                or _folded(found[0].get("title")) != _folded(scope.investigation_titles[inv_id])):
            own = None
            break
        own.add(inv_id)
    if own is None:
        return PaperSplit(True, None, (), wanted)
    withheld = tuple(s for s in wanted if scope.study_investigation.get(s) is None
                     or scope.study_investigation.get(s) in own)
    return PaperSplit(True, frozenset(own), tuple(s for s in wanted if s not in withheld), withheld)


def _seek_study_rows(studies) -> list[dict]:
    """``sources.studies()`` rows as the statements' rows, one per study id, ascending."""
    rows: dict[int, dict] = {}
    for study in studies:
        sid = int(study["id"])
        rows.setdefault(sid, {"study_id": sid, "title": study.get("title"), "description": study.get("description"),
                              "investigation_id": study.get("investigation_id")})
    return [rows[key] for key in sorted(rows)]


def write_study_investigations(driver, db, studies, tables: SeekTables) -> dict:
    """MERGE the Investigation node of every investigation ``studies`` name, and its IN_PROJECT, from ``tables``, so a
    study in an investigation created in SEEK that day is linked when its first samples are written. The same
    properties ``write_investigation_projects`` sets; nothing is deleted (the whole-table write owns the deletes). An
    investigation id with no SEEK row is counted in ``investigation_ids_not_in_seek`` and written nowhere."""
    wanted = {int(s["investigation_id"]) for s in studies if s.get("investigation_id") is not None}
    known = {int(i["id"]): i for i in tables.investigations}
    link_rows, seen, project_of = [], set(), {}
    for link in tables.investigation_projects:
        pair = (int(link["investigation_id"]), int(link["project_id"]))
        if pair[0] in wanted and pair[0] in known and pair not in seen:
            seen.add(pair)
            link_rows.append({"investigation_id": pair[0], "project_id": pair[1]})
            project_of[pair[0]] = min(project_of.get(pair[0], pair[1]), pair[1])
    rows = [{"id": inv_id, "title": known[inv_id].get("title"), "description": known[inv_id].get("description"),
             "project_id": project_of.get(inv_id)} for inv_id in sorted(wanted & set(known))]
    for batch in _batches(rows, REL_CHUNK):
        _run(driver, db, q.MERGE_INVESTIGATIONS, {"rows": batch})
    projects = merge_missing_projects(driver, db, [r["project_id"] for r in link_rows], tables.projects)
    linked = sum(_one(_run(driver, db, q.MERGE_INVESTIGATION_IN_PROJECT, {"rows": batch}), "linked")
                 for batch in _batches(link_rows, REL_CHUNK))
    return {"investigations_written": len(rows), "investigation_links": linked,
            "investigation_links_dropped": len(link_rows) - linked,
            "investigation_ids_not_in_seek": len(wanted - set(known)),
            "investigation_projects_written": projects["projects_written_for_links"],
            "investigation_project_ids_not_in_seek": projects["project_ids_not_in_seek"]}


def write_seek_study_nodes(driver, db, studies, *, tables: SeekTables | None = None) -> dict:
    """MERGE the Study node of each SEEK study on ``seek_study_id`` and make its title, description and
    IN_INVESTIGATION SEEK's. ``studies`` are ``sources.studies()`` rows. With ``tables`` the investigations they name
    are written first (``write_study_investigations``); without, the caller has just written every Investigation
    (the full sync's and the small tables' whole-table write). ``seek_study_investigation_missing`` counts the study
    rows whose Investigation node is still missing: expected 0."""
    rows = _seek_study_rows(studies)
    report = write_study_investigations(driver, db, studies, tables) if tables is not None else {}
    written = missing = 0
    for batch in _batches(rows, REL_CHUNK):
        record = _first(_run(driver, db, q.MERGE_SEEK_STUDIES, {"rows": batch}))
        written += int(record.get("n") or 0)
        missing += int(record.get("investigation_missing") or 0)
    report.update(seek_studies=len(rows), seek_study_nodes_written=written, seek_study_investigation_missing=missing)
    return report


def _links(record) -> list[dict]:
    return [{"element_id": link.get("element_id"), "seek_study_id": link.get("seek_study_id"), "id": link.get("id"),
             "investigations": [dict(i) for i in (link.get("investigations") or [])]}
            for link in (record["studies"] or [])]


def sample_studies(driver, db, ids) -> dict[int, list[dict]]:
    """The IN_STUDY edges of these Sample nodes: sample id to ``{"element_id", "seek_study_id", "id",
    "investigations"}`` per edge (``investigations`` the Study's ``{"id", "title"}``); a Sample with none maps to [], an
    id with no Sample node is absent. Read-only."""
    found: dict[int, list[dict]] = {}
    for batch in _batches(dict.fromkeys(ids), REL_CHUNK):
        for record in _records(_run(driver, db, q.SAMPLE_STUDIES_OF, {"ids": batch}, read=True)):
            found[record["id"]] = _links(record)
    return found


def sample_study_pages(driver, db, page: int = HASH_PAGE) -> Iterator[list[dict]]:
    """Keyset pages of every Sample with a numeric id, ascending, as ``{"id", "studies"}`` rows; memory holds one
    page. Read-only."""
    after = _INT64_MIN
    while True:
        rows = [{"id": record["id"], "studies": _links(record)}
                for record in _records(_run(driver, db, q.SAMPLE_STUDIES_PAGE, {"after": after, "limit": page},
                                            read=True))]
        if rows:
            yield rows
        if len(rows) < page:
            return
        after = rows[-1]["id"]


def seek_study_id_duplicates(driver, db) -> list[dict]:
    """``{"seek_study_id", "nodes"}`` for every seek_study_id more than one Study node carries. Read-only."""
    return [{"seek_study_id": r["seek_study_id"], "nodes": r["nodes"]}
            for r in _records(_run(driver, db, q.STUDY_SEEK_ID_DUPLICATES, read=True))]


def orphan_in_study(driver, db) -> int:
    """IN_STUDY edges whose source is not a Sample. Read-only."""
    return int(_one(_run(driver, db, q.ORPHAN_IN_STUDY, read=True), "n"))


def _in_study_line(sample_id, link: dict, path: str) -> str:
    return "\t".join(_tsv_field(v) for v in (sample_id, link.get("seek_study_id"), link.get("id"),
                                             link.get("element_id"), path)) + "\n"


def replace_seek_in_study(driver, db, rows, *, remove: bool, archive_path: str | None, scope: PaperScope,
                          path: str = "sync") -> dict:
    """Make each sample's IN_STUDY follow SEEK (docs/neo4j-schema.md, v1.2 "Study nodes and IN_STUDY").

    ``rows`` are ``{"sample_id", "study_ids"}``, ``study_ids`` SEEK's studies of that sample ([] for none); a sample is
    sent once, the first row winning. Per ``REL_CHUNK`` samples this reads their IN_STUDY (``SAMPLE_STUDIES_OF``) and
    works out the stale links: each IN_STUDY to a SEEK-keyed Study outside ``study_ids``, and none at all for a sample
    SEEK places in no study (it keeps its links; ``in_study_kept_no_seek_study`` counts it). With ``remove`` the stale
    links are appended to ``archive_path``, ``path`` naming who removed them, and flushed before
    ``REPLACE_SEEK_IN_STUDY`` deletes them; without it they are counted in ``in_study_stale`` and kept. The statement
    links each sample to its SEEK studies' nodes, except a paper sample's links to studies of its own investigation
    (``paper_split`` with ``scope``, a required keyword: a forgotten caller fails its test rather than withholding
    silently). Returns every key of ``IN_STUDY_COUNTS``.

    Raises ValueError when a chunk has links to remove and there is no ``archive_path``, and OSError when the archive
    cannot be written; either before that chunk's write.
    """
    payload: dict[int, list[int]] = {}
    for row in rows:
        payload.setdefault(int(row["sample_id"]), sorted({int(s) for s in (row.get("study_ids") or ())}))
    counts = dict.fromkeys(IN_STUDY_COUNTS, 0)
    counts["in_study_rows"] = len(payload)
    for batch in _batches(sorted(payload), REL_CHUNK):
        current = sample_studies(driver, db, batch)
        statement_rows, lines = [], []
        for sample_id in batch:
            wanted = payload[sample_id]
            links = current.get(sample_id, ())
            split = paper_split(links, wanted, scope)
            counts["in_study_paper_investigation_unknown"] += split.paper and split.own is None
            seek_links = [link for link in links if link["seek_study_id"] is not None]
            if wanted:
                stale = [link for link in seek_links if link["seek_study_id"] not in wanted]
            else:
                stale = []
                counts["in_study_kept_no_seek_study"] += bool(seek_links)
            if remove:
                lines.extend(_in_study_line(sample_id, link, path) for link in stale)
            else:
                counts["in_study_stale"] += len(stale)
            statement_rows.append({"sample_id": sample_id, "study_ids": wanted, "withhold": list(split.withheld),
                                   "paper": split.paper,
                                   "remove": [link["element_id"] for link in stale] if remove else []})
        if lines:
            if not archive_path:
                raise ValueError(f"{len(lines)} IN_STUDY links to remove and no archive path to record them in")
            _append_rows(archive_path, IN_STUDY_ARCHIVE_HEADER, lines)
        record = _first(_run(driver, db, q.REPLACE_SEEK_IN_STUDY, {"rows": statement_rows}))
        removed = int(record.get("removed") or 0)
        if removed != len(lines):
            log.warning("IN_STUDY: %d links archived for removal but %d removed (changed since the read)",
                        len(lines), removed)
        counts["in_study_removed"] += removed
        counts["in_study_added"] += int(record.get("added") or 0)
        counts["in_study_paper_samples"] += int(record.get("paper_samples") or 0)
        counts["in_study_paper_links_written"] += int(record.get("paper_added") or 0)
        counts["in_study_withheld"] += int(record.get("withheld") or 0)
        counts["in_study_studies_missing"] += int(record.get("studies_missing") or 0)
        counts["in_study_samples_missing"] += len(batch) - int(record.get("samples") or 0)
    return counts


def write_seek_studies(driver, db, links: list[dict], sample_ids, *, remove: bool, archive_path: str | None,
                       tables: SeekTables) -> dict:
    """The by-id path's SEEK studies: the Study node of every study ``links`` names (its Investigation node first,
    from ``tables``), then ``replace_seek_in_study`` with one row per sample of ``sample_ids``, a sample with no link
    getting an empty list (it keeps its links and is counted). ``links`` are ``sources.seek_study_links_for`` rows
    (``sample_id``, ``study_id``, ``study_title``, ``study_description``, ``investigation_id``); ``tables`` is what
    the caller read once from SEEK (``study_links.seek_tables``), and gives the paper-sample rule its scope. A study
    is given its node even when every one of its samples is a paper sample: only the edge is withheld."""
    studies: dict[int, dict] = {}
    per_sample: dict[int, set[int]] = {int(s): set() for s in sample_ids}
    for link in links:
        sample_id, study_id = int(link["sample_id"]), int(link["study_id"])
        studies.setdefault(study_id, {"id": study_id, "title": link.get("study_title"),
                                      "description": link.get("study_description"),
                                      "investigation_id": link.get("investigation_id")})
        if sample_id in per_sample:
            per_sample[sample_id].add(study_id)
    report = write_seek_study_nodes(driver, db, list(studies.values()), tables=tables)
    report.update(replace_seek_in_study(
        driver, db, [{"sample_id": s, "study_ids": sorted(found)} for s, found in sorted(per_sample.items())],
        remove=remove, archive_path=archive_path, scope=paper_scope(tables.studies, tables.investigations),
        path="by_id"))
    return report


# --- the studies tool's share check (read only) ---------------------------------------------------------------------

SHARE_CHECK_COUNTS = ("found", "has_project", "in_project", "in_study", "paper", "paper_in_study")
SHARE_CHECK_MISSING_CAP = 50


def share_graph_check(driver, db, ids, *, project_id: int, study_id: int) -> dict:
    """How a share's samples stand in the graph (tool spec 16.8), read only, ``REL_CHUNK`` ids a statement: how many
    exist, carry the destination project (property and IN_PROJECT), link the destination study's node, are paper
    samples and, of those, link it anyway; and up to 50 of the ids with no node."""
    wanted = sorted({int(i) for i in ids})
    counts = dict.fromkeys(SHARE_CHECK_COUNTS, 0)
    missing: list[int] = []
    for batch in _batches(wanted, REL_CHUNK):
        rows = _records(_run(driver, db, q.SHARE_CHECK, {"ids": batch, "project_id": int(project_id),
                                                         "study_id": int(study_id)}, read=True))
        for row in rows:
            for key in ("found", "has_project", "in_project", "in_study", "paper"):
                counts[key] += 1 if row[key] else 0
            counts["paper_in_study"] += 1 if (row["paper"] and row["in_study"]) else 0
            if not row["found"] and len(missing) < SHARE_CHECK_MISSING_CAP:
                missing.append(int(row["id"]))
    return {"ids": len(wanted), **counts, "missing_ids": missing}


# --- GraphMeta -----------------------------------------------------------------------------------

def write_graphmeta(driver, db, catalog_hash: str, label_maps_hash: str | None = None) -> dict:
    """Stamp the single GraphMeta node with the schema version, the catalog hash and ``synced_at``, and with
    ``label_maps_hash`` (a digest of the resolved assay map and ``sops``) when one is given. Without it the node keeps
    the ``label_maps_hash`` it has."""
    if label_maps_hash is None:
        _run(driver, db, q.WRITE_GRAPHMETA, {"schema_version": SCHEMA_VERSION, "catalog_hash": catalog_hash})
        return {"schema_version": SCHEMA_VERSION, "catalog_hash": catalog_hash}
    params = {"schema_version": SCHEMA_VERSION, "catalog_hash": catalog_hash, "label_maps_hash": label_maps_hash}
    _run(driver, db, q.WRITE_GRAPHMETA_WITH_LABEL_MAPS, params)
    return dict(params)


def graphmeta(driver, db) -> dict:
    """Read the GraphMeta node: ``nodes`` (how many there are) and each of ``GRAPHMETA_KEYS``, None when absent
    (``synced_at`` as ISO text). A graph with no GraphMeta node, or with several, reads as having no values, so a
    caller that compares ``schema_version`` with ``SCHEMA_VERSION`` refuses it. Read-only."""
    records = _records(_run(driver, db, q.READ_GRAPHMETA, read=True))
    props = dict(records[0]["props"] or {}) if len(records) == 1 else {}
    meta = {"nodes": len(records)}
    for key in GRAPHMETA_KEYS:
        value = props.get(key)
        iso_format = getattr(value, "iso_format", None)
        meta[key] = iso_format() if callable(iso_format) else value
    return meta
