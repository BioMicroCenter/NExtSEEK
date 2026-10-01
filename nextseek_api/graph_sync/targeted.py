"""The by-id entry points of graph_sync (the sync design, section 7.1; R14, R15).

What the outbox drain, batch upload's inline sync, the nightly targeted sync and ``graph_sync --samples`` call to
bring part of the graph up to date without a full sync:

- ``sync_samples(driver, db, ids)``: those samples, their lineage as children, the labels of every edge incident to
  them, their SEEK studies, the undeclared keys they carry and their types' counts; an id MySQL no longer returns is
  retired.
- ``sync_samples_of_type(driver, db, type_id)``: ``sync_samples`` over every sample of a type, one chunk at a time.
- ``retire_samples(driver, db, ids)``: the deletion rule (section 9) for ids MySQL no longer holds.
- ``relabel_for_maps(driver, db)``: the labels a change to the resolved assay map or to ``sops`` affects.
- ``sync_small_tables(driver, db)``: projects, investigations, people and memberships, and every SEEK study's
  node with its title, description and investigation.
- ``preview_labels(driver, db, ids)``: read only, no lock: how an approved ``sync_samples`` would class each
  DERIVED_FROM edge incident to ``ids`` (the studies tool's label approval check).

**Every call is one write unit.** It first reads ``GraphMeta.schema_version`` and refuses, writing nothing, unless it
is the writer's (``status: not_at_version``): a 1.1 graph waits for the operator's first full sync at 1.2. Then it
takes the graph-write lock (``state.graph_write_lock``), waiting at most ``lock_timeout_s`` (``LOCK_WAIT_S``, the
spec's 60 s, R10); without the lock it returns ``status: lock_timeout`` and writes nothing, so the caller's outbox row
stays pending. Otherwise it returns ``status: ok`` and every step's counts. An error part way raises: every step is
idempotent, so the caller retries the whole call. ``sync_samples_of_type`` takes the lock once per chunk, so a long
type sync lets other writers in between chunks.

**Order of ``sync_samples``**, per chunk of ids: read the rows, their projects, assay ids and parent tokens; read the
types the nodes point at now; retire the ids MySQL did not return; run ``run.catalog_sync`` when a row's type has no
SampleType node or one holding another title; project and write the samples (``source_hash`` and the parent lists
always, R1, R4); the declared lineage of these samples as children (create what is missing, then archive and delete
what MySQL does not declare); label every edge incident to them, both directions, the ones just created included;
IN_STUDY, which follows SEEK (``study_links``: a link SEEK no longer holds is removed only where the box's switch is
on, archived first); ``declared: false`` Attribute nodes; the touched types' counts. A sample that cannot be projected
is counted and skipped whole, its lineage included: its parent tokens could not be read, and reading them as none
would delete every edge it has.

**Labels** (section 7.3). Each edge is labelled by ``labels.edge_labels`` from MySQL and classified against what it
stores (``labels.classify``). Without the operator's approval ``new`` edges are written, and the writer's own guard
skips an edge labelled meanwhile (R14); so are ``renamed`` and ``protocol_filled`` edges (an internal assay renamed
under its id, a protocol filled where none was stored), only where the stored values still equal those read.
``changed``, ``cleared`` and ``plural_missing`` edges are counted per property with a few examples, and written only
with ``apply_label_changes=True``, then only where the stored values still equal those read. An edge a call creates
has no label, so it is ``new`` and labelled in the same call (R15).

**Archives.** ``retired.tsv``, ``derived_from_undeclared_archive.tsv`` and ``in_study_removed.tsv`` are appended
in ``run_dir``; without one,
in a new ``targeted-<UTC time>`` directory under ``$GS_RUN_DIR``, else under ``<LOG_DIR>/graph_sync``, created only
when a row is archived. The writer writes and flushes each archive before the delete it records.

The statements that ``cypher.py`` does not hold are module constants here.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from django.conf import settings

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.graph_sync import catalog, labels, run, sources, state, study_links, writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.projection import SYSTEM_KEYS, parent_lists, project_sample
from nextseek_api.graph_sync.writer import _batches, _one, _records, _run

log = logging.getLogger(__name__)

OK, NOT_AT_VERSION, LOCK_TIMEOUT = "ok", "not_at_version", "lock_timeout"
LOCK_WAIT_S = 60          # the spec's bounded wait for the graph-write lock (R10)
RETIRED_FILE = "retired.tsv"
DERIVED_FROM_ARCHIVE_FILE = "derived_from_undeclared_archive.tsv"   # the full sync's name, so a run keeps one
EXAMPLES = 20             # examples kept per report list
LIST_CAP = 1_000          # longest id list copied into a report

# The counts of a by-id sync that mean a structural link was left unwritten: the sample's OF_TYPE or an IN_PROJECT,
# its IN_STUDY row, a Study node for one of its SEEK studies, or a Study's IN_INVESTIGATION. A sample a report names
# in structural_gap_samples is not done (the drain fails it on a row of its own, and its source_hash stays null for
# the nightly); every other sample of the call is. A parent not yet uploaded (lineage_dropped) and an edge gone before
# its label was written (labels_edges_missing) are expected states instead.
STRUCTURAL_GAP_KEYS = ("untyped", "in_project_missing", "in_study_samples_missing", "in_study_studies_missing",
                       "seek_study_investigation_missing")
# How a gap the reads cannot trace to its samples names them: every written sample of its chunk carries it.
UNTRACED_MARK = "not traced to a sample"
UNTRACED_GAP = "{part} {count} in its chunk, " + UNTRACED_MARK

_NO_LABEL_WRITES = {"labels_rows": 0, "labels_written": 0, "labels_skipped_labelled": 0,
                    "labels_skipped_changed": 0, "labels_edges_missing": 0, "labels_refresh_rows": 0,
                    "labels_refreshed": 0, "labels_refresh_skipped_changed": 0, "labels_refresh_edges_missing": 0}

# --- statements ----------------------------------------------------------------------------------

# The SampleType nodes of these type ids and their titles: a missing node, or one holding another title, makes a
# by-id sync run the catalog sync before it writes a sample of that type.
SAMPLE_TYPES_PRESENT = """
MATCH (t:SampleType) WHERE t.id IN $ids
RETURN t.id AS id, t.title AS title
"""
# The types the Sample nodes of $ids point at before a sync moves or retires them; their counts are set afterwards.
TYPES_OF_SAMPLES = """
UNWIND $ids AS id
MATCH (:Sample {id: id})-[:OF_TYPE]->(t:SampleType)
RETURN DISTINCT t.id AS id
"""
# cypher.SET_SAMPLE_TYPE_COUNTS for these types only.
SET_SAMPLE_TYPE_COUNTS_FOR = """
UNWIND $ids AS id
MATCH (t:SampleType {id: id})
SET t.sample_count = COUNT { (t)<-[:OF_TYPE]-(:Sample) },
    t.attribute_count = COUNT { (t)-[:HAS_ATTRIBUTE]->(:Attribute) }
RETURN count(t) AS n
"""
ATTRIBUTE_KEYS_PRESENT = """
MATCH (a:Attribute) WHERE a.key IN $keys
RETURN a.key AS key
"""
# A declared: false Attribute for a key a sample carries and its type does not declare, linked from its SampleType.
# An existing node is left as it is; a new one counts 0 samples until the next full sync counts it.
CREATE_UNDECLARED_ATTRIBUTES = """
UNWIND $rows AS r
MERGE (a:Attribute {key: r.key})
ON CREATE SET a = r, a.sample_count = 0
WITH a, r
MATCH (t:SampleType {id: r.sample_type_id})
MERGE (t)-[:HAS_ATTRIBUTE]->(a)
RETURN count(*) AS linked
"""
# relabel_for_maps: every winning assay resolution and every protocol the edges carry, to compare with the maps.
GRAPH_ASSAY_LABELS = """
MATCH (:Sample)-[e:DERIVED_FROM]->(:Sample)
WHERE e.assay_id IS NOT NULL
RETURN DISTINCT e.assay_id AS assay_id, e.internal_assay_id AS internal_assay_id,
       e.internal_assay_title AS internal_assay_title
"""
GRAPH_PROTOCOL_LABELS = """
MATCH (:Sample)-[e:DERIVED_FROM]->(:Sample)
WHERE e.protocol_id IS NOT NULL
RETURN DISTINCT e.protocol_id AS protocol_id, e.protocol_title AS protocol_title
"""
EDGES_WITH_PROTOCOLS = """
MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)
WHERE e.protocol_id IN $ids
RETURN c.id AS child_id, p.id AS parent_id, elementId(e) AS element_id, properties(e) AS props
"""
# --- plumbing ------------------------------------------------------------------------------------

def _ids(ids) -> list[int]:
    """The distinct sample ids as ints, ascending."""
    return sorted({int(i) for i in ids})


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _default_run_dir() -> str:
    """A new ``targeted-<UTC time>`` directory under ``$GS_RUN_DIR``, else under ``<LOG_DIR>/graph_sync`` (the
    loop's run root). Only named here: the writer creates it when it first archives a row."""
    base = os.environ.get("GS_RUN_DIR") or os.path.join(
        getattr(settings, "LOG_DIR", None) or tempfile.gettempdir(), "graph_sync")
    return os.path.join(base, "targeted-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def _add(total: dict, part: dict) -> dict:
    """Add ``part``'s counts into ``total``: numbers are summed, nested dicts merged, lists joined up to
    ``EXAMPLES`` entries, and any other value (a status, a path) taken from the latest part that has one."""
    for key, value in part.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, dict, list)):
            if value is not None or key not in total:
                total[key] = value
        elif isinstance(value, dict):
            _add(total.setdefault(key, {}), value)
        elif isinstance(value, list):
            total[key] = (list(total.get(key) or []) + value)[:EXAMPLES]
        else:
            total[key] = total.get(key, 0) + value
    return total


def _metadata(raw) -> dict:
    """``json_metadata`` as a dict; unreadable or non-object metadata reads as empty (the projection refuses it)."""
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


class _Context:
    """What one call reads once and reuses across its chunks: where it archives, the operator's approval of label
    changes, the box's study-link switch, the catalog, the label maps and SEEK's small tables (each read on first
    use)."""

    def __init__(self, run_dir: str | None, apply_label_changes: bool = False):
        self.run_dir = os.path.abspath(run_dir) if run_dir else _default_run_dir()
        self.apply_label_changes = bool(apply_label_changes)
        self.follow_seek = study_links.follows_seek()   # the box's study-link switch, read once per call
        self._catalog = None
        self._maps = None
        self._tables = None

    def archive(self, name: str) -> str:
        return os.path.join(self.run_dir, name)

    def catalog(self):
        if self._catalog is None:
            self._catalog = run.build_catalog()
        return self._catalog

    def seek_tables(self):
        """SEEK's studies, investigations, investigation projects and projects (``study_links.seek_tables``)."""
        if self._tables is None:
            self._tables = study_links.seek_tables()
        return self._tables

    def maps(self) -> tuple[dict, dict, dict]:
        """The resolved assay map, ``sops`` and the SOP title index ``labels.resolve_protocol`` takes."""
        if self._maps is None:
            sops = sources.sops_map()
            self._maps = (sources.resolved_assay_map(), sops, labels.sop_title_index(sops))
        return self._maps


def _refusal(driver, db) -> dict | None:
    """None when the graph is at the writer's version; otherwise the ``not_at_version`` result. Read-only."""
    found = writer.graphmeta(driver, db).get("schema_version")
    if found == writer.SCHEMA_VERSION:
        return None
    log.info("graph_sync: the graph is at schema version %r, not %s; writing nothing", found, writer.SCHEMA_VERSION)
    return {"status": NOT_AT_VERSION, "schema_version": found, "writer_version": writer.SCHEMA_VERSION}


def _guarded(driver, db, lock_timeout_s: float, work) -> dict:
    """Run ``work()`` as one write unit: refused on a graph not at the writer's version, and only under the
    graph-write lock."""
    refused = _refusal(driver, db)
    if refused is not None:
        return refused
    with state.graph_write_lock(lock_timeout_s) as held:
        if not held:
            log.info("graph_sync: the graph-write lock was busy for %s s; writing nothing", lock_timeout_s)
            return {"status": LOCK_TIMEOUT, "lock_timeout_s": lock_timeout_s}
        return work()


# --- sync_samples --------------------------------------------------------------------------------

def _types_of_samples(driver, db, ids) -> set[int]:
    found: set[int] = set()
    for batch in _batches(ids, writer.REL_CHUNK):
        found.update(r["id"] for r in _records(_run(driver, db, TYPES_OF_SAMPLES, {"ids": batch}, read=True)))
    return found


def _set_type_counts(driver, db, type_ids) -> int:
    ids = sorted(t for t in type_ids if _is_id(t))
    return sum(_one(_run(driver, db, SET_SAMPLE_TYPE_COUNTS_FOR, {"ids": batch}), "n")
               for batch in _batches(ids, writer.REL_CHUNK))


def _ensure_sample_types(driver, db, rows, cat) -> list[int]:
    """Run the catalog sync when a row's type has no SampleType node, or one holding another title. Returns those
    type ids."""
    type_ids = sorted({r["sample_type_id"] for r in rows if r["sample_type_id"] in cat.type_titles})
    if not type_ids:
        return []
    held = {r["id"]: r["title"]
            for r in _records(_run(driver, db, SAMPLE_TYPES_PRESENT, {"ids": type_ids}, read=True))}
    stale = [t for t in type_ids if held.get(t) != cat.type_titles[t]]
    if stale:
        log.info("graph_sync: sample types %s have no current SampleType node; running the catalog sync first", stale)
        run.catalog_sync(driver, db)
    return stale


def _project_rows(rows, cat):
    """Project each row with its projects, assay ids and parent lists. Returns the projections, each row's metadata
    and parent tokens by id, and the rows that could not be projected."""
    ids = [r["id"] for r in rows]
    projects = sources.sample_projects_for(ids)
    assays = sources.sample_assay_ids_for(ids)
    metas = {r["id"]: _metadata(r["json_metadata"]) for r in rows}
    tokens = {sid: collect_parent_tokens(meta) for sid, meta in metas.items()}
    uids = sorted({t for found in tokens.values() for t in found if UID_RE.match(t)})
    identities = sources.parent_identities(uids) if uids else {}
    projections, errors = [], []
    for row in rows:
        sid, type_id = row["id"], row["sample_type_id"]
        title = cat.type_titles.get(type_id)
        if title is None:
            errors.append({"id": sid, "error": f"sample type {type_id} is not in sample_types"})
            continue
        try:
            projections.append(project_sample(row, title, cat.value_types.get(type_id, {}), projects.get(sid, ()),
                                              assay_ids=assays.get(sid, ()),
                                              parent_lists=parent_lists(tokens[sid], identities)))
        except (ValueError, TypeError) as exc:
            errors.append({"id": sid, "error": str(exc)})
    return projections, metas, tokens, errors


def _lineage(driver, db, rows, tokens, ctx: _Context) -> dict:
    """Make the DERIVED_FROM edges from these samples to Sample parents equal to what their parent tokens declare."""
    uids = sorted({t for r in rows for t in tokens[r["id"]] if UID_RE.match(t)})
    index = sources.uuid_to_ids_for(uids) if uids else {}
    pairs = sorted(set(sources.declared_lineage(rows, index)))
    report = writer.write_missing_lineage(driver, db, pairs)
    report.update(writer.archive_and_drop_undeclared_for_children(
        driver, db, [r["id"] for r in rows], set(pairs), ctx.archive(DERIVED_FROM_ARCHIVE_FILE)))
    return report


def _undeclared_attributes(driver, db, projections, cat) -> dict:
    """A ``declared: false`` Attribute for every key a sample carries that its type does not declare. When one is
    created, ``_sync_ids`` runs the catalog sync to restamp ``GraphMeta.catalog_hash``, which graph_search caches the
    catalog on."""
    wanted: dict[str, dict] = {}
    for proj in projections:
        declared = cat.value_types.get(proj.sample_type_id, {})
        for key in proj.props:
            if key in SYSTEM_KEYS or key in declared:
                continue
            attr = catalog.undeclared_attribute(proj.sample_type_id, cat.type_titles[proj.sample_type_id], key)
            wanted.setdefault(attr["key"], attr)
    if not wanted:
        return {"undeclared_attributes_created": 0}
    present = {r["key"] for r in _records(_run(driver, db, ATTRIBUTE_KEYS_PRESENT, {"keys": sorted(wanted)},
                                               read=True))}
    new = [wanted[key] for key in sorted(wanted) if key not in present]
    if not new:
        return {"undeclared_attributes_created": 0}
    for batch in _batches(new, writer.REL_CHUNK):
        _run(driver, db, CREATE_UNDECLARED_ATTRIBUTES, {"rows": batch})
    log.info("graph_sync: %d undeclared Attribute nodes created", len(new))
    return {"undeclared_attributes_created": len(new), "undeclared_attribute_keys": [a["key"] for a in new]}


def _attribute_counts(driver, db, projections) -> dict:
    """Count the attributes still at 0 that a written sample now carries (a new attribute filled by an upload, or an
    undeclared key this sync created), from their type's samples. When one rose above 0, ``_sync_ids`` runs the
    catalog sync: it restamps ``GraphMeta.catalog_hash``, which holds which attributes carry values, so Nessie's
    catalog snapshot and graph_search's cache re-read. A steady-state sync finds no such attribute and writes
    nothing."""
    carried: dict[int, set] = {}
    for proj in projections:
        carried.setdefault(proj.sample_type_id, set()).update(proj.props)
    type_ids = sorted(t for t in carried if _is_id(t))
    if not type_ids:
        return {"attribute_counts_raised": 0}
    rows = [{"type_id": r["type_id"], "key": r["key"], "title": r["title"]}
            for r in _records(_run(driver, db, q.ATTRIBUTES_AT_ZERO, {"type_ids": type_ids}, read=True))
            if r["title"] in carried.get(r["type_id"], ())]
    raised = sum(_one(_run(driver, db, q.SET_ATTRIBUTE_COUNTS_FROM_TYPE, {"rows": batch}), "raised")
                 for batch in _batches(rows, writer.REL_CHUNK))
    if raised:
        log.info("graph_sync: %d attributes now hold values", raised)
    return {"attribute_counts_raised": raised}


def _sync_ids(driver, db, wanted: list[int], ctx: _Context) -> dict:
    """``sync_samples``' work for one chunk of ids, under the lock (module docstring, "Order")."""
    report = {"status": OK, "requested": len(wanted)}
    rows = sources.samples_by_ids(wanted)
    found = {r["id"] for r in rows}
    gone = [i for i in wanted if i not in found]
    report.update(found=len(rows), missing_in_mysql=len(gone))
    old_types = _types_of_samples(driver, db, wanted)
    if gone:
        report.update(writer.retire_samples(driver, db, gone, ctx.archive(RETIRED_FILE)))

    projections, links = [], []
    if rows:
        cat = ctx.catalog()
        report["catalog_synced_for_types"] = _ensure_sample_types(driver, db, rows, cat)
        projections, metas, tokens, errors = _project_rows(rows, cat)
        report.update(projected=len(projections), projection_errors=len(errors),
                      projection_error_examples=errors[:EXAMPLES])
        if projections:
            report.update(writer.merge_missing_projects(
                driver, db, {pid for proj in projections for pid in proj.props.get("project_ids") or ()},
                ctx.seek_tables().projects))
            report.update(writer.write_samples(driver, db, projections))
            written = {p.id for p in projections}
            report.update(_lineage(driver, db, [r for r in rows if r["id"] in written], tokens, ctx))
            report.update(_label_edges(driver, db, writer.edges_incident(driver, db, sorted(written)), ctx, metas))
            links = sources.seek_study_links_for(sorted(written))
            report.update(writer.write_seek_studies(driver, db, links, sorted(written), remove=ctx.follow_seek,
                                                    archive_path=ctx.archive(study_links.ARCHIVE_FILE),
                                                    tables=ctx.seek_tables()))
            undeclared = _undeclared_attributes(driver, db, projections, cat)
            counted = _attribute_counts(driver, db, projections)
            report.update(undeclared)
            report.update(counted)
            if undeclared["undeclared_attributes_created"] or counted["attribute_counts_raised"]:
                # One catalog sync restamps the catalog hash for both (the lock nests).
                report["catalog_resynced"] = run.catalog_sync(driver, db).get("status")
    report["sample_type_counts_set"] = _set_type_counts(driver, db,
                                                        old_types | {p.sample_type_id for p in projections})
    parts = {key: int(report[key]) for key in STRUCTURAL_GAP_KEYS if report.get(key)}
    report.update(structural_gaps=sum(parts.values()), structural_gap_parts=parts)
    if parts:
        report["structural_gap_samples"] = _gap_samples(driver, db, parts, projections, links, ctx.seek_tables())
    return report


def _project_text(ids, in_seek) -> str:
    lacking = [p for p in ids if p not in in_seek]
    other = [p for p in ids if p in in_seek]
    return "; ".join(text for text in (
        "project ids SEEK lacks: " + ", ".join(map(str, lacking)) if lacking else "",
        "no Project node for " + ", ".join(map(str, other)) if other else "") if text)


def _gap_samples(driver, db, parts: dict, projections, links, tables) -> dict[int, str]:
    """Sample id to why the chunk's structural gaps (``parts``) name it, read after the writes under the same lock
    and only when a gap was counted: no SampleType node for its type; IN_PROJECT to project ids with no Project node,
    those SEEK's ``projects`` lacks named so; one of its SEEK studies whose Investigation node is missing. A part
    whose reads do not account for the count its statement returned, and the two IN_STUDY parts (a sample or a Study
    node gone between the write and the link, which no read here can place), name every written sample of the chunk
    (``UNTRACED_GAP``), as the whole row failed before."""
    reasons: dict[int, dict[str, None]] = {}
    written = sorted(p.id for p in projections)

    def name(sample_id, text):
        reasons.setdefault(int(sample_id), {})[text] = None

    def untraced(part):
        for sample_id in written:
            name(sample_id, UNTRACED_GAP.format(part=part, count=parts[part]))

    if parts.get("untyped"):
        type_ids = sorted({p.sample_type_id for p in projections if _is_id(p.sample_type_id)})
        present = {r["id"] for r in _records(_run(driver, db, SAMPLE_TYPES_PRESENT, {"ids": type_ids}, read=True))}
        found = [p for p in projections if p.sample_type_id not in present]
        if len(found) != parts["untyped"]:
            untraced("untyped")
        else:
            for p in found:
                name(p.id, f"untyped (no SampleType node for type {p.sample_type_id})")
    if parts.get("in_project_missing"):
        wanted = sorted({pid for p in projections for pid in p.props.get("project_ids") or ()})
        present = {r["id"] for r in _records(_run(driver, db, q.PROJECT_IDS_PRESENT, {"ids": wanted}, read=True))}
        in_seek = {int(r["id"]) for r in tables.projects}
        absent = {p.id: sorted(set(p.props.get("project_ids") or ()) - present) for p in projections}
        if sum(len(ids) for ids in absent.values()) != parts["in_project_missing"]:
            untraced("in_project_missing")
        else:
            for sample_id, ids in absent.items():
                if ids:
                    name(sample_id, f"in_project_missing ({_project_text(ids, in_seek)})")
    if parts.get("seek_study_investigation_missing"):
        investigation_of = {int(link["study_id"]): link.get("investigation_id") for link in links}
        in_seek = {int(i["id"]) for i in tables.investigations}
        missing = set()
        for r in _records(_run(driver, db, q.STUDY_NODES, read=True)):
            study_id = (r["props"] or {}).get("seek_study_id")
            inv = investigation_of.get(study_id) if _is_id(study_id) else None
            if inv is not None and not any(i.get("id") == inv for i in r["investigations"] or ()):
                missing.add(study_id)
        if len(missing) != parts["seek_study_investigation_missing"]:
            untraced("seek_study_investigation_missing")
        else:
            for link in links:
                study_id = int(link["study_id"])
                if study_id not in missing:
                    continue
                inv = investigation_of[study_id]
                why = (f"investigation {inv}, which SEEK lacks" if inv not in in_seek
                       else f"no Investigation node for {inv}")
                name(link["sample_id"], f"seek_study_investigation_missing (study {study_id}: {why})")
    for part in ("in_study_samples_missing", "in_study_studies_missing"):
        if parts.get(part):
            untraced(part)
    return {sample_id: "; ".join(texts) for sample_id, texts in sorted(reasons.items())}


def sync_samples(driver, db, ids, *, run_dir: str | None = None, apply_label_changes: bool = False,
                 lock_timeout_s: float = LOCK_WAIT_S, chunk: int = writer.SAMPLE_CHUNK) -> dict:
    """Bring the samples ``ids`` up to date from MySQL (module docstring, "Order"), in chunks of ``chunk`` ids under
    one hold of the graph-write lock. An id MySQL does not return is retired.

    Returns ``status`` (``ok``, ``not_at_version`` or ``lock_timeout``, the last two having written nothing) and the
    summed counts of every step: ``requested``, ``found``, ``missing_in_mysql``, ``projected``,
    ``projection_errors`` with examples, the writer's sample, lineage, retire and IN_STUDY counts, ``labels_edges``
    and one ``labels_<class>`` count per ``labels.CLASSES``, ``label_differences`` (class to property to edges),
    ``label_examples``, the label write counts, ``undeclared_attributes_created``, ``catalog_synced_for_types`` and
    ``sample_type_counts_set``; ``structural_gaps`` and ``structural_gap_parts`` count the structural links left
    unwritten, and when there are any ``structural_gap_samples`` names each sample they belong to, with why
    (``STRUCTURAL_GAP_KEYS``). ``apply_label_changes`` is the operator's approval (R14).
    """
    wanted = _ids(ids)
    if not wanted:
        return {"status": OK, "requested": 0}
    ctx = _Context(run_dir, apply_label_changes)

    def work():
        total = {"status": OK}
        for batch in _batches(wanted, chunk):
            _add(total, _sync_ids(driver, db, batch, ctx))
        return total

    result = _guarded(driver, db, lock_timeout_s, work)
    result.setdefault("requested", len(wanted))
    return result


def sync_samples_of_type(driver, db, type_id, *, run_dir: str | None = None, apply_label_changes: bool = False,
                         lock_timeout_s: float = LOCK_WAIT_S, chunk: int = writer.SAMPLE_CHUNK) -> dict:
    """``sync_samples`` over every sample of the type ``type_id``, streamed from MySQL in chunks of ``chunk`` ids,
    each chunk its own write unit. The catalog and the label maps are read once for the whole type.

    Stops at the first chunk that is refused or cannot take the lock, and returns that ``status`` with the counts of
    the chunks written before it; ``chunks`` counts those. The drain retries the whole type, which is idempotent.
    """
    type_id = int(type_id)
    refused = _refusal(driver, db)
    if refused is not None:
        return {**refused, "sample_type_id": type_id}
    ctx = _Context(run_dir, apply_label_changes)
    total = {"status": OK, "sample_type_id": type_id, "chunks": 0}
    for ids in sources.ids_of_type(type_id, chunk):
        part = _guarded(driver, db, lock_timeout_s, lambda ids=ids: _sync_ids(driver, db, ids, ctx))
        if part["status"] != OK:
            total.update({k: v for k, v in part.items() if k in ("status", "schema_version", "writer_version",
                                                                  "lock_timeout_s")})
            return total
        _add(total, part)
        total["chunks"] += 1
    return total


# --- retire_samples ------------------------------------------------------------------------------

def retire_samples(driver, db, ids, *, run_dir: str | None = None, lock_timeout_s: float = LOCK_WAIT_S) -> dict:
    """The deletion rule (the sync design, section 9) for the ids of ``ids`` that MySQL no longer holds.

    MySQL is read again first: an id it still holds is left as it is and counted in ``retire_skipped_in_mysql``.
    A synced ``:Sample`` of a gone id is archived to ``retired.tsv`` and deleted, a never-synced one becomes an
    ``:OrphanSample`` (``writer.retire_samples``), and the counts of the types they held are set again.
    """
    wanted = _ids(ids)
    if not wanted:
        return {"status": OK, "requested": 0}
    ctx = _Context(run_dir)

    def work():
        still = {r["id"] for r in sources.samples_by_ids(wanted)}
        gone = [i for i in wanted if i not in still]
        report = {"status": OK, "requested": len(wanted), "retire_skipped_in_mysql": len(still)}
        if still:
            log.info("graph_sync: not retiring %d samples MySQL still holds", len(still))
        if gone:
            old_types = _types_of_samples(driver, db, gone)
            report.update(writer.retire_samples(driver, db, gone, ctx.archive(RETIRED_FILE)))
            report["sample_type_counts_set"] = _set_type_counts(driver, db, old_types)
        return report

    result = _guarded(driver, db, lock_timeout_s, work)
    result.setdefault("requested", len(wanted))
    return result


# --- relabel_for_maps ----------------------------------------------------------------------------

def _label_edges(driver, db, edges: list[dict], ctx: _Context, metas: dict | None = None) -> dict:
    """Label ``edges`` (``{"child_id", "parent_id", "element_id", "stored"}``, ``stored`` holding the seven label
    keys) by the rule, write what R14 allows and count the rest.

    ``metas`` maps a child id to its metadata when the caller has it; the other children's rows are read by id, for
    their ``Protocol``. Without ``ctx.apply_label_changes`` the edges of class ``new`` are written, and those of a
    class in ``labels.REFRESH_CLASSES`` (a rename under the same id, a protocol filled) through
    ``writer.write_edge_label_refreshes``; with it every edge that differs.
    """
    report = {"labels_edges": len(edges), **{f"labels_{c}": 0 for c in labels.CLASSES},
              "label_differences": {}, "label_examples": [], **_NO_LABEL_WRITES}
    if not edges:
        return report
    assay_map, sops, sop_index = ctx.maps()
    endpoints = sorted({v for e in edges for v in (e["child_id"], e["parent_id"]) if _is_id(v)})
    assays = sources.sample_assay_ids_for(endpoints)
    metas = dict(metas or {})
    unread = sorted({e["child_id"] for e in edges if _is_id(e["child_id"]) and e["child_id"] not in metas})
    for row in (sources.samples_by_ids(unread) if unread else ()):
        metas[row["id"]] = row["json_metadata"]

    rows, refresh = [], []
    for edge in edges:
        child, parent, stored = edge["child_id"], edge["parent_id"], edge["stored"]
        protocol = labels.resolve_protocol(labels.protocol_value_of(metas.get(child)), sops, sop_index)
        computed = labels.edge_labels(assays.get(child), assays.get(parent), assay_map, protocol)
        cls = labels.classify(stored, computed)
        report[f"labels_{cls}"] += 1
        if cls == labels.EQUAL:
            continue
        if cls != labels.NEW:
            diff = labels.differences(stored, computed)
            per_property = report["label_differences"].setdefault(cls, {})
            for key in diff:
                per_property[key] = per_property.get(key, 0) + 1
            if len(report["label_examples"]) < EXAMPLES:
                report["label_examples"].append({
                    "child_id": child, "parent_id": parent, "class": cls, "properties": diff,
                    "stored": {k: stored.get(k) for k in diff}, "computed": {k: computed[k] for k in diff}})
        if cls == labels.NEW or ctx.apply_label_changes:
            row = {"child_id": child, "parent_id": parent, "labels": computed}
            if ctx.apply_label_changes:
                row["stored"] = stored
            rows.append(row)
        elif cls in labels.REFRESH_CLASSES:
            refresh.append({"child_id": child, "parent_id": parent, "labels": computed, "stored": stored})
    if rows:
        report.update(writer.write_edge_labels(driver, db, rows, apply_label_changes=ctx.apply_label_changes))
    if refresh:
        report.update(writer.write_edge_label_refreshes(driver, db, refresh))
    return report


def preview_labels(driver, db, ids) -> list[dict]:
    """Read only: how ``sync_samples(..., apply_label_changes=True)`` would class every DERIVED_FROM edge incident to
    ``ids`` now (the studies tool's approval check, T12). Each item holds ``child_id``, ``parent_id``, ``element_id``,
    ``class`` (one of ``labels.CLASSES``), ``properties`` (the label keys that differ), ``stored`` and ``computed``
    (all seven keys). The labels are computed exactly as ``_label_edges`` computes them; nothing is written and no
    lock is taken."""
    wanted = _ids(ids)
    if not wanted:
        return []
    edges = writer.edges_incident(driver, db, wanted)
    if not edges:
        return []
    assay_map, sops, sop_index = _Context(None).maps()
    endpoints = sorted({v for e in edges for v in (e["child_id"], e["parent_id"]) if _is_id(v)})
    assays = sources.sample_assay_ids_for(endpoints)
    children = sorted({e["child_id"] for e in edges if _is_id(e["child_id"])})
    metas = {row["id"]: row["json_metadata"] for row in sources.samples_by_ids(children)} if children else {}
    out = []
    for edge in edges:
        child, parent, stored = edge["child_id"], edge["parent_id"], edge["stored"]
        protocol = labels.resolve_protocol(labels.protocol_value_of(metas.get(child)), sops, sop_index)
        computed = labels.edge_labels(assays.get(child), assays.get(parent), assay_map, protocol)
        cls = labels.classify(stored, computed)
        out.append({"child_id": child, "parent_id": parent, "element_id": edge["element_id"], "class": cls,
                    "properties": [] if cls == labels.EQUAL else labels.differences(stored, computed),
                    "stored": dict(stored), "computed": computed})
    return out


def _edge(record, props) -> dict:
    return {"child_id": record["child_id"], "parent_id": record["parent_id"], "element_id": record["element_id"],
            "stored": {key: (props or {}).get(key) for key in q.EDGE_LABEL_KEYS}}


def _resolution(assay_id: int, assay_map) -> tuple | None:
    """The ``(internal_assay_id, internal_assay_title)`` an edge won by the SEEK assay ``assay_id`` carries under the
    rule (``labels.edge_labels``), or None when the map no longer holds the assay."""
    entry = assay_map.get(assay_id)
    if entry is None:
        return None
    internal_id, title = entry
    return (assay_id, title or "") if internal_id is None else (internal_id, title)


def _changed_assays(driver, db, assay_map) -> list[int]:
    """The SEEK assays whose resolution on the edges they win differs from the map's now. Read-only."""
    changed = set()
    for r in _records(_run(driver, db, GRAPH_ASSAY_LABELS, read=True)):
        assay_id = r["assay_id"]
        if _is_id(assay_id) and _resolution(assay_id, assay_map) != (r["internal_assay_id"],
                                                                     r["internal_assay_title"]):
            changed.add(assay_id)
    return sorted(changed)


def _changed_sops(driver, db, sops, sop_index) -> list[int]:
    """The SOPs the edges carry whose title changed, or whose title another SOP now shares (a title-format
    ``Protocol`` then resolves to nothing). Read-only."""
    ambiguous = {sop_id for ids in sop_index.values() if len(ids) > 1 for sop_id in ids}
    changed = set()
    for r in _records(_run(driver, db, GRAPH_PROTOCOL_LABELS, read=True)):
        sop_id = r["protocol_id"]
        if _is_id(sop_id) and (sop_id in ambiguous or sops.get(sop_id) != r["protocol_title"]):
            changed.add(sop_id)
    return sorted(changed)


def _assay_members(assay_ids) -> dict[int, frozenset[int]]:
    """Sample id to the assays among ``assay_ids`` it belongs to (``assay_assets`` Sample rows), read by bound
    parameters in chunks of ``sources.IN_CHUNK``."""
    members: dict[int, set[int]] = {}
    for chunk in sources._id_chunks(assay_ids):
        sql = ("SELECT asset_id, assay_id FROM assay_assets WHERE asset_type = %s AND asset_id IS NOT NULL "
               f"AND assay_id IN ({sources._placeholders(len(chunk))})")
        for sample_id, assay_id in sources._rows(sources._seek(), sql, ["Sample", *chunk]):
            members.setdefault(int(sample_id), set()).add(int(assay_id))
    return {sid: frozenset(found) for sid, found in members.items()}


def _share(members: dict, edge: dict) -> bool:
    """Whether both ends of ``edge`` belong to one assay of ``members``."""
    return bool(members.get(edge["child_id"], frozenset()) & members.get(edge["parent_id"], frozenset()))


def _relabel(driver, db, ctx: _Context, chunk: int) -> dict:
    assay_map, sops, sop_index = ctx.maps()
    new_hash = labels.label_maps_hash(assay_map, sops)
    meta = writer.graphmeta(driver, db)
    report = {"status": OK, "label_maps_hash": new_hash, "previous_label_maps_hash": meta["label_maps_hash"],
              "maps_changed": meta["label_maps_hash"] != new_hash}
    if not report["maps_changed"]:
        return report
    changed_assays = _changed_assays(driver, db, assay_map)
    changed_sops = _changed_sops(driver, db, sops, sop_index)
    members = _assay_members(changed_assays) if changed_assays else {}
    report.update(changed_assays=changed_assays[:LIST_CAP], changed_sops=changed_sops[:LIST_CAP],
                  members=len(members))

    counts = _label_edges(driver, db, [], ctx)
    # Edges between members of a changed assay, each read once from its child.
    for batch in _batches(sorted(members), chunk):
        records = _records(_run(driver, db, q.DERIVED_FROM_OF_CHILDREN, {"ids": batch}, read=True))
        edges = [e for e in (_edge(r, r["props"]) for r in records) if _share(members, e)]
        _add(counts, _label_edges(driver, db, edges, ctx))
    # Edges whose child's protocol resolution may have changed, unless the loop above labelled them.
    if changed_sops:
        edges = []
        for batch in _batches(changed_sops, writer.REL_CHUNK):
            records = _records(_run(driver, db, EDGES_WITH_PROTOCOLS, {"ids": batch}, read=True))
            edges.extend(e for e in (_edge(r, r["props"]) for r in records) if not _share(members, e))
        for batch in _batches(edges, chunk):
            _add(counts, _label_edges(driver, db, batch, ctx))
    report.update(counts)
    writer.write_graphmeta(driver, db, meta["catalog_hash"], label_maps_hash=new_hash)
    return report


def relabel_for_maps(driver, db, *, apply_label_changes: bool = False, lock_timeout_s: float = LOCK_WAIT_S,
                     chunk: int = writer.SAMPLE_CHUNK) -> dict:
    """Relabel the edges a change to the resolved assay map or to ``sops`` affects, then stamp
    ``GraphMeta.label_maps_hash``.

    Nothing is read or written when ``labels.label_maps_hash`` of the maps now equals the stamped hash
    (``maps_changed`` False). Otherwise the graph's own labels say what moved, because the old maps are not kept:
    a SEEK assay whose resolution on the edges it wins differs from the map's now is a changed assay, and the edges
    between its members (``assay_assets``) are labelled again; a SOP the edges carry whose title changed, or that
    shares its title with another SOP now, is a changed SOP, and the edges carrying it are labelled again. Each edge
    goes through the rule and R14 as in ``sync_samples``: a renamed title makes a ``renamed`` label, written at once;
    a mapping moved to another assay makes a ``changed`` one, reported and written only with ``apply_label_changes``.
    An assay that wins no edge before the change and some edge after it is not seen here; the full sync's label step
    reports it.
    """
    ctx = _Context(None, apply_label_changes)
    return _guarded(driver, db, lock_timeout_s, lambda: _relabel(driver, db, ctx, chunk))


# --- sync_small_tables ---------------------------------------------------------------------------

def _small_tables(driver, db, ctx: _Context) -> dict:
    report = {"status": OK}
    studies = sources.studies()
    report.update(writer.write_projects(driver, db, sources.projects()))
    report.update(writer.write_investigation_projects(driver, db, sources.investigations(),
                                                      sources.investigation_projects(),
                                                      archive_path=ctx.archive(writer.INVESTIGATIONS_DELETED_FILE),
                                                      seek_study_ids=[s["id"] for s in studies]))
    report.update(writer.write_people_and_memberships(driver, db, sources.memberships()))
    # Every SEEK study gets its node, with no sample yet included; the Investigation nodes were written just above.
    report.update(writer.write_seek_study_nodes(driver, db, studies))
    return report


def sync_small_tables(driver, db, *, lock_timeout_s: float = LOCK_WAIT_S, run_dir: str | None = None) -> dict:
    """Rewrite the small tables from MySQL: Project nodes (a project gone from MySQL is deleted), Investigation nodes
    and their IN_PROJECT, Person nodes and MEMBER_OF, and the node of every SEEK study (made when missing) with SEEK's
    title, description and investigation. Tens to hundreds of rows each, so every call rewrites them whole."""
    ctx = _Context(run_dir)
    return _guarded(driver, db, lock_timeout_s, lambda: _small_tables(driver, db, ctx))
