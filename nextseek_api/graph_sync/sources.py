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
  batches.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, Iterator

from django.conf import settings
from django.db import connections

from nextseek_api.batch_upload.helpers import UID_RE, collect_parent_tokens
from nextseek_api.services.template_catalog import is_deprecated
from seek.models import Sample_types_context

logger = logging.getLogger(__name__)

# Rows pulled from a cursor per fetchmany() on the large pair reads.
FETCH_BATCH = 10_000

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
