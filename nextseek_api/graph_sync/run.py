"""The ordered graph_sync runs (the design, section 6; the sync design, section 11; docs/neo4j-schema.md, sections
"v1.1" and "v1.2").

``full_sync`` rebuilds graph schema v1.2 from MySQL in the design's order:

    preflight > delete ghosts > retire graph-only samples (the deletion rule) > orphan id-less samples > archive and
    delete CHILD_OF > constraints > SampleType > Attribute (declared) > Project, Person, MEMBER_OF > Investigation
    IN_PROJECT > samples (per chunk, with the census, the source hash and the parent lists) > missing lineage >
    archive and delete undeclared DERIVED_FROM > DERIVED_FROM labels > re-key SEEK Study nodes > SEEK studies and
    IN_STUDY > Attribute (declared plus undeclared) > attribute and sample type counts > the index budget > the
    fulltext index > await indexes > GraphMeta (with the label maps hash)

The whole run, preflight included, holds the graph-write lock (``state.graph_write_lock``), so no other graph_sync
writer can add a sample between the MySQL scan and the graph read. A run that is not a dry run records itself in
``graph_sync_run`` (best-effort) and, when it ends ``ok``, marks done every outbox row enqueued before it started: it
read everything those rows ask for. A drift slot is the exception, a check the graph still owes after a sync, and so
is a row still inside its writer's delay when the sync started (``state.mark_done_before``).

The deletion rule (the sync design, section 9): a graph-only Sample id goes to ``writer.retire_samples``, which
archives to ``retired.tsv`` and deletes a node graph_sync wrote and makes an ``:OrphanSample`` (no ``T_`` label) of
one it never wrote. A Sample node with no id is orphaned the same way by ``writer.relabel_orphans``.

The lineage steps leave DERIVED_FROM between Sample nodes equal to what MySQL's parent tokens declare: a declared
pair the graph lacks is created (declared edges keep their properties), then every edge between two Sample nodes
that is not declared is archived to ``derived_from_undeclared_archive.tsv`` and deleted. Edges touching an
OrphanSample are left alone.

The label step (the sync design, section 7.3; R14, R15) then reads every DERIVED_FROM between two Sample nodes and
classifies it against batch upload's rule (``labels.py``), fed from what the sample pass read: each sample's SEEK
assays and its protocol's SOP. An edge with no singular assay label (every edge the run created among them) is
labelled; a label that differs (``changed``, ``cleared``) and a missing plural list (``plural_missing``) are counted
per property in the report and written only with ``apply_label_changes``, and then only where the stored values
still equal the ones read just before the write.

The preflight writes nothing. It builds the catalog (which enforces the label rule), scans every MySQL sample once
(projecting it, collecting ids and the declared lineage), reads the ghost list and checks SampleType titles against
the graph. A problem it finds raises ``PreflightError`` before the first write; a dry run reports the same numbers,
plus previews of the CHILD_OF archive, the index budget and the Study re-key, and stops there. A dry run takes no
lock and records no run.

Undeclared Attribute nodes are known only once every sample has been projected, so the Attribute catalog is written
twice: the declared attributes before the sample pass (the design's order) and the full catalog after it. The
counts follow the second write, because ``writer.write_attributes`` replaces each node's properties.

``catalog_sync`` rewrites the catalog nodes only: SampleType, Attribute and HAS_ATTRIBUTE, their counts and GraphMeta.
It keeps the undeclared Attribute nodes and the attribute sample counts a full sync wrote. It holds the lock, records
a run, and refuses a graph that is not at the writer's schema version: stamping GraphMeta would otherwise turn a
graph into 1.2 without the full sync that makes one.

What is held across a run is one entry per sample in three indexes (sample ids, uuids and declared lineage pairs),
and, from the sample pass to the label step, each sample's assay and SOP ids packed 8 bytes a link; the sample data
itself is bounded by the chunk. Project and assay links come with each page (``sources.iter_digest_rows``), the
stream the nightly sync hashes, so both compute the same ``source_hash``.
"""
from __future__ import annotations

import heapq
import json
import logging
import os
import time
from array import array
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.db import DatabaseError
from django.utils import timezone as dj_timezone

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.graph_sync import catalog, labels, projection, sources, state, writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.projection import SYSTEM_KEYS, project_sample
from nextseek_api.graph_sync.writer import _batches, _one, _records, _run

log = logging.getLogger(__name__)

REPORT_FILE = "full_sync.json"
CENSUS_FILE = "census.json"
ARCHIVE_FILE = "child_of_archive.tsv"
DERIVED_FROM_ARCHIVE_FILE = "derived_from_undeclared_archive.tsv"
RETIRED_FILE = "retired.tsv"
LIST_CAP = 1_000        # longest id list copied into a report
EXAMPLES = 20           # examples kept per problem
PROGRESS_EVERY = 20     # sample pages between progress lines

# How long a run waits for the graph-write lock before it refuses. A full sync outwaits every other holder (one
# drained outbox row, a batch upload's inline sync, the nightly sync); a catalog sync waits as a drained row does.
FULL_LOCK_TIMEOUT_S = 3600
CATALOG_LOCK_TIMEOUT_S = 60

# The outbox kinds a successful full sync closes: it read everything they ask for. A drift slot asks for a check of
# the graph the sync leaves, so it stays pending.
FULL_SYNC_COVERS = tuple(kind for kind in state.KINDS if kind != "drift")

# The Attribute nodes as a previous run left them; catalog_sync keeps their undeclared keys and counts.
ATTRIBUTE_STATE = """
MATCH (a:Attribute)
RETURN a.key AS key, a.declared AS declared, a.sample_type_id AS sample_type_id, a.title AS title,
       a.sample_count AS sample_count
"""

# The seven label properties of a DERIVED_FROM edge (null when absent), as `stored`.
_STORED_LABELS = "e {" + ", ".join("." + key for key in q.EDGE_LABEL_KEYS) + "}"
# Every DERIVED_FROM between two Sample nodes with its stored labels: the label step's one pass over the edges. An
# edge touching an OrphanSample is not read.
LABEL_EDGES = """
MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id, {stored} AS stored
""".replace("{stored}", _STORED_LABELS)
# The stored labels of these (child id, parent id) pairs, read again just before an approved write compares them.
LABELS_FOR_PAIRS = """
UNWIND $rows AS r
MATCH (:Sample {id: r[0]})-[e:DERIVED_FROM]->(:Sample {id: r[1]})
RETURN r[0] AS child_id, r[1] AS parent_id, {stored} AS stored
""".replace("{stored}", _STORED_LABELS)

# SEEK Study nodes keyed on `id` (R8). Batch upload keys a SEEK study on its SEEK id; schema 1.1 keys it on
# seek_study_id and leaves `id` to the paper-level studies. A graph where any Study carries seek_study_id was
# written by graph_sync, so its id-keyed Study nodes are paper-level and none moves.
SEEK_KEYED_STUDIES = "MATCH (st:Study) WHERE st.seek_study_id IS NOT NULL RETURN count(st) AS n"
STUDIES_KEYED_BY_ID = """
MATCH (st:Study) WHERE st.id IS NOT NULL AND st.seek_study_id IS NULL
RETURN elementId(st) AS element_id, st.id AS id, st.title AS title,
       st.DOI IS NOT NULL OR st.PMID IS NOT NULL AS paper
"""
REKEY_STUDY = """
UNWIND $element_ids AS eid
MATCH (st:Study) WHERE elementId(st) = eid AND st.id IS NOT NULL AND st.seek_study_id IS NULL
SET st.seek_study_id = st.id
REMOVE st.id
RETURN count(st) AS n
"""


class PreflightError(RuntimeError):
    """A run refused before its first write. ``problems`` says why; ``report`` holds what the preflight found."""

    def __init__(self, problems: list[str], report: dict):
        super().__init__("graph_sync refused before writing: " + "; ".join(problems))
        self.problems = list(problems)
        self.report = report


class LockTimeout(PreflightError):
    """A run that refused because another graph_sync write held the graph-write lock past its wait. Nothing was
    written and nothing is wrong with the graph, so unlike every other refusal it is retried: the command exits 1 on it,
    not 2, and the loop backs its slot off instead of closing it."""


# --- the catalog ---------------------------------------------------------------------------------

@dataclass
class Catalog:
    sample_types: list[dict]                   # SampleType property maps (catalog.build_sample_types)
    attributes: list[dict]                     # declared Attribute property maps (catalog.build_attributes)
    type_titles: dict[int, str]                # sample type id to title
    value_types: dict[int, dict[str, str]]     # sample type id to {attribute title: value_type}


def build_catalog() -> Catalog:
    """Read SEEK and the dmac context tables and build the catalog. Raises ValueError on a label collision."""
    types = sources.sample_types()
    sample_types = catalog.build_sample_types(types, sources.type_context(), sources.type_clades(),
                                              sources.deprecated_titles())
    type_titles = {int(t["id"]): t["title"] for t in types}
    attributes = catalog.build_attributes(sources.sample_attributes(), sources.sample_attribute_types(),
                                          sources.attribute_meanings(), type_titles)
    value_types: dict[int, dict[str, str]] = defaultdict(dict)
    for attr in attributes:
        value_types[attr["sample_type_id"]][attr["title"]] = attr["value_type"]
    return Catalog(sample_types, attributes, type_titles, dict(value_types))


def undeclared_attributes(cat: Catalog, census: dict) -> list[dict]:
    """An undeclared Attribute for every key the census saw on a type that does not declare it, sorted by key."""
    rows = [catalog.undeclared_attribute(e["sample_type_id"], e["sample_type"], e["title"])
            for e in census.values() if not e["declared"]]
    return sorted(rows, key=lambda r: r["key"])


# --- declared lineage ----------------------------------------------------------------------------

_ID_LIMIT = 1 << 31


def encode_pair(child: int, parent: int) -> int:
    """One int for a (child id, parent id) pair: a set of these costs a fraction of a set of tuples."""
    if not (0 <= child < _ID_LIMIT and 0 <= parent < _ID_LIMIT):
        raise ValueError(f"sample id out of range for a lineage pair: ({child}, {parent})")
    return (child << 32) | parent


def decode_pair(code: int) -> tuple[int, int]:
    return code >> 32, code & 0xFFFFFFFF


def _is_packable(value) -> bool:
    """An int ``encode_pair`` can pack: not a bool, not negative, below 2**31."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < _ID_LIMIT


class DeclaredUuidPairs:
    """``(child uuid, parent uuid) in pairs`` for the CHILD_OF archive, answered from the declared id pairs.

    A uuid pair is declared when some sample carrying the child uuid declares some sample carrying the parent uuid
    (MySQL holds duplicate uuids), so no set of uuid pairs is built.
    """

    def __init__(self, codes: set[int], uuid_index: dict[str, list[int]]):
        self.codes = codes
        self.uuid_index = uuid_index

    def __contains__(self, pair) -> bool:
        child_uuid, parent_uuid = pair
        for child in self.uuid_index.get(child_uuid, ()):
            for parent in self.uuid_index.get(parent_uuid, ()):
                if encode_pair(child, parent) in self.codes:
                    return True
        return False


class DeclaredIdPairs:
    """``(child id, parent id) in pairs`` for the undeclared DERIVED_FROM archive.

    Answered by bisecting the sorted ``encode_pair`` codes the lineage step already holds, so no second copy of the
    declared pairs is built. An id that is not a non-negative int below 2**31 is never declared.
    """

    def __init__(self, sorted_codes: list[int]):
        self.codes = sorted_codes

    def __len__(self) -> int:
        return len(self.codes)

    def __contains__(self, pair) -> bool:
        child, parent = pair
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (child, parent)):
            return False
        try:
            code = encode_pair(child, parent)
        except ValueError:
            return False
        i = bisect_left(self.codes, code)
        return i < len(self.codes) and self.codes[i] == code


# --- what the label step needs from the sample pass ----------------------------------------------

class SampleLinks:
    """Sample id to a few small ids (its SEEK assays, its protocol's SOP), as one sorted array of ``encode_pair``
    codes: 8 bytes a link, where a dict of lists costs about a hundred bytes a sample. A pair the array cannot pack
    (an id at or above 2**31) is kept in a small dict beside it."""

    def __init__(self):
        self._codes = array("q")
        self._sorted = True
        self._other: dict[int, set[int]] = {}

    def __len__(self) -> int:
        return len(self._codes) + sum(len(v) for v in self._other.values())

    def add(self, sample_id: int, values) -> None:
        for value in sorted(set(values)):
            if not (_is_packable(sample_id) and _is_packable(value)):
                self._other.setdefault(sample_id, set()).add(value)
                continue
            code = encode_pair(sample_id, value)
            if self._codes and code < self._codes[-1]:
                self._sorted = False
            self._codes.append(code)

    def get(self, sample_id) -> tuple[int, ...]:
        """The sample's ids, ascending; empty for a sample with none and for an id that is not an int."""
        if not isinstance(sample_id, int) or isinstance(sample_id, bool):
            return ()
        found: set[int] = set(self._other.get(sample_id, ()))
        if _is_packable(sample_id):
            if not self._sorted:
                self._codes = array("q", sorted(self._codes))
                self._sorted = True
            lo = bisect_left(self._codes, sample_id << 32)
            hi = bisect_left(self._codes, (sample_id + 1) << 32, lo)
            found.update(code & 0xFFFFFFFF for code in self._codes[lo:hi])
        return tuple(sorted(found))


@dataclass
class LabelSources:
    """The label rule's inputs: the maps from MySQL, and what the sample pass recorded for each sample."""
    assay_map: dict                                     # sources.resolved_assay_map()
    sops: dict                                          # sources.sops_map()
    sop_index: dict                                     # labels.sop_title_index(sops)
    assays: SampleLinks = field(default_factory=SampleLinks)      # sample id to its SEEK assay ids
    protocols: SampleLinks = field(default_factory=SampleLinks)   # sample id to its protocol's SOP id

    @classmethod
    def read(cls) -> "LabelSources":
        sops = sources.sops_map()
        return cls(sources.resolved_assay_map(), sops, labels.sop_title_index(sops))

    def maps_hash(self) -> str:
        return labels.label_maps_hash(self.assay_map, self.sops)

    def record(self, row: dict, meta: dict) -> None:
        """Keep one sample's assays and the SOP its stored ``Protocol`` names (``labels.resolve_protocol``)."""
        self.assays.add(row["id"], row.get("assay_ids") or ())
        sop_id, _title = labels.resolve_protocol(labels.protocol_value_of(meta), self.sops, self.sop_index)
        if sop_id is not None:
            self.protocols.add(row["id"], (sop_id,))

    def edge(self, child_id, parent_id) -> dict:
        """The rule's seven labels for the edge from ``child_id`` to ``parent_id``."""
        sop = self.protocols.get(child_id)
        protocol = (sop[0], self.sops.get(sop[0])) if sop else None
        return labels.edge_labels(self.assays.get(child_id), self.assays.get(parent_id), self.assay_map, protocol)


def _metadata(raw) -> dict:
    """``json_metadata`` as a dict; unreadable or non-object metadata reads as empty (the projection refuses it)."""
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _page_parents(page: list[dict], label_sources: LabelSources) -> dict[int, tuple[list[str], list[str]]]:
    """Sample id to its ``parent_titles`` and ``parent_title_hashes`` for one page (R4), recording each sample's
    assays and protocol for the label step on the way.

    The page's UID tokens resolve through ``sources.parent_identities``, the reader the by-id syncs use, so every
    path computes the same lists.
    """
    tokens_by_id: dict[int, list[str]] = {}
    uid_tokens: set[str] = set()
    for row in page:
        meta = _metadata(row.get("json_metadata"))
        tokens = collect_parent_tokens(meta)
        tokens_by_id[row["id"]] = tokens
        uid_tokens.update(t for t in tokens if UID_RE.match(t))
        label_sources.record(row, meta)
    identities = sources.parent_identities(uid_tokens) if uid_tokens else {}
    return {sid: projection.parent_lists(tokens, identities) for sid, tokens in tokens_by_id.items()}


# --- the sample scan -----------------------------------------------------------------------------

@dataclass
class SampleScan:
    """What one pass over MySQL's samples collected."""
    samples: int = 0
    projected: int = 0
    errors: int = 0
    error_examples: list = field(default_factory=list)
    cast_failures: int = 0
    ids: set = field(default_factory=set)
    lineage: set = field(default_factory=set)       # encode_pair codes
    census: dict = field(default_factory=dict)      # attribute key to its census entry


def _census(cat: Catalog) -> dict:
    """One census entry per declared attribute, before any sample is seen."""
    return {a["key"]: {"sample_type_id": a["sample_type_id"], "sample_type": a["sample_type"], "title": a["title"],
                       "value_type": a["value_type"], "role": a["role"], "declared": True,
                       "sample_count": 0, "max_len": 0, "cast_failures": 0}
            for a in cat.attributes}


def _stored_len(value) -> int:
    """Length of a stored value as the index budget measures it: characters, or UTF-8 bytes when not ASCII.

    Neo4j's range index limit is in bytes, so a non-ASCII value is measured in bytes (never fewer than its
    characters).
    """
    text = value if isinstance(value, str) else str(value)
    return len(text) if text.isascii() else len(text.encode("utf-8"))


def _observe(census: dict, cat: Catalog, proj) -> None:
    type_id = proj.sample_type_id
    failures = set(proj.cast_failures)
    for title, value in proj.props.items():
        if title in SYSTEM_KEYS:
            continue
        key = catalog.attribute_key(type_id, title)
        entry = census.get(key)
        if entry is None:
            entry = census[key] = {"sample_type_id": type_id, "sample_type": cat.type_titles[type_id],
                                   "title": title, "value_type": "string", "role": catalog.role_for(title),
                                   "declared": False, "sample_count": 0, "max_len": 0, "cast_failures": 0}
        entry["sample_count"] += 1
        length = _stored_len(value)
        if length > entry["max_len"]:
            entry["max_len"] = length
        if title in failures:
            entry["cast_failures"] += 1


def _error(scan: SampleScan, sample_id, message: str) -> None:
    scan.errors += 1
    if len(scan.error_examples) < EXAMPLES:
        scan.error_examples.append({"id": sample_id, "error": message})


def _project(row: dict, cat: Catalog, scan: SampleScan, parents=None):
    type_id = row.get("sample_type_id")
    title = cat.type_titles.get(type_id)
    if title is None:
        _error(scan, row.get("id"), f"sample type {type_id} is not in sample_types")
        return None
    try:
        proj = project_sample(row, title, cat.value_types.get(type_id, {}), row.get("project_ids") or (),
                              assay_ids=row.get("assay_ids") or (), parent_lists=parents)
    except (ValueError, TypeError) as exc:
        _error(scan, row.get("id"), str(exc))
        return None
    scan.projected += 1
    scan.cast_failures += len(proj.cast_failures)
    _observe(scan.census, cat, proj)
    return proj


def scan_samples(cat: Catalog, chunk: int, *, uuid_index=None, collect_ids=False, on_page=None,
                 parents=None) -> SampleScan:
    """Read every sample with its project and assay ids (``sources.iter_digest_rows``) in keyset pages of ``chunk``
    and project it, accumulating the census.

    ``uuid_index`` (``sources.uuid_to_ids()``) also collects the declared lineage; ``collect_ids`` collects every
    sample id; ``parents(page)`` gives each sample's parent lists (sample id to the pair ``projection.parent_lists``
    returns); ``on_page(projections)`` receives each page's projections (the write pass). A sample that cannot be
    projected is counted in ``errors`` and skipped.
    """
    scan = SampleScan(census=_census(cat))
    pages = 0
    for page in sources.iter_digest_rows(chunk=chunk):
        if uuid_index is not None:
            for child, parent in sources.declared_lineage(page, uuid_index):
                scan.lineage.add(encode_pair(child, parent))
        lists = parents(page) if parents is not None else {}
        projections = []
        for row in page:
            scan.samples += 1
            if collect_ids:
                scan.ids.add(row["id"])
            proj = _project(row, cat, scan, lists.get(row["id"]))
            if proj is not None:
                projections.append(proj)
        if on_page is not None and projections:
            on_page(projections)
        pages += 1
        if pages % PROGRESS_EVERY == 0:
            log.info("graph_sync: %d samples read (%d errors)", scan.samples, scan.errors)
    return scan


# --- plumbing ------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _cap(items) -> list:
    items = list(items)
    return items[:LIST_CAP]


def _timed(report: dict, name: str, fn, *args, **kwargs):
    log.info("graph_sync: %s", name)
    started = time.monotonic()
    out = fn(*args, **kwargs)
    report["timings_s"][name] = round(time.monotonic() - started, 1)
    return out


def _step(report: dict, name: str, fn, *args, **kwargs):
    """Run one writer step, keep its counts under ``steps`` and at the top level of the report."""
    out = _timed(report, name, fn, *args, **kwargs)
    if isinstance(out, dict):
        report["steps"][name] = out
        report.update(out)
    return out


def _write_json(path: str, payload) -> None:
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    os.replace(partial, path)


def _resolve_run_dir(run_dir: str | None, report: dict | None = None) -> str:
    """The run directory: the one given, else a new ``graph_sync-<UTC time>`` under ``$GS_RUN_DIR``."""
    if run_dir:
        return os.path.abspath(run_dir)
    base = os.environ.get("GS_RUN_DIR")
    if base:
        return os.path.join(base, "graph_sync-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    raise PreflightError(["a full sync needs a run directory (--run-dir or GS_RUN_DIR) for its archives and its "
                          "report"], report if report is not None else {"mode": "full"})


def _title_conflicts(driver, db, cat: Catalog) -> list[dict]:
    keys = [{"id": int(t["id"]), "title": t["title"]} for t in cat.sample_types]
    return [{"title": r["title"], "graph_id": r["graph_id"], "mysql_id": r["mysql_id"]}
            for r in _records(_run(driver, db, q.SAMPLE_TYPE_TITLE_CONFLICTS, {"rows": keys}, read=True))]


def _summarize_census(report: dict, census: dict, cat: Catalog) -> None:
    undeclared = undeclared_attributes(cat, census)
    report["undeclared_attributes"] = len(undeclared)
    report["undeclared_attribute_keys"] = [a["key"] for a in undeclared]
    report["attributes_with_values"] = sum(1 for e in census.values() if e["sample_count"])
    failing = [(e["cast_failures"], key) for key, e in census.items() if e["cast_failures"]]
    report["cast_failure_keys"] = {key: n for n, key in heapq.nlargest(EXAMPLES, failing)}


def _lock_problem(timeout_s: float) -> str:
    return (f"the graph-write lock was not acquired within {timeout_s} s: another graph_sync write holds it "
            "(a drained outbox row, a batch upload's graph stage, a nightly or full sync)")


# Report details a run record keeps beside the scalar counts; the id lists stay in the run directory's report.
_RECORDED_DETAILS = ("problems", "timings_s", "labels_by_property")


def _recorded_counts(report: dict) -> dict:
    counts = {k: v for k, v in report.items() if v is None or isinstance(v, (bool, int, float, str))}
    for key in _RECORDED_DETAILS:
        if key in report:
            counts[key] = report[key]
    return counts


def _start_record(kind: str, trigger: str, started: datetime, record: bool):
    """The run's ``graph_sync_run`` handle, or None when the caller asked for no record."""
    return state.start_run(kind, trigger=trigger, now=started) if record else None


def _finish_record(handle, report: dict) -> None:
    if handle is not None:
        handle.finish(report.get("status") or "failed", counts=_recorded_counts(report))


def _close_outbox(started: datetime) -> int | None:
    """Mark done the outbox rows a successful full sync covered; None when the outbox cannot be written (production
    has no migration 0021), which leaves the run ``ok``."""
    try:
        return state.mark_done_before(started, kinds=FULL_SYNC_COVERS)
    except DatabaseError as exc:
        log.warning("graph_sync: the full sync succeeded but the outbox rows before it were not marked done: %s", exc)
        return None


# --- the DERIVED_FROM label step -----------------------------------------------------------------

_REPORTED_CLASSES = (labels.CHANGED, labels.CLEARED, labels.PLURAL_MISSING)
_LABEL_COUNT_KEYS = ("labels_rows", "labels_written", "labels_skipped_labelled", "labels_skipped_changed",
                     "labels_edges_missing")


def _sorted_unique_batches(codes, size: int):
    batch, last = [], None
    for code in sorted(codes):
        if code == last:
            continue
        last = code
        batch.append(code)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _approved_rows(driver, db, pairs: list[tuple[int, int]], label_sources: LabelSources) -> list[dict]:
    """Write rows for an approved change: each edge's labels read again now, and kept only where they still differ,
    so the write's guard compares with what the graph holds at this moment."""
    rows = []
    records = _records(_run(driver, db, LABELS_FOR_PAIRS, {"rows": [list(p) for p in pairs]}, read=True))
    for record in records:
        child, parent = record["child_id"], record["parent_id"]
        stored = {key: (record["stored"] or {}).get(key) for key in q.EDGE_LABEL_KEYS}
        computed = label_sources.edge(child, parent)
        if labels.classify(stored, computed) != labels.EQUAL:
            rows.append({"child_id": child, "parent_id": parent, "labels": computed, "stored": stored})
    return rows


def label_edges(driver, db, label_sources: LabelSources, *, apply_label_changes: bool = False) -> dict:
    """Classify every DERIVED_FROM between two Sample nodes against the label rule, then write what may be written.

    Without ``apply_label_changes`` only ``new`` edges (no singular assay label) are written, through the writer's
    guarded statement; ``changed``, ``cleared`` and ``plural_missing`` edges are counted per property
    (``labels_by_property``) with capped examples (``labels_examples``) and left as they are. With it every edge that
    differs is written where its stored labels still equal the ones read just before the write. An edge whose end
    has no int id (a legacy node) is counted and left alone.
    """
    def classify_all(result):
        # Built here, so a retried read starts clean.
        classes: Counter = Counter()
        by_property = {cls: Counter() for cls in _REPORTED_CLASSES}
        examples = {cls: [] for cls in _REPORTED_CLASSES}
        targets = array("q")
        edges = legacy = 0
        for record in result:
            edges += 1
            child, parent = record["child_id"], record["parent_id"]
            if not (_is_packable(child) and _is_packable(parent)):
                legacy += 1
                continue
            stored = record["stored"] or {}
            computed = label_sources.edge(child, parent)
            cls = labels.classify(stored, computed)
            classes[cls] += 1
            if cls in by_property:
                diff = labels.differences(stored, computed)
                by_property[cls].update(diff)
                if len(examples[cls]) < EXAMPLES:
                    examples[cls].append({"child_id": child, "parent_id": parent,
                                          "stored": {k: stored.get(k) for k in diff},
                                          "computed": {k: computed[k] for k in diff}})
            if cls == labels.NEW or (apply_label_changes and cls != labels.EQUAL):
                targets.append(encode_pair(child, parent))
        return edges, legacy, classes, by_property, examples, targets

    edges, legacy, classes, by_property, examples, targets = _run(driver, db, LABEL_EDGES, read=True,
                                                                  transformer=classify_all)
    written: Counter = Counter()
    for batch in _sorted_unique_batches(targets, writer.REL_CHUNK):
        pairs = [decode_pair(code) for code in batch]
        if apply_label_changes:
            rows = _approved_rows(driver, db, pairs, label_sources)
        else:
            rows = [{"child_id": c, "parent_id": p, "labels": label_sources.edge(c, p)} for c, p in pairs]
        if rows:
            out = writer.write_edge_labels(driver, db, rows, apply_label_changes=apply_label_changes)
            written.update({key: out.get(key, 0) for key in _LABEL_COUNT_KEYS})
    report = {"labels_edges": edges, "labels_legacy_id_edges": legacy, "labels_apply_changes": apply_label_changes}
    report.update({f"labels_{cls}": classes.get(cls, 0) for cls in labels.CLASSES})
    report.update({key: written.get(key, 0) for key in _LABEL_COUNT_KEYS})
    report["labels_by_property"] = {cls: dict(sorted(c.items())) for cls, c in by_property.items()}
    report["labels_examples"] = examples
    log.info("graph_sync: DERIVED_FROM labels: %d edges, %s; %d written", edges, dict(classes),
             report["labels_written"])
    return report


# --- SEEK Study nodes keyed on id ----------------------------------------------------------------

def _study_rekey_plan(driver, db) -> dict:
    """Which Study nodes carry a SEEK study on ``id`` and move to ``seek_study_id`` (R8). Read-only.

    Only on a graph where no Study carries ``seek_study_id`` yet (graph_sync never wrote its Study nodes), and only
    a node whose ``id`` is a SEEK study, whose title is that study's (surrounding whitespace aside) and which carries
    no DOI or PMID (the paper-level markers). A node whose id is a SEEK study but fails a test is left and listed.
    """
    if _one(_run(driver, db, SEEK_KEYED_STUDIES, read=True), "n"):
        return {"element_ids": [], "left_ids": []}
    seek_titles = {int(s["id"]): s.get("title") for s in sources.studies()}
    move, left = [], []
    for record in _records(_run(driver, db, STUDIES_KEYED_BY_ID, read=True)):
        study_id = record["id"]
        if not (_is_packable(study_id) and study_id in seek_titles):
            continue
        same_title = (record["title"] or "").strip() == (seek_titles[study_id] or "").strip()
        if same_title and not record["paper"]:
            move.append(record["element_id"])
        else:
            left.append(study_id)
    return {"element_ids": move, "left_ids": sorted(left)}


def rekey_seek_studies(driver, db, plan: dict) -> dict:
    """Move the planned Study nodes' key from ``id`` to ``seek_study_id``, so ``write_seek_studies`` finds them."""
    moved = 0
    for batch in _batches(plan["element_ids"], writer.REL_CHUNK):
        moved += _one(_run(driver, db, REKEY_STUDY, {"element_ids": batch}), "n")
    return {"studies_rekeyed": moved, "study_ids_left_keyed_by_id": _cap(plan["left_ids"])}


# --- the full sync -------------------------------------------------------------------------------

@dataclass
class _Preflight:
    cat: Catalog
    uuid_index: dict | None
    scan: SampleScan
    ghosts: dict
    problems: list


def _build_or_refuse(report: dict) -> Catalog:
    try:
        return _timed(report, "catalog", build_catalog)
    except ValueError as exc:  # a label collision, or a SEEK row the catalog cannot hold
        report["catalog_error"] = str(exc)
        raise PreflightError([f"the catalog does not build: {exc}"], report) from exc


def _preflight(driver, db, chunk: int, report: dict) -> _Preflight:
    cat = _build_or_refuse(report)
    report.update(sample_types=len(cat.sample_types), attributes_declared=len(cat.attributes), label_collisions=0)
    uuid_index = _timed(report, "read_uuids", sources.uuid_to_ids)
    scan = _timed(report, "preflight_scan", scan_samples, cat, chunk, uuid_index=uuid_index, collect_ids=True)
    report.update(samples_read=scan.samples, samples_projected=scan.projected, projection_errors=scan.errors,
                  projection_error_examples=scan.error_examples, lineage_declared_pairs=len(scan.lineage),
                  duplicate_uuids=sum(1 for ids in uuid_index.values() if len(ids) > 1))

    ghosts = _timed(report, "find_ghosts", writer.find_ghosts, driver, db, scan.ids, uuid_index.keys())
    scan.ids = set()  # only find_ghosts needs every id
    report.update(graph_sample_nodes=ghosts["sample_nodes"], graph_duplicate_ids=ghosts["duplicate_ids"],
                  ghosts=len(ghosts["ghost_element_ids"]), ghost_element_ids=_cap(ghosts["ghost_element_ids"]),
                  orphans=len(ghosts["orphan_ids"]), orphan_ids=_cap(ghosts["orphan_ids"]),
                  idless_samples=len(ghosts["idless_element_ids"]),
                  unresolved_duplicate_ids=_cap(ghosts["unresolved_duplicate_ids"]))
    conflicts = _title_conflicts(driver, db, cat)
    report["sample_type_title_conflicts"] = conflicts

    problems = []
    if scan.errors:
        problems.append(f"{scan.errors} samples cannot be projected (projection_error_examples)")
    if ghosts["unresolved_duplicate_ids"]:
        problems.append(f"{len(ghosts['unresolved_duplicate_ids'])} sample ids sit on more than one node whose "
                        "uuid is in MySQL (unresolved_duplicate_ids); Sample.id cannot be made unique")
    if conflicts:
        problems.append(f"{len(conflicts)} SampleType titles are held under other ids in the graph "
                        "(sample_type_title_conflicts)")
    report["problems"] = problems
    return _Preflight(cat, uuid_index, scan, ghosts, problems)


def _preview(driver, db, state_: _Preflight, report: dict, bench_keys) -> None:
    """Dry run: what the write steps would do, from reads only."""
    census = state_.scan.census
    _summarize_census(report, census, state_.cat)
    report["cast_failures"] = state_.scan.cast_failures
    declared = DeclaredUuidPairs(state_.scan.lineage, state_.uuid_index)

    def count(result):
        pairs = undeclared = 0
        for record in result:
            pairs += 1
            undeclared += (record["child_uuid"] or "", record["parent_uuid"] or "") not in declared
        return pairs, undeclared

    report["child_of_pairs"], report["child_of_undeclared"] = _timed(
        report, "child_of_preview", _run, driver, db, q.CHILD_OF_PAIRS, read=True, transformer=count)
    budget = sorted(key for key, entry in census.items() if writer._qualifies(key, entry, bench_keys))
    report["index_budget"] = len(budget)
    report["index_budget_keys"] = _cap(budget)
    plan = _timed(report, "study_rekey_preview", _study_rekey_plan, driver, db)
    report["studies_to_rekey"] = len(plan["element_ids"])
    report["study_ids_left_keyed_by_id"] = _cap(plan["left_ids"])


def _write(driver, db, chunk: int, run_dir: str, bench_keys, state_: _Preflight, report: dict,
           apply_label_changes: bool) -> None:
    cat, ghosts = state_.cat, state_.ghosts
    _step(report, "delete_ghosts", writer.delete_ghosts, driver, db, ghosts["ghost_element_ids"])
    _step(report, "retire", writer.retire_samples, driver, db, ghosts["orphan_ids"],
          os.path.join(run_dir, RETIRED_FILE))
    _step(report, "orphan_idless", writer.relabel_orphans, driver, db, [],
          element_ids=ghosts["idless_element_ids"])
    _step(report, "archive_child_of", writer.archive_and_drop_child_of, driver, db,
          os.path.join(run_dir, ARCHIVE_FILE), DeclaredUuidPairs(state_.scan.lineage, state_.uuid_index))
    _step(report, "constraints", writer.ensure_constraints_v11, driver, db)
    _step(report, "sample_types", writer.write_sample_types, driver, db, cat.sample_types)
    _step(report, "attributes_declared", writer.write_attributes, driver, db, cat.attributes)
    _step(report, "projects", writer.write_projects, driver, db, sources.projects())
    _step(report, "people", writer.write_people_and_memberships, driver, db, sources.memberships())
    _step(report, "investigations", writer.write_investigation_projects, driver, db, sources.investigations(),
          sources.investigation_projects())

    label_sources = _timed(report, "read_label_maps", LabelSources.read)
    label_maps_hash = label_sources.maps_hash()
    totals: Counter = Counter()

    def write_page(projections):
        counts = writer.write_samples(driver, db, projections, chunk=chunk)
        totals.update({k: v for k, v in counts.items() if isinstance(v, int)})

    written = _timed(report, "samples", scan_samples, cat, chunk, on_page=write_page,
                     parents=lambda page: _page_parents(page, label_sources))
    report["steps"]["samples"] = dict(totals)
    report.update(totals)
    report.update(samples_read_in_write_pass=written.samples, projection_errors_in_write_pass=written.errors,
                  projection_error_examples_in_write_pass=written.error_examples)

    lineage = sorted(state_.scan.lineage)
    state_.uuid_index = None  # free the per-sample index before the study links load
    state_.scan.lineage = set()
    _step(report, "lineage", writer.write_missing_lineage, driver, db, (decode_pair(code) for code in lineage))
    _step(report, "lineage_undeclared", writer.archive_and_drop_undeclared_derived_from, driver, db,
          os.path.join(run_dir, DERIVED_FROM_ARCHIVE_FILE), DeclaredIdPairs(lineage))
    del lineage
    _step(report, "labels", label_edges, driver, db, label_sources, apply_label_changes=apply_label_changes)
    del label_sources
    plan = _timed(report, "study_rekey_plan", _study_rekey_plan, driver, db)
    _step(report, "study_rekey", rekey_seek_studies, driver, db, plan)
    _step(report, "seek_studies", writer.write_seek_studies, driver, db, sources.seek_study_links())

    census = written.census
    attributes = cat.attributes + undeclared_attributes(cat, census)
    _summarize_census(report, census, cat)
    _step(report, "attributes", writer.write_attributes, driver, db, attributes)
    _step(report, "attribute_counts", writer.write_attribute_counts, driver, db,
          {key: entry["sample_count"] for key, entry in census.items()})
    _step(report, "sample_type_counts", writer.write_sample_type_counts, driver, db)
    names = _timed(report, "index_budget", writer.ensure_index_budget, driver, db, census, bench_keys=bench_keys)
    report["index_budget"] = len(names)
    report["index_budget_names"] = names
    _step(report, "fulltext", writer.ensure_fulltext, driver, db)
    _step(report, "await_indexes", writer.await_indexes, driver, db)
    _step(report, "graphmeta", writer.write_graphmeta, driver, db, catalog.catalog_hash(cat.sample_types, attributes),
          label_maps_hash)
    _write_json(os.path.join(run_dir, CENSUS_FILE), dict(sorted(census.items())))
    report["census_path"] = os.path.join(run_dir, CENSUS_FILE)


def full_sync(driver, db, chunk: int = writer.SAMPLE_CHUNK, dry_run: bool = False, run_dir: str | None = None,
              bench_keys=frozenset(), *, apply_label_changes: bool = False,
              lock_timeout_s: float = FULL_LOCK_TIMEOUT_S, record: bool = True, trigger: str = "command") -> dict:
    """Rebuild graph schema v1.2 from MySQL, in the design's order (module docstring). Returns the report.

    ``run_dir`` receives ``full_sync.json`` (written even when the run fails or is refused), ``census.json``,
    ``retired.tsv`` (when a Sample node graph_sync wrote has left MySQL), ``child_of_archive.tsv`` (when the graph
    had CHILD_OF) and ``derived_from_undeclared_archive.tsv`` (when it had an undeclared DERIVED_FROM between two
    Sample nodes); without one, a new directory under ``$GS_RUN_DIR`` is used, and a run with neither is refused.
    ``bench_keys`` holds attribute keys or (sample type title, attribute title) pairs the index budget must cover.
    ``dry_run`` reads MySQL and the graph, writes nothing, touches no file, takes no lock and records no run.

    ``apply_label_changes`` (the operator's approval, R14) also writes the DERIVED_FROM labels that differ from the
    rule; without it only new labels are written and the rest are counted in the report. ``lock_timeout_s`` bounds
    the wait for the graph-write lock. ``record`` writes a ``graph_sync_run`` row started by ``trigger``.

    Raises PreflightError, before any write, when the preflight finds a problem (``problems`` in the report), and its
    subclass LockTimeout when the lock is not acquired.
    """
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")
    report = {"mode": "full", "dry_run": dry_run, "chunk": chunk, "schema_version": writer.SCHEMA_VERSION,
              "apply_label_changes": apply_label_changes, "started_at": _now(), "timings_s": {}, "steps": {}}
    if dry_run:
        try:
            _preview(driver, db, _preflight(driver, db, chunk, report), report, bench_keys)
            report["status"] = "dry_run"
            return report
        except PreflightError:
            report["status"] = "refused"
            raise
        finally:
            report["finished_at"] = _now()

    started = dj_timezone.now()
    report["started_at"] = _iso(started)
    handle = _start_record("full", trigger, started, record)
    try:
        run_dir = _resolve_run_dir(run_dir, report)
        os.makedirs(run_dir, exist_ok=True)
        report["run_dir"] = run_dir
        with state.graph_write_lock(lock_timeout_s) as held:
            if not held:
                raise LockTimeout([_lock_problem(lock_timeout_s)], report)
            preflight = _preflight(driver, db, chunk, report)
            if preflight.problems:
                raise PreflightError(preflight.problems, report)
            _write(driver, db, chunk, run_dir, bench_keys, preflight, report, apply_label_changes)
        report["status"] = "ok"
        report["outbox_marked_done"] = _close_outbox(started)
        return report
    except PreflightError:
        report["status"] = "refused"
        raise
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = _now()
        if report.get("run_dir"):
            _write_json(os.path.join(report["run_dir"], REPORT_FILE), report)
        _finish_record(handle, report)


# --- the catalog sync ----------------------------------------------------------------------------

def _catalog_plan(driver, db, report: dict) -> tuple[Catalog, list[dict], dict[str, int], str]:
    """Build the catalog and read what the graph keeps (undeclared attributes, counts, its schema version)."""
    cat = _build_or_refuse(report)
    declared_keys = {a["key"] for a in cat.attributes}
    counts: dict[str, int] = {}
    kept: dict[str, dict] = {}
    for row in _records(_run(driver, db, ATTRIBUTE_STATE, read=True)):
        key = row["key"]
        if key is None:
            continue
        if row["sample_count"] is not None:
            counts[key] = int(row["sample_count"])
        type_id = row["sample_type_id"]
        if (row["declared"] is False and key not in declared_keys and row["title"]
                and type_id is not None and int(type_id) in cat.type_titles):
            attr = catalog.undeclared_attribute(int(type_id), cat.type_titles[int(type_id)], row["title"])
            kept[attr["key"]] = attr
    attributes = cat.attributes + [kept[k] for k in sorted(kept)]
    catalog_hash = catalog.catalog_hash(cat.sample_types, attributes)
    report.update(sample_types=len(cat.sample_types), attributes_declared=len(cat.attributes),
                  undeclared_attributes_kept=len(kept), label_collisions=0, catalog_hash=catalog_hash,
                  sample_type_title_conflicts=_title_conflicts(driver, db, cat),
                  graph_schema_version=writer.graphmeta(driver, db)["schema_version"])
    return cat, attributes, counts, catalog_hash


def catalog_sync(driver, db, dry_run: bool = False, *, lock_timeout_s: float = CATALOG_LOCK_TIMEOUT_S,
                 record: bool = True, trigger: str = "command") -> dict:
    """Rewrite the catalog nodes only: SampleType, Attribute, HAS_ATTRIBUTE, their counts and GraphMeta.

    Undeclared Attribute nodes a full sync wrote are kept while their type exists and does not now declare the
    key, and every Attribute keeps its ``sample_count`` (a new one gets 0). GraphMeta keeps its ``label_maps_hash``.
    ``dry_run`` reads and writes nothing, takes no lock and records no run.

    Holds the graph-write lock (``lock_timeout_s``) and records a ``graph_sync_run`` row (``record``, ``trigger``).
    Raises PreflightError, before any write, when a SampleType title is held under another id in the graph, or the
    graph is not at the writer's schema version (``graph_schema_version``): a catalog sync stamps GraphMeta with that
    version, which only a full sync may do first. Raises its subclass LockTimeout when the lock is not acquired.
    """
    report = {"mode": "catalog", "dry_run": dry_run, "schema_version": writer.SCHEMA_VERSION,
              "started_at": _now(), "timings_s": {}, "steps": {}}
    if dry_run:
        _catalog_plan(driver, db, report)
        report["status"] = "dry_run"
        report["finished_at"] = _now()
        return report

    started = dj_timezone.now()
    report["started_at"] = _iso(started)
    handle = _start_record("catalog", trigger, started, record)
    try:
        with state.graph_write_lock(lock_timeout_s) as held:
            if not held:
                raise LockTimeout([_lock_problem(lock_timeout_s)], report)
            cat, attributes, counts, catalog_hash = _catalog_plan(driver, db, report)
            problems = []
            if report["sample_type_title_conflicts"]:
                problems.append(f"{len(report['sample_type_title_conflicts'])} SampleType titles are held under "
                                "other ids in the graph (sample_type_title_conflicts)")
            if report["graph_schema_version"] != writer.SCHEMA_VERSION:
                problems.append(f"the graph is at schema {report['graph_schema_version']!r}, not "
                                f"{writer.SCHEMA_VERSION!r}; run a full sync first, which brings it there")
            if problems:
                report["problems"] = problems
                raise PreflightError(problems, report)
            _step(report, "sample_types", writer.write_sample_types, driver, db, cat.sample_types)
            _step(report, "attributes", writer.write_attributes, driver, db, attributes)
            _step(report, "attribute_counts", writer.write_attribute_counts, driver, db,
                  {a["key"]: counts.get(a["key"], 0) for a in attributes})
            _step(report, "sample_type_counts", writer.write_sample_type_counts, driver, db)
            _step(report, "graphmeta", writer.write_graphmeta, driver, db, catalog_hash)
        report["status"] = "ok"
        return report
    except PreflightError:
        report["status"] = "refused"
        raise
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = _now()
        _finish_record(handle, report)
