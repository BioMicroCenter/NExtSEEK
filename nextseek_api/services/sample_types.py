from typing import Optional, List

import hashlib
import json
import time
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse
# from drf_spectacular.types import OpenApiTypes
from django.conf import settings
from django.core.cache import caches
from pydantic import ValidationError
import requests

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.graph_sync import hooks
from nextseek_api.services.sops import _json, _seek_refused, _title_lock, _unconfirmed
from nextseek_api.graph_search.query import _LINEAGE_PATH_SCOPE, _SCOPE_MATCH
from nextseek_api.graph_search.scope import Scope, resolve_scope
import logging


def _caller_scope(request) -> Optional[Scope]:
    """The caller's project scope, as graph_search resolves it, or None when it cannot be resolved.

    A superuser is unscoped; anyone else sees the samples whose ``project_ids`` meet their projects (``is_staff`` is
    never read: the SEEK login sets it on everyone). None, like an empty project set, sees nothing.
    """
    try:
        return resolve_scope(request.user)
    except Exception as exc:  # noqa: BLE001 (ScopeUnavailable or a membership read failure: fail closed)
        logging.getLogger(__name__).warning("sample type lineage: the caller's project scope could not be "
                                            "resolved (%s); returning nothing", type(exc).__name__)
        return None


def _sees_nothing(scope: Optional[Scope]) -> bool:
    return scope is None or (not scope.is_admin and not scope.project_ids)


# The lineage statements. A superuser's are unchanged. For anyone else the sample named in the request and every
# sample on the DERIVED_FROM path must pass graph_search's scope clause, so lineage stops at the edge of the caller's
# projects (docs/superpowers/specs/2026-09-18-graph-cypher-scope.md, decision 3).
_CHILD_TYPES_CYPHER = """
                    MATCH (s: Sample {id: toInteger($id)})
                    MATCH (s)<-[:DERIVED_FROM*1..]-(child)
                    RETURN DISTINCT child.type AS type
                    ORDER BY type
                    """
# One row per visible child type; a single row whose type is null when the sample is visible and has no visible
# child; no row when the sample itself is not visible.
_CHILD_TYPES_SCOPED_CYPHER = f"""
                    MATCH (s: Sample {{id: toInteger($id)}})
                    WHERE {_SCOPE_MATCH}
                    OPTIONAL MATCH lineage_path = (s)<-[:DERIVED_FROM*1..]-(child)
                    WHERE {_LINEAGE_PATH_SCOPE}
                    RETURN DISTINCT child.type AS type
                    ORDER BY type
                    """


def _parents_cypher(with_parent_filter: bool, scoped: bool) -> str:
    """The parents-by-child-types statement; ``scoped`` adds the caller's scope on every node of the path."""
    head = "WITH $types AS input_types, $parent_types AS parent_types" if with_parent_filter \
        else "WITH $types AS input_types"
    parent_where = "WHERE p.type IN parent_types" if with_parent_filter else ""
    if not scoped:
        return f"""
                {head}
                MATCH (p:Sample)
                {parent_where}
                MATCH (c:Sample)-[:DERIVED_FROM*1..]->(p)
                WHERE c.type IN input_types
                WITH p, collect(DISTINCT c.type) AS matched_types, input_types
                WHERE size(matched_types) = size(input_types)
                RETURN p.id AS id, p.uuid AS uuid, p.type AS type
                ORDER BY id
            """
    parent_scope = "any(q IN p.project_ids WHERE q IN $projects)"
    parent_where = f"{parent_where} AND {parent_scope}" if with_parent_filter else f"WHERE {parent_scope}"
    return f"""
                {head}
                MATCH (p:Sample)
                {parent_where}
                MATCH lineage_path = (c:Sample)-[:DERIVED_FROM*1..]->(p)
                WHERE c.type IN input_types AND {_LINEAGE_PATH_SCOPE}
                WITH p, collect(DISTINCT c.type) AS matched_types, input_types
                WHERE size(matched_types) = size(input_types)
                RETURN p.id AS id, p.uuid AS uuid, p.type AS type
                ORDER BY id
            """


from nextseek_api.endpoint_descriptions import (
    SAMPLETYPE_LIST_DESC,
    SAMPLETYPE_FETCH_DESC,
    SAMPLETYPE_CREATE_DESC,
    SAMPLETYPE_UPDATE_DESC,
    SAMPLETYPE_CHILDREN_DESC,
    PARENTS_BY_CHILD_TYPES_DESC,
)
from nextseek_api.services.samples import _resolve_uid_to_seek_id as _resolve_sample_uid_to_seek_id
from nextseek_api.models import (
    SampleTypeListResponse,
    SampleTypeSingleResponse,
    SampleTypeCreateRequest,
    SampleTypeUpdateRequest,
)
from nextseek_api.models import SamplesByChildTypesRequest, SampleUIDItem, SamplesByChildTypesResponse
from nextseek_api.models import ChildSampleTypeItem, ChildSampleTypesResponse
from seek.dbtable_sampletype import DBtable_sampletype
from seek.models import Sample_types

# Neo4j
import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError


class TitleLookupUnavailable(Exception):
    """SEEK's sample_types table could not be read to resolve a title."""


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """The SEEK id a path segment names: digits as they are, else the one type holding exactly that title (as SEEK's
    table compares titles); None for no type or several. Not ``getSampleTypeID``, which cuts a title at its first
    ``_`` (so ``D.HEM_v2`` read as ``D.HEM``) for the legacy search and upload callers that rely on it. Raises
    ``TitleLookupUnavailable`` when the table cannot be read, so a client following a 202 never reads "not found"
    as "not created"."""
    s = str(uid_or_id)
    if s.isdigit():
        return s
    ids = _existing_type_ids(s)
    if ids is None:
        raise TitleLookupUnavailable(s)
    return ids[0] if len(ids) == 1 else None


def _lookup_unavailable() -> HttpResponse:
    return HttpResponse(b'{"errors":[{"title":"Could not look up the title","detail":"The sample types table could '
                        b'not be read. Nothing was changed. Try again later."}]}', status=503,
                        content_type='application/json')


def _find_by_title(title: str) -> str:
    """How a client finds the type a create may have made: the path that matches the exact title, or the list for a
    title of only digits, which that path reads as an id."""
    if str(title).isdigit():
        return ("GET /nextseek_api/sample_types/<title>/ reads a title of only digits as an id, so look for this one "
                "by its title in GET /nextseek_api/sample_types/.")
    return ("GET /nextseek_api/sample_types/<title>/ finds it by its exact title while no other sample type holds "
            "that title.")


def _graph_sync_type_id(data, fallback: Optional[str] = None) -> Optional[str]:
    """The sample type id a graph_sync hook enqueues: the one SEEK returned, else the one the path resolved to."""
    try:
        seek_id = str(((data or {}).get("data") or {}).get("id") or "")
    except Exception:
        seek_id = ""
    if seek_id.isdigit():
        return seek_id
    fallback = str(fallback or "")
    return fallback if fallback.isdigit() else None


# SEEK takes about 25 s to list sample types on prod, past the client's 20 s default; every other call keeps 20 s.
# Under nginx's default 60 s proxy_read_timeout (the host nginx is box config), so the app's JSON 504 answers first.
LIST_TIMEOUT_S = 50

# A create of 70+ attributes outlived the front proxy on dev and prod while SEEK went on to commit it (#140). The
# create waits as long as the list, so the app's own JSON 202 answers before the proxy's 504.
CREATE_TIMEOUT_S = LIST_TIMEOUT_S


def _existing_type_ids(title: str) -> Optional[List[str]]:
    """Ids of the sample types that hold exactly this title (as SEEK's table compares titles), read from SEEK's
    table. None when it cannot be read: the caller must then not act as if the title were free."""
    try:
        rows = DBtable_sampletype("DEFAULT").queryRecordsByConstraint({"title": title})
    except Exception:
        logging.getLogger(__name__).warning("sample_types_proxy: could not read the sample_types table for the "
                                            "title %r", title, exc_info=True)
        return None
    if not isinstance(rows, list):
        logging.getLogger(__name__).warning("sample_types_proxy: the sample_types table read for the title %r gave "
                                            "%s, not rows", title, type(rows).__name__)
        return None
    return [str(r["id"]) for r in rows if r.get("id") is not None]


def _duplicate_title(client, request, title: str) -> Optional[HttpResponse]:
    """409 when a sample type already holds ``title``: SEEK allows two, and a client retrying a create it saw time
    out would make the second. The id is given only to a caller SEEK lets read that type. 503 when the table
    cannot be read, since a create sent then could be that second type."""
    ids = _existing_type_ids(title)
    if ids is None:
        return _not_checked(title, "the sample types table could not be read to check that no other type holds "
                                   "this title")
    if not ids:
        return None
    if not any(resolve_seek_auth(request)):
        # No SEEK login: the create's own 401, so a taken title answers the same as a free one.
        return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
    visible = None
    for type_id in ids:
        try:
            if client.get_sample_type(request, type_id)[1] == 200:
                visible = type_id
                break
        except requests.RequestException:
            continue
    body = {"errors": [{"title": "A sample type with this title already exists",
                        "detail": "Nothing was created. Sample types are found by title, so a second type with the "
                                  "same title would stop resolving."}],
            "title": title}
    if visible:
        body["id"] = visible
    return _json(body, 409)


# How long a create SEEK left unanswered keeps its title in flight: a retry of that title inside it is answered 202
# again and sends nothing, because SEEK may still be committing the first create, which the title check cannot see
# until it does (#141 review). As long as graph_sync holds back the rows of an unconfirmed write.
IN_FLIGHT_S = hooks.UNCONFIRMED_DELAY_S


def _in_flight_key(title: str) -> str:
    """The cache key of ``title``'s unanswered create: stripped and case-blind, as the title lock's."""
    return "sample_type_create_in_flight:" + hashlib.sha256(title.strip().casefold().encode()).hexdigest()


def _in_flight_store():
    """The ``throttle`` cache alias: a file cache every gunicorn worker in the container shares (dmac/settings.py)."""
    return caches["throttle"]


def _mark_in_flight(title: str) -> None:
    """Record that SEEK may still be creating ``title``. A failed write is logged: the title lock still covers two
    creates at once, only a retry after the 202 is then unguarded."""
    try:
        _in_flight_store().set(_in_flight_key(title), int(time.time()), IN_FLIGHT_S)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("sample_types_proxy.create: could not record the unanswered create of "
                                            "%r", title, exc_info=True)


def _in_flight_since(title: str) -> Optional[int]:
    """When an unanswered create of ``title`` was sent, or None. Raises when the store cannot be read."""
    return _in_flight_store().get(_in_flight_key(title))


def _no_seek_login(request) -> Optional[HttpResponse]:
    """The create's own 401 for a caller with no SEEK login, so a title being created answers as a free one."""
    if any(resolve_seek_auth(request)):
        return None
    return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')


def _still_unconfirmed(title: str, detail: str) -> HttpResponse:
    """The 202 of a create this request did not send, because another of the same title may still be in SEEK."""
    return _json({"status": "unconfirmed", "sent": False, "title": title,
                  "detail": f"{detail} Nothing was sent to SEEK for this request. Once SEEK has committed the type, "
                            f"{_find_by_title(title)} Check before you send the create again."}, 202)


def _not_checked(title: str, what: str) -> HttpResponse:
    """503: the guard against a second type with this title could not run, so nothing was sent."""
    return _json({"errors": [{"title": "Could not check the title",
                              "detail": f"Nothing was created: {what}. Try again later."}], "title": title}, 503)


def _seek_unanswered(action: str, seek_id, exc: Exception, timeout_s) -> HttpResponse:
    """The response for a SEEK call that raised instead of answering: 504 on a timeout, 502 otherwise.

    Uncaught, the requests exception reached the caller as a bare 500 (measured on fairdata-dev 2026-09-22:
    SEEK took 23-25 s to serve GET /sample_types/11 against the proxy's 20 s timeout), which says the proxy is
    broken when the truth is that SEEK was too slow or unreachable.
    """
    if isinstance(exc, requests.Timeout):
        logging.getLogger(__name__).warning("sample_types_proxy.%s: SEEK did not answer for sample type %s "
                                            "within %s s", action, seek_id, timeout_s)
        payload = {"errors": [{"title": "Upstream timeout",
                               "detail": f"SEEK did not answer within {timeout_s} s."}]}
        return HttpResponse(json.dumps(payload).encode(), status=504, content_type='application/json')
    logging.getLogger(__name__).warning("sample_types_proxy.%s: SEEK could not be reached for sample type %s: %s",
                                        action, seek_id, type(exc).__name__)
    payload = {"errors": [{"title": "Upstream connection error", "detail": "SEEK could not be reached."}]}
    return HttpResponse(json.dumps(payload).encode(), status=502, content_type='application/json')


_UID_DESC = ("SEEK id (numeric), or the exact title of one sample type (resolved to its id; a title of only digits "
             "is read as an id)")


class SampleTypeProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List SampleTypes",
        description=SAMPLETYPE_LIST_DESC,
        responses={200: SampleTypeListResponse},
        tags=['SampleTypes'],
        examples=[
            OpenApiExample(
                name="List SampleTypes",
                value={
                    "data": [
                        {
                            "id": "12",
                            "type": "sample_types",
                            "attributes": {"title": "Experimental type"},
                            "links": {"self": "/sample_types/12"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/sample_types?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        try:
            body, code, headers, resp = self.client.list_sample_types(request, params=request.query_params,
                                                                      timeout_s=LIST_TIMEOUT_S)
        except requests.RequestException as exc:
            return _seek_unanswered("list", "list", exc, LIST_TIMEOUT_S)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleTypeListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        description=SAMPLETYPE_FETCH_DESC,
        operation_id="Fetch a SampleType",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description=_UID_DESC),
        ],
        responses={
            200: SampleTypeSingleResponse,
            404: OpenApiResponse(description="No sample type holds this exact title, or more than one does"),
            503: OpenApiResponse(description="The sample types table could not be read to resolve the title"),
            502: OpenApiResponse(description="SEEK could not be reached, or answered with something that is not "
                                             "a sample type document"),
            504: OpenApiResponse(description="SEEK did not answer within the proxy's timeout"),
        },
        tags=['SampleTypes'],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            seek_id = _resolve_uid_to_seek_id(uid)
        except TitleLookupUnavailable:
            return _lookup_unavailable()
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')

        try:
            body, code, headers, resp = self.client.get_sample_type(request, str(seek_id))
        except requests.RequestException as exc:
            return _seek_unanswered("retrieve", seek_id, exc, self.client.timeout_s)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleTypeSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create a SampleType",
        description=SAMPLETYPE_CREATE_DESC,
        request=SampleTypeCreateRequest,
        responses={
            201: SampleTypeSingleResponse,
            200: SampleTypeSingleResponse,
            202: OpenApiResponse(description=(
                f"SEEK did not answer within {CREATE_TIMEOUT_S} s: it received the create and may still complete it. "
                "Body: {\"status\": \"unconfirmed\", \"title\": ..., \"detail\": ...}. Look the type up by its exact "
                "title with GET /nextseek_api/sample_types/<title>/ before you send the create again (that path reads "
                "a title of only digits as an id: find such a title in GET /nextseek_api/sample_types/). A create of the same title "
                f"sent while another is waiting for SEEK, or within {IN_FLIGHT_S // 60} minutes of one SEEK left "
                "unanswered, gets this 202 with \"sent\": false and is not sent.")),
            409: OpenApiResponse(description=(
                "A sample type with this title already exists and nothing was created. Body: {\"errors\": [...], "
                "\"title\": ...}, with \"id\" only when the caller can read that type.")),
            422: OpenApiResponse(description="The request is not valid, or SEEK refused it. Nothing was created."),
            502: OpenApiResponse(description=(
                "SEEK could not be reached, answered with an error, or answered with something that is not a sample "
                "type document. The type may still exist: Body: {\"errors\": [...], \"title\": ...}.")),
            503: OpenApiResponse(description=(
                "The check that no other sample type holds this title could not run, so nothing was sent to SEEK. "
                "Body: {\"errors\": [...], \"title\": ...}. Try again later.")),
        },
        tags=['SampleTypes'],
    )
    def create(self, request):
        try:
            payload = SampleTypeCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        title = payload["data"]["attributes"].get("title")
        if not title:
            return self._create(request, payload, title)
        # One create of a title at a time, across every worker: the check below sees only committed rows, so two
        # creates sent together would each find the title free (#141 review).
        with _title_lock(title, kind="sample_type") as held:
            if held is None:
                return _not_checked(title, "the lock that keeps two creates of one title apart could not be taken")
            if not held:
                return _no_seek_login(request) or _still_unconfirmed(
                    title, "Another create of this title is still waiting for SEEK.")
            taken = _duplicate_title(self.client, request, title)
            if taken is not None:
                return taken
            try:
                since = _in_flight_since(title)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).warning("sample_types_proxy.create: could not read the record of "
                                                    "unanswered creates", exc_info=True)
                return _not_checked(title, "the record of creates SEEK has not yet answered could not be read")
            if since is not None:
                return _no_seek_login(request) or _still_unconfirmed(
                    title, f"A create of this title sent less than {IN_FLIGHT_S // 60} minutes ago got no answer "
                           "from SEEK, which may still complete it.")
            return self._create(request, payload, title)

    def _create(self, request, payload, title):
        """SEEK's create; ``create`` holds the title's lock around the title check and this."""
        try:
            body, code, headers, resp = self.client.create_sample_type(request, payload,
                                                                       timeout_s=(10, CREATE_TIMEOUT_S))
        except requests.RequestException as exc:
            # SEEK may have committed the write it did not answer: its rows go in, held back.
            hooks.enqueue("catalog", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)
            if title and not isinstance(exc, requests.ConnectTimeout):
                # A connect timeout sent nothing; anything later may have reached SEEK.
                _mark_in_flight(title)
            return _unconfirmed(title, exc, CREATE_TIMEOUT_S, kind="sample type", path="sample_types",
                                find=_find_by_title(title) if title else None)
        delay = hooks.write_delay(code)
        if delay is not None:
            # A 2xx at once, whatever its body says; a 5xx held back, since SEEK may have committed it.
            hooks.enqueue("catalog", "*", delay_s=delay)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        if code >= 400:
            if code >= 500 and title:
                # SEEK's 5xx may follow a commit (the 502 says the type may exist): a retry waits, as after a 202.
                _mark_in_flight(title)
            return _seek_refused(body, code, headers, title, kind="sample type", path="sample_types",
                                 find=_find_by_title(title) if title else None)

        try:
            data = json.loads(body or b"{}")
            SampleTypeSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        if 200 <= code < 300:
            # A new type is a catalog entry (enqueued above), and its samples take their T_ label from it (spec 5
            # E4, E10).
            type_id = _graph_sync_type_id(data)
            if type_id is not None:
                hooks.enqueue("samples_of_type", f"type:{type_id}")

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a SampleType",
        description=SAMPLETYPE_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description=_UID_DESC)
        ],
        request=SampleTypeUpdateRequest,
        responses={
            200: SampleTypeSingleResponse,
            503: OpenApiResponse(description="The sample types table could not be read to resolve the title"),
        },
        tags=['SampleTypes'],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = SampleTypeUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        try:
            path_id = _resolve_uid_to_seek_id(uid) if uid is not None else None
        except TitleLookupUnavailable:
            return _lookup_unavailable()
        body_id = payload.get('data', {}).get('id')

        seek_id: Optional[str] = None
        if path_id is not None:
            seek_id = path_id
            if body_id is not None and str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json')
            if body_id is None:
                payload['data']['id'] = str(seek_id)
        else:
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)

        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')

        try:
            body, code, headers, resp = self.client.update_sample_type(request, str(seek_id), payload)
        except requests.RequestException:
            # SEEK may have committed the write it did not answer: its rows go in, held back.
            hooks.enqueue("catalog", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)
            hooks.enqueue("samples_of_type", f"type:{seek_id}", delay_s=hooks.UNCONFIRMED_DELAY_S)
            raise
        delay = hooks.write_delay(code)
        if delay is not None:
            # A 2xx at once, whatever its body says; a 5xx held back, since SEEK may have committed it.
            hooks.enqueue("catalog", "*", delay_s=delay)
            # SEEK re-saves a renamed type's samples in a later job: their sync always waits.
            hooks.enqueue("samples_of_type", f"type:{seek_id}", delay_s=hooks.UNCONFIRMED_DELAY_S)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleTypeSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


class SampleTypeChildrenViewSet(viewsets.GenericViewSet):
    """Return unique child sample type titles for a given Sample (by id or UID)."""
    permission_classes = [IsAuthenticated]
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="Get Child Sample Types",
        description=SAMPLETYPE_CHILDREN_DESC,
        parameters=[
            OpenApiParameter(
                name='uid',
                type=str,
                location=OpenApiParameter.PATH,
                description='SEEK id (numeric) or Sample UID (string)'
            )
        ],
        responses={200: ChildSampleTypesResponse},
        examples=[
            OpenApiExample(
                name="Sample response",
                value={
                    "total": 3,
                    "child_types": [
                        {"id": "5", "title": "D.IMG", "description": "Imaging data samples"},
                        {"id": "12", "title": "D.SEQ", "description": "Sequencing data samples"},
                        {"id": "8", "title": "TIS", "description": "Tissue samples"}
                    ],
                    "msg": "OK"
                }
            ),
        ],
        tags=['Samples'],
    )
    @action(detail=True, methods=["get"], url_path="child_types")
    def child_types(self, request, uid=None, pk=None):
        # Auth gate: allow BASIC header or session; otherwise 401
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Resolve uid/id to numeric SEEK id
        path_value = uid or pk
        seek_id = _resolve_sample_uid_to_seek_id(str(path_value))
        if seek_id is None:
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)

        # A sample outside the caller's projects reads as one that does not exist.
        scope = _caller_scope(request)
        if _sees_nothing(scope):
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)
        if scope.is_admin:
            query, query_params = _CHILD_TYPES_CYPHER, {"id": int(seek_id)}
        else:
            query = _CHILD_TYPES_SCOPED_CYPHER
            query_params = {"id": int(seek_id), "projects": list(scope.project_ids)}

        NEO4J_DATABASE = settings.NEO4J_DATABASE
        try:
            with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
                records, summary, keys = driver.execute_query(
                    query,
                    **query_params,
                    database_=NEO4J_DATABASE['NAME']
                )
                # Deduplicate and sort types (Neo4j DISTINCT may not work with variable-length paths)
                types_list = sorted(set(r["type"] for r in records if r["type"] is not None))
        except Neo4jError:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)
        if not scope.is_admin and not records:
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)

        # Look up sample type details (id, description) from database
        type_details: dict = {}
        desc_lookup_failed = False
        n_failed = 0
        if types_list:
            try:
                qs = Sample_types.objects.filter(title__in=types_list).values('id', 'title', 'description')
                for row in qs:
                    type_details[row['title']] = {
                        'id': str(row['id']),
                        'description': row.get('description')
                    }
                n_failed = len(types_list) - len(type_details)
            except Exception:
                desc_lookup_failed = True

        # Build response items using Pydantic models
        items = []
        for title in types_list:
            details = type_details.get(title, {})
            items.append(ChildSampleTypeItem(
                id=details.get('id', ''),
                title=title,
                description=details.get('description')
            ))

        # Build status message
        if n_failed > 0:
            status_msg = f"OK, but details not available for {n_failed} sample type(s)."
        elif desc_lookup_failed:
            status_msg = "OK, but sample type details not available."
        else:
            status_msg = "OK"

        response = ChildSampleTypesResponse(
            total=len(items),
            child_types=items,
            msg=status_msg
        )
        return Response(response.model_dump(), status=status.HTTP_200_OK)


class SamplesByChildTypesViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Get samples with children of specified sample types",
        description=PARENTS_BY_CHILD_TYPES_DESC,
        request=SamplesByChildTypesRequest,
        responses={200: SamplesByChildTypesResponse},
        examples=[
            OpenApiExample(
                name="By child type titles",
                value={
                    "child_sample_types": ["D.IMG", "TIS"]
                },
            ),
            OpenApiExample(
                name="With parent type filter",
                value={
                    "child_sample_types": ["D.IMG", "D.SEQ"],
                    "parent_sample_type_filters": ["NHP"]
                },
            ),
        ],
        tags=["SampleTypes"],
    )
    @action(detail=False, methods=["post"], url_path="parents_by_child_types")
    def parents_by_child_types(self, request):
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Validation using Pydantic V2
        try:
            req = SamplesByChildTypesRequest.model_validate(request.data)
        except ValidationError as e:
            return Response({"errors": [{"title": "Invalid request", "detail": e.errors()}]}, status=422)

        # Normalize child sample types (titles or IDs -> titles)
        child_titles: List[str] = []
        if req.child_sample_types:
            for item in req.child_sample_types:
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                    # Resolve ID to title via ORM
                    try:
                        st = Sample_types.objects.filter(id=int(item)).values_list('title', flat=True).first()
                        if st:
                            child_titles.append(st)
                    except Exception:
                        pass
                elif isinstance(item, str):
                    child_titles.append(item)

        input_types = sorted({t.strip().upper() for t in child_titles if t and isinstance(t, str)})
        if not input_types:
            return Response({"errors": [{"title": "Invalid request", "detail": "Provide at least one valid child sample type."}]}, status=422)

        # Normalize parent sample type filters (titles or IDs -> titles)
        parent_types: List[str] = []
        if req.parent_sample_type_filters:
            for item in req.parent_sample_type_filters:
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                    # Resolve ID to title via ORM
                    try:
                        st = Sample_types.objects.filter(id=int(item)).values_list('title', flat=True).first()
                        if st:
                            parent_types.append(st.strip().upper())
                    except Exception:
                        pass
                elif isinstance(item, str):
                    parent_types.append(item.strip().upper())

        # Data scope. This endpoint returns real sample UUIDs: a caller who is not a superuser reads only parents,
        # children and path samples in their own projects, so a visible parent is never returned because of a
        # descendant the caller cannot see.
        scope = _caller_scope(request)
        records = []
        if not _sees_nothing(scope):
            cypher = _parents_cypher(with_parent_filter=bool(parent_types), scoped=not scope.is_admin)
            query_params = {"types": input_types}
            if parent_types:
                query_params["parent_types"] = parent_types
            if not scope.is_admin:
                query_params["projects"] = list(scope.project_ids)
            NEO4J_DATABASE = settings.NEO4J_DATABASE
            try:
                with GraphDatabase.driver(NEO4J_DATABASE["URI"], auth=NEO4J_DATABASE["AUTH"]) as driver:
                    records, summary, keys = driver.execute_query(
                        cypher,
                        **query_params,
                        database_=NEO4J_DATABASE["NAME"],
                    )
            except Neo4jError:
                return Response({"errors": [{"title": "Invalid upstream response"}]},
                                status=status.HTTP_502_BAD_GATEWAY)
            except Exception:
                return Response({"errors": [{"title": "Invalid upstream response"}]},
                                status=status.HTTP_502_BAD_GATEWAY)

        # Collect unique sample types from results for description lookup
        unique_types = set()
        for r in records:
            rtype = r.get("type")
            if rtype:
                unique_types.add(rtype)

        # Lookup sample type descriptions from database
        desc_lookup: dict = {}
        desc_lookup_failed = False
        n_failed = 0
        if unique_types:
            try:
                qs = Sample_types.objects.filter(title__in=unique_types).values('title', 'description')
                for row in qs:
                    desc_lookup[row['title']] = row.get('description')
                n_failed = len(unique_types) - len(desc_lookup)
            except Exception:
                desc_lookup_failed = True  # Continue without descriptions if lookup fails

        # Build response items using Pydantic models
        items = []
        for r in records:
            rid = r["id"]
            ruid = r["uuid"]
            rtype = r.get("type")
            if rid is not None and ruid is not None:
                items.append(SampleUIDItem(
                    id=str(rid),
                    uuid=str(ruid),
                    sample_type=rtype or "",
                    sample_type_description=desc_lookup.get(rtype) if rtype else None
                ))
        if n_failed > 0:
            status_msg = f"OK, but sample type descriptions not available for {n_failed} sample types."
        elif desc_lookup_failed:
            status_msg = "OK, but sample type descriptions not available."
        else:
            status_msg = "OK"
        response = SamplesByChildTypesResponse(
            total=len(items),
            samples=items,
            msg=status_msg
        )
        return Response(response.model_dump(), status=status.HTTP_200_OK)
