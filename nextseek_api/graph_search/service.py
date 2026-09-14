"""graph_search's service: a validated request and the caller's Scope in, one page of ids and the total out.

The ViewSet (``nextseek_api/services/graph_search.py``) and the lane's parity harness call this module; neither builds
Cypher. ``query.build`` turns the request, the scope and the graph's catalog into statements; this module runs them in
READ transactions with a timeout and returns plain values. Hydrating the page's rows from MySQL is the ViewSet's job.

A non-admin whose project list is empty sees nothing: both entry points return an empty answer without reading the
catalog or opening a session, and the builder would scope such a caller to ``$projects = []`` in any case.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from neo4j import READ_ACCESS, unit_of_work

from nextseek_api.graph_search import catalog_cache
from nextseek_api.graph_search.query import BuiltQuery, build
from nextseek_api.graph_search.scope import Scope
from nextseek_api.helpers import resolve_sampletype_to_seek_id

# Every graph_search statement is cancelled by the server after this many seconds.
TIMEOUT_SECONDS = 60


def db_filters(req) -> dict:
    """advanced_search's filters for ``req``, sample types resolved to ids exactly as advanced_search resolves them."""
    return req.to_db_filters(sampletype_resolver=resolve_sampletype_to_seek_id)


def _sees_nothing(scope: Scope) -> bool:
    return not scope.is_admin and not scope.project_ids


def _build(req, scope: Scope, page: int, page_size: int, driver, db: str, filters: Optional[dict]) -> BuiltQuery:
    catalog = catalog_cache.get_catalog(driver, db)
    if filters is None:
        filters = db_filters(req)
    return build(filters, req.extensions, scope, catalog, page, page_size)


def _read(session, text: str, params: dict, transform: Callable, timeout: float):
    """Run one statement in a managed READ transaction; ``transform`` consumes the result inside it."""

    @unit_of_work(timeout=timeout)
    def work(tx):
        return transform(tx.run(text, params))

    return session.execute_read(work)


def _ids(result) -> list[int]:
    return [int(record["id"]) for record in result]


def _count(result) -> tuple[int, list[str]]:
    record = result.single(strict=True)
    return int(record["total"]), [t for t in (record["types"] or []) if t is not None]


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def search(req, scope: Scope, page: int, page_size: int, *, driver, db: str, filters: Optional[dict] = None) -> dict:
    """One page of matching sample ids, the total and the matching sample types.

    Returns ``{"total", "ids", "sample_types", "timings": {"cypher_ms", "count_ms"}}``; ``ids`` are in ascending id
    order and ``sample_types`` is sorted. ``filters`` is ``db_filters(req)`` when the caller already has it, so the
    sample types are not resolved twice. Raises ``query.GraphSearchInvalid`` for a request the catalog rejects and the
    driver's exceptions for anything the graph refuses.
    """
    if _sees_nothing(scope):
        return {"total": 0, "ids": [], "sample_types": [], "timings": {"cypher_ms": 0.0, "count_ms": 0.0}}
    built = _build(req, scope, page, page_size, driver, db, filters)
    with driver.session(database=db, default_access_mode=READ_ACCESS) as session:
        start = time.perf_counter()
        ids = _read(session, built.page_cypher, built.params, _ids, TIMEOUT_SECONDS)
        cypher_ms = _ms(start)
        start = time.perf_counter()
        total, types = _read(session, built.count_cypher, built.params, _count, TIMEOUT_SECONDS)
        count_ms = _ms(start)
    return {
        "total": total,
        "ids": ids,
        "sample_types": sorted(set(types)),
        "timings": {"cypher_ms": cypher_ms, "count_ms": count_ms},
    }


def all_ids(req, scope: Scope, *, driver, db: str, filters: Optional[dict] = None,
            timeout: float = TIMEOUT_SECONDS) -> list[int]:
    """Every matching sample id in ascending order, unpaged. For the parity harness, not the endpoint."""
    if _sees_nothing(scope):
        return []
    built = _build(req, scope, 1, 1, driver, db, filters)
    with driver.session(database=db, default_access_mode=READ_ACCESS) as session:
        return _read(session, built.ids_cypher, built.params, _ids, timeout)
