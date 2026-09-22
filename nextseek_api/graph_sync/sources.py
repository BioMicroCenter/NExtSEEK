"""Read the graph schema v1.1 sources from MySQL (docs/neo4j-schema.md, section "v1.1").

Two Django connections: `settings.SEEK_DATABASE` (SEEK's `seek_production`) and
`settings.NEXTSEEK_DATABASE` (the `dmac` schema). Every statement uses bound parameters.
Nothing here writes.

Rules the readers keep:

- **Byte-exact titles.** MySQL compares titles case-insensitively and ignores trailing spaces;
  Neo4j property names are exact. So no reader filters or joins on a title in SQL: rows come back
  as stored and every title comparison happens in Python.
- **Soft on the dmac context tables.** The dev box has no `sample_types_context`. A missing
  context table gives an empty result (the catalog then writes `has_context = false`), never a
  failure. The check is explicit (`table_exists`), not a broad `except`, so a genuine error still
  raises.
- **`sample_types_context` is read through the ORM**, selecting the field `tags`, whose db_column
  is capital-T `Tags`; `CONTEXT_FIELDS` is pinned against the model by a unit test.
- **Bounded memory.** Samples are read in keyset pages; the large pair lists are fetched in
  batches. The digest stream (`iter_digest_rows`) groups one page's links at a time.
- **By id, in chunks.** A by-id reader binds at most `IN_CHUNK` values per `IN` list and splits
  a longer list over several statements.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, Iterator

from django.conf import settings
from django.db import connections

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.batch_upload.identity import extract_identity
from nextseek_api.services.template_catalog import is_deprecated
from seek.models import Sample_types_context

logger = logging.getLogger(__name__)

# Rows pulled from a cursor per fetchmany() on the large pair reads.
FETCH_BATCH = 10_000

# Values bound per `IN (...)` list on the by-id reads.
IN_CHUNK = 1000

# FIELD names on Sample_types_context, not db_columns: `tags` maps to the `Tags` column.
CONTEXT_FIELDS = ("sample_type", "sampletype_id", "name", "description", "tags",
                  "parent_sampletypes", "child_sampletypes", "clade")


def _seek():
    return connections[settings.SEEK_DATABASE]


def _dmac():
    return connections[settings.NEXTSEEK_DATABASE]


def _text(value):
    """A str column value; some MySQL column types come back as bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    return value


def _rows(conn, sql: str, params=None) -> Iterator[tuple]:
    """Execute one read and yield its rows, fetched in batches."""
    with conn.cursor() as cursor:
        cursor.execute(sql, params if params is not None else [])
        while True:
            batch = cursor.fetchmany(FETCH_BATCH)
            if not batch:
                return
            yield from batch


def table_exists(alias: str, table: str) -> bool:
    """Whether `table` exists in the database behind the Django connection `alias`."""
    conn = connections[alias]
    with conn.cursor() as cursor:
        return table in conn.introspection.table_names(cursor)


# --- samples -----------------------------------------------------------------------------------

_SAMPLES_PAGE_SQL = (
    "SELECT id, uuid, title, sample_type_id, json_metadata FROM samples "
    "WHERE id > %s ORDER BY id LIMIT %s"
)


def iter_samples(chunk: int = 5000, after_id: int = 0) -> Iterator[list[dict]]:
    """Keyset pages of samples ordered by id, each at most `chunk` rows."""
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")
    last = int(after_id)
    while True:
        with _seek().cursor() as cursor:
            cursor.execute(_SAMPLES_PAGE_SQL, [last, chunk])
            fetched = cursor.fetchall()
        page = [{"id": int(sid), "uuid": _text(uuid), "title": _text(title),
                 "sample_type_id": int(type_id) if type_id is not None else None,
                 "json_metadata": _text(meta)}
                for sid, uuid, title, type_id, meta in fetched]
        if not page:
            return
        yield page
        last = page[-1]["id"]
        if len(page) < chunk:
            return


def sample_projects() -> dict[int, list[int]]:
    """Sample id to its sorted distinct project ids, from `projects_samples`."""
    pairs: dict[int, set[int]] = {}
    for sample_id, project_id in _rows(
            _seek(), "SELECT sample_id, project_id FROM projects_samples "
                     "WHERE sample_id IS NOT NULL AND project_id IS NOT NULL"):
        pairs.setdefault(int(sample_id), set()).add(int(project_id))
    return {sid: sorted(pids) for sid, pids in pairs.items()}


def uuid_to_ids() -> dict[str, list[int]]:
    """Sample uuid (as stored) to every sample id carrying it; duplicates keep all ids."""
    index: dict[str, list[int]] = {}
    for uuid, sid in _rows(_seek(), "SELECT uuid, id FROM samples ORDER BY id"):
        uuid = _text(uuid)
        if not uuid:
            continue
        index.setdefault(uuid, []).append(int(sid))
    return index


def declared_lineage(sample_rows: Iterable[dict], uuid_index) -> Iterator[tuple[int, int]]:
    """(child id, parent id) pairs declared by the samples' parent tokens.

    The batch-upload rule (`collect_parent_tokens` over every key containing "parent", split on
    `;`, `UID_RE` tokens only), resolved through `uuid_index`. A token naming the sample's own
    uuid, and a parent id equal to the child id, yield nothing. A row whose metadata cannot be
    read as a JSON object yields nothing.
    """
    for row in sample_rows:
        raw = _text(row.get("json_metadata"))
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        child = int(row["id"])
        own_uuid = row.get("uuid")
        for token in collect_parent_tokens(meta):
            if token == own_uuid or not UID_RE.match(token):
                continue
            for parent in uuid_index.get(token, ()):
                if parent != child:
                    yield child, int(parent)


# --- samples by id -----------------------------------------------------------------------------

def _placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


def _chunks(values: list) -> Iterator[list]:
    for start in range(0, len(values), IN_CHUNK):
        yield values[start:start + IN_CHUNK]


def _id_chunks(ids: Iterable) -> Iterator[list[int]]:
    """The distinct ids as ints, ascending, in lists of at most `IN_CHUNK`."""
    return _chunks(sorted({int(i) for i in ids}))


def _wanted_tokens(tokens: Iterable) -> set[str]:
    return {t for t in tokens if isinstance(t, str) and t}


def _sample_row(sid, uuid, title, type_id, meta) -> dict:
    return {"id": int(sid), "uuid": _text(uuid), "title": _text(title),
            "sample_type_id": int(type_id) if type_id is not None else None,
            "json_metadata": _text(meta)}


def samples_by_ids(ids: Iterable[int]) -> list[dict]:
    """The `samples` rows with these ids, in `iter_samples`' shape, ordered by id.

    An id MySQL no longer holds is simply absent: the caller retires it.
    """
    rows: list[dict] = []
    for chunk in _id_chunks(ids):
        sql = ("SELECT id, uuid, title, sample_type_id, json_metadata FROM samples "
               f"WHERE id IN ({_placeholders(len(chunk))}) ORDER BY id")
        rows.extend(_sample_row(*row) for row in _rows(_seek(), sql, chunk))
    return rows


def _links_for(sql_head: str, ids: Iterable[int], lead: list) -> dict[int, list[int]]:
    """Sample id to its sorted distinct linked ids, for `sql_head ... IN (ids)` in chunks."""
    pairs: dict[int, set[int]] = {}
    for chunk in _id_chunks(ids):
        sql = f"{sql_head} IN ({_placeholders(len(chunk))})"
        for sample_id, other_id in _rows(_seek(), sql, [*lead, *chunk]):
            pairs.setdefault(int(sample_id), set()).add(int(other_id))
    return {sid: sorted(others) for sid, others in sorted(pairs.items())}


def sample_projects_for(ids: Iterable[int]) -> dict[int, list[int]]:
    """`sample_projects` for these sample ids only; an id with no project link is absent."""
    return _links_for("SELECT sample_id, project_id FROM projects_samples "
                      "WHERE project_id IS NOT NULL AND sample_id", ids, [])


def sample_assay_ids_for(ids: Iterable[int]) -> dict[int, list[int]]:
    """Sample id to its sorted distinct SEEK assay ids (`assay_assets` Sample rows).

    An id with no assay link is absent.
    """
    return _links_for("SELECT asset_id, assay_id FROM assay_assets "
                      "WHERE asset_type = %s AND assay_id IS NOT NULL AND asset_id", ids, ["Sample"])


def uuid_to_ids_for(tokens: Iterable[str]) -> dict[str, list[int]]:
    """`uuid_to_ids` for these tokens only: a stored uuid equal to a token, byte for byte.

    MySQL's `IN` also returns a stored uuid differing in case or trailing spaces; those rows
    are dropped here, as `uuid_to_ids` would key them apart. Blank tokens are ignored.
    """
    wanted = _wanted_tokens(tokens)
    index: dict[str, set[int]] = {}
    for chunk in _chunks(sorted(wanted)):
        sql = f"SELECT uuid, id FROM samples WHERE uuid IN ({_placeholders(len(chunk))}) ORDER BY id"
        for uuid, sid in _rows(_seek(), sql, chunk):
            uuid = _text(uuid)
            if uuid in wanted:
                index.setdefault(uuid, set()).add(int(sid))
    return {uuid: sorted(ids) for uuid, ids in index.items()}


def _metadata_object(raw) -> dict:
    """`json_metadata` as a dict; unreadable or non-object metadata reads as empty."""
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def parent_identities(uuids: Iterable[str]) -> dict[str, str | None]:
    """Stored uuid to the identity a child's `parent_titles` names it by.

    The external-UID lookup of `batch_upload/neo4j_sync.py::enrich_parent_titles`:
    `extract_identity(meta, uid=uuid)` over the row's metadata, unreadable or non-object
    metadata reading as empty (the identity is then None). Only stored uuids equal to a
    requested one byte for byte are kept. Rows are read in id order and a later row replaces an
    earlier one, as in that loop, so on a duplicated uuid the highest id wins. A uuid MySQL does
    not hold is absent.
    """
    wanted = _wanted_tokens(uuids)
    identities: dict[str, str | None] = {}
    for chunk in _chunks(sorted(wanted)):
        sql = ("SELECT uuid, json_metadata FROM samples "
               f"WHERE uuid IN ({_placeholders(len(chunk))}) ORDER BY id")
        for uuid, raw in _rows(_seek(), sql, chunk):
            uuid = _text(uuid)
            if uuid in wanted:
                identities[uuid] = extract_identity(_metadata_object(_text(raw)), uid=uuid)
    return identities


# --- keyed streams -----------------------------------------------------------------------------

def _check_chunk(chunk: int) -> None:
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")


def ids_of_type(type_id: int, chunk: int = 5000) -> Iterator[list[int]]:
    """Keyset pages of the ids of one sample type, ascending, each at most `chunk` ids."""
    _check_chunk(chunk)
    last = 0
    while True:
        with _seek().cursor() as cursor:
            cursor.execute("SELECT id FROM samples WHERE sample_type_id = %s AND id > %s "
                           "ORDER BY id LIMIT %s", [int(type_id), last, chunk])
            ids = [int(row[0]) for row in cursor.fetchall()]
        if not ids:
            return
        yield ids
        last = ids[-1]
        if len(ids) < chunk:
            return


def _names_any(raw, own_uuid, wanted: set[str]) -> bool:
    """Whether the metadata declares a parent token in `wanted`, `declared_lineage`'s rule."""
    for token in collect_parent_tokens(_metadata_object(raw)):
        if token in wanted and token != own_uuid and UID_RE.match(token):
            return True
    return False


def samples_naming(uuids: Iterable[str], chunk: int = 5000) -> list[int]:
    """Ids of the samples whose parent tokens name one of these uuids, ascending.

    One keyset pass over `samples.json_metadata` (spec 10.3, step 4), tokens read by the
    `declared_lineage` rule: keys containing "parent", split on `;`, `UID_RE` tokens only, a
    sample naming its own uuid skipped, compared byte for byte. No uuid, no read.
    """
    _check_chunk(chunk)
    wanted = _wanted_tokens(uuids)
    if not wanted:
        return []
    found: list[int] = []
    last = 0
    while True:
        with _seek().cursor() as cursor:
            cursor.execute("SELECT id, uuid, json_metadata FROM samples WHERE id > %s "
                           "ORDER BY id LIMIT %s", [last, chunk])
            fetched = cursor.fetchall()
        if not fetched:
            return found
        for sid, uuid, raw in fetched:
            if _names_any(_text(raw), _text(uuid), wanted):
                found.append(int(sid))
        last = int(fetched[-1][0])
        if len(fetched) < chunk:
            return found


_DIGEST_PAGE_SQL = (
    "SELECT id, uuid, title, sample_type_id, json_metadata, updated_at FROM samples "
    "WHERE id > %s ORDER BY id LIMIT %s"
)
_PROJECT_LINK_STREAM_SQL = (
    "SELECT sample_id, project_id FROM projects_samples "
    "WHERE sample_id IS NOT NULL AND project_id IS NOT NULL ORDER BY sample_id"
)
_ASSAY_LINK_STREAM_SQL = (
    "SELECT asset_id, assay_id FROM assay_assets "
    "WHERE asset_type = %s AND asset_id IS NOT NULL AND assay_id IS NOT NULL ORDER BY asset_id"
)


class _LinkStream:
    """(sample id, linked id) rows in sample-id order, taken one sample page at a time."""

    def __init__(self, table: str, rows: Iterator[tuple]):
        self.table, self._rows = table, rows
        self._pending: tuple | None = None
        self._last: int | None = None

    def take_through(self, last_id: int) -> dict[int, set[int]]:
        """Every link not yet taken whose sample id is at most `last_id`, by sample id.

        Links to ids the caller's page does not hold are returned too, and dropped there. A
        row out of sample-id order raises: the merge would silently lose links otherwise.
        """
        links: dict[int, set[int]] = {}
        while True:
            row, self._pending = self._pending, None
            if row is None:
                row = next(self._rows, None)
                if row is None:
                    return links
                if self._last is not None and int(row[0]) < self._last:
                    raise RuntimeError(
                        f"{self.table} came back out of sample-id order ({int(row[0])} after "
                        f"{self._last}); the digest merge would drop links")
                self._last = int(row[0])
            sample_id = int(row[0])
            if sample_id > last_id:
                self._pending = row
                return links
            links.setdefault(sample_id, set()).add(int(row[1]))

    def close(self) -> None:
        close = getattr(self._rows, "close", None)
        if close is not None:
            close()


def iter_digest_rows(chunk: int = 5000) -> Iterator[list[dict]]:
    """Keyset pages of every sample, each row carrying its `project_ids` and `assay_ids`.

    The MySQL half of the nightly targeted sync's merge (spec 10.3): `samples` by primary-key
    keyset, merged by id with `projects_samples` ordered by `sample_id` and the `assay_assets`
    Sample rows ordered by `asset_id`, each read by one statement. A row is `iter_samples`'
    plus `updated_at` (for the run record's watermark) and the two sorted distinct id lists,
    empty when the sample has no link. A link naming an id `samples` does not hold is dropped.

    Memory: Python holds one page and that page's links. mysqlclient's default cursor still
    buffers each link statement's whole result on the client as tuples, so the peak is the two
    link tables' rows, never a dict of every sample's links.
    """
    _check_chunk(chunk)
    projects = _LinkStream("projects_samples", _rows(_seek(), _PROJECT_LINK_STREAM_SQL))
    assays = _LinkStream("assay_assets", _rows(_seek(), _ASSAY_LINK_STREAM_SQL, ["Sample"]))
    try:
        last = 0
        while True:
            with _seek().cursor() as cursor:
                cursor.execute(_DIGEST_PAGE_SQL, [last, chunk])
                fetched = cursor.fetchall()
            if not fetched:
                return
            page = []
            for sid, uuid, title, type_id, meta, updated_at in fetched:
                row = _sample_row(sid, uuid, title, type_id, meta)
                row["updated_at"] = updated_at
                page.append(row)
            last = page[-1]["id"]
            project_links = projects.take_through(last)
            assay_links = assays.take_through(last)
            for row in page:
                row["project_ids"] = sorted(project_links.get(row["id"], ()))
                row["assay_ids"] = sorted(assay_links.get(row["id"], ()))
            yield page
            if len(page) < chunk:
                return
    finally:
        projects.close()
        assays.close()


# --- the catalog -------------------------------------------------------------------------------

def sample_types() -> list[dict]:
    """Every SEEK sample type: `id`, `title`, `uuid`, `description`."""
    return [{"id": int(tid), "title": _text(title), "uuid": _text(uuid), "description": _text(desc)}
            for tid, title, uuid, desc in _rows(
                _seek(), "SELECT id, title, uuid, description FROM sample_types ORDER BY id")]


def sample_attributes() -> list[dict]:
    """Every SEEK sample attribute, titles as stored (case and trailing spaces kept)."""
    sql = ("SELECT id, sample_type_id, title, pos, required, is_title, sample_attribute_type_id, "
           "description FROM sample_attributes ORDER BY sample_type_id, pos, id")
    return [{"id": int(aid), "sample_type_id": int(type_id) if type_id is not None else None,
             "title": _text(title), "pos": pos, "required": bool(required),
             "is_title": bool(is_title), "sample_attribute_type_id": attr_type_id,
             "description": _text(desc)}
            for aid, type_id, title, pos, required, is_title, attr_type_id, desc
            in _rows(_seek(), sql)]


def sample_attribute_types() -> dict[int, dict]:
    """SEEK attribute types by id: `id`, `title`, `base_type`, `regexp`."""
    return {int(tid): {"id": int(tid), "title": _text(title), "base_type": _text(base),
                       "regexp": _text(regexp)}
            for tid, title, base, regexp in _rows(
                _seek(), "SELECT id, title, base_type, `regexp` FROM sample_attribute_types ORDER BY id")}


def _context_rows() -> list[dict]:
    """Raw `sample_types_context` rows through the ORM. Its own function so tests replace it."""
    return list(Sample_types_context.objects.using(settings.NEXTSEEK_DATABASE)
                .order_by("id").values(*CONTEXT_FIELDS))


def type_context() -> dict[str, dict]:
    """`sample_types_context` rows keyed by `sample_type` exactly as stored.

    Joins to SEEK by code (the type title), never by `sampletype_id`, which differs across
    instances. Empty when the table is absent. On a duplicate code the lowest id wins.
    """
    if not table_exists(settings.NEXTSEEK_DATABASE, "sample_types_context"):
        return {}
    context: dict[str, dict] = {}
    for row in _context_rows():
        code = row.get("sample_type")
        if not code:
            continue
        if code in context:
            logger.warning("sample_types_context has more than one row for %r; keeping the first", code)
            continue
        context[code] = row
    return context


def attribute_meanings() -> dict[str, str]:
    """Global `sample_attributes_unique` meanings keyed by `field_name`, byte-exact.

    Only rows whose scope is exactly '' count, compared in Python because the column's
    collation would also match a padded scope. Empty when the table is absent.
    """
    if not table_exists(settings.NEXTSEEK_DATABASE, "sample_attributes_unique"):
        return {}
    meanings: dict[str, str] = {}
    for field_name, scope, meaning in _rows(
            _dmac(), "SELECT field_name, sample_type, meaning FROM sample_attributes_unique ORDER BY id"):
        field_name, scope, meaning = _text(field_name), _text(scope), _text(meaning)
        if scope != "" or not field_name or meaning is None:
            continue
        meanings.setdefault(field_name, meaning)
    return meanings


def type_clades() -> dict[int, str]:
    """SEEK sample type id to its clade title, from `sample_types_clades` and `clades`.

    Clades join by id inside one instance. A type with no clade is left out; on a duplicate
    type row the lowest id wins. Empty when either table is absent.
    """
    alias = settings.NEXTSEEK_DATABASE
    if not (table_exists(alias, "sample_types_clades") and table_exists(alias, "clades")):
        return {}
    sql = ("SELECT stc.sample_type_id, c.title FROM sample_types_clades stc "
           "LEFT JOIN clades c ON c.id = stc.clade_id "
           "WHERE stc.sample_type_id IS NOT NULL ORDER BY stc.id")
    clades: dict[int, str] = {}
    for type_id, title in _rows(_dmac(), sql):
        title = _text(title)
        if not title:
            continue
        clades.setdefault(int(type_id), title)
    return clades


def deprecated_titles() -> set[str]:
    """Titles of sample types SEEK's description marks retired (`template_catalog.is_deprecated`)."""
    return {t["title"] for t in sample_types() if t["title"] and is_deprecated(t["description"])}


# --- projects, people, investigations, studies -------------------------------------------------

def projects() -> list[dict]:
    """Every SEEK project: `id`, `title`."""
    return [{"id": int(pid), "title": _text(title)}
            for pid, title in _rows(_seek(), "SELECT id, title FROM projects ORDER BY id")]


def memberships() -> list[dict]:
    """One row per (person, project) from `group_memberships` joined to `work_groups`.

    A person can hold several memberships in one project (one per work group). The pair is
    current when any of them has not left (`has_left` NULL or 0, the rule the scope helpers use);
    `has_left` is True only when every membership has left, and `time_left_at` is then the
    latest leaving time.
    """
    sql = ("SELECT gm.person_id, wg.project_id, gm.has_left, gm.time_left_at "
           "FROM group_memberships gm JOIN work_groups wg ON wg.id = gm.work_group_id "
           "WHERE gm.person_id IS NOT NULL AND wg.project_id IS NOT NULL "
           "ORDER BY gm.person_id, wg.project_id, gm.id")
    pairs: dict[tuple[int, int], dict] = {}
    for person_id, project_id, has_left, time_left_at in _rows(_seek(), sql):
        key = (int(person_id), int(project_id))
        left = bool(has_left)
        current = pairs.get(key)
        if current is None:
            pairs[key] = {"person_id": key[0], "project_id": key[1], "has_left": left,
                          "time_left_at": time_left_at if left else None}
        elif not left:
            current["has_left"], current["time_left_at"] = False, None
        elif current["has_left"] and time_left_at is not None and (
                current["time_left_at"] is None or time_left_at > current["time_left_at"]):
            current["time_left_at"] = time_left_at
    return [pairs[key] for key in sorted(pairs)]


def investigation_projects() -> list[dict]:
    """Distinct (`investigation_id`, `project_id`) pairs from `investigations_projects`."""
    sql = ("SELECT DISTINCT investigation_id, project_id FROM investigations_projects "
           "WHERE investigation_id IS NOT NULL AND project_id IS NOT NULL "
           "ORDER BY investigation_id, project_id")
    seen, out = set(), []
    for inv_id, project_id in _rows(_seek(), sql):
        pair = (int(inv_id), int(project_id))
        if pair not in seen:
            seen.add(pair)
            out.append({"investigation_id": pair[0], "project_id": pair[1]})
    return out


def investigations() -> list[dict]:
    """Every SEEK investigation: `id`, `title`, `description`."""
    return [{"id": int(iid), "title": _text(title), "description": _text(desc)}
            for iid, title, desc in _rows(
                _seek(), "SELECT id, title, description FROM investigations ORDER BY id")]


def seek_study_links() -> list[dict]:
    """Distinct (sample, SEEK study) links through `assay_assets` (Sample assets) and `assays`."""
    sql = ("SELECT DISTINCT aa.asset_id, s.id, s.title, s.investigation_id "
           "FROM assay_assets aa "
           "JOIN assays a ON a.id = aa.assay_id "
           "JOIN studies s ON s.id = a.study_id "
           "WHERE aa.asset_type = %s "
           "ORDER BY aa.asset_id, s.id")
    titles: dict[str, str] = {}  # one string object per study title across a million rows
    links = []
    for sample_id, study_id, title, inv_id in _rows(_seek(), sql, ["Sample"]):
        title = _text(title)
        if title is not None:
            title = titles.setdefault(title, title)
        links.append({"sample_id": int(sample_id), "study_id": int(study_id), "study_title": title,
                      "investigation_id": int(inv_id) if inv_id is not None else None})
    return links


def seek_study_links_for(ids: Iterable[int]) -> list[dict]:
    """`seek_study_links` for these sample ids only, ordered by sample id, then study id."""
    links = []
    for chunk in _id_chunks(ids):
        sql = ("SELECT DISTINCT aa.asset_id, s.id, s.title, s.investigation_id "
               "FROM assay_assets aa "
               "JOIN assays a ON a.id = aa.assay_id "
               "JOIN studies s ON s.id = a.study_id "
               f"WHERE aa.asset_type = %s AND aa.asset_id IN ({_placeholders(len(chunk))}) "
               "ORDER BY aa.asset_id, s.id")
        for sample_id, study_id, title, inv_id in _rows(_seek(), sql, ["Sample", *chunk]):
            links.append({"sample_id": int(sample_id), "study_id": int(study_id),
                          "study_title": _text(title),
                          "investigation_id": int(inv_id) if inv_id is not None else None})
    return links


def studies() -> list[dict]:
    """Every SEEK study: `id`, `title`, `investigation_id`."""
    return [{"id": int(sid), "title": _text(title),
             "investigation_id": int(inv_id) if inv_id is not None else None}
            for sid, title, inv_id in _rows(
                _seek(), "SELECT id, title, investigation_id FROM studies ORDER BY id")]


# --- the label maps (spec 7.3) -----------------------------------------------------------------

def internal_assay_links() -> dict[int, tuple[int, str | None]]:
    """SEEK assay id to its internal assay `(id, title)`, the smallest internal id on 1:N.

    `dmac.assays_internal_assays` joined to `dmac.internal_assays`, the lookup of batch
    upload's `neo4j_sync.py::_resolve_internal_assays`; the title is kept as stored, None
    included. Empty when either table is absent.
    """
    alias = settings.NEXTSEEK_DATABASE
    if not (table_exists(alias, "assays_internal_assays") and table_exists(alias, "internal_assays")):
        logger.warning("assays_internal_assays or internal_assays is absent; every assay label "
                       "falls back to its SEEK assay")
        return {}
    sql = ("SELECT ia.id, aia.assay_id, ia.internal_assay_title FROM assays_internal_assays aia "
           "JOIN internal_assays ia ON ia.id = aia.internal_assay_id "
           "WHERE aia.assay_id IS NOT NULL ORDER BY aia.assay_id, ia.id")
    links: dict[int, tuple[int, str | None]] = {}
    for ia_id, assay_id, title in _rows(_dmac(), sql):
        assay_id, ia_id = int(assay_id), int(ia_id)
        current = links.get(assay_id)
        if current is None or ia_id < current[0]:
            links[assay_id] = (ia_id, _text(title))
    return links


def resolved_assay_map() -> dict[int, tuple[int | None, str | None]]:
    """SEEK assay id to `(internal assay id or None, title)`, the label rule's assay map.

    Batch upload's resolution (`neo4j_sync.py::build_derived_from_payloads_from_db`, step 3):
    a mapped assay resolves to `internal_assay_links`' pair; every other SEEK assay falls back
    to `(None, its own title or "")`, the label rule then using the SEEK assay id itself (R6).
    An assay id mapped in dmac but absent from SEEK's `assays` keeps its mapping, as there.
    """
    resolved: dict[int, tuple[int | None, str | None]] = {
        int(assay_id): (None, _text(title) or "")
        for assay_id, title in _rows(_seek(), "SELECT id, title FROM assays ORDER BY id")}
    resolved.update(internal_assay_links())
    return dict(sorted(resolved.items()))


def sops_map() -> dict[int, str | None]:
    """SEEK SOP id to its title as stored: the protocol half of the label rule."""
    return {int(sop_id): _text(title)
            for sop_id, title in _rows(_seek(), "SELECT id, title FROM sops ORDER BY id")}
