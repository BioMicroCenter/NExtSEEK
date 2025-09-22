from typing import Optional, Any

import json
import logging
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from pydantic import ValidationError
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.helpers import paginate_rows_in_envelope
from nextseek_api.models import (
    SampleSingleResponse,
    SampleCreateRequest,
    SampleUpdateRequest,
    SampleAdvancedSearchRequest,
    SampleAdvancedSearchResult,
)

log = logging.getLogger(__name__)

try:
    from seek.dbtable_sample import DBtable_sample
except Exception:  # pragma: no cover - optional resolver
    DBtable_sample = None  # type: ignore


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment to a SEEK sample id.
    - If numeric, use as-is.
    - Else: attempt to resolve via DBtable_sample().getSampleID(uid) when available.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        if DBtable_sample is None:
            return None
        dbs = DBtable_sample()
        sid = dbs.getSampleID(s)
        sid = int(sid) if sid is not None else 0
        return str(sid) if sid > 0 else None
    except Exception:
        return None


class SampleProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        description="Fetch a single Sample by SEEK id or NExtSEEK UID (uuid).",
        operation_id="Fetch a Sample",
        parameters=[
            OpenApiParameter(
                name='uid',
                type=str,
                location=OpenApiParameter.PATH,
                description='SEEK id (numeric) or Sample UUID (string)'
            ),
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        responses={200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Get Sample",
                value={
                    "data": {
                        "id": "321",
                        "type": "samples",
                        "attributes": {"title": "A Sample"},
                        "relationships": {
                            "sample_type": {"data": {"type": "sample_types", "id": "12"}},
                            "creators": {"data": []},
                            "projects": {"data": []},
                            "people": {"data": []},
                            "assays": {"data": []},
                            "data_files": {"data": []}
                        },
                        "links": {"self": "/samples/321"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sample(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=retrieve errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=retrieve error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create a Sample",
        description="Create a Sample via JSON:API (proxy to SEEK).",
        parameters=[
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        request=SampleCreateRequest,
        responses={201: SampleSingleResponse, 200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Create Sample",
                value={
                    "data": {
                        "type": "samples",
                        "attributes": {"title": "A Sample"},
                        "relationships": {"sample_type": {"data": {"type": "sample_types", "id": "12"}}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            # Best-effort normalization happens inside model using optional db_resolver if supplied
            payload = SampleCreateRequest.model_validate(request.data).to_seek_payload(db_resolver=None)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sample(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=create errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a Sample",
        description="Update a Sample by SEEK id or NExtSEEK UID (uuid).",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or Sample UUID (string)'),
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
        ],
        request=SampleUpdateRequest,
        responses={200: SampleSingleResponse},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Patch Sample",
                value={
                    "data": {
                        "type": "samples",
                        "id": "321",
                        "attributes": {"title": "Revised title"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = SampleUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload(db_resolver=None)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        path_id = str(uid) if uid is not None else None
        body_id: Optional[str] = payload.get('data', {}).get('id')

        seek_id: Optional[str] = None
        if path_id and path_id.isdigit():
            seek_id = path_id
            if body_id is not None and str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json')
            if body_id is None:
                payload['data']['id'] = str(seek_id)
        else:
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)
            elif path_id:
                resolved = _resolve_uid_to_seek_id(path_id)
                if resolved:
                    seek_id = resolved
                    if body_id is not None and str(body_id) != str(seek_id):
                        return HttpResponse(b'{"errors":[{"title":"Payload id does not match resolved uid"}]}', status=422, content_type='application/json')
                    payload['data']['id'] = str(seek_id)

        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_sample(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            SampleSingleResponse.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_proxy.validation_error action=partial_update errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_proxy.validation_exception action=partial_update error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # Optional delete endpoint, behind feature flag if needed by caller
    @extend_schema(
        operation_id="Delete a Sample",
        description="Delete a Sample by SEEK id or NExtSEEK UID (uuid).",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or Sample UUID (string)')
        ],
        responses={200: None},
        tags=['Samples'],
    )
    def destroy(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Sample not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.delete_sample(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')
        # Pass through upstream response on success; SEEK returns okResponse JSON
        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


def _resolve_sampletype_to_seek_id(s: str) -> Optional[str]:
    """Resolve a sample type display title to a SEEK numeric id string.
    - If input is numeric, return as-is.
    - Else, use DBtable_sampletype("DEFAULT").getSampleTypeID(title).
    """
    val = str(s)
    if val.isdigit():
        return val
    try:
        from seek.dbtable_sampletype import DBtable_sampletype  # local import for optional dependency
        rid = DBtable_sampletype("DEFAULT").getSampleTypeID(val)
        if isinstance(rid, int) and rid > 0:
            return str(rid)
        return None
    except Exception:
        return None


class SampleAdvancedSearchViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Advanced Search Samples",
        description="Advanced Samples search via SEEK DB helper.",
        parameters=[
            OpenApiParameter(
                name='debug_validation',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Include validation error details in 502 response'
            ),
            OpenApiParameter(
                name='page',
                type=int,
                location=OpenApiParameter.QUERY,
                description='1-based page number'
            ),
            OpenApiParameter(
                name='page_size',
                type=int,
                location=OpenApiParameter.QUERY,
                description='Items per page (default 100, max 1000)'
            ),
        ],
        request=SampleAdvancedSearchRequest,
        responses={200: SampleAdvancedSearchResult},
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Numeric sampletype",
                value={
                    "sampletype": "2",
                    "filter_searchText": "lung",
                    "attribute": "Organ",
                    "filter_matchType": "PARTIAL"
                }
            ),
            OpenApiExample(
                name="Title sampletype",
                value={
                    "sampletype": "TIS",
                    "filter_searchText": "lung",
                    "attribute": "Organ",
                    "filter_matchType": "EXACT"
                }
            ),
        ],
    )
    def create(self, request):
        # Parse and validate request
        try:
            req = SampleAdvancedSearchRequest.model_validate(request.data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Pre-check for resolvable sample type to return 404 when clearly not found
        sampletype_raw = str(req.sampletype)
        if not sampletype_raw.isdigit():
            resolved = _resolve_sampletype_to_seek_id(sampletype_raw)
            if resolved is None:
                return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')

        # Build resolver closure and DB filters
        try:
            def resolver(title: str) -> Optional[str]:
                return _resolve_sampletype_to_seek_id(title)

            filters = req.to_db_filters(sampletype_resolver=resolver)
        except ValueError as ve:
            # Distinguish resolution failure (404) vs generic validation (422)
            if "SampleType not found" in str(ve):
                return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Execute DB search
        try:
            searchType = "Advanced"
            user_seek = getattr(request, "user", None)
            if DBtable_sample is None:
                raise RuntimeError("DBtable_sample unavailable")
            report = DBtable_sample().searchAdvanced(user_seek, filters, searchType)
        except Exception as e:
            log.warning("samples_advanced.exec_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        # Validate output schema
        try:
            data = json.loads(report or "{}")
            SampleAdvancedSearchResult.model_validate(data)
        except ValidationError as ve:
            try:
                errs = ve.errors()
            except Exception:
                errs = str(ve)
            log.warning("samples_advanced.validation_error action=create errors=%s", errs)
            debug_flag = bool(getattr(settings, 'DEBUG', False) or str(request.GET.get('debug_validation', '0')).lower() in ('1', 'true', 'yes'))
            if debug_flag:
                resp_payload = {
                    "errors": [
                        {
                            "title": "Invalid upstream response",
                            "detail": "JSON schema mismatch",
                            "validation_errors": errs,
                        }
                    ]
                }
                return HttpResponse(json.dumps(resp_payload).encode(), status=502, content_type='application/json')
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')
        except Exception as e:
            log.warning("samples_advanced.validation_exception action=create error=%s", str(e))
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        # Paginate rows within the envelope while preserving schema
        try:
            data = paginate_rows_in_envelope(request, data)
        except Exception:
            # Fallback to unpaginated envelope on pagination error
            pass

        # Always return the mutated dict to ensure pagination is applied
        return HttpResponse(json.dumps(data).encode(), status=200, content_type='application/json')

