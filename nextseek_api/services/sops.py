from typing import Any, Dict, List, Optional

import json
import hashlib
import logging
import datetime
import math
import re
import threading
from contextlib import contextmanager
from functools import partial
from time import monotonic
from types import SimpleNamespace

import requests

from django.conf import settings
from django.db import DatabaseError, connections
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse
from drf_spectacular.types import OpenApiTypes

from nextseek_api.helpers import SOP_CREATE_TIMEOUT_S, SeekAPIClient, resolve_seek_auth
from nextseek_api.graph_sync import hooks
from nextseek_api.endpoint_descriptions import (
    SOP_LIST_DESC,
    SOP_FETCH_DESC,
    SOP_CREATE_DESC,
    SOP_UPDATE_DESC,
    SOP_DOWNLOAD_DESC,
    SOP_DELETE_DESC,
)
from nextseek_api.models import (
    SopCreateRequest,
    SopUpdateRequest,
    SopSingleResponse,
    SopMultiResponse,
    SopListResponse,
    SopDownloadRequest,
    SeekContentBlobPathParams,
    SeekContentBlobDownloadRequest,
    ContentBlobUploadResponse,
    ContentBlobUploadStatus,
)
from nextseek_api.services.content_blobs import (
    _resolve_uid_to_seek_id,
    HasOwnSeekLogin,
    caller_cannot_view,
    download_single,
    download_batch,
    upload_content_blobs,
    check_unmatched_files,
    auto_populate_content_blobs,
)

log = logging.getLogger(__name__)


# [P.<INST>-]<YYMMDD>-V<n>_<name>: the title a SOP is cited by (Protocol values on samples name it).
_SCHEME_TITLE = re.compile(r"^(P\.[A-Z]{3}-)?\d{6}-V\d+_")


# Every 401 of this module, byte for byte: a taken title must answer exactly as a free one.
_AUTH_REQUIRED = b'{"detail":"Authentication required"}'


def _json(obj: dict, status: int) -> HttpResponse:
    return HttpResponse(json.dumps(obj).encode(), status=status, content_type='application/json')


def _existing_sop_ids(title: str) -> List[str]:
    """Ids of the SOPs that already hold this exact title, read from SEEK's table. [] when it cannot be read."""
    try:
        from seek.dbtable_sops import DBtable_sops
        rows = DBtable_sops("DEFAULT").queryRecordsByConstraint({"title": title})
        return [str(r["id"]) for r in rows if r.get("id") is not None] if isinstance(rows, list) else []
    except Exception:
        log.warning("sops_proxy.create: could not read the sops table for the title check", exc_info=True)
        return []


def _duplicate_title(client, request, title: str, kind: str = "SOP") -> Optional[HttpResponse]:
    """409 when a SOP already holds ``title``. The id is given only to a caller SEEK lets read that SOP."""
    ids = _existing_sop_ids(title)
    if not ids:
        return None
    if not any(resolve_seek_auth(request)):
        # No SEEK login: the create's own 401, so a taken title answers the same as a free one.
        return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
    visible = None
    for sop_id in ids:
        try:
            if client.get_sop(request, sop_id)[1] == 200:
                visible = sop_id
                break
        except requests.RequestException:
            continue
    body = {"errors": [{"title": f"A {kind} with this title already exists",
                        "detail": "Nothing was created. Titles are the UIDs other records cite, so a second SOP "
                                  "with the same title would stop resolving."}],
            "title": title}
    if visible:
        body["id"] = visible
    return _json(body, 409)


def _unconfirmed(title: str, exc: Exception, timeout_s, *, created=(), not_sent=(), kind="SOP", path="sops") -> HttpResponse:
    """The create got no answer from SEEK, so the record may exist. A read timeout is 202 (the proxy cannot say it
    failed, and a client that retries would duplicate); a lost connection is 502. Both carry the title so a client
    can look the record up before it sends anything again."""
    extra: Dict[str, Any] = {"title": title}
    if created:
        extra["created"] = list(created)
    if not_sent:
        extra["not_sent"] = list(not_sent)
    sent_note = " The file was not sent." if not_sent else ""
    find = (f" GET /nextseek_api/{path}/<title>/ finds it while no other {kind} has that title; a {kind} found that way "
            f"is finished by sending the file with PATCH /nextseek_api/{path}/<id>/." if kind == "SOP" else
            f" GET /nextseek_api/{path}/<title>/ finds it while no other {kind} has that title.")
    if isinstance(exc, requests.ReadTimeout):
        log.warning("%s_proxy.create: SEEK did not answer the create of %r within %s s", path, title, timeout_s)
        detail = (f"SEEK did not answer within {timeout_s} s. It received the create and may still complete it."
                  f"{sent_note}{find} Check before you send it again.")
        return _json({"status": "unconfirmed", "detail": detail, **extra}, 202)
    log.warning("%s_proxy.create: SEEK could not be reached for the create of %r: %s", path, title, exc)
    detail = f"SEEK could not be reached, so the {kind} may or may not have been created.{sent_note}{find}"
    return _json({"errors": [{"title": "Upstream connection error", "detail": detail}], **extra}, 502)


def _seek_refused(body, code: int, headers, title: str, *, created=(), not_sent=(), kind="SOP", path="sops") -> HttpResponse:
    """SEEK answered the create with an error. A 5xx becomes JSON that says the record may exist; a 4xx is SEEK
    refusing, forwarded with SEEK's status (as JSON when SEEK's own body is not, such as a Rails error page)."""
    ct = (headers.get('Content-Type') or 'application/json')
    extra: Dict[str, Any] = {}
    if created:
        extra["created"] = list(created)
    if not_sent:
        extra["not_sent"] = list(not_sent)
    if code >= 500:
        detail = (f"SEEK answered {code}. The {kind} may still have been created: GET /nextseek_api/{path}/<title>/ "
                  "finds it while no other record has that title. Check before you send it again.")
        return _json({"errors": [{"title": f"SEEK could not create the {kind}", "detail": detail}],
                      "seek_status": code, "title": title, **extra}, 502)
    if 'json' not in ct.lower():
        return _json({"errors": [{"title": f"SEEK refused the {kind}", "detail": f"SEEK answered {code}."}],
                      "seek_status": code, "title": title, **extra}, code)
    if extra:
        try:
            merged = json.loads(body or b"{}")
            if isinstance(merged, dict):
                return _json({**merged, **extra}, code)
        except ValueError:
            pass
    return HttpResponse(body, status=code, content_type=ct)


def _within(deadline: float, cap: float):
    """(connect, read) seconds for a SEEK call that must end by the create's ``deadline``, at most its own ``cap``;
    None once the deadline has passed, so no new call starts."""
    left = deadline - monotonic()
    return (min(10, left), min(cap, left)) if left > 0 else None


def _time_up(results: list, created: list, not_sent: list, *, kind="SOP", path="sops") -> HttpResponse:
    """The create stopped at its request limit: what was created, and the files never sent to SEEK."""
    finish = (f"A file in not_sent that a {kind} in created names as its file is finished by sending it with PATCH "
              f"/nextseek_api/{path}/<id>/; " if kind == "SOP" else
              f"A {kind} in created that names a file in not_sent was created without that file; ")
    detail = (f"The request reached its {SOP_CREATE_TIMEOUT_S} s limit, so the files in not_sent were not sent to "
              f"SEEK. {finish}send the others again in a new request.")
    body: Dict[str, Any] = {"errors": [{"title": "Time limit reached", "detail": detail}],
                            "created": created, "not_sent": not_sent}
    if results:
        body["data"] = results
    return _json(body, 207 if created else 504)


_NOT_FOUND = b'{"errors":[{"title":"SOP not found"}]}'

# Rows per read of SEEK's samples in the in-use check: about 5 MB at the seed's ~2.4 KB of metadata a row.
_CITING_PAGE = 2000


def _samples_citing(sop_id: str, title: str) -> int:
    """How many samples name this SOP as their Protocol: by this instance's ``/sops/<id>`` URL, or by its title
    (stripped and case-blind, as the graph's title key), under batch upload's one Protocol rule. A title another SOP
    also holds does not count: the graph resolves it to neither now and to the one left after the delete
    (operator 2026-10-13). Raises when the read fails."""
    from django.db.models import Q
    from seek.models import Samples, Sops
    from nextseek_api.batch_upload.helpers import parse_protocol_value
    from nextseek_api.graph_sync.labels import protocol_value_of

    key = title.strip().casefold()
    # SEEK's whole sops table, as the graph's own title index reads it (sources.sops_map).
    by_title = not any(str(i) != str(sop_id) and t is not None and t.strip().casefold() == key
                       for i, t in Sops.objects.values_list("id", "title"))
    q = Q(json_metadata__contains=f"/sops/{sop_id}")
    if by_title:
        # SQL narrows by plain text: the longest run of the title that JSON never escapes (Rails escapes & < >, and
        # some writers every non-ASCII letter); an empty run matches every row. Python then decides.
        piece = max(re.split(r'[^\x20-\x7e]|[&<>"\\]', title.strip()), key=len)
        q |= Q(json_metadata__icontains=piece)
    rows = Samples.objects.filter(q).order_by("id")
    # Read in id pages: MySQL's driver buffers a whole result, and a title as broad as "Protocol" (the key name)
    # matches every sample. ponytail: still one pass over every matching row per DELETE; a protocol column on
    # SEEK's samples, or the graph, is the upgrade if a broad title is slow on a box.
    count, last = 0, 0
    while True:
        page = list(rows.filter(id__gt=last).values_list("id", "json_metadata")[:_CITING_PAGE])
        for _, meta in page:
            ref = parse_protocol_value(protocol_value_of(meta))
            if str(ref.sop_id) == str(sop_id) or (by_title and ref.title is not None
                                                  and ref.title.strip().casefold() == key):
                count += 1
        if len(page) < _CITING_PAGE:
            return count
        last = page[-1][0]


def _in_use_body(seek_id: str, title: str) -> dict:
    """The DELETE's 409 when samples name the SOP as their Protocol (its API example is built from this too). No
    count: some of those samples may be in projects the caller cannot see (operator 2026-10-13)."""
    return {"errors": [{"title": "SOP in use",
                         "detail": "Samples name this SOP as their Protocol. Nothing was deleted: point those samples "
                                   "at another SOP first."}],
            "id": seek_id, "title": title}


# NExtSEEK's own SOP deletes go one at a time per title key, so two deletes of the two SOPs that share a cited
# title cannot each see the other still holding it. Whole seconds: MySQL's GET_LOCK wait.
_TITLE_LOCK_WAIT_S = 10
# ponytail: where the seek database is not MySQL (the SQLite test lane) one process lock stands in for every title.
_TITLE_LOCK_LOCAL = threading.Lock()


@contextmanager
def _title_lock(title: str):
    """Hold the delete lock of ``title``'s key (stripped and case-blind, as the graph's) for the ``with`` block. Yields
    False when another delete held it past the wait or it could not be asked for: the block must then delete nothing.
    MySQL: GET_LOCK on the seek connection, so every worker process shares it."""
    conn = connections["seek"]
    if conn.vendor != "mysql":
        got = _TITLE_LOCK_LOCAL.acquire(timeout=_TITLE_LOCK_WAIT_S)
        release = _TITLE_LOCK_LOCAL.release
    else:
        name = "nextseek_sop_title_" + hashlib.sha256(title.strip().casefold().encode()).hexdigest()[:40]
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT GET_LOCK(%s, %s)", [name, _TITLE_LOCK_WAIT_S])
                got = (cur.fetchone() or [None])[0] == 1
        except DatabaseError:
            log.warning("sops_proxy.destroy: could not ask for the delete lock %s", name, exc_info=True)
            got = False

        def release():
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", [name])
            except DatabaseError:
                log.warning("sops_proxy.destroy: could not release %s (MySQL does when the session ends)", name)
    try:
        yield got
    finally:
        if got:
            release()


def _not_checked(seek_id: str) -> HttpResponse:
    return _json({"errors": [{"title": "Could not check whether samples use this SOP",
                              "detail": "Nothing was deleted. Try again later."}], "id": seek_id}, 503)


def _sop_blob_rows(ids: List[str]) -> list:
    """SEEK's content_blobs rows for these SOP ids: one MySQL read, no SEEK call per SOP."""
    from seek.dbtable_content_blobs import DBtable_content_blobs
    from django.db.models import Q
    return DBtable_content_blobs("DEFAULT").queryRecordsCustom(
        Q(asset_type="Sop", asset_id__in=[int(i) for i in ids])) or []


def _add_file_names(data: dict) -> bool:
    """Put each listed SOP's file names (its latest version's blobs) on the SEEK index ``data``. False and the list
    untouched when the read fails: the list is still true without them."""
    items = data.get("data") or []
    ids = [str(i["id"]) for i in items if str(i.get("id", "")).isdigit()]
    if not ids:
        return False
    try:
        rows = _sop_blob_rows(ids)
    except Exception:
        log.warning("sops_proxy.list: could not read content_blobs for the listed SOPs", exc_info=True)
        return False
    latest: Dict[str, int] = {}
    for r in rows:
        key = str(r.get("asset_id"))
        latest[key] = max(latest.get(key, 0), int(r.get("asset_version") or 0))
    for item in items:
        item.setdefault("attributes", {})["content_blobs"] = [
            {"original_filename": r.get("original_filename"), "content_type": r.get("content_type")}
            for r in rows
            if str(r.get("asset_id")) == str(item.get("id")) and int(r.get("asset_version") or 0) == latest[str(item.get("id"))]
        ]
    return True


def _blob_status(exc_or_name, error: str):
    return ContentBlobUploadStatus(blob_id="", original_filename=exc_or_name, status="failed", error=error)


class SopProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, HasOwnSeekLogin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    client = SeekAPIClient()
    # Ensure router renders /sops/{uid}
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /sops
    @extend_schema(
        operation_id="List SOPs",
        description=SOP_LIST_DESC,
        responses={200: SopListResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="List Sops",
                value={
                    "data": [
                        {
                            "id": "131",
                            "type": "sops",
                            "attributes": {"title": "This Sop",
                                           "content_blobs": [{"original_filename": "this_sop.pdf",
                                                              "content_type": "application/pdf"}]},
                            "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}},
                            "links": {"self": "/sops/131"},
                            "meta": {}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/sops?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_sops(request, params=request.query_params)
        if code == 401:
            return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')

        if code >= 400:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        # Validate response
        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        if _add_file_names(data):
            return HttpResponse(json.dumps(data).encode(), status=code, content_type=ct)
        return HttpResponse(body, status=code, content_type=ct)

    # GET /sops/{uid}
    @extend_schema(
        description=SOP_FETCH_DESC,
        operation_id="Fetch a SOP",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)'),
            OpenApiParameter(name='version', type=int, location=OpenApiParameter.QUERY, required=False, description='Optional SOP version to fetch')
        ],
        responses={200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Get Sop",
                value={
                    "data": {
                        "id": "132",
                        "type": "sops",
                        "attributes": {"title": "A Maximal SOP"},
                        "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}},
                        "links": {"self": "/sops/132"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        # Optional version query param (proxied as-is by client layer if SEEK supports it)
        _ = request.query_params.get('version')
        seek_id = _resolve_uid_to_seek_id(uid, "sops")
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sop(request, seek_id)
        if code == 401:
            return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
        if code in (403, 404):
            # A SOP the caller may not see answers as one that does not exist (SEEK answers 403 for it).
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        if code >= 400:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /sops
    @extend_schema(
        operation_id="Create a SOP",
        description=SOP_CREATE_DESC,
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "One or more files to upload as content blobs.",
                    },
                    "metadata": {
                        "type": "string",
                        "description": (
                            'JSON string with SOP metadata. Minimum: '
                            '{"data":{"type":"sops","attributes":{"title":"..."},'
                            '"relationships":{"projects":{"data":[{"id":"...","type":"projects"}]}}}}'
                            ' — content_blobs are auto-generated from uploaded files if omitted.'
                        ),
                    },
                    "institution": {
                      "type": "string",
                      "description": (
                          '3-letter institution code.'
                          'Example: "WHI"'
                      )  
                    },
                },
                "required": ["metadata"],
            },
            "application/json": SopCreateRequest,
        },
        responses={
            200: SopMultiResponse,
            201: SopMultiResponse,
            202: OpenApiResponse(description=(
                "SEEK did not answer before the request's 120 s limit: it received the create and may still complete it. "
                "Body: {\"status\": \"unconfirmed\", \"title\": <the title the server assigned>, \"detail\": ...}, "
                "plus \"created\" and \"not_sent\" when earlier files of the same request were created. Look the SOP "
                "up by that title before you send the create again.")),
            207: OpenApiResponse(description=(
                "SOPs created, but at least one file upload failed. Body: \"data\" as for 201, plus \"created\": "
                "each SOP's id, title, \"file\" (the file it was made for) and \"blobs\" (the upload result per "
                "file). Also when the request reached its 120 s limit after creating some SOPs: then \"errors\", "
                "\"created\" and \"not_sent\" (the files never sent to SEEK).")),
            409: OpenApiResponse(description=(
                "A SOP with this title already exists and nothing was created. Body: {\"errors\": [...], \"title\": ...}, "
                "with \"id\" only when the caller can read that SOP.")),
            422: OpenApiResponse(description="The metadata is not valid, or SEEK refused it. Nothing was created."),
            502: OpenApiResponse(description=(
                "SEEK could not be reached, answered with an error, or answered without a record id. The SOP may "
                "still exist: Body: {\"errors\": [...], \"title\": ...}.")),
            504: OpenApiResponse(description=(
                "The request reached its 120 s limit before any SOP was created. Nothing was created. Body: "
                "{\"errors\": [...], \"created\": [], \"not_sent\": [...]}.")),
        },
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Create SOP (JSON only, no file)",
                value={
                    "data": {
                        "type": "sops",
                        "attributes": {
                            "title": "A Maximal SOP",
                            "content_blobs": [
                                {"original_filename": "a_pdf_file.pdf", "content_type": "application/pdf"}
                            ],
                            "policy": {"access": "download", "permissions": [{"resource": {"id": "2558", "type": "projects"}, "access": "edit"}]}
                        },
                        "relationships": {"projects": {"data": [{"id": "2558", "type": "projects"}]}}
                    }
                },
                request_only=True,
                media_type="application/json",
            ),
        ],
    )
    def create(self, request):
        deadline = monotonic() + SOP_CREATE_TIMEOUT_S  # the whole request, so no answer outlasts prod's TLS proxy
        raw = request.data.get('metadata')
        if raw is None and 'data' in request.data and not request.FILES:
            raw = {"data": request.data['data']}  # the JSON body the schema documents
        try:
            metadata = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            return _json({"errors": [{"title": "Invalid metadata"}]}, 422)
        if not isinstance(metadata, dict):
            return _json({"errors": [{"title": "Invalid metadata"}]}, 422)

        institution_code = request.data.get('institution', '')
        has_files = bool(request.FILES)

        if has_files:
            files = request.FILES.getlist("file")
            max_bytes = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', 200 * 1024 * 1024)
            l_size = sum(f.size for f in files)
            if l_size > max_bytes:
                return _json({"errors": [{"title": f"Total upload size {l_size} exceeds limit {max_bytes}"}]}, 413)
            if not isinstance(metadata.get("data"), dict):
                return _json({"errors": [{"title": "Invalid metadata"}]}, 422)
        else:
            files = [None]

        # Every SOP's payload and title before the first create, so a 422 or a 409 means nothing was created.
        planned = []
        for file in files:
            meta = metadata
            if file is not None:
                # Each SOP starts from the request's own metadata, so it carries only its own file's placeholder.
                meta = auto_populate_content_blobs(metadata, [file])
                attrs = meta["data"].setdefault("attributes", {})
                submitted = attrs.get("title")
                if len(files) == 1 and isinstance(submitted, str) and _SCHEME_TITLE.match(submitted):
                    pass  # one file whose title already follows the scheme: the author's date and version stay
                elif len(files) == 1 and _SCHEME_TITLE.match(file.name):
                    attrs["title"] = file.name  # one file whose name already follows the scheme: the same
                else:
                    formatted_date = datetime.datetime.now().strftime("%y%m%d")
                    if institution_code != "":
                        attrs["title"] = f"P.{institution_code}-{formatted_date}-V1_{file.name}"
                    else:
                        attrs["title"] = f"{formatted_date}-V1_{file.name}"
            try:
                payload = SopCreateRequest.model_validate(meta).model_dump(exclude_none=True)
            except Exception:
                return _json({"errors": [{"title": "Invalid metadata"}]}, 422)
            planned.append((file, payload, payload["data"]["attributes"]["title"]))

        titles = [title for _, _, title in planned]
        folded = [t.casefold() for t in titles]  # SEEK's sops.title collation ignores case
        for title in titles:
            if folded.count(title.casefold()) > 1:
                return _json({"errors": [{"title": "Two files in this request would get the same SOP title",
                                          "detail": "Nothing was created. Send files with different names."}],
                              "title": title}, 409)
            dup = _duplicate_title(self.client, request, title)
            if dup is not None:
                return dup

        sop_results = []
        created = []
        warnings = []
        all_blobs = []
        for idx, (file, payload, title) in enumerate(planned):
            not_sent = [f.name for f in files[idx:]] if file is not None else []
            fail = dict(created=created, not_sent=not_sent)

            wait = _within(deadline, SOP_CREATE_TIMEOUT_S)
            if wait is None:
                return _time_up(sop_results, created, not_sent)
            try:
                body, code, headers, resp = self.client.create_sop(request, payload, timeout_s=wait)
            except requests.RequestException as exc:
                # SEEK may have committed the write it did not answer: its rows go in, held back.
                hooks.enqueue("protocol_map", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)
                return _unconfirmed(title, exc, math.ceil(wait[1]), **fail)
            delay = hooks.write_delay(code)
            if delay is not None:
                # A 2xx at once, whatever its body says; a 5xx held back, since SEEK may have committed it.
                hooks.enqueue("protocol_map", "*", delay_s=delay)
            if code == 401:
                return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
            if code >= 400:
                return _seek_refused(body, code, headers, title, **fail)

            data = None  # an unreadable body must not fall back to the previous file's answer
            try:
                data = json.loads(body or b"{}")
                sop_data = SopSingleResponse.model_validate(data).model_dump(exclude_none=True)
            except Exception:
                # SEEK wrote the SOP; if its answer still names the id, report it and go on to the file.
                try:
                    sop_data = data if data["data"]["id"] else None
                except Exception:
                    sop_data = None
                if sop_data is None:
                    return _json({"errors": [{"title": "Invalid upstream response",
                                              "detail": "SEEK answered the create without a record id. The SOP may "
                                                        "exist: look it up by title before you send it again."}],
                                  "title": title, **fail}, 502)
                warnings.append(f"SEEK created SOP {sop_data['data']['id']} but its answer did not have the "
                                "expected shape; the id is as SEEK gave it.")
            sop_results.append(sop_data)
            attrs = sop_data["data"].get("attributes") or {}
            created.append({"id": sop_data["data"]["id"], "title": attrs.get("title", title)})

            if file is not None:
                created[-1]["file"] = file.name  # pairs a SOP with its file when the file is in not_sent
                wait = _within(deadline, 300)  # the upload's own (10, 300), inside the request limit
                if wait is None:
                    return _time_up(sop_results, created, not_sent)
                # upload_content_blobs calls only client.upload_content_blob: this gives that call the limit.
                capped = SimpleNamespace(upload_content_blob=partial(self.client.upload_content_blob, timeout_s=wait))
                try:
                    blobs = upload_content_blobs(capped, request, "sops", sop_data["data"]["id"],
                                                 attrs.get("content_blobs") or [], [file])
                except Exception as exc:
                    log.warning("sops_proxy.create: file upload for SOP %s failed: %s", sop_data["data"]["id"], exc)
                    blobs = [_blob_status(file.name, f"upload failed: {exc}")]
                created[-1]["blobs"] = [b.model_dump() for b in blobs]
                all_blobs.extend(blobs)

        body = {"data": sop_results}
        if warnings:
            body["warnings"] = warnings
        status_code = 207 if any(r.status == "failed" for r in all_blobs) else 201
        if status_code == 207:
            body["created"] = created  # each SOP's id, title and upload results: which file failed
        return _json(body, status_code)

    # PATCH /sops/{uid}
    @extend_schema(
        operation_id="Update a SOP",
        description=SOP_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "One or more files to replace content blobs.",
                    },
                    "metadata": {
                        "type": "string",
                        "description": 'JSON string with SOP patch metadata (e.g., {"data":{"type":"sops","id":"42","attributes":{"title":"..."}}}). content_blobs auto-generated from files.',
                    },
                },
                "required": ["metadata"],
            },
            "application/json": SopUpdateRequest,
        },
        responses={200: SopSingleResponse, 207: ContentBlobUploadResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Patch SOP (JSON only)",
                value={
                    "data": {
                        "type": "sops",
                        "id": "132",
                        "attributes": {"title": "A Maximally Patched SOP"},
                        "relationships": {"projects": {"data": [{"id": "2653", "type": "projects"}]}}
                    }
                },
                media_type="application/json",
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        has_files = bool(request.FILES)

        if has_files:
            metadata_str = request.data.get('metadata', '{}')
            try:
                metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                metadata = auto_populate_content_blobs(metadata, request.FILES.getlist('file'))
                update_req = SopUpdateRequest.model_validate(metadata)
                payload = update_req.to_seek_payload()
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid metadata"}]}', status=422, content_type='application/json')

            max_bytes = getattr(settings, 'BATCH_UPLOAD_MAX_TOTAL_BYTES', 200 * 1024 * 1024)
            total_size = sum(f.size for f in request.FILES.getlist('file'))
            if total_size > max_bytes:
                return HttpResponse(
                    json.dumps({"errors": [{"title": f"Total upload size {total_size} exceeds limit {max_bytes}"}]}).encode(),
                    status=413, content_type='application/json')
        else:
            try:
                update_req = SopUpdateRequest.model_validate(request.data)
                payload = update_req.to_seek_payload()
            except Exception:
                return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # The path names the SOP. One the caller cannot see answers as one that does not exist, and SEEK is sent the
        # path's id whatever title a body uid names.
        not_found = HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')
        seek_id = _resolve_uid_to_seek_id(uid, "sops", as_written_only=True)
        if seek_id is None:
            return not_found
        body_uid = str(update_req.data.uid or "")
        body_id = update_req.data.id or (body_uid if body_uid.isdigit() else None)
        if body_id is not None and str(body_id) != str(seek_id):
            if caller_cannot_view(self.client, request, "sops", seek_id):
                return not_found
            return HttpResponse(b'{"errors":[{"title":"Payload id does not match resolved uid"}]}', status=422, content_type='application/json')
        payload['data']['id'] = str(seek_id)

        try:
            body, code, headers, resp = self.client.update_sop(request, seek_id, payload)
        except requests.RequestException:
            # SEEK may have committed the write it did not answer: its rows go in, held back.
            hooks.enqueue("protocol_map", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)
            raise
        delay = hooks.write_delay(code)
        if delay is not None:
            # A 2xx at once, whatever its body says; a 5xx held back, since SEEK may have committed it.
            hooks.enqueue("protocol_map", "*", delay_s=delay)
        if code == 401:
            return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
        if code == 404 or (code == 403 and caller_cannot_view(self.client, request, "sops", seek_id)):
            # A SOP SEEK does not have, or one the caller cannot even view, answers the same not-found.
            return not_found

        # Forward SEEK errors before attempting response validation
        if code >= 400:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        # Validate response
        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        if not has_files:
            ct = headers.get('Content-Type', 'application/json')
            return HttpResponse(body, status=code, content_type=ct)

        # Upload files to content blob endpoints
        asset_id = data.get("data", {}).get("id")
        content_blobs_meta = data.get("data", {}).get("attributes", {}).get("content_blobs", [])
        files = request.FILES.getlist('file')

        unmatched = check_unmatched_files(content_blobs_meta, files)
        if unmatched:
            return HttpResponse(
                json.dumps({"errors": [{"title": "Uploaded files do not match any content_blob placeholder",
                                        "detail": f"Unmatched filenames: {unmatched}"}]}).encode(),
                status=400, content_type='application/json')

        blob_results = upload_content_blobs(self.client, request, "sops", asset_id, content_blobs_meta, files)

        any_failed = any(r.status == "failed" for r in blob_results)
        upload_resp = ContentBlobUploadResponse(
            asset_id=asset_id, asset_type="sops",
            blob_uploads=blob_results, asset_data=data)

        resp_status = 207 if any_failed else 200
        return HttpResponse(
            upload_resp.model_dump_json(), status=resp_status,
            content_type='application/json')

    # DELETE /sops/{uid}
    @extend_schema(
        operation_id="Delete a SOP",
        description=SOP_DELETE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Deleted. Body: {\"status\": \"deleted\", \"id\": ..., \"title\": ...}."),
            202: OpenApiResponse(description=(
                "SEEK did not answer within the proxy's timeout: it received the delete and may still complete it. "
                "Body: {\"status\": \"unconfirmed\", \"id\": ..., \"title\": ..., \"detail\": ...}.")),
            403: OpenApiResponse(description="SEEK's own rules do not let the caller delete this SOP."),
            404: OpenApiResponse(description="No SOP with this id or title that the caller can see."),
            409: OpenApiResponse(response=OpenApiTypes.OBJECT, description=(
                "Samples name this SOP as their Protocol, by its id, or by its title while no other SOP holds that "
                "title, so nothing was deleted. Body: {\"errors\": [...], \"id\": ..., \"title\": ...}, the same "
                "however many samples, so it says nothing of samples the caller cannot see.")),
            502: OpenApiResponse(description=(
                "SEEK could not be reached or answered with an error. Its detail says whether the delete was sent: when "
                "the read before it failed, nothing was deleted; otherwise the delete may or may not have happened.")),
            503: OpenApiResponse(description="Whether samples use this SOP could not be checked, so nothing was deleted."),
            504: OpenApiResponse(description=(
                "SEEK did not answer the read before the delete in time. Nothing was deleted.")),
        },
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="SOP deleted",
                value={"status": "deleted", "id": "132", "title": "P.WHI-251028-V1_NG_8-GEX.docx"},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="SOP in use",
                value=_in_use_body("132", "P.WHI-251028-V1_NG_8-GEX.docx"),
                response_only=True,
                status_codes=["409"],
            ),
        ],
    )
    def destroy(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid, "sops", as_written_only=True)
        if seek_id is None:
            return HttpResponse(_NOT_FOUND, status=404, content_type='application/json')

        # Read it as the caller first: a SOP the caller cannot see answers as one that does not exist, before the
        # in-use check could say otherwise.
        try:
            body, code, headers, resp = self.client.get_sop(request, seek_id)
        except requests.RequestException as exc:
            timed_out = isinstance(exc, requests.ReadTimeout)
            log.warning("sops_proxy.destroy: SEEK did not answer the read of %s: %s", seek_id, exc)
            return _json({"errors": [{"title": "SEEK did not answer",
                                      "detail": "SEEK did not answer the read of the SOP, so the delete was not "
                                                "sent. Nothing was deleted. Try again later."}]},
                         504 if timed_out else 502)
        if code == 401:
            return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
        if code in (403, 404):
            return HttpResponse(_NOT_FOUND, status=404, content_type='application/json')
        try:
            title = json.loads(body)["data"]["attributes"]["title"] if code < 400 else None
        except Exception:
            title = None
        if not isinstance(title, str):
            return _json({"errors": [{"title": "Invalid upstream response",
                                      "detail": f"SEEK answered {code} to the read. Nothing was deleted."}]}, 502)

        with _title_lock(title) as held:
            if not held:
                return _not_checked(seek_id)
            return self._delete_unused(request, seek_id, title)

    def _delete_unused(self, request, seek_id: str, title: str):
        """The in-use check, then SEEK's delete; ``destroy`` holds the title's lock around both."""
        try:
            in_use = _samples_citing(seek_id, title)
        except Exception:
            log.warning("sops_proxy.destroy: could not check which samples use SOP %s", seek_id, exc_info=True)
            return _not_checked(seek_id)
        if in_use:
            return _json(_in_use_body(seek_id, title), 409)
        # Accepted: nothing locks SEEK's samples or sops between this check and SEEK's delete below, so a sample
        # written in that window, or a SOP deleted in SEEK's own pages, can still leave a cited title with no SOP.
        # NExtSEEK cannot hold SEEK's tables across a SEEK call; the title lock covers only NExtSEEK's own deletes,
        # and a delete SEEK leaves unanswered (202) frees it while SEEK may still be deleting.

        done = {"id": seek_id, "title": title}
        try:
            body, code, headers, resp = self.client.delete_sop(request, seek_id)
        except requests.RequestException as exc:
            # SEEK may have committed the delete it did not answer: the protocol labels are redone, held back.
            hooks.enqueue("protocol_map", "*", delay_s=hooks.UNCONFIRMED_DELAY_S)
            if isinstance(exc, requests.ReadTimeout):
                log.warning("sops_proxy.destroy: SEEK did not answer the delete of %s within %s s",
                            seek_id, self.client.timeout_s)
                return _json({"status": "unconfirmed", **done,
                              "detail": f"SEEK did not answer within {self.client.timeout_s} s. It received the "
                                        "delete and may still complete it: GET /nextseek_api/sops/<id>/ answers "
                                        "404 once it has."}, 202)
            log.warning("sops_proxy.destroy: SEEK could not be reached for the delete of %s: %s", seek_id, exc)
            return _json({"errors": [{"title": "Upstream connection error",
                                      "detail": "SEEK could not be reached, so the SOP may or may not have been "
                                                "deleted."}], **done}, 502)
        delay = hooks.write_delay(code)
        if delay is not None:
            hooks.enqueue("protocol_map", "*", delay_s=delay)
        if code == 401:
            return HttpResponse(_AUTH_REQUIRED, status=401, content_type='application/json')
        if code >= 500:
            return _json({"errors": [{"title": "SEEK could not delete the SOP",
                                      "detail": f"SEEK answered {code}. The SOP may or may not have been deleted: "
                                                "GET /nextseek_api/sops/<id>/ answers 404 once it is gone."}],
                          "seek_status": code, **done}, 502)
        if code >= 400:
            ct = headers.get('Content-Type') or ''
            if 'json' in ct.lower():
                return HttpResponse(body, status=code, content_type=ct)
            return _json({"errors": [{"title": "SEEK refused the delete", "detail": f"SEEK answered {code}."}],
                          "seek_status": code, **done}, code)
        return _json({"status": "deleted", **done}, 200)

    # POST /sops/download
    @extend_schema(
        operation_id="Download SOP content blob",
        description=SOP_DOWNLOAD_DESC,
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.BINARY},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Download by SEEK ID",
                description="Download a SOP's content blob using its numeric SEEK ID",
                value={"uid_or_id": "142"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download by SOP title/UID",
                description="Download a SOP's content blob using its NExtSEEK UID (title)",
                value={"uid_or_id": "P.ESS-251028-V1_NG_8-GEX.docx"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download as CSV",
                description="Download content blob with CSV conversion (SEEK readContentBlob with Accept: text/csv)",
                value={"uid_or_id": "142", "output_format": "csv"},
                request_only=True,
            ),
            OpenApiExample(
                name="Download with explicit blob_id",
                description="Specify blob_id when a SOP has multiple content blobs",
                value={"uid_or_id": "142", "blob_id": 246},
                request_only=True,
            ),
            OpenApiExample(
                name="Batch download (zip)",
                description="Download multiple SOPs as a zip archive",
                value=[{"uid_or_id": "142"}, {"uid_or_id": "P.ESS-251028-V1_NG_8-GEX.docx"}],
                request_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="download")
    def download(self, request):
        """Dispatcher: single dict → streaming file; list → zip archive."""
        data = request.data
        if isinstance(data, dict):
            return download_single(self.client, request, "sops", data)
        elif isinstance(data, list):
            return download_batch(self.client, request, "sops", data)
        else:
            return HttpResponse(
                b'{"errors":[{"title":"Request body must be a JSON object or array"}]}',
                status=422, content_type='application/json',
            )
