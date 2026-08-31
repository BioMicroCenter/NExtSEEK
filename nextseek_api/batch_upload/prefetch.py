"""Stage 3: PREFETCH — Thread-safe cached validation against the database."""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .config import BatchUploadConfig

log = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()

# Module-level caches
_ASSAY_EXISTS_CACHE: Dict[int, bool] = {}
_SAMPLE_TYPE_TITLE_TO_ID: Dict[str, int] = {}
_PROJECT_SAMPLE_TYPE_LINKED: Dict[int, Set[int]] = {}
_SAMPLE_TYPE_ATTRIBUTES_CACHE: Dict[int, Set[str]] = {}
# Database-side generation stamp for _SAMPLE_TYPE_ATTRIBUTES_CACHE.
# See refresh_sample_type_attributes_cache.
_ATTRIBUTES_GENERATION: Optional[Tuple[int, Optional[str]]] = None

_config = BatchUploadConfig()


def _trim_cache(cache_dict: dict, max_size: int) -> None:
    """Evict oldest entries (insertion order) when exceeding max_size."""
    while len(cache_dict) > max_size:
        cache_dict.pop(next(iter(cache_dict)))


def prefetch_sample_types(titles: List[str], conn: Connection) -> Dict[str, int]:
    """Bulk-fetch sample type title -> id mappings, using cache.

    Returns mapping of title -> sample_type_id for all found titles.
    """
    with _CACHE_LOCK:
        uncached = [t for t in titles if t not in _SAMPLE_TYPE_TITLE_TO_ID]

    if uncached:
        # Bulk query for uncached titles
        params = {f"t_{i}": t for i, t in enumerate(uncached)}
        placeholders = ", ".join(f":t_{i}" for i in range(len(uncached)))
        sql = text(f"SELECT title, id FROM sample_types WHERE title IN ({placeholders})")
        rows = conn.execute(sql, params).fetchall()

        with _CACHE_LOCK:
            for title, st_id in rows:
                _SAMPLE_TYPE_TITLE_TO_ID[title] = st_id
            _trim_cache(_SAMPLE_TYPE_TITLE_TO_ID, _config.title_to_id_cache_max)

    with _CACHE_LOCK:
        return {t: _SAMPLE_TYPE_TITLE_TO_ID[t] for t in titles if t in _SAMPLE_TYPE_TITLE_TO_ID}


def prefetch_assay_ids(assay_ids: List[int], conn: Connection) -> Set[int]:
    """Bulk-fetch assay ID existence checks, using cache.

    Returns set of assay IDs that exist in the database.
    """
    with _CACHE_LOCK:
        uncached = [aid for aid in assay_ids if aid not in _ASSAY_EXISTS_CACHE]

    if uncached:
        # Fetch in chunks of 1000
        for chunk_start in range(0, len(uncached), 1000):
            chunk = uncached[chunk_start : chunk_start + 1000]
            params = {f"id_{i}": aid for i, aid in enumerate(chunk)}
            placeholders = ", ".join(f":id_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id FROM assays WHERE id IN ({placeholders})")
            rows = conn.execute(sql, params).fetchall()
            found = {r[0] for r in rows}

            with _CACHE_LOCK:
                for aid in chunk:
                    _ASSAY_EXISTS_CACHE[aid] = aid in found
                _trim_cache(_ASSAY_EXISTS_CACHE, _config.assay_cache_max)

    with _CACHE_LOCK:
        return {aid for aid in assay_ids if _ASSAY_EXISTS_CACHE.get(aid, False)}


def prefetch_project_sample_type_links(
    project_id: int,
    sample_type_ids: List[int],
    conn: Connection,
    mutate_project_links: bool = True,
) -> None:
    """Ensure project <-> sample_type links exist, creating missing ones.

    When ``mutate_project_links`` is False, missing links are detected but NOT
    created — no INSERT is issued. This is the validate-mode path
    (``run_validation_multi``), where the pipeline must have zero side effects.
    In that case only already-existing links are recorded in the cache, so a
    later real upload still creates the missing ones.
    """
    if not project_id or not sample_type_ids:
        return

    with _CACHE_LOCK:
        cached = _PROJECT_SAMPLE_TYPE_LINKED.get(project_id, set())
        unchecked = [st for st in sample_type_ids if st not in cached]

    if not unchecked:
        return

    # Check existing links
    params = {"pid": project_id}
    params.update({f"st_{i}": st for i, st in enumerate(unchecked)})
    placeholders = ", ".join(f":st_{i}" for i in range(len(unchecked)))
    sql = text(
        f"SELECT sample_type_id FROM projects_sample_types "
        f"WHERE project_id = :pid AND sample_type_id IN ({placeholders})"
    )
    rows = conn.execute(sql, params).fetchall()
    existing = {r[0] for r in rows}

    # Create missing links
    to_create = [st for st in unchecked if st not in existing]
    if to_create and mutate_project_links:
        values_parts = []
        insert_params = {}
        for i, st in enumerate(to_create):
            values_parts.append(f"(:pid_{i}, :stid_{i})")
            insert_params[f"pid_{i}"] = project_id
            insert_params[f"stid_{i}"] = st
        sql = text(
            f"INSERT IGNORE INTO projects_sample_types (project_id, sample_type_id) "
            f"VALUES {', '.join(values_parts)}"
        )
        conn.execute(sql, insert_params)
        log.info(
            "Created %d project-sample_type links for project %d",
            len(to_create),
            project_id,
        )

    # Update cache. In validate mode (no mutation) only existing links are
    # cached — never-created links must stay uncached so a real upload sees them.
    with _CACHE_LOCK:
        all_linked = existing | (set(to_create) if mutate_project_links else set())
        _PROJECT_SAMPLE_TYPE_LINKED.setdefault(project_id, set()).update(all_linked)


def resolve_sample_type_id(title: str, project_id: Optional[int], conn: Connection) -> int:
    """Resolve a sample type title to its ID, with cache lookup and fallback query.

    Raises ValueError if the sample type is not found.
    """
    with _CACHE_LOCK:
        if title in _SAMPLE_TYPE_TITLE_TO_ID:
            return _SAMPLE_TYPE_TITLE_TO_ID[title]

    # Fallback query
    sql = text("SELECT id FROM sample_types WHERE title = :title LIMIT 1")
    row = conn.execute(sql, {"title": title}).fetchone()
    if row is None:
        raise ValueError(f"Sample type not found: {title!r}")

    st_id = row[0]
    with _CACHE_LOCK:
        _SAMPLE_TYPE_TITLE_TO_ID[title] = st_id
        _trim_cache(_SAMPLE_TYPE_TITLE_TO_ID, _config.title_to_id_cache_max)

    return st_id


def validate_assay_ids(
    assay_ids: List[int], conn: Connection
) -> Tuple[Set[int], Set[int]]:
    """Validate assay IDs against the database.

    Returns (valid_set, missing_set).
    """
    if not assay_ids:
        return set(), set()

    with _CACHE_LOCK:
        known_valid = {aid for aid in assay_ids if _ASSAY_EXISTS_CACHE.get(aid, False)}
        known_invalid = {
            aid
            for aid in assay_ids
            if aid in _ASSAY_EXISTS_CACHE and not _ASSAY_EXISTS_CACHE[aid]
        }
        unknown = [
            aid for aid in assay_ids if aid not in _ASSAY_EXISTS_CACHE
        ]

    if unknown:
        # Query in chunks of 1000
        found_in_db: Set[int] = set()
        for chunk_start in range(0, len(unknown), 1000):
            chunk = unknown[chunk_start : chunk_start + 1000]
            params = {f"id_{i}": aid for i, aid in enumerate(chunk)}
            placeholders = ", ".join(f":id_{i}" for i in range(len(chunk)))
            sql = text(f"SELECT id FROM assays WHERE id IN ({placeholders})")
            rows = conn.execute(sql, params).fetchall()
            found_in_db.update(r[0] for r in rows)

        with _CACHE_LOCK:
            for aid in unknown:
                _ASSAY_EXISTS_CACHE[aid] = aid in found_in_db
            _trim_cache(_ASSAY_EXISTS_CACHE, _config.assay_cache_max)

        known_valid.update(found_in_db)
        known_invalid.update(aid for aid in unknown if aid not in found_in_db)

    return known_valid, known_invalid


def refresh_sample_type_attributes_cache(conn: Connection) -> None:
    """Drop this process's cached attribute sets if sample_attributes has changed.

    Call ONCE PER BATCH, not per row. prefetch_sample_type_attributes is called per
    row (transform.py:79), and the whole point of its cache is that a cache hit costs
    no SQL; putting this check inside it would add an aggregate query per row.

    Why a database stamp rather than an invalidation hook on the writer:
    _SAMPLE_TYPE_ATTRIBUTES_CACHE is a plain module-level dict, so it is PER PROCESS,
    and gunicorn runs 4 workers (gunicorn.conf.py:2). A write served by worker 1 cannot
    reach worker 2's dict, and the project configures no shared cache backend (no CACHES
    in dmac/settings.py; Django's default LocMemCache is per-process too). An in-process
    hook would look correct under a single local worker and still leave production
    serving stale attribute sets from the other three -- the reported symptom being a
    rejection count that oscillates between runs on an unchanged file.

    Reading the stamp from the one thing every worker shares -- the database -- also
    makes this writer-agnostic. It equally catches writes from the attributes API, from
    NExtSEEK's own /seek/samples/attributes/ editor, from the dmac-curation
    sampletype_attr.py stopgap, and from manual SQL. No writer has to cooperate, or even
    know this cache exists.

    (COUNT(*), MAX(updated_at)) suffices for THIS cache because it stores attribute
    TITLES: inserts and deletes move the count, and title edits move updated_at
    (attributes/executor.py:681-684 sets updated_at=NOW(6)). The one write that moves
    neither is the position-only UPDATE at executor.py:668-671, which sets just `pos`,
    and pos is not cached -- so not reacting to it is correct, not a gap.
    """
    global _ATTRIBUTES_GENERATION
    row = conn.execute(
        text("SELECT COUNT(*), MAX(updated_at) FROM sample_attributes")
    ).fetchone()
    if row is None:
        generation: Tuple[int, Optional[str]] = (0, None)
    else:
        generation = (int(row[0]), str(row[1]) if row[1] is not None else None)

    with _CACHE_LOCK:
        if generation == _ATTRIBUTES_GENERATION:
            return
        if _ATTRIBUTES_GENERATION is not None:
            log.info(
                "sample_attributes changed (%s -> %s); dropping %d cached attribute set(s)",
                _ATTRIBUTES_GENERATION, generation, len(_SAMPLE_TYPE_ATTRIBUTES_CACHE),
            )
        _SAMPLE_TYPE_ATTRIBUTES_CACHE.clear()
        _ATTRIBUTES_GENERATION = generation


def prefetch_sample_type_attributes(
    sample_type_ids: List[int], conn: Connection
) -> Dict[int, Set[str]]:
    """Bulk-fetch sample type id -> attribute titles set, using cache.

    Returns mapping of sample_type_id -> {attribute_title, ...} for IDs that
    have at least one row in sample_attributes. IDs with no rows are omitted
    from the result, but are remembered in the cache as an empty set so
    repeated calls do not re-query.
    """
    if not sample_type_ids:
        return {}

    with _CACHE_LOCK:
        uncached = [
            sid for sid in sample_type_ids if sid not in _SAMPLE_TYPE_ATTRIBUTES_CACHE
        ]

    if uncached:
        for chunk_start in range(0, len(uncached), 1000):
            chunk = uncached[chunk_start : chunk_start + 1000]
            params = {f"id_{i}": sid for i, sid in enumerate(chunk)}
            placeholders = ", ".join(f":id_{i}" for i in range(len(chunk)))
            sql = text(
                f"SELECT sample_type_id, title FROM sample_attributes "
                f"WHERE sample_type_id IN ({placeholders})"
            )
            rows = conn.execute(sql, params).fetchall()

            chunk_result: Dict[int, Set[str]] = {sid: set() for sid in chunk}
            for st_id, title in rows:
                chunk_result.setdefault(st_id, set()).add(title)

            with _CACHE_LOCK:
                for sid, attr_set in chunk_result.items():
                    _SAMPLE_TYPE_ATTRIBUTES_CACHE[sid] = attr_set
                _trim_cache(_SAMPLE_TYPE_ATTRIBUTES_CACHE, _config.attribute_cache_max)

    with _CACHE_LOCK:
        return {
            sid: _SAMPLE_TYPE_ATTRIBUTES_CACHE[sid]
            for sid in sample_type_ids
            if _SAMPLE_TYPE_ATTRIBUTES_CACHE.get(sid)
        }


def clear_caches() -> None:
    """Clear all in-process caches."""
    with _CACHE_LOCK:
        _ASSAY_EXISTS_CACHE.clear()
        _SAMPLE_TYPE_TITLE_TO_ID.clear()
        _PROJECT_SAMPLE_TYPE_LINKED.clear()
        _SAMPLE_TYPE_ATTRIBUTES_CACHE.clear()
        global _ATTRIBUTES_GENERATION
        _ATTRIBUTES_GENERATION = None
    log.debug("Prefetch caches cleared")


def cache_stats() -> Dict[str, int]:
    """Return current cache sizes."""
    with _CACHE_LOCK:
        return {
            "assay_exists": len(_ASSAY_EXISTS_CACHE),
            "sample_type_title_to_id": len(_SAMPLE_TYPE_TITLE_TO_ID),
            "project_sample_type_linked": sum(
                len(v) for v in _PROJECT_SAMPLE_TYPE_LINKED.values()
            ),
            "sample_type_attributes": len(_SAMPLE_TYPE_ATTRIBUTES_CACHE),
        }
