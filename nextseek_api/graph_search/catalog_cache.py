"""The sample-type and attribute catalog, read from the graph and cached per process.

The query builder (``query.build``) needs to know which sample types exist, their labels, which
attribute titles each type may carry and each attribute's ``value_type``. All of that lives in the
graph as ``(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)`` (``docs/neo4j-schema.md``, v1.1), written by
``graph_sync``, which also stamps ``GraphMeta.catalog_hash``.

The catalog is read in one statement and kept for the life of the process. At most every
``RECHECK_SECONDS`` a request re-reads the hash; the catalog itself is re-read only when the hash has
changed, or when the graph has no ``GraphMeta`` yet (an unsynced graph gives no signal, so each
re-check re-reads it). The cache is keyed by database name, not by driver, because callers may open
a driver per request.

Both reads run as READ transactions with a timeout.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from neo4j import Query, RoutingControl

from .query import Catalog

log = logging.getLogger(__name__)

CATALOG_CYPHER = (
    "CYPHER 25 MATCH (t:SampleType) OPTIONAL MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute) "
    "RETURN t.id AS id, t.title AS title, t.label AS label, collect([a.title, a.value_type]) AS attributes"
)
HASH_CYPHER = "CYPHER 25 MATCH (g:GraphMeta) RETURN g.catalog_hash AS catalog_hash"

RECHECK_SECONDS = 60
TIMEOUT_SECONDS = 60

# The clock; tests patch this name.
_now = time.monotonic


def build_catalog(records: Iterable) -> Catalog:
    """A ``Catalog`` from ``CATALOG_CYPHER``'s records (``id``, ``title``, ``label``, ``attributes``).

    Titles are kept byte-exact. A type without a title is skipped; a type without an ``id`` or a
    ``label`` is left out of that map only. The ``[null, null]`` pair ``collect`` yields for a type
    with no attribute is dropped. An attribute without a ``value_type`` gets no entry, which the query
    builder reads as ``string``. Undeclared attributes are included: they are keys samples carry.
    """
    type_title_by_id: dict[int, str] = {}
    label_by_title: dict[str, str] = {}
    titles_by_type: dict[str, frozenset[str]] = {}
    value_type: dict[tuple[str, str], str] = {}

    for record in records:
        title = record["title"]
        if title is None:
            continue
        if record["id"] is not None:
            type_title_by_id[int(record["id"])] = title
        if record["label"] is not None:
            label_by_title[title] = record["label"]

        attribute_titles = set(titles_by_type.get(title, ()))
        for pair in record["attributes"] or ():
            if not pair or pair[0] is None:
                continue
            attribute_titles.add(pair[0])
            if len(pair) > 1 and pair[1] is not None:
                value_type[(title, pair[0])] = pair[1]
        titles_by_type[title] = frozenset(attribute_titles)

    return Catalog(
        type_title_by_id=type_title_by_id,
        label_by_title=label_by_title,
        titles_by_type=titles_by_type,
        value_type=value_type,
    )


def _read(driver, db, text: str) -> list:
    result = driver.execute_query(
        Query(text, timeout=TIMEOUT_SECONDS), {}, routing_=RoutingControl.READ, database_=db
    )
    return list(result.records)


def read_catalog_hash(driver, db) -> Optional[str]:
    """``GraphMeta.catalog_hash``, or None when the graph has no GraphMeta."""
    records = _read(driver, db, HASH_CYPHER)
    return records[0]["catalog_hash"] if records else None


def read_catalog(driver, db) -> Catalog:
    """The whole catalog, uncached."""
    return build_catalog(_read(driver, db, CATALOG_CYPHER))


@dataclass(frozen=True)
class _Entry:
    catalog: Catalog
    catalog_hash: Optional[str]
    checked_at: float


class CatalogCache:
    """A catalog per database name, re-validated against ``GraphMeta.catalog_hash``."""

    def __init__(self, recheck_seconds: float = RECHECK_SECONDS):
        self.recheck_seconds = recheck_seconds
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, driver, db) -> Catalog:
        now = _now()
        with self._lock:
            entry = self._entries.get(db)
        if entry is not None and now - entry.checked_at < self.recheck_seconds:
            return entry.catalog

        current = read_catalog_hash(driver, db)
        if current is None:
            log.warning("graph_search: database %r has no GraphMeta catalog_hash; re-reading the catalog", db)
        elif entry is not None and entry.catalog_hash == current:
            with self._lock:
                self._entries[db] = _Entry(entry.catalog, current, now)
            return entry.catalog

        catalog = read_catalog(driver, db)
        with self._lock:
            self._entries[db] = _Entry(catalog, current, now)
        log.info("graph_search: catalog read from %r (%d sample types, hash %s)",
                 db, len(catalog.titles_by_type), current)
        return catalog

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_PROCESS_CACHE = CatalogCache()


def get_catalog(driver, db) -> Catalog:
    """The process's cached catalog for database ``db``, re-read when ``GraphMeta.catalog_hash`` changes."""
    return _PROCESS_CACHE.get(driver, db)


def clear() -> None:
    """Forget every cached catalog (tests, or after a manual graph load)."""
    _PROCESS_CACHE.clear()
