"""The drift check (the sync design, sections 10.3 and 13; CI-4): does the graph still equal MySQL? Read-only.

``detect_sample_drift`` is the nightly targeted sync's detection without its writes. Two streams ordered by sample id
are merged with bounded memory: MySQL's samples in keyset pages, each row carrying its project and assay ids
(``sources.iter_digest_rows``), and the graph's ``(id, source_hash)`` pairs (``writer.sample_hashes``). A row's digest
is ``projection.source_hash`` over what its node is projected from, with the catalog ``run.build_catalog`` builds,
exactly as ``project_sample`` computes it when the node is written. A sample is:

- ``changed`` when its node's hash differs, when the node has none (written before schema 1.2, or by anything but
  graph_sync), or when its sample type is not in the catalog, so that no digest can be computed;
- ``missing_in_graph`` when no Sample node has its id;
- ``not_in_mysql`` when a Sample node has an id MySQL does not hold.

The uuids of missing and changed samples that no Sample node carries are the ``new_uuids``: parents an older row may
name that the graph could not link yet (spec 10.3, step 4). Counts are exact; the id lists are capped.

``drift_check`` is what ``manage.py graph_sync --drift`` runs, in gate G's shape (``checks``, ``pass``, ``stats``)
plus a ``status``: ``ok``, ``drift`` (a check failed) or ``refused`` (the graph is not at the writer's schema version,
with a ``reason``; nothing else is read). Its checks, under these names:

- ``samples.missing_in_graph``, ``samples.not_in_mysql``, ``samples.source_hash_mismatch``: the detection, each
  expecting 0; ``samples.new_uuids`` is reported, never failed;
- ``freshness.full``, ``freshness.reconcile``, ``freshness.outbox``: ``state.freshness``, each expecting ``ok``, so a
  stale or never-run sync fails; ``freshness.readable`` fails instead when the run records cannot be read;
- gate G's checks under their own names (``verify.gate_g``), without the named accounts of the merged dataset.

Nothing is written to the graph. With a ``trigger`` the run is recorded in ``graph_sync_run`` (best-effort, as every
run record); without one nothing is written at all.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

from django.db import DatabaseError

from nextseek_api.graph_sync import run, sources, state, verify, writer
from nextseek_api.graph_sync.projection import source_hash
from nextseek_api.graph_sync.verify import EXAMPLES, _check
from nextseek_api.graph_sync.writer import _records, _run

log = logging.getLogger(__name__)

ID_CAP = 1_000          # ids (and uuids) kept per list; the counts are exact
LOOKUP_BATCH = 1_000    # ids or uuids per graph read of the new-uuid pass
FRESHNESS_JOBS = ("full", "reconcile", "outbox")
DETECTION_CHECKS = ("samples.missing_in_graph", "samples.not_in_mysql", "samples.source_hash_mismatch")

CHANGED, MISSING_IN_GRAPH, NOT_IN_MYSQL = "changed", "missing_in_graph", "not_in_mysql"
OK, DRIFT, REFUSED = "ok", "drift", "refused"

# The uuid on each node of these ids: a changed sample whose node carries another uuid brings a new one.
NODE_UUIDS = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})
RETURN s.id AS id, s.uuid AS uuid
"""
# Which of these uuids some Sample node carries (the sample_uuid index).
UUIDS_ON_NODES = """
UNWIND $uuids AS uuid
MATCH (s:Sample {uuid: uuid})
RETURN DISTINCT s.uuid AS uuid
"""


# --- the merge -----------------------------------------------------------------------------------

class Difference(NamedTuple):
    """One sample on which the two streams disagree. ``item`` is the MySQL item (None for ``not_in_mysql``);
    ``graph_hash`` is the node's ``source_hash`` (None for ``missing_in_graph``, and for a node without one)."""

    kind: str
    id: int
    item: tuple | None
    graph_hash: str | None


def _ascending(items: Iterable[tuple], name: str) -> Iterator[tuple]:
    """``items`` as they come, raising RuntimeError on an id not above the one before: a merge over a stream out of
    order, or over a repeated id, would report samples that did not drift."""
    last = None
    for item in items:
        if last is not None and not item[0] > last:
            raise RuntimeError(f"{name} came back out of id order ({item[0]!r} after {last!r})")
        last = item[0]
        yield item


def merge_digests(mysql_rows: Iterable[tuple], graph_rows: Iterable[tuple]) -> Iterator[Difference]:
    """Merge MySQL's ``(id, digest, ...)`` items with the graph's ``(id, source_hash)`` pairs, both ascending by id,
    and yield every ``Difference`` in id order; an equal sample yields nothing.

    A digest or a stored hash that is None never equals anything, so either side's missing hash is ``changed``.
    Holds one item of each stream.
    """
    graph = _ascending(graph_rows, "the graph's (id, source_hash) stream")
    pending = next(graph, None)
    for item in _ascending(mysql_rows, "MySQL's sample stream"):
        sample_id, digest = item[0], item[1]
        while pending is not None and pending[0] < sample_id:
            yield Difference(NOT_IN_MYSQL, pending[0], None, pending[1])
            pending = next(graph, None)
        if pending is not None and pending[0] == sample_id:
            if digest is None or pending[1] is None or pending[1] != digest:
                yield Difference(CHANGED, sample_id, item, pending[1])
            pending = next(graph, None)
        else:
            yield Difference(MISSING_IN_GRAPH, sample_id, item, None)
    while pending is not None:
        yield Difference(NOT_IN_MYSQL, pending[0], None, pending[1])
        pending = next(graph, None)


# --- detection -----------------------------------------------------------------------------------

class _Ids:
    """A count, and the first ``cap`` values added (every value when ``cap`` is None)."""

    def __init__(self, cap: int | None):
        self.cap, self.count, self.values = cap, 0, []

    def add(self, value) -> None:
        self.count += 1
        if self.cap is None or len(self.values) < self.cap:
            self.values.append(value)


class _NewUuids:
    """The uuids of missing and changed samples that no Sample node carries. Looked up in batches of
    ``LOOKUP_BATCH`` as the merge finds them, so memory holds one batch and the answer."""

    def __init__(self, driver, db):
        self.driver, self.db = driver, db
        self.changed: list[tuple[int, str]] = []   # (id, MySQL uuid) of changed samples not yet looked up
        self.candidates: set[str] = set()         # uuids not yet looked up
        self.found: set[str] = set()

    def _read(self, query, params) -> list:
        return _records(_run(self.driver, self.db, query, params, read=True))

    def missing(self, uuid) -> None:
        """A sample with no node: its uuid is new unless another node carries it."""
        if uuid and uuid not in self.found:
            self.candidates.add(uuid)
            if len(self.candidates) >= LOOKUP_BATCH:
                self._look_up_candidates()

    def changed_sample(self, sample_id: int, uuid) -> None:
        """A changed sample: its uuid is new when its node carries another one and no node carries it."""
        if uuid:
            self.changed.append((sample_id, uuid))
            if len(self.changed) >= LOOKUP_BATCH:
                self._look_up_changed()

    def _look_up_changed(self) -> None:
        batch, self.changed = self.changed, []
        on_node = {r["id"]: r["uuid"] for r in self._read(NODE_UUIDS, {"ids": [i for i, _ in batch]})}
        for sample_id, uuid in batch:
            if on_node.get(sample_id) != uuid:
                self.missing(uuid)

    def _look_up_candidates(self) -> None:
        batch, self.candidates = sorted(self.candidates - self.found), set()
        if not batch:
            return
        carried = {r["uuid"] for r in self._read(UUIDS_ON_NODES, {"uuids": batch})}
        self.found.update(u for u in batch if u not in carried)

    def finish(self) -> set[str]:
        if self.changed:
            self._look_up_changed()
        if self.candidates:
            self._look_up_candidates()
        return self.found


@dataclass
class _Seen:
    mysql: int = 0
    graph: int = 0
    untyped: int = 0
    max_id: int | None = None
    max_updated_at: object = None


def _iso(value):
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else (None if value is None else str(value))


def detect_sample_drift(driver, db, *, chunk: int = writer.SAMPLE_CHUNK, cap: int | None = ID_CAP,
                        cat: run.Catalog | None = None) -> dict:
    """Merge MySQL's samples with the graph's source hashes (module docstring) and report what differs. Reads only.

    ``chunk`` is the MySQL page size; ``cap`` bounds each id list (None keeps every id, for a caller that syncs
    them); ``cat`` is a catalog the caller built already, else ``run.build_catalog()`` builds one, raising ValueError
    when it does not build. Returns the counts ``changed`` (``changed_without_hash`` of them on a node with no hash),
    ``missing_in_graph``, ``not_in_mysql`` and ``new_uuids``, their lists (``changed_ids``, ``missing_in_graph_ids``,
    ``not_in_mysql_ids``, ``new_uuid_list``, ascending, each capped), ``untyped`` (rows whose sample type is not in
    the catalog), ``mysql_samples``, ``graph_samples``, and the highest ``samples.id`` and ``updated_at`` seen
    (``max_id``, ``max_updated_at``) for a run record's watermark.
    """
    if cap is not None and cap < 0:
        raise ValueError(f"cap must be None or not negative, got {cap}")
    cat = cat if cat is not None else run.build_catalog()
    seen = _Seen()

    def mysql_rows():
        for page in sources.iter_digest_rows(chunk=chunk):
            for row in page:
                seen.mysql += 1
                sample_id, updated_at = row["id"], row.get("updated_at")
                seen.max_id = sample_id if seen.max_id is None else max(seen.max_id, sample_id)
                if updated_at is not None and (seen.max_updated_at is None or updated_at > seen.max_updated_at):
                    seen.max_updated_at = updated_at
                type_id = row["sample_type_id"]
                title = cat.type_titles.get(type_id)
                digest = None
                if title is None:
                    seen.untyped += 1
                else:
                    digest = source_hash(row, title, cat.value_types.get(type_id, {}), row["project_ids"],
                                         row["assay_ids"])
                yield sample_id, digest, row["uuid"]

    def graph_rows():
        for pair in writer.sample_hashes(driver, db):
            seen.graph += 1
            yield pair

    changed, missing, extra = _Ids(cap), _Ids(cap), _Ids(cap)
    without_hash = 0
    new = _NewUuids(driver, db)
    for diff in merge_digests(mysql_rows(), graph_rows()):
        if diff.kind == CHANGED:
            changed.add(diff.id)
            without_hash += diff.graph_hash is None
            new.changed_sample(diff.id, diff.item[2])
        elif diff.kind == MISSING_IN_GRAPH:
            missing.add(diff.id)
            new.missing(diff.item[2])
        else:
            extra.add(diff.id)
    uuids = sorted(new.finish())
    return {
        "mysql_samples": seen.mysql, "graph_samples": seen.graph,
        "changed": changed.count, "changed_ids": changed.values, "changed_without_hash": without_hash,
        "missing_in_graph": missing.count, "missing_in_graph_ids": missing.values,
        "not_in_mysql": extra.count, "not_in_mysql_ids": extra.values,
        "new_uuids": len(uuids), "new_uuid_list": uuids if cap is None else uuids[:cap],
        "untyped": seen.untyped, "max_id": seen.max_id, "max_updated_at": _iso(seen.max_updated_at),
        "id_cap": cap,
    }


# --- the drift check -----------------------------------------------------------------------------

def _timed(timings: dict, name: str, fn, *args, **kwargs):
    log.info("drift check: %s", name)
    started = time.monotonic()
    out = fn(*args, **kwargs)
    timings[name] = round(time.monotonic() - started, 1)
    return out


def _check_detection(driver, db, chunk: int, checks: list, stats: dict):
    """Returns the built catalog so the caller can reuse it, or None when it does not build."""
    try:
        cat = run.build_catalog()
    except ValueError as exc:   # a label collision: no digest can be computed (gate G's 8.catalog.builds says why)
        detail = f"not compared: the catalog does not build: {exc}"
        for name in DETECTION_CHECKS:
            _check(checks, name, 0, "not compared", passed=False, detail=detail)
        stats["detection"] = {"error": detail}
        return None
    found = detect_sample_drift(driver, db, chunk=chunk, cat=cat)
    stats["detection"] = found
    for name, key in zip(DETECTION_CHECKS, ("missing_in_graph", "not_in_mysql", "changed")):
        _check(checks, name, 0, found[key], detail=found[f"{key}_ids"][:EXAMPLES])
    _check(checks, "samples.new_uuids", "any", found["new_uuids"], passed=True,
           detail=found["new_uuid_list"][:EXAMPLES])
    return cat


# The names the assistant is told to scope by, and whether the graph answers them. Measured 2026-09-17 on a graph
# graph_sync had just written at 1.2: of the eight names capabilities.md lists, GBM matched no Investigation at all
# and Griffith, Impact, SRP and Shoulders each matched one holding zero studies and zero samples. A full sync does
# not repair that, so nothing caught it and the agent answered a confident zero. Raised by the graph-evidence POC.
ASSISTANT_CAPABILITIES = ("NessieAI", "chat_nextseek", "src", "chat_nextseek", "context", "capabilities.md")
_CAPABILITIES_SECTION = re.compile(r"^##\s+Known Projects and Investigations\s*$", re.M)
_CAPABILITIES_NAME = re.compile(r"^-\s+\*\*([^*]+)\*\*(.*)$", re.M)
# The phrase the generated block (scripts/context_gen.py) appends to the bullet of a name that is not on every
# instance. The generator writes the same string; NessieAI/tests/api/test_context_gen.py ties the two.
NOT_EVERYWHERE_MARK = "(not on every instance:"

ASSISTANT_INVESTIGATIONS = """
UNWIND $titles AS title
OPTIONAL MATCH (i:Investigation {title: title})
OPTIONAL MATCH (i)<-[:IN_INVESTIGATION]-(:Study)<-[:IN_STUDY]-(s:Sample)
RETURN title AS title, count(DISTINCT i) AS nodes, count(DISTINCT s) AS samples
"""


def assistant_investigation_entries(text: str) -> list[tuple[str, bool]]:
    """The investigation names under "Known Projects and Investigations", in file order, each with whether it is
    on every instance: False when its bullet carries NOT_EVERYWHERE_MARK.

    Only that section is read: the file carries bulleted bold terms elsewhere that are not investigations. The
    section ends at the next horizontal rule or heading.
    """
    start = _CAPABILITIES_SECTION.search(text)
    if start is None:
        return []
    rest = text[start.end():]
    end = re.search(r"^(?:---\s*|##\s+)", rest, re.M)
    body = rest[:end.start()] if end else rest
    return [(m.group(1).strip(), NOT_EVERYWHERE_MARK not in m.group(2)) for m in _CAPABILITIES_NAME.finditer(body)]


def assistant_investigation_names(text: str) -> list[str]:
    """The investigation names under "Known Projects and Investigations", in file order."""
    return [name for name, _ in assistant_investigation_entries(text)]


def _capabilities_text(repo_root=None) -> str | None:
    path = Path(repo_root or Path(__file__).resolve().parents[2]).joinpath(*ASSISTANT_CAPABILITIES)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _check_assistant_investigations(driver, db, entries, checks: list, stats: dict) -> None:
    """Every investigation name the assistant is told to use must resolve to one that answers (CI-4, the POC).

    ``entries`` are ``assistant_investigation_entries``; a plain name is read as on every instance. An empty node is
    worse than a missing one: the agent scopes to it and gets a confident zero rather than an error. So a name on
    every instance fails when no Investigation carries it AND when the one that does holds no samples. A name the
    block marks as not on every instance fails only as an empty node (nodes but no samples): absent here is what
    its mark says, so it passes and is listed under ``absent_here``.
    """
    entries = [(entry, True) if isinstance(entry, str) else tuple(entry) for entry in entries]
    names = [name for name, _ in entries]
    if not names:
        stats["assistant_investigations"] = {"names": 0, "unresolved": [], "absent_here": [],
                                             "note": "no names found to check"}
        _check(checks, "catalog.assistant_investigations", 0, 0,
               detail="capabilities.md has no Known Projects and Investigations section")
        return
    rows = _records(_run(driver, db, ASSISTANT_INVESTIGATIONS, {"titles": names}, read=True))
    samples = {r["title"]: int(r["samples"] or 0) for r in rows}
    nodes = {r["title"]: int(r.get("nodes") or 0) for r in rows}
    unresolved, absent_here = [], []
    for name, everywhere in entries:
        if samples.get(name, 0) > 0:
            continue
        if everywhere or nodes.get(name, 0) > 0:
            unresolved.append(name)
        else:
            absent_here.append(name)
    stats["assistant_investigations"] = {"names": len(names), "unresolved": unresolved, "absent_here": absent_here,
                                         "samples": {k: samples.get(k, 0) for k in names},
                                         "nodes": {k: nodes.get(k, 0) for k in names}}
    _check(checks, "catalog.assistant_investigations", 0, len(unresolved),
           detail={"unresolved": unresolved[:EXAMPLES],
                   "hint": "capabilities.md names these but the graph answers nothing for them"})


def _check_catalog(driver, db, cat, checks: list, stats: dict) -> None:
    """The graph's catalog against the one MySQL declares (spec CI-4, `drift.catalog.*`).

    Gate G's `3.catalog.*` checks the graph's catalog against the graph's own sample nodes, which is
    internal consistency and cannot see that MySQL has moved. These two are the other comparison.

    Only the DECLARED side is compared. A key a sample carries that its type does not declare becomes
    an Attribute with `declared` false, which is a normal state and not drift, so a graph attribute
    with no declared counterpart is ignored rather than reported.

    `types_without_context` is a stat and never a check: `catalog.py` states that a type with no
    `sample_types_context` row is a normal state, so any threshold here would be invented. The number
    is reported so that a type which silently lost its curated card is visible.
    """
    rows = _records(_run(driver, db, verify.GRAPH_CATALOG, read=True))
    graph_titles = {r["title"] for r in rows if r.get("title") is not None}
    mysql_titles = {t["title"] for t in cat.sample_types if t.get("title") is not None}
    only_mysql, only_graph = sorted(mysql_titles - graph_titles), sorted(graph_titles - mysql_titles)

    declared: dict[int, set] = {}
    for attr in cat.attributes:
        declared.setdefault(int(attr["sample_type_id"]), set()).add(attr["title"])
    in_graph = {int(r["id"]): {t for t in (r.get("titles") or []) if t is not None}
                for r in rows if r.get("id") is not None}
    differing = {}
    for type_id, titles in sorted(declared.items()):
        missing = sorted(titles - in_graph.get(type_id, set()))
        if missing:
            differing[str(type_id)] = missing[:EXAMPLES]

    without_context = sum(1 for t in cat.sample_types if t.get("has_context") is False)
    stats["catalog"] = {"mysql_sample_types": len(mysql_titles), "graph_sample_types": len(graph_titles),
                        "only_in_mysql": only_mysql[:EXAMPLES], "only_in_graph": only_graph[:EXAMPLES],
                        "types_with_attribute_set_diff": len(differing),
                        "types_without_context": without_context}
    _check(checks, "catalog.sample_types", 0, len(only_mysql) + len(only_graph),
           detail={"only_in_mysql": only_mysql[:EXAMPLES], "only_in_graph": only_graph[:EXAMPLES]})
    _check(checks, "catalog.types_with_attribute_set_diff", 0, len(differing),
           detail=dict(list(differing.items())[:EXAMPLES]))


def _check_freshness(now, checks: list, stats: dict) -> None:
    try:
        fresh = state.freshness(now=now)
    except DatabaseError as exc:   # no migration 0021 (production), or the dmac database is down
        _check(checks, "freshness.readable", True, False, passed=False, detail=f"{type(exc).__name__}: {exc}")
        stats["freshness"] = None
        return
    stats["freshness"] = fresh
    for job in FRESHNESS_JOBS:
        _check(checks, f"freshness.{job}", "ok", fresh[job]["status"], detail=fresh[job])


def _drift(driver, db, sample_size: int, seed, chunk: int, now) -> dict:
    timings: dict = {}
    stats: dict = {"timings_s": timings}
    meta = writer.graphmeta(driver, db)
    stats["graphmeta"] = meta
    version = meta.get("schema_version")
    if version != writer.SCHEMA_VERSION:
        reason = (f"the graph's GraphMeta reads schema version {version!r} ({meta.get('nodes')} GraphMeta nodes), "
                  f"not the writer's {writer.SCHEMA_VERSION!r}; only a graph that graph_sync --full wrote at "
                  f"{writer.SCHEMA_VERSION} is compared")
        log.info("drift check refused: %s", reason)
        return {"status": REFUSED, "reason": reason, "checks": [], "pass": False, "stats": stats}
    checks: list = []
    cat = _timed(timings, "detection", _check_detection, driver, db, chunk, checks, stats)
    if cat is not None:
        _timed(timings, "catalog", _check_catalog, driver, db, cat, checks, stats)
    text = _capabilities_text()
    if text is not None:
        _timed(timings, "assistant_investigations", _check_assistant_investigations,
               driver, db, assistant_investigation_entries(text), checks, stats)
    _timed(timings, "freshness", _check_freshness, now, checks, stats)
    gate = _timed(timings, "gate_g", verify.gate_g, driver, db, sample_size, seed=seed, accounts=(), chunk=chunk)
    checks.extend(gate["checks"])
    stats["gate_g"] = gate["stats"]
    passed = all(c["pass"] for c in checks)
    return {"status": OK if passed else DRIFT, "checks": checks, "pass": passed, "stats": stats}


def _run_counts(result: dict) -> dict:
    detection = result["stats"].get("detection") or {}
    counts = {key: detection[key] for key in ("mysql_samples", "graph_samples", "changed", "missing_in_graph",
                                              "not_in_mysql", "new_uuids") if key in detection}
    counts["failed_checks"] = [c["name"] for c in result["checks"] if not c["pass"]]
    return counts


def drift_check(driver, db, *, sample_size: int = verify.SAMPLE_SIZE, seed: int | None = None,
                chunk: int = writer.SAMPLE_CHUNK, now=None, trigger: str | None = None) -> dict:
    """Run the drift check (module docstring) and return its result; reads only.

    ``sample_size`` and ``seed`` are gate G's random samples, ``chunk`` the MySQL page size, ``now`` the time
    freshness is judged at (the clock when omitted). With ``trigger`` (who started it: the command, the loop) the run
    is recorded as a ``drift`` run ending ``ok``, ``drift``, ``refused``, or ``failed`` when an error stops it, which
    is raised. Raises ValueError on a ``sample_size`` that is not positive, before anything is read.
    """
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive, got {sample_size}")
    record = state.start_run("drift", trigger=trigger, now=now) if trigger else None
    try:
        result = _drift(driver, db, sample_size, seed, chunk, now)
    except Exception as exc:
        if record is not None:
            record.finish("failed", counts={"error": f"{type(exc).__name__}: {exc}"})
        raise
    if record is not None:
        record.finish(result["status"], counts=_run_counts(result), drift=result)
    return result
