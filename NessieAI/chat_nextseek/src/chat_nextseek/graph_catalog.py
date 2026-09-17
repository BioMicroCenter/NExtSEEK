"""The v1.1 graph catalog, read live from Neo4j and cached per process (plan task T1; spec section 4.1, D6, D7).

``graph_sync`` writes the catalog into the graph (``docs/neo4j-schema.md``, "v1.1"):
``(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)`` and one ``GraphMeta {schema_version, catalog_hash, synced_at}``.
This module reads it for the graph agent: the type index and the per-label property sets on every graph turn
(``get_snapshot``), the resolved types in full (``get_type_details``) and the keyword-gated vocabulary
(``get_vocabulary``). ``graph_context.py`` renders them; ``agents/graph.py`` checks Cypher against them.

Lazy and read-only: nothing runs at ``ChatConfig`` construction, nothing is written to disk, and every statement runs
as ``session.execute_read`` with ``QUERY_TIMEOUT_S``. The cache is keyed by ``(NEO4J_URI, NEO4J_DATABASE)``, with
one driver per key:

- the snapshot re-reads ``GraphMeta`` at most every ``HASH_RECHECK_S``, and the index and the guard only when
  ``catalog_hash`` has changed (a new hash also drops the cached type details);
- a type detail lives ``DETAIL_TTL_S`` per (hash, title), because statistics can change without the hash;
- the vocabulary lives ``VOCAB_TTL_S``; a source that failed is left empty and read again after ``FAILURE_MEMORY_S``;
- no ``GraphMeta``, a ``schema_version`` below ``SCHEMA_VERSION`` (or not a version at all), or a failed read raises
  ``CatalogUnavailable``, remembered for ``FAILURE_MEMORY_S`` so an outage costs one timeout a minute. A failed read
  also closes and forgets the driver. The caller then uses the committed ``context/neo4j_schema.json``. A later
  version is read as it is: the snapshot records it in ``schema_version``.

This is the admin form (node-level statistics over every project). A non-admin form needs per-project usage and the
caller's scope (spec D10, stage A1).

The per-attribute statistics (``top_values``, ``top_counts``, the ranges and ``distinct_count``) are optional: they
are read when an ``Attribute`` carries them and are absent otherwise, so no ``SCHEMA_VERSION`` or ``catalog_hash``
change is needed to read a catalog that gains them. No writer in this tree writes any of them yet, and the deployed
graph carries none (measured 2026-09-17 against ``catalog_hash 1168b5e6…a362ba``: 3,568 ``Attribute`` nodes, 0 with
``top_values``). ``distinct_count`` is the one that makes a value list usable: without it ten values are a top ten,
and a predicate built from them silently excludes real data. The statistics pass must write it in the same pass as
``top_values`` (``PilotAPOC/review/PROPOSALS.md`` P7b).

Tests replace ``_make_driver`` and ``_now``.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

# The minimum GraphMeta.schema_version this reader accepts. Later versions are read as they are: 1.2 (the sync work)
# adds Sample.source_hash and GraphMeta.label_maps_hash and leaves the catalog as v1.1 defines it.
SCHEMA_VERSION = "1.1"
HASH_RECHECK_S, DETAIL_TTL_S, VOCAB_TTL_S, FAILURE_MEMORY_S, QUERY_TIMEOUT_S = 60, 600, 3600, 60, 10

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def _version_tuple(value) -> tuple[int, int] | None:
    match = _VERSION_RE.match(str(value).strip()) if value is not None else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def schema_version_supported(value) -> bool:
    """True when ``value`` is a ``major.minor`` version at or above ``SCHEMA_VERSION`` ("1.2" and "1.10" are)."""
    version = _version_tuple(value)
    return version is not None and version >= _version_tuple(SCHEMA_VERSION)

# The catalog keeps at most ten top values per attribute; TYPES_ADMIN never reads more. Ten values are a top ten
# unless something says otherwise, which is what ``AttributeRow.values_complete`` is for (P7b).
TOP_VALUES_MAX = 10

# The clock; tests patch this name.
_now = time.monotonic


# --- statements (docs/neo4j-schema.md "v1.1" property names) --------------------------------------------------------

META = """
MATCH (m:GraphMeta)
RETURN m.schema_version AS schema_version, m.catalog_hash AS catalog_hash,
       toString(m.synced_at) AS synced_at,
       EXISTS { MATCH ()-[:USED_IN]->() } AS has_usage
LIMIT 1
""".strip()

INDEX = """
MATCH (t:SampleType)
RETURN t.title AS title, t.label AS label, t.name AS name, t.clade AS clade,
       t.sample_count AS sample_count, coalesce(t.deprecated, false) AS deprecated,
       COUNT { (t)-[:HAS_ATTRIBUTE]->(a:Attribute) WHERE a.sample_count > 0 } AS attributes_with_values
ORDER BY title
""".strip()

GUARD = """
MATCH (t:SampleType)-[:HAS_ATTRIBUTE]->(a:Attribute)
WHERE a.sample_count > 0
RETURN t.label AS label, collect(DISTINCT a.title) AS titles
ORDER BY label
""".strip()

# $types: SampleType titles (the entity codes); $top: TOP_VALUES_MAX.
TYPES_ADMIN = """
MATCH (t:SampleType)
WHERE t.title IN $types
RETURN t.title AS title, t.label AS label, t.name AS name, t.summary AS summary, t.clade AS clade,
       t.sample_count AS sample_count, t.curated_parents AS curated_parents,
       t.curated_children AS curated_children,
       COLLECT {
         MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute)
         WHERE a.sample_count > 0
         RETURN a { .title, .value_type, .declared, .needs_backticks, .sample_count, .meaning, .unit_key, .role,
                    .num_min, .num_max, .date_min, .date_max, .distinct_count,
                    top_values: a.top_values[0..$top], top_counts: a.top_counts[0..$top] } AS attribute
         ORDER BY a.sample_count DESC, a.title
       } AS attributes,
       COUNT {
         (t)-[:HAS_ATTRIBUTE]->(z:Attribute)
         WHERE coalesce(z.sample_count, 0) = 0 AND coalesce(z.declared, true)
       } AS never_filled
ORDER BY title
""".strip()

VOCAB_INVESTIGATIONS = """
MATCH (i:Investigation) WHERE i.title IS NOT NULL
RETURN DISTINCT i.title AS title ORDER BY title
""".strip()

VOCAB_PROJECTS = """
MATCH (p:Project) WHERE p.title IS NOT NULL
RETURN DISTINCT p.title AS title ORDER BY title
""".strip()

VOCAB_STUDIES = """
MATCH (s:Study) WHERE s.title IS NOT NULL
RETURN DISTINCT s.title AS title ORDER BY title
""".strip()

# The unset DOI and PMID are empty strings on these graphs, so IS NOT NULL would match every study.
VOCAB_PUBLISHED = """
MATCH (s:Study)
WHERE coalesce(s.DOI, '') <> '' OR coalesce(s.PMID, '') <> ''
RETURN s.title AS title, s.DOI AS doi, s.PMID AS pmid ORDER BY title
""".strip()

# One pass over DERIVED_FROM gives the assay titles, the protocol titles and the assay connections, with no cap
# (the old connection fetch stopped at LIMIT 300). Only Sample-to-Sample edges: no search sees an OrphanSample.
VOCAB_EDGES = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
WHERE r.internal_assay_title IS NOT NULL OR r.protocol_title IS NOT NULL
RETURN DISTINCT r.internal_assay_title AS assay, r.protocol_title AS protocol,
       p.type AS parent_type, c.type AS child_type
""".strip()


# --- what the reader returns ----------------------------------------------------------------------------------------


class CatalogUnavailable(RuntimeError):
    """The live catalog cannot be used: no URI or password, no GraphMeta, another schema version, or a failed read."""


@dataclass(frozen=True)
class TypeIndexRow:
    """One SampleType as the type index lists it."""

    title: str
    label: str
    name: str | None
    clade: str | None
    sample_count: int | None
    deprecated: bool
    attributes_with_values: int


def _values_complete(top_values, top_counts, sample_count, distinct_count) -> bool | None:
    """``AttributeRow.values_complete``, as a function of the four fields. Never raises: it is on the prompt path."""
    values, counts = tuple(top_values or ()), tuple(top_counts or ())
    listed = len(values)
    if listed == 0:
        return None  # nothing is listed, so there is nothing to qualify
    distinct = _opt_int(distinct_count)
    if distinct is not None:
        if distinct < listed:
            return None  # the count contradicts the list, so neither is trusted
        return distinct == listed
    samples = _opt_int(sample_count)
    counted = [_opt_int(c) for c in counts[:listed]]
    if samples is None or len(counted) < listed or any(c is None for c in counted):
        return None  # the list cannot be totalled
    total = sum(counted)
    if total > samples:
        return None  # multi-valued or stale statistics: a total above the samples holding a value proves nothing
    return total == samples


@dataclass(frozen=True)
class AttributeRow:
    """One attribute of a resolved type that holds a value on at least one sample."""

    title: str
    value_type: str
    declared: bool
    needs_backticks: bool
    sample_count: int | None
    meaning: str | None
    unit_key: str | None
    role: str | None
    top_values: tuple = ()
    top_counts: tuple = ()
    num_min: float | None = None
    num_max: float | None = None
    date_min: str | None = None
    date_max: str | None = None
    # How many distinct values the attribute takes (P7b), when the catalog carries it. Written by the statistics
    # pass that writes ``top_values``; a catalog without it reads None, which means unknown and never zero.
    distinct_count: int | None = None

    @property
    def values_complete(self) -> bool | None:
        """Whether ``top_values`` is the whole value set: True, False, or None when nothing settles it (P7b).

        True means the listed values may be treated as every value there is. False means values are missing, so a
        predicate built from the list alone silently excludes real data. The failure this serves is the other way
        round: ``toLower(s.Strain) CONTAINS 'mtb'`` answered 0 because mTB is not one of the 8 designations BAC.Strain
        holds, which a value list known to be complete says outright.

        ``distinct_count`` decides it when the catalog carries one; otherwise the sample counts can, because listed
        counts that total ``sample_count`` account for every sample holding a value. It describes the catalog's list,
        not the text a renderer prints: a renderer that drops a value of its own must not pass this on as
        completeness.
        """
        return _values_complete(self.top_values, self.top_counts, self.sample_count, self.distinct_count)


@dataclass(frozen=True)
class TypeDetail:
    """A resolved type in full: its attributes with values, most filled first, and how many declared ones are empty."""

    title: str
    label: str
    name: str | None
    summary: str | None
    clade: str | None
    sample_count: int | None
    curated_parents: str | None
    curated_children: str | None
    attributes: tuple[AttributeRow, ...]
    never_filled: int


@dataclass(frozen=True)
class Vocabulary:
    """Titles and edge vocabulary for the keyword-gated context blocks, each sorted."""

    investigation_titles: tuple[str, ...]
    project_titles: tuple[str, ...]
    study_titles: tuple[str, ...]
    published_studies: tuple[dict, ...]
    assay_titles: tuple[str, ...]
    protocol_titles: tuple[str, ...]
    assay_connections: tuple[dict, ...]


@dataclass(frozen=True)
class CatalogSnapshot:
    """The part of the catalog every graph turn needs."""

    catalog_hash: str
    synced_at: str | None
    has_usage: bool
    index: tuple[TypeIndexRow, ...]
    guard: Mapping[str, frozenset[str]]  # T_ label -> attribute titles with values; every known type has a key
    schema_version: str = SCHEMA_VERSION  # the graph's own GraphMeta.schema_version, at or above SCHEMA_VERSION


# --- the cache ------------------------------------------------------------------------------------------------------


@dataclass(eq=False)
class _Entry:
    lock: threading.RLock = field(default_factory=threading.RLock)
    driver: Any = None
    snapshot: CatalogSnapshot | None = None
    checked_at: float | None = None
    failure: str | None = None
    failed_at: float | None = None
    details: dict = field(default_factory=dict)  # title -> (catalog_hash, read_at, TypeDetail)
    vocab: Vocabulary | None = None
    vocab_at: float | None = None
    vocab_ttl: float = VOCAB_TTL_S
    vocab_failed: tuple[str, ...] = ()


_ENTRIES: dict[tuple[str, str], _Entry] = {}
_ENTRIES_LOCK = threading.Lock()


def _make_driver(config):
    """A driver for ``config``'s graph; tests replace this name.

    Short connection timeouts and no transaction retries: a failed catalog read is remembered and the caller falls
    back, so waiting longer buys nothing.
    """
    from neo4j import GraphDatabase  # noqa: PLC0415 (lazy: importing config must not need the driver)

    auth = (getattr(config, "NEO4J_USER", None) or "neo4j", config.NEO4J_PASSWORD)
    options = {
        "connection_timeout": QUERY_TIMEOUT_S,
        "connection_acquisition_timeout": QUERY_TIMEOUT_S,
        "max_transaction_retry_time": 0,
    }
    try:
        return GraphDatabase.driver(config.NEO4J_URI, auth=auth, notifications_min_severity="OFF", **options)
    except TypeError:  # a driver without notification filters
        return GraphDatabase.driver(config.NEO4J_URI, auth=auth, **options)


def _row(record) -> dict:
    data = getattr(record, "data", None)
    return data() if callable(data) else dict(record)


def _read(driver, database: str, statement: str, params: dict | None = None) -> list[dict]:
    """Run one statement in a managed READ transaction with the catalog timeout; the rows as dicts."""
    from neo4j import READ_ACCESS, unit_of_work  # noqa: PLC0415

    @unit_of_work(timeout=QUERY_TIMEOUT_S)
    def work(tx):
        return [_row(record) for record in tx.run(statement, params or {})]

    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return session.execute_read(work)


def _key(config) -> tuple[str, str]:
    uri = getattr(config, "NEO4J_URI", None)
    if not uri:
        raise CatalogUnavailable("NEO4J_URI is not set")
    return str(uri), str(getattr(config, "NEO4J_DATABASE", None) or "neo4j")


def _entry_for(key: tuple[str, str]) -> _Entry:
    with _ENTRIES_LOCK:
        entry = _ENTRIES.get(key)
        if entry is None:
            entry = _ENTRIES[key] = _Entry()
        return entry


def _close(driver) -> None:
    if driver is None:
        return
    try:
        driver.close()
    except Exception:  # noqa: BLE001 (cleanup must never mask the failure being reported)
        pass


def _driver_locked(entry: _Entry, config):
    if entry.driver is None:
        if not getattr(config, "NEO4J_PASSWORD", None):
            raise CatalogUnavailable("NEO4J_PASSWORD is not set")
        entry.driver = _make_driver(config)
    return entry.driver


def _fail_locked(entry: _Entry, reason: str, now: float, *, close: bool) -> None:
    entry.failure = reason[:300]
    entry.failed_at = now
    entry.snapshot = None
    entry.checked_at = None
    if close:
        _close(entry.driver)
        entry.driver = None
    log.warning("graph catalog unavailable for %ss: %s", FAILURE_MEMORY_S, entry.failure)


def _read_failed(exc: Exception) -> str:
    return f"graph catalog read failed: {type(exc).__name__}: {exc}"


def _snapshot_locked(entry: _Entry, key: tuple[str, str], config) -> CatalogSnapshot:
    now = _now()
    if entry.failed_at is not None and now - entry.failed_at < FAILURE_MEMORY_S:
        raise CatalogUnavailable(entry.failure)
    if entry.snapshot is not None and entry.checked_at is not None and now - entry.checked_at < HASH_RECHECK_S:
        return entry.snapshot
    try:
        driver = _driver_locked(entry, config)
        rows = _read(driver, key[1], META)
        if not rows:
            raise CatalogUnavailable("the graph has no GraphMeta node, so it was never synced to v1.1")
        meta = rows[0]
        version = meta.get("schema_version")
        if not schema_version_supported(version):
            raise CatalogUnavailable(
                f"GraphMeta.schema_version is {version!r}; this reader needs {SCHEMA_VERSION} or later")
        version = str(version).strip()
        catalog_hash = meta.get("catalog_hash")
        if not catalog_hash:
            raise CatalogUnavailable("GraphMeta has no catalog_hash")
        synced_at, has_usage = _opt_str(meta.get("synced_at")), bool(meta.get("has_usage"))
        if entry.snapshot is not None and entry.snapshot.catalog_hash == catalog_hash:
            snapshot = replace(entry.snapshot, synced_at=synced_at, has_usage=has_usage, schema_version=version)
        else:
            index = tuple(_index_row(r) for r in _read(driver, key[1], INDEX) if r.get("title") is not None)
            guard = _guard_map(index, _read(driver, key[1], GUARD))
            snapshot = CatalogSnapshot(str(catalog_hash), synced_at, has_usage, index, guard, version)
            entry.details.clear()
            log.info("graph catalog read: %d sample types, catalog_hash %s", len(index), str(catalog_hash)[:12])
    except CatalogUnavailable as exc:
        _fail_locked(entry, str(exc), now, close=False)
        raise
    except Exception as exc:  # noqa: BLE001 (any driver or server error means the fallback)
        _fail_locked(entry, _read_failed(exc), now, close=True)
        raise CatalogUnavailable(entry.failure) from exc
    entry.snapshot, entry.checked_at = snapshot, now
    entry.failure = entry.failed_at = None
    return snapshot


# --- the interface --------------------------------------------------------------------------------------------------


def get_snapshot(config) -> CatalogSnapshot:
    """The catalog snapshot for ``config``'s graph, re-validated against ``GraphMeta.catalog_hash``.

    Raises ``CatalogUnavailable`` when the graph cannot serve the v1.1 catalog; the caller falls back.
    """
    key = _key(config)
    entry = _entry_for(key)
    with entry.lock:
        return _snapshot_locked(entry, key, config)


def get_type_details(config, titles: Iterable[str]) -> list[TypeDetail]:
    """The admin form of each requested type the index knows, in the order asked; unknown titles are ignored.

    One statement reads every title not cached for the current hash within ``DETAIL_TTL_S``. Raises
    ``CatalogUnavailable`` like ``get_snapshot``, and when the read fails.
    """
    key = _key(config)
    entry = _entry_for(key)
    with entry.lock:
        snapshot = _snapshot_locked(entry, key, config)
        known = {row.title for row in snapshot.index}
        wanted = list(dict.fromkeys(t for t in (titles or ()) if isinstance(t, str) and t in known))
        if not wanted:
            return []
        now = _now()
        missing = [t for t in wanted if not _detail_fresh(entry.details.get(t), snapshot.catalog_hash, now)]
        if missing:
            try:
                rows = _read(_driver_locked(entry, config), key[1], TYPES_ADMIN,
                             {"types": missing, "top": TOP_VALUES_MAX})
            except CatalogUnavailable as exc:
                _fail_locked(entry, str(exc), now, close=False)
                raise
            except Exception as exc:  # noqa: BLE001
                _fail_locked(entry, _read_failed(exc), now, close=True)
                raise CatalogUnavailable(entry.failure) from exc
            for row in rows:
                if row.get("title") is None:
                    continue
                detail = _type_detail(row)
                entry.details[detail.title] = (snapshot.catalog_hash, now, detail)
        found = (entry.details.get(t) for t in wanted)
        return [cached[2] for cached in found if cached is not None and cached[0] == snapshot.catalog_hash]


def get_vocabulary(config) -> Vocabulary:
    """Investigation, project and study titles, published studies, and the DERIVED_FROM assay and protocol titles and
    assay connections, cached ``VOCAB_TTL_S``.

    Raises ``CatalogUnavailable`` like ``get_snapshot``. A source whose read fails is empty (logged, and named in
    ``cache_state``) and the whole vocabulary is read again after ``FAILURE_MEMORY_S``.
    """
    key = _key(config)
    entry = _entry_for(key)
    with entry.lock:
        _snapshot_locked(entry, key, config)
        now = _now()
        if entry.vocab is not None and entry.vocab_at is not None and now - entry.vocab_at < entry.vocab_ttl:
            return entry.vocab
        driver = _driver_locked(entry, config)
        failed: list[str] = []

        def read(name: str, statement: str) -> list[dict]:
            try:
                return _read(driver, key[1], statement)
            except Exception as exc:  # noqa: BLE001 (one source failing leaves the others usable)
                failed.append(name)
                log.warning("graph catalog vocabulary %s unavailable: %s", name, _read_failed(exc)[:300])
                return []

        investigations = _titles(read("investigation_titles", VOCAB_INVESTIGATIONS))
        projects = _titles(read("project_titles", VOCAB_PROJECTS))
        studies = _titles(read("study_titles", VOCAB_STUDIES))
        published = tuple(
            {"title": _opt_str(r.get("title")), "doi": r.get("doi"), "pmid": r.get("pmid")}
            for r in read("published_studies", VOCAB_PUBLISHED)
        )
        edges = read("assay_connections", VOCAB_EDGES)
        vocab = Vocabulary(
            investigation_titles=investigations,
            project_titles=projects,
            study_titles=studies,
            published_studies=published,
            assay_titles=_titles({"title": r.get("assay")} for r in edges),
            protocol_titles=_titles({"title": r.get("protocol")} for r in edges),
            assay_connections=_connections(edges),
        )
        entry.vocab, entry.vocab_at = vocab, now
        entry.vocab_ttl = FAILURE_MEMORY_S if failed else VOCAB_TTL_S
        entry.vocab_failed = tuple(failed)
        return vocab


def cache_state(config) -> dict:
    """What the cache holds for ``config``'s graph, from memory only: no driver, no statement.

    For ``ChatConfig.get_config_snapshot`` and the venue check. ``state`` is ``unconfigured`` (no URI), ``unread``,
    ``live`` or ``unavailable``.
    """
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "graph_schema_version": None, "state": "unconfigured", "database": None,
        "catalog_hash": None, "synced_at": None, "has_usage": None, "types": 0, "checked_age_s": None, "failure": None,
        "failure_age_s": None, "cached_type_details": 0, "vocabulary_age_s": None, "vocabulary_failed": [],
    }
    uri = getattr(config, "NEO4J_URI", None)
    if not uri:
        return state
    key = (str(uri), str(getattr(config, "NEO4J_DATABASE", None) or "neo4j"))
    state.update(state="unread", database=key[1])
    with _ENTRIES_LOCK:
        entry = _ENTRIES.get(key)
    if entry is None:
        return state
    now = _now()
    snapshot, checked_at, failed_at = entry.snapshot, entry.checked_at, entry.failed_at
    if snapshot is not None:
        state.update(state="live", graph_schema_version=snapshot.schema_version, catalog_hash=snapshot.catalog_hash,
                     synced_at=snapshot.synced_at, has_usage=snapshot.has_usage, types=len(snapshot.index),
                     checked_age_s=_age(now, checked_at))
    elif failed_at is not None:
        state.update(state="unavailable", failure=entry.failure, failure_age_s=_age(now, failed_at))
    state["cached_type_details"] = len(entry.details)
    if entry.vocab is not None:
        state["vocabulary_age_s"] = _age(now, entry.vocab_at)
        state["vocabulary_failed"] = list(entry.vocab_failed)
    return state


def reset_cache() -> None:
    """Forget every cached catalog and close every driver (tests)."""
    with _ENTRIES_LOCK:
        entries = list(_ENTRIES.values())
        _ENTRIES.clear()
    for entry in entries:
        _close(entry.driver)


# --- row conversion -------------------------------------------------------------------------------------------------

_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def _age(now: float, then: float | None) -> float | None:
    return None if then is None else round(now - then, 1)


def _opt_str(value) -> str | None:
    return None if value is None else str(value)


def _opt_int(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _delimited(value) -> str | None:
    """``curated_parents`` and ``curated_children`` are delimited strings; a list is joined the same way."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v is not None)
    return str(value) or None


def _titles(rows: Iterable[dict]) -> tuple[str, ...]:
    return tuple(sorted({str(r["title"]) for r in rows if r.get("title") is not None}))


def _connections(edges: list[dict]) -> tuple[dict, ...]:
    triples = {(str(r["assay"]), _opt_str(r.get("parent_type")), _opt_str(r.get("child_type")))
               for r in edges if r.get("assay") is not None}
    ordered = sorted(triples, key=lambda t: (t[0], t[1] or "", t[2] or ""))
    return tuple({"assay": a, "parent_type": p, "child_type": c} for a, p, c in ordered)


def _index_row(row: dict) -> TypeIndexRow:
    title = str(row["title"])
    return TypeIndexRow(
        title=title,
        label=str(row.get("label") or "T_" + _LABEL_UNSAFE.sub("_", title)),
        name=_opt_str(row.get("name")),
        clade=_opt_str(row.get("clade")),
        sample_count=_opt_int(row.get("sample_count")),
        deprecated=bool(row.get("deprecated")),
        attributes_with_values=_opt_int(row.get("attributes_with_values")) or 0,
    )


def _guard_map(index: tuple[TypeIndexRow, ...], rows: list[dict]) -> Mapping[str, frozenset[str]]:
    titles: dict[str, set[str]] = {row.label: set() for row in index}
    for row in rows:
        label = row.get("label")
        if not label:
            continue
        titles.setdefault(str(label), set()).update(str(t) for t in (row.get("titles") or ()) if t is not None)
    return MappingProxyType({label: frozenset(names) for label, names in titles.items()})


def _attribute_row(attr: dict) -> AttributeRow:
    title = str(attr["title"])
    declared, backticks = attr.get("declared"), attr.get("needs_backticks")
    return AttributeRow(
        title=title,
        value_type=str(attr.get("value_type") or "string"),
        declared=True if declared is None else bool(declared),
        needs_backticks=(not _PLAIN_NAME.fullmatch(title)) if backticks is None else bool(backticks),
        sample_count=_opt_int(attr.get("sample_count")),
        meaning=_opt_str(attr.get("meaning")),
        unit_key=_opt_str(attr.get("unit_key")),
        role=_opt_str(attr.get("role")),
        top_values=tuple(v if isinstance(v, str) else str(v) for v in (attr.get("top_values") or ())),
        top_counts=tuple(_opt_int(c) or 0 for c in (attr.get("top_counts") or ())),
        distinct_count=_opt_int(attr.get("distinct_count")),
        num_min=_opt_float(attr.get("num_min")),
        num_max=_opt_float(attr.get("num_max")),
        date_min=_opt_str(attr.get("date_min")),
        date_max=_opt_str(attr.get("date_max")),
    )


def _type_detail(row: dict) -> TypeDetail:
    title = str(row["title"])
    return TypeDetail(
        title=title,
        label=str(row.get("label") or "T_" + _LABEL_UNSAFE.sub("_", title)),
        name=_opt_str(row.get("name")),
        summary=_opt_str(row.get("summary")),
        clade=_opt_str(row.get("clade")),
        sample_count=_opt_int(row.get("sample_count")),
        curated_parents=_delimited(row.get("curated_parents")),
        curated_children=_delimited(row.get("curated_children")),
        attributes=tuple(_attribute_row(a) for a in (row.get("attributes") or ()) if a and a.get("title") is not None),
        never_filled=_opt_int(row.get("never_filled")) or 0,
    )


def _detail_fresh(cached, catalog_hash: str, now: float) -> bool:
    return cached is not None and cached[0] == catalog_hash and now - cached[1] < DETAIL_TTL_S
