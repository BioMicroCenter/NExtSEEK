"""Resolve the rows behind a sample download: the requested samples and, optionally, their lineage.

This is the data path of ``POST /nextseek_api/samples/retrieve/`` (and its deprecated alias
``admin/samples/retrieve/``), the single API behind every sample-download control in the UI.

MySQL is the authority for what a download contains; the graph only says which samples are related.

- Every requested sample the caller may see is exported, whether or not the graph holds it yet. Before,
  a sample the graph sync had not reached answered 404 (none in the graph) or was silently left out.
- For a superuser the graph maps UIDs to primary keys, because SEEK's ``samples.uuid`` has no index:
  each unscoped ``uuid IN (...)`` read is a scan of the whole table (about 1.4 s on the production
  snapshot). A UID the graph cannot map, or maps to a row whose uuid no longer matches, is looked up by
  uuid instead. Every relative the walk finds is mapped the same way, for every caller.
- Lineage is every ancestor and descendant over ``DERIVED_FROM``, through any node, as before. A
  relative that is not a live ``Sample`` node (an ``OrphanSample``) is looked up by uuid, as before.
- Rows are read by primary key in chunks, and for a caller who is not a superuser only from their
  projects: ``projects_samples`` is the authority on scope, never the graph's ``project_ids``. Such a
  caller's UIDs are resolved by one project-scoped statement, not through the graph, so a UID in another
  lab's project costs exactly what an unknown one does (#74).
- Known gap, superusers only: a UID that sits on two MySQL rows (a handful do) of which the graph holds
  one is exported with that one row until the sync reaches the other.
- Any graph failure (an error, the server down, a timeout) exports the requested samples without
  lineage and says so in ``lineage_complete``; it is never a 500 and never silent.

The caller's projects come from ``graph_search.scope.resolve_scope`` (MySQL), not SEEK's
``/people/current``, which had no timeout.
"""

import datetime
import json
import logging
import os
import tempfile
from dataclasses import dataclass

import pandas as pd
from django.conf import settings
from django.db import connections
from django.http import FileResponse
from drf_spectacular.openapi import OpenApiExample
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from neo4j import GraphDatabase, Query
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.endpoint_descriptions import SAMPLE_RETRIEVE_DESC
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable, resolve_scope
from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.models import SampleGroup, SampleRetrieveRequest, SampleRetrieveResponse

log = logging.getLogger(__name__)

COLUMNS = ["id", "sample_type_id", "uuid", "json_metadata"]

# Ids per MySQL statement, as graph_search's hydrate reads them.
MAX_IDS_PER_STATEMENT = 1000

# The graph is asked twice per download (UID lookup, then lineage). Neither may stall a download: past
# these bounds the request degrades to the requested samples and says so. Settings may override them.
NEO4J_CONNECT_TIMEOUT_SECONDS = 5
NEO4J_QUERY_TIMEOUT_SECONDS = 30

RESOLVE_CYPHER = """
UNWIND $uuids AS u
MATCH (s:Sample {uuid: u})
RETURN s.uuid AS uuid, s.id AS id
"""

# Distinct nodes, not paths: the old form (two var-length MATCHes, then collect DISTINCT) enumerated
# every ancestor path times every descendant path per start node, about 3x the db hits on the largest
# local lineages. Intermediate and end nodes carry no label, as before, so the walk still passes
# through an OrphanSample. twin_ids are every live node sharing a relative's uuid: a few UIDs sit on two
# MySQL rows, and the old read by uuid returned both.
LINEAGE_CYPHER = """
CYPHER 25
UNWIND $uuids AS u
MATCH (s:Sample {uuid: u})
CALL (s) {
  MATCH (s)-[:DERIVED_FROM]->+(a) RETURN a AS r
  UNION
  MATCH (s)<-[:DERIVED_FROM]-+(d) RETURN d AS r
  UNION
  RETURN s AS r
}
WITH DISTINCT r
OPTIONAL MATCH (twin:Sample {uuid: r.uuid})
RETURN r.id AS id, r.uuid AS uuid, r:Sample AS live, collect(DISTINCT twin.id) AS twin_ids
"""


class GraphUnavailable(Exception):
    """The graph could not answer; the caller exports without it."""


@dataclass
class RetrieveResult:
    frame: pd.DataFrame  # COLUMNS, one row per sample, ordered by id
    requested_uids: list  # requested UIDs in request order, numeric ids already resolved, deduped
    unresolved_numeric: int  # numeric identifiers that matched no sample
    lineage_complete: bool  # False when lineage was asked for and could not be fully read


def _chunks(items, size=MAX_IDS_PER_STATEMENT):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _placeholders(n):
    return ", ".join(["%s"] * n)


def _cursor():
    return connections[settings.SEEK_DATABASE].cursor()


# -- MySQL -----------------------------------------------------------------------------------------


def _ids_to_uuids(ids):
    """{id: uuid} for these primary keys. Unscoped: the caller's scope is applied to the ids afterwards."""
    found = {}
    with _cursor() as cursor:
        for chunk in _chunks(sorted({int(i) for i in ids})):
            cursor.execute(f"SELECT id, uuid FROM samples WHERE id IN ({_placeholders(len(chunk))})", chunk)
            for sample_id, uuid in cursor.fetchall():
                if sample_id is not None and uuid is not None:
                    found[int(sample_id)] = str(uuid)
    return found


def _uuids_to_ids(uuids):
    """{id: uuid} for every row carrying one of these uuids. A full table scan per statement: the fallback."""
    uuids = sorted({str(u) for u in uuids})
    found = {}
    if not uuids:
        return found
    with _cursor() as cursor:
        for chunk in _chunks(uuids):
            cursor.execute(f"SELECT id, uuid FROM samples WHERE uuid IN ({_placeholders(len(chunk))})", chunk)
            for sample_id, uuid in cursor.fetchall():
                if sample_id is not None and uuid is not None:
                    found[int(sample_id)] = str(uuid)
    return found


def _scoped_uuids_to_ids(uuids, scope):
    """{id: uuid} of the rows carrying these uuids that sit in the caller's projects.

    Used for every requested UID of a caller who is not a superuser, found or not: the statement walks the
    caller's projects (``idx_project_sample``), so its cost depends on those projects and never on whether a UID
    exists in someone else's. Resolving through the graph first would answer a foreign UID fast and an unknown one
    through a full scan, and that difference in time would confirm the foreign sample exists (#74)."""
    uuids = sorted({str(u) for u in uuids})
    found = {}
    if not uuids or not scope.project_ids:
        return found
    projects = [int(p) for p in scope.project_ids]
    with _cursor() as cursor:
        for chunk in _chunks(uuids):
            cursor.execute(
                f"SELECT DISTINCT s.id, s.uuid FROM samples s JOIN projects_samples ps ON s.id = ps.sample_id "
                f"WHERE s.uuid IN ({_placeholders(len(chunk))}) AND ps.project_id IN ({_placeholders(len(projects))})",
                chunk + projects,
            )
            for sample_id, uuid in cursor.fetchall():
                if sample_id is not None and uuid is not None:
                    found[int(sample_id)] = str(uuid)
    return found


def _hydrate(ids, scope):
    """The download rows for these ids, ordered by id, each once, restricted to the caller's projects."""
    ids = sorted({int(i) for i in ids})
    rows = []
    if ids and (scope.is_admin or scope.project_ids):
        projects = [int(p) for p in scope.project_ids]
        with _cursor() as cursor:
            for chunk in _chunks(ids):
                sql = f"SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata FROM samples s WHERE s.id IN ({_placeholders(len(chunk))})"
                params = list(chunk)
                if not scope.is_admin:
                    sql += (
                        " AND EXISTS (SELECT 1 FROM projects_samples ps WHERE ps.sample_id = s.id"
                        f" AND ps.project_id IN ({_placeholders(len(projects))}))"
                    )
                    params += projects
                cursor.execute(sql + " ORDER BY s.id", params)
                rows.extend(cursor.fetchall())
    return pd.DataFrame([tuple(r) for r in rows], columns=COLUMNS)


# -- Neo4j -----------------------------------------------------------------------------------------


def _neo4j_run(cypher, **params):
    config = settings.NEO4J_DATABASE
    connect_timeout = getattr(settings, "SAMPLE_RETRIEVE_NEO4J_CONNECT_TIMEOUT", NEO4J_CONNECT_TIMEOUT_SECONDS)
    query_timeout = getattr(settings, "SAMPLE_RETRIEVE_NEO4J_QUERY_TIMEOUT", NEO4J_QUERY_TIMEOUT_SECONDS)
    try:
        with GraphDatabase.driver(
            config["URI"],
            auth=config["AUTH"],
            connection_timeout=connect_timeout,
            max_transaction_retry_time=connect_timeout,
        ) as driver:
            records, _, _ = driver.execute_query(
                Query(cypher, timeout=query_timeout), params, database_=config.get("NAME"),
            )
        return records
    except Exception as exc:  # noqa: BLE001 (every graph failure degrades the download the same way)
        if isinstance(exc, (ServiceUnavailable, SessionExpired, TransientError)) or "timeout" in str(exc).lower():
            log.warning("sample retrieve: graph unavailable (%s: %s)", type(exc).__name__, exc)
        else:
            # Not the graph being down or slow: a rejected statement (an older Neo4j without CYPHER 25, say) or a
            # bug. Downloads still degrade to the requested samples, but this must not read as a transient blip.
            log.error("sample retrieve: graph query FAILED, lineage is missing from every download (%s: %s)",
                      type(exc).__name__, exc)
        raise GraphUnavailable(str(exc)) from exc


def _graph_uuid_ids(uuids):
    """{id: uuid} of the live Sample nodes carrying these uuids."""
    records = _neo4j_run(RESOLVE_CYPHER, uuids=sorted(set(uuids)))
    return {int(r["id"]): str(r["uuid"]) for r in records if r["id"] is not None and r["uuid"] is not None}


def _graph_lineage(start_uuids):
    """(ids of live Sample relatives, uuids of relatives to look up by uuid, start uuids the graph holds)."""
    records = _neo4j_run(LINEAGE_CYPHER, uuids=sorted(set(start_uuids)))
    by_id, by_uuid = {}, set()
    for r in records:
        uuid = r["uuid"]
        if uuid is None:
            continue
        if r["live"] and r["id"] is not None:
            by_id[int(r["id"])] = str(uuid)
        else:
            by_uuid.add(str(uuid))
        for twin in r.get("twin_ids") or ():
            if twin is not None:
                by_id[int(twin)] = str(uuid)
    seen = set(by_id.values()) | by_uuid
    return by_id, by_uuid, {u for u in start_uuids if u in seen}


# -- the flow --------------------------------------------------------------------------------------


def _verified(candidates):
    """Split graph-mapped {id: uuid} into those MySQL agrees with, and the uuids it does not (stale nodes)."""
    actual = _ids_to_uuids(candidates) if candidates else {}
    good = {i: u for i, u in candidates.items() if actual.get(i) == u}
    stale = {u for i, u in candidates.items() if actual.get(i) != u}
    return good, stale


def retrieve_samples(identifiers, include_tree: bool, scope: Scope) -> RetrieveResult:
    """The rows a download of ``identifiers`` contains for this caller. See the module docstring."""
    requested_uids, numeric_ids = [], []
    for item in identifiers:
        text = str(item or "").strip()
        if not text:
            continue
        # ASCII digits only: "²".isdigit() is True and int() refuses it. Anything else is a UID.
        (numeric_ids if text.isascii() and text.isdigit() else requested_uids).append(text)

    # Numeric identifiers are SEEK sample ids. Any failure counts them all unresolved, as before.
    requested = {}  # id -> uuid, every requested sample MySQL holds, before scope
    unresolved_numeric = 0
    if numeric_ids:
        try:
            by_id = _ids_to_uuids(numeric_ids)
        except Exception:  # noqa: BLE001
            log.exception("sample retrieve: numeric id lookup failed")
            by_id = {}
        for text in numeric_ids:
            uuid = by_id.get(int(text))
            if uuid is None:
                unresolved_numeric += 1
            else:
                requested[int(text)] = uuid
                requested_uids.append(uuid)
    requested_uids = list(dict.fromkeys(requested_uids))

    graph_ok = True
    if scope.is_admin:
        # A superuser sees everything, so how long a lookup takes reveals nothing: the graph's uuid index first,
        # verified against MySQL, and only what it misses by a full uuid scan.
        try:
            mapped = _graph_uuid_ids(requested_uids) if requested_uids else {}
        except GraphUnavailable:
            graph_ok, mapped = False, {}
        good, _ = _verified(mapped)
        requested.update(good)
        unmapped = set(requested_uids) - set(requested.values())
        if unmapped:
            requested.update(_uuids_to_ids(unmapped))
        visible = set(requested)
    else:
        # Everyone else: one scoped statement for every requested UID (numeric ids are UIDs by now).
        requested = _scoped_uuids_to_ids(requested_uids, scope)
        visible = set(requested)
    wanted = set(visible)

    lineage_complete = True
    if include_tree and visible:
        start_uuids = {requested[i] for i in visible}
        try:
            if not graph_ok:
                raise GraphUnavailable("uid lookup already failed")
            relatives, by_uuid, in_graph = _graph_lineage(start_uuids)
        except GraphUnavailable:
            lineage_complete = False
        else:
            # A requested sample the graph does not hold yet has no lineage to offer: exported, flagged.
            if in_graph != start_uuids:
                lineage_complete = False
            good, stale = _verified(relatives)
            wanted.update(good)
            fallback = (by_uuid | stale) - set(good.values())
            if fallback:
                wanted.update(_uuids_to_ids(fallback))

    frame = _hydrate(wanted, scope)
    return RetrieveResult(
        frame=frame,
        requested_uids=requested_uids,
        unresolved_numeric=unresolved_numeric,
        lineage_complete=lineage_complete,
    )


# -- HTTP ------------------------------------------------------------------------------------------
#
# One handler behind two routes: POST samples/retrieve/ (SampleRetrieveViewSet, below) and the deprecated
# POST admin/samples/retrieve/ (AdminSampleViewSet in nextseek_api/views.py). Same body, same response.


# Every response says whether the lineage is whole, JSON and workbook alike, so a UI can warn.
LINEAGE_HEADER = "X-NExtSEEK-Lineage-Complete"
LINEAGE_NOTICE = (
    "The sample graph could not supply the full lineage for this download, so some parent or derived samples "
    "may be missing. The samples you asked for are all here. Try again in a minute."
)
UID_PREFIX_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"


def _caller_scope(request) -> Scope:
    """The caller's projects from MySQL. A caller who maps to no SEEK person, or whose membership cannot be read,
    sees nothing (the old SEEK call answered [] on any failure too)."""
    try:
        return resolve_scope(request.user)
    except ScopeUnavailable:
        log.warning("sample retrieve: the caller maps to no SEEK person; scoped to no projects")
    except Exception:  # noqa: BLE001 (fail closed)
        log.exception("sample retrieve: project membership could not be read; scoped to no projects")
    return Scope(is_admin=False, person_id=None, project_ids=())


def _parse_meta(val):
    try:
        if isinstance(val, str):
            return json.loads(val) if val else {}
        if isinstance(val, dict):
            return val
    except Exception:  # noqa: BLE001
        pass
    return {}


def _record(row):
    return {
        "id": str(row.get("id")) if row.get("id") is not None else None,
        "uuid": str(row.get("uuid")) if row.get("uuid") is not None else None,
        "sample_type_id": row.get("sample_type_id"),
        "metadata": _parse_meta(row.get("json_metadata")),
    }


def _json_body(result: RetrieveResult) -> dict:
    frame = result.frame
    returned_uids = {str(u) for u in frame["uuid"]}
    requested = set(result.requested_uids)
    failed_uids = result.unresolved_numeric + len(requested - returned_uids)

    # Grouped by UID prefix, the same extraction the workbook uses for its sheet names.
    df = frame.copy()
    df["uuid"] = df["uuid"].astype(str)
    df["sample_type"] = df["uuid"].str.extract(UID_PREFIX_RE, expand=False).fillna("UNKNOWN")
    groups = []
    for sample_type, gdf in df.groupby("sample_type"):
        records = [_record(row) for row in gdf.to_dict("records")]
        groups.append(SampleGroup(sample_type=str(sample_type), samples=records, n_samples=len(records)))

    total_samples = int(frame.shape[0])
    body = SampleRetrieveResponse(
        data=groups,
        total_samples=total_samples,
        total_sample_types=len(groups),
        # Every relative returned, ancestors included: the historical name is kept for its callers.
        total_children=max(0, total_samples - len(requested & returned_uids)),
        failed_uids=int(failed_uids),
        lineage_complete=result.lineage_complete,
    )
    return body.model_dump(mode="json", exclude_none=True)


def _workbook_response(result: RetrieveResult):
    from seek.dbtable_sample import DBtable_sample  # deferred: seek imports Django models

    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"download-samples-{datenow}.xlsx"
    # A private temporary file with a random name, never MEDIA_ROOT/download: /media/ serves that tree to anyone,
    # and a per-minute name there was guessable and shared by two exports in the same minute. It is unlinked as
    # soon as it is open, so the response streams it and nothing is left on disk; the download name is unchanged.
    fd, path = tempfile.mkstemp(prefix="download-samples-", suffix=".xlsx")
    os.close(fd)
    try:
        DBtable_sample().sampleRetrievalData(
            result.frame.copy(), path, notice=None if result.lineage_complete else LINEAGE_NOTICE,
        )
        try:
            fh = open(path, "rb")
        except FileNotFoundError:
            return Response({"detail": "Export failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return FileResponse(fh, content_type="application/vnd.ms-excel", as_attachment=True, filename=filename)


def handle_retrieve(request):
    """Validate the body, read the rows, answer JSON or a workbook."""
    # Basic header or session only, never a token (the historical gate, kept).
    basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
    if not basic_tuple:
        return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

    # Legacy field names are still accepted; unknown keys are ignored, as they always were.
    body = request.data or {}
    if isinstance(body, dict):
        output_format = body.get("output_format", "json")
        # Form-encoded callers send "false"/"0"; pydantic coerces both.
        include_tree = body.get("include_tree", True)
        raw_identifiers = body.get("identifiers")
        if raw_identifiers is None:
            raw_identifiers = body.get("retrieval_uids") or body.get("uids") or body.get("retrieval_uids_text") or ""
    else:
        output_format, include_tree, raw_identifiers = "json", True, ""

    if isinstance(raw_identifiers, list):
        identifiers = [str(u).strip() for u in raw_identifiers if str(u).strip()]
    else:
        identifiers = str(raw_identifiers or "").strip().split()

    try:
        req = SampleRetrieveRequest.model_validate(
            {"identifiers": identifiers, "output_format": output_format, "include_tree": include_tree}
        )
    except ValidationError as e:
        return Response({"detail": "Invalid request", "errors": e.errors()}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not req.identifiers:
        return Response({"detail": "identifiers required"}, status=status.HTTP_400_BAD_REQUEST)

    # Project membership is the data-scope boundary (#74). `is_staff` must NOT widen it: the SEEK login sets it
    # on every user. Only is_superuser is unscoped (resolve_scope). A sample outside the caller's projects and
    # a nonexistent UID both answer 404, on purpose: a 403 would confirm the UID is real.
    scope = _caller_scope(request)

    try:
        result = retrieve_samples(req.identifiers, req.include_tree, scope)
    except Exception as e:  # noqa: BLE001 (a MySQL failure; the graph never raises out of retrieve_samples)
        log.exception("sample retrieve failed")
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if result.frame.empty:
        return Response({"detail": "No samples found for provided UIDs"}, status=status.HTTP_404_NOT_FOUND)

    if req.output_format == "json":
        response = Response(_json_body(result), status=status.HTTP_200_OK)
    else:
        response = _workbook_response(result)
    response[LINEAGE_HEADER] = "true" if result.lineage_complete else "false"
    return response


RETRIEVE_EXAMPLES = [
    OpenApiExample(
        name="JSON output (default)",
        value={"identifiers": ["NHP-220630FLY-1-PUB", "TIS-230324BOO-39-PUB"]},
        request_only=True,
    ),
    OpenApiExample(
        name="Excel output with mixed IDs",
        value={"identifiers": ["NHP-220630FLY-1-PUB", "12345"], "output_format": "excel"},
        request_only=True,
    ),
    OpenApiExample(
        name="Only the named samples, no lineage",
        value={"identifiers": ["12345", "67890"], "output_format": "json", "include_tree": False},
        request_only=True,
    ),
    OpenApiExample(
        name="JSON response",
        value={
            "total_samples": 2, "total_sample_types": 2, "total_children": 1, "failed_uids": 0,
            "lineage_complete": True,
            "data": [
                {"sample_type": "NHP", "n_samples": 1, "samples": [
                    {"id": "81271", "uuid": "NHP-220630FLY-1-PUB", "sample_type_id": 41,
                     "metadata": {"UID": "NHP-220630FLY-1-PUB"}}]},
                {"sample_type": "TIS", "n_samples": 1, "samples": [
                    {"id": "81302", "uuid": "TIS-230324BOO-39-PUB", "sample_type_id": 7,
                     "metadata": {"UID": "TIS-230324BOO-39-PUB", "Parent": "NHP-220630FLY-1-PUB"}}]},
            ],
        },
        response_only=True,
        status_codes=["200"],
    ),
]

RETRIEVE_RESPONSES = {
    (200, "application/json"): SampleRetrieveResponse,
    (200, "application/vnd.ms-excel"): OpenApiResponse(
        response=OpenApiTypes.BINARY,
        description="Excel workbook (XLSX): a README sheet, then one sheet per sample type.",
    ),
}


class SampleRetrieveViewSet(viewsets.GenericViewSet):
    """POST /nextseek_api/samples/retrieve/: the sample download API (see the module docstring).

    Registered before ``samples`` in urls.py, or ``samples/retrieve/`` would be the sample proxy's detail route
    for a UID named "retrieve". Authentication is the project default (session or basic; a token is refused by
    the handler), the same as the ``admin/samples/retrieve/`` alias, so the two answer identically.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Sample Retrieval",
        tags=["Samples"],
        request=SampleRetrieveRequest,
        description=SAMPLE_RETRIEVE_DESC,
        responses=RETRIEVE_RESPONSES,
        examples=RETRIEVE_EXAMPLES,
    )
    def create(self, request):
        """Sample download: UIDs and/or SEEK ids in, JSON (default) or an Excel workbook out."""
        return handle_retrieve(request)
