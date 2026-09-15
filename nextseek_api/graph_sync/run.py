"""The ordered graph_sync runs (the design, section 6; docs/neo4j-schema.md, section "v1.1").

``full_sync`` rebuilds graph schema v1.1 from MySQL in the design's order:

    preflight > delete ghosts > relabel orphans > archive and delete CHILD_OF > constraints > SampleType >
    Attribute (declared) > Project, Person, MEMBER_OF > Investigation IN_PROJECT > samples (per chunk, with the
    census) > missing lineage > SEEK studies and IN_STUDY > Attribute (declared plus undeclared) > attribute and
    sample type counts > the index budget > the fulltext index > await indexes > GraphMeta

The preflight writes nothing. It builds the catalog (which enforces the label rule), scans every MySQL sample once
(projecting it, collecting ids and the declared lineage), reads the ghost list and checks SampleType titles against
the graph. A problem it finds raises ``PreflightError`` before the first write; a dry run reports the same numbers,
plus previews of the CHILD_OF archive and the index budget, and stops there.

Undeclared Attribute nodes are known only once every sample has been projected, so the Attribute catalog is written
twice: the declared attributes before the sample pass (the design's order) and the full catalog after it. The
counts follow the second write, because ``writer.write_attributes`` replaces each node's properties.

``catalog_sync`` rewrites the catalog nodes only: SampleType, Attribute and HAS_ATTRIBUTE, their counts and GraphMeta.
It keeps the undeclared Attribute nodes and the attribute sample counts a full sync wrote.

What is held across a run is one entry per sample in four indexes (sample ids, uuids, project links and declared
lineage pairs); the sample data itself is bounded by the chunk.
"""
from __future__ import annotations

import heapq
import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from nextseek_api.graph_sync import catalog, sources, writer
from nextseek_api.graph_sync import cypher as q
from nextseek_api.graph_sync.projection import SYSTEM_KEYS, project_sample
from nextseek_api.graph_sync.writer import _records, _run

log = logging.getLogger(__name__)

REPORT_FILE = "full_sync.json"
CENSUS_FILE = "census.json"
ARCHIVE_FILE = "child_of_archive.tsv"
LIST_CAP = 1_000        # longest id list copied into a report
EXAMPLES = 20           # examples kept per problem
PROGRESS_EVERY = 20     # sample pages between progress lines

# The Attribute nodes as a previous run left them; catalog_sync keeps their undeclared keys and counts.
ATTRIBUTE_STATE = """
MATCH (a:Attribute)
RETURN a.key AS key, a.declared AS declared, a.sample_type_id AS sample_type_id, a.title AS title,
       a.sample_count AS sample_count
"""


class PreflightError(RuntimeError):
    """A run refused before its first write. ``problems`` says why; ``report`` holds what the preflight found."""

    def __init__(self, problems: list[str], report: dict):
        super().__init__("graph_sync refused before writing: " + "; ".join(problems))
        self.problems = list(problems)
        self.report = report


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


def _project(row: dict, cat: Catalog, projects: dict, scan: SampleScan):
    type_id = row.get("sample_type_id")
    title = cat.type_titles.get(type_id)
    if title is None:
        _error(scan, row.get("id"), f"sample type {type_id} is not in sample_types")
        return None
    try:
        proj = project_sample(row, title, cat.value_types.get(type_id, {}), projects.get(row["id"], ()))
    except (ValueError, TypeError) as exc:
        _error(scan, row.get("id"), str(exc))
        return None
    scan.projected += 1
    scan.cast_failures += len(proj.cast_failures)
    _observe(scan.census, cat, proj)
    return proj


def scan_samples(cat: Catalog, projects: dict, chunk: int, *, uuid_index=None, collect_ids=False,
                 on_page=None) -> SampleScan:
    """Read every sample in keyset pages of ``chunk`` and project it, accumulating the census.

    ``uuid_index`` (``sources.uuid_to_ids()``) also collects the declared lineage; ``collect_ids`` collects every
    sample id; ``on_page(projections)`` receives each page's projections (the write pass). A sample that cannot be
    projected is counted in ``errors`` and skipped.
    """
    scan = SampleScan(census=_census(cat))
    pages = 0
    for page in sources.iter_samples(chunk=chunk):
        if uuid_index is not None:
            for child, parent in sources.declared_lineage(page, uuid_index):
                scan.lineage.add(encode_pair(child, parent))
        projections = []
        for row in page:
            scan.samples += 1
            if collect_ids:
                scan.ids.add(row["id"])
            proj = _project(row, cat, projects, scan)
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


def _resolve_run_dir(run_dir: str | None) -> str:
    """The run directory: the one given, else a new ``graph_sync-<UTC time>`` under ``$GS_RUN_DIR``."""
    if run_dir:
        return os.path.abspath(run_dir)
    base = os.environ.get("GS_RUN_DIR")
    if base:
        return os.path.join(base, "graph_sync-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    raise PreflightError(["a full sync needs a run directory (--run-dir or GS_RUN_DIR) for its CHILD_OF archive "
                          "and its report"], {"mode": "full"})


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


# --- the full sync -------------------------------------------------------------------------------

@dataclass
class _Preflight:
    cat: Catalog
    uuid_index: dict | None
    projects: dict | None
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
    projects = _timed(report, "read_project_links", sources.sample_projects)
    scan = _timed(report, "preflight_scan", scan_samples, cat, projects, chunk,
                  uuid_index=uuid_index, collect_ids=True)
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
    return _Preflight(cat, uuid_index, projects, scan, ghosts, problems)


def _preview(driver, db, state: _Preflight, report: dict, bench_keys) -> None:
    """Dry run: what the write steps would do, from reads only."""
    census = state.scan.census
    _summarize_census(report, census, state.cat)
    report["cast_failures"] = state.scan.cast_failures
    declared = DeclaredUuidPairs(state.scan.lineage, state.uuid_index)

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


def _write(driver, db, chunk: int, run_dir: str, bench_keys, state: _Preflight, report: dict) -> None:
    cat, ghosts = state.cat, state.ghosts
    _step(report, "delete_ghosts", writer.delete_ghosts, driver, db, ghosts["ghost_element_ids"])
    _step(report, "relabel_orphans", writer.relabel_orphans, driver, db, ghosts["orphan_ids"],
          element_ids=ghosts["idless_element_ids"])
    _step(report, "archive_child_of", writer.archive_and_drop_child_of, driver, db,
          os.path.join(run_dir, ARCHIVE_FILE), DeclaredUuidPairs(state.scan.lineage, state.uuid_index))
    _step(report, "constraints", writer.ensure_constraints_v11, driver, db)
    _step(report, "sample_types", writer.write_sample_types, driver, db, cat.sample_types)
    _step(report, "attributes_declared", writer.write_attributes, driver, db, cat.attributes)
    _step(report, "projects", writer.write_projects, driver, db, sources.projects())
    _step(report, "people", writer.write_people_and_memberships, driver, db, sources.memberships())
    _step(report, "investigations", writer.write_investigation_projects, driver, db, sources.investigations(),
          sources.investigation_projects())

    totals: Counter = Counter()

    def write_page(projections):
        counts = writer.write_samples(driver, db, projections, chunk=chunk)
        totals.update({k: v for k, v in counts.items() if isinstance(v, int)})

    written = _timed(report, "samples", scan_samples, cat, state.projects, chunk, on_page=write_page)
    report["steps"]["samples"] = dict(totals)
    report.update(totals)
    report.update(samples_read_in_write_pass=written.samples, projection_errors_in_write_pass=written.errors,
                  projection_error_examples_in_write_pass=written.error_examples)

    lineage = sorted(state.scan.lineage)
    state.uuid_index = state.projects = None  # free the per-sample indexes before the study links load
    state.scan.lineage = set()
    _step(report, "lineage", writer.write_missing_lineage, driver, db, (decode_pair(code) for code in lineage))
    del lineage
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
    _step(report, "graphmeta", writer.write_graphmeta, driver, db, catalog.catalog_hash(cat.sample_types, attributes))
    _write_json(os.path.join(run_dir, CENSUS_FILE), dict(sorted(census.items())))
    report["census_path"] = os.path.join(run_dir, CENSUS_FILE)


def full_sync(driver, db, chunk: int = writer.SAMPLE_CHUNK, dry_run: bool = False, run_dir: str | None = None,
              bench_keys=frozenset()) -> dict:
    """Rebuild graph schema v1.1 from MySQL, in the design's order (module docstring). Returns the report.

    ``run_dir`` receives ``full_sync.json`` (written even when the run fails or is refused), ``census.json`` and
    ``child_of_archive.tsv``; without one, a new directory under ``$GS_RUN_DIR`` is used, and a run with neither is
    refused. ``bench_keys`` holds attribute keys or (sample type title, attribute title) pairs the index budget
    must cover. ``dry_run`` reads MySQL and the graph, writes nothing and touches no file.

    Raises PreflightError, before any write, when the preflight finds a problem (``problems`` in the report).
    """
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")
    report = {"mode": "full", "dry_run": dry_run, "chunk": chunk, "schema_version": writer.SCHEMA_VERSION,
              "started_at": _now(), "timings_s": {}, "steps": {}}
    if not dry_run:
        run_dir = _resolve_run_dir(run_dir)
        os.makedirs(run_dir, exist_ok=True)
        report["run_dir"] = run_dir
    try:
        state = _preflight(driver, db, chunk, report)
        if dry_run:
            _preview(driver, db, state, report, bench_keys)
            report["status"] = "dry_run"
            return report
        if state.problems:
            raise PreflightError(state.problems, report)
        _write(driver, db, chunk, run_dir, bench_keys, state, report)
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
        if not dry_run:
            _write_json(os.path.join(run_dir, REPORT_FILE), report)


# --- the catalog sync ----------------------------------------------------------------------------

def catalog_sync(driver, db, dry_run: bool = False) -> dict:
    """Rewrite the catalog nodes only: SampleType, Attribute, HAS_ATTRIBUTE, their counts and GraphMeta.

    Undeclared Attribute nodes a full sync wrote are kept while their type exists and does not now declare the
    key, and every Attribute keeps its ``sample_count`` (a new one gets 0). ``dry_run`` reads and writes nothing.
    """
    report = {"mode": "catalog", "dry_run": dry_run, "schema_version": writer.SCHEMA_VERSION,
              "started_at": _now(), "timings_s": {}, "steps": {}}
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
                  sample_type_title_conflicts=_title_conflicts(driver, db, cat))
    if dry_run:
        report["status"] = "dry_run"
        report["finished_at"] = _now()
        return report
    if report["sample_type_title_conflicts"]:
        raise PreflightError([f"{len(report['sample_type_title_conflicts'])} SampleType titles are held under "
                              "other ids in the graph (sample_type_title_conflicts)"], report)
    _step(report, "sample_types", writer.write_sample_types, driver, db, cat.sample_types)
    _step(report, "attributes", writer.write_attributes, driver, db, attributes)
    _step(report, "attribute_counts", writer.write_attribute_counts, driver, db,
          {a["key"]: counts.get(a["key"], 0) for a in attributes})
    _step(report, "sample_type_counts", writer.write_sample_type_counts, driver, db)
    _step(report, "graphmeta", writer.write_graphmeta, driver, db, catalog_hash)
    report["status"] = "ok"
    report["finished_at"] = _now()
    return report
