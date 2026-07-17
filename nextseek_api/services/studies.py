from typing import Optional

import orjson
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    STUDY_LIST_DESC,
    STUDY_FETCH_DESC,
    STUDY_CREATE_DESC,
    STUDY_UPDATE_DESC,
)
from nextseek_api.models import (
    StudyListResponse,
    StudySingleResponse,
    StudyCreateRequest,
    StudyUpdateRequest,
)


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment to a SEEK study id.
    - If numeric, use as-is.
    - Else: DB resolver is not available in this codebase; return None.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    # No DBtable_studies available → cannot resolve non-numeric UID
    return None


class StudyProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List Studies",
        description=STUDY_LIST_DESC,
        responses={200: StudyListResponse},
        tags=['Studies'],
        examples=[
            OpenApiExample(
                name="List Studies",
                value={
                    "data": [
                        {
                            "id": "746",
                            "type": "studies",
                            "attributes": {"title": "Vaccine Dose Response"},
                            "links": {"self": "/studies/746"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/studies?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_studies(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = orjson.loads(body or b"{}")
            StudyListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        description=STUDY_FETCH_DESC,
        operation_id="Fetch a Study",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.'),
        ],
        responses={200: StudySingleResponse},
        tags=['Studies'],
        examples=[
            OpenApiExample(
                name="Get Study",
                value={
                    "data": {
                        "id": "746",
                        "type": "studies",
                        "attributes": {"title": "Vaccine Dose Response"},
                        "relationships": {"investigation": {"data": {"id": "763", "type": "investigations"}}},
                        "links": {"self": "/studies/746"},
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
            return HttpResponse(b'{"errors":[{"title":"Study not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_study(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = orjson.loads(body or b"{}")
            StudySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create a Study",
        description=STUDY_CREATE_DESC,
        request=StudyCreateRequest,
        responses={201: StudySingleResponse, 200: StudySingleResponse},
        tags=['Studies'],
        examples=[
            OpenApiExample(
                name="Create Study",
                value={
                    "data": {
                        "type": "studies",
                        "attributes": {
                            "title": "Vaccine Dose Response",
                            "description": "Comparison of immune response across doses",
                            "experimentalists": "Wet lab team"
                        },
                        "relationships": {"investigation": {"data": {"id": "763", "type": "investigations"}}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = StudyCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_study(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = orjson.loads(body or b"{}")
            StudySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a Study",
        description=STUDY_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.')
        ],
        request=StudyUpdateRequest,
        responses={200: StudySingleResponse},
        tags=['Studies'],
        examples=[
            OpenApiExample(
                name="Patch Study",
                value={
                    "data": {
                        "id": "746",
                        "type": "studies",
                        "attributes": {"title": "Revised Vaccine Dose Response"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = StudyUpdateRequest.model_validate(request.data)
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
            return HttpResponse(b'{"errors":[{"title":"Study not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_study(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = orjson.loads(body or b"{}")
            StudySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)
