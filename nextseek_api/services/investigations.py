from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    INVESTIGATION_LIST_DESC,
    INVESTIGATION_FETCH_DESC,
    INVESTIGATION_CREATE_DESC,
    INVESTIGATION_UPDATE_DESC,
)
from nextseek_api.models import (
    InvestigationListResponse,
    InvestigationSingleResponse,
    InvestigationCreateRequest,
    InvestigationUpdateRequest,
)


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment to a SEEK investigation id.
    - If numeric, use as-is.
    - Else: DB resolver is not available in this codebase; return None.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    # No DBtable_investigations available → cannot resolve non-numeric UID
    return None


class InvestigationProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List Investigations",
        description=INVESTIGATION_LIST_DESC,
        responses={200: InvestigationListResponse},
        tags=['Investigations'],
        examples=[
            OpenApiExample(
                name="List Investigations",
                value={
                    "data": [
                        {
                            "id": "763",
                            "type": "investigations",
                            "attributes": {"title": "My Investigation"},
                            "links": {"self": "/investigations/763"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/investigations?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_investigations(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            InvestigationListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        description=INVESTIGATION_FETCH_DESC,
        operation_id="Fetch an Investigation",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.'),
        ],
        responses={200: InvestigationSingleResponse},
        tags=['Investigations'],
        examples=[
            OpenApiExample(
                name="Get Investigation",
                value={
                    "data": {
                        "id": "763",
                        "type": "investigations",
                        "attributes": {"title": "My Investigation"},
                        "relationships": {"projects": {"data": []}},
                        "links": {"self": "/investigations/763"},
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
            return HttpResponse(b'{"errors":[{"title":"Investigation not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_investigation(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            InvestigationSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create an Investigation",
        description=INVESTIGATION_CREATE_DESC,
        request=InvestigationCreateRequest,
        responses={201: InvestigationSingleResponse, 200: InvestigationSingleResponse},
        tags=['Investigations'],
        examples=[
            OpenApiExample(
                name="Create Investigation",
                value={
                    "data": {
                        "type": "investigations",
                        "attributes": {"title": "My Investigation"},
                        "relationships": {"projects": {"data": [{"type": "projects", "id": "4475"}]}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = InvestigationCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_investigation(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            InvestigationSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update an Investigation",
        description=INVESTIGATION_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.')
        ],
        request=InvestigationUpdateRequest,
        responses={200: InvestigationSingleResponse},
        tags=['Investigations'],
        examples=[
            OpenApiExample(
                name="Patch Investigation",
                value={
                    "data": {
                        "id": "763",
                        "type": "investigations",
                        "attributes": {"title": "Revised Title"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = InvestigationUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        path_id = str(uid) if uid is not None else None
        body_id = payload.get('data', {}).get('id')

        seek_id: Optional[str] = None
        if path_id and path_id.isdigit():
            seek_id = path_id
            if body_id is not None and str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json')
            if body_id is None:
                payload['data']['id'] = str(seek_id)
        else:
            # No DB resolver; fall back to body id
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)

        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Investigation not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_investigation(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            InvestigationSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


