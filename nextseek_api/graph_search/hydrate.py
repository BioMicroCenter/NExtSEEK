"""Hydrate one page of graph_search ids into advanced_search-shaped rows from SEEK's MySQL.

The graph holds no ``json_metadata`` string, so the page the query builder returns (ids only) is read
back from ``seek_production`` by primary key: the columns ``advanced_search`` returns, in the order
the ids were given. Two statements per chunk of ids, both with bound parameters only:

- the sample rows, with the sample type title and the contributor's first name;
- the assay titles, joined in Python into the comma-separated ``assays`` string. advanced_search uses
  ``GROUP_CONCAT``, whose default 1,024-byte limit truncates silently; this does not.

Row shape, as ``seek.sample.core.reformatDataForClient`` and the advanced_search view render it,
without the HTML anchors (``idlink``, ``idurl``, ``uid``): ``created_at`` is ``str()`` of the
datetime, ``json_metadata`` is parsed to a dict (``{}`` when it is empty or not a JSON object),
``assays`` is None when the sample is in no assay, and ``attributeValue`` is ``""``.
"""

import json
import logging

from django.conf import settings
from django.db import connections

log = logging.getLogger(__name__)

ROWS_SQL = (
    "SELECT A.id, A.title, A.sample_type_id, B.title AS sample_type, A.uuid, A.contributor_id, "
    "C.first_name, A.created_at, A.json_metadata "
    "FROM samples A "
    "LEFT JOIN sample_types B ON A.sample_type_id = B.id "
    "LEFT JOIN people C ON A.contributor_id = C.id "
    "WHERE A.id IN ({placeholders})"
)

ASSAYS_SQL = (
    "SELECT D.asset_id, E.title FROM assay_assets D JOIN assays E ON E.id = D.assay_id "
    "WHERE D.asset_type = 'Sample' AND D.asset_id IN ({placeholders}) "
    "ORDER BY D.asset_id, E.title"
)

# A page is at most 1,000 ids; larger lists (a caller other than the endpoint) are read in chunks.
MAX_IDS_PER_STATEMENT = 1000


def _metadata(raw, sample_id) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        log.warning("graph_search hydrate: sample %s has unparseable json_metadata", sample_id)
        return {}
    if not isinstance(value, dict):
        log.warning("graph_search hydrate: sample %s json_metadata is not an object", sample_id)
        return {}
    return value


def _row(sample, assay_titles) -> dict:
    (sample_id, title, sample_type_id, sample_type, uuid, contributor_id,
     first_name, created_at, json_metadata) = sample
    return {
        "id": sample_id,
        "title": title,
        "uuid": uuid,
        "sample_type_id": sample_type_id,
        "contributor_id": contributor_id,
        "created_at": None if created_at is None else str(created_at),
        "json_metadata": _metadata(json_metadata, sample_id),
        "sample_type": sample_type,
        "first_name": first_name,
        "assays": ",".join(assay_titles) if assay_titles else None,
        "attributeValue": "",
    }


def _read_chunk(cursor, ids: list[int]) -> dict[int, dict]:
    placeholders = ", ".join(["%s"] * len(ids))
    cursor.execute(ROWS_SQL.format(placeholders=placeholders), list(ids))
    samples = cursor.fetchall()
    cursor.execute(ASSAYS_SQL.format(placeholders=placeholders), list(ids))
    assays: dict[int, list[str]] = {}
    for asset_id, assay_title in cursor.fetchall():
        if assay_title is not None:
            assays.setdefault(int(asset_id), []).append(assay_title)
    return {int(s[0]): _row(s, assays.get(int(s[0]))) for s in samples}


def hydrate(ids: list[int]) -> list[dict]:
    """advanced_search-shaped rows for ``ids``, in the given order.

    Duplicate ids are read once (first position kept). An id with no row in MySQL is dropped and
    logged. An empty list issues no SQL.
    """
    order: list[int] = []
    seen: set[int] = set()
    for raw in ids:
        sample_id = int(raw)
        if sample_id not in seen:
            seen.add(sample_id)
            order.append(sample_id)
    if not order:
        return []

    found: dict[int, dict] = {}
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        for start in range(0, len(order), MAX_IDS_PER_STATEMENT):
            found.update(_read_chunk(cursor, order[start:start + MAX_IDS_PER_STATEMENT]))

    missing = [i for i in order if i not in found]
    if missing:
        log.warning("graph_search hydrate: %d of %d ids have no row in MySQL and were dropped: %s",
                    len(missing), len(order), missing[:20])
    return [found[i] for i in order if i in found]
