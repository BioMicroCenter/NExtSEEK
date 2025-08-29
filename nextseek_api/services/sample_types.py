from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.models import (
    SampleTypeListResponse,
    SampleTypeSingleResponse,
    SampleTypeCreateRequest,
    SampleTypeUpdateRequest,
)
from seek.dbtable_sampletype import DBtable_sampletype


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        dbst = DBtable_sampletype("DEFAULT")
        # Attempt resolution by title
        rid = dbst.getSampleTypeID(s)
        if isinstance(rid, int) and rid > 0:
            return str(rid)
    except Exception:
        return None
    return None


class SampleTypeProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List SampleTypes",
        description="List SampleTypes (proxy to SEEK). JSON:API pass-through.",
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
        body, code, headers, resp = self.client.list_sample_types(request, params=request.query_params)
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
        description="Fetch a single SampleType by SEEK id (numeric) or by title (resolved).",
        operation_id="Fetch a SampleType",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or title (resolved to id)'),
        ],
        responses={200: SampleTypeSingleResponse},
        tags=['SampleTypes'],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SampleType not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sample_type(request, str(seek_id))
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
        description="Create a SampleType via JSON:API (proxy to SEEK).",
        request=SampleTypeCreateRequest,
        responses={201: SampleTypeSingleResponse, 200: SampleTypeSingleResponse},
        tags=['SampleTypes'],
    )
    def create(self, request):
        try:
            payload = SampleTypeCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sample_type(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            SampleTypeSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a SampleType",
        description="Update a SampleType by SEEK id (numeric) or resolved title.",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or title (resolved to id)')
        ],
        request=SampleTypeUpdateRequest,
        responses={200: SampleTypeSingleResponse},
        tags=['SampleTypes'],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = SampleTypeUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        path_id = _resolve_uid_to_seek_id(uid) if uid is not None else None
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

        body, code, headers, resp = self.client.update_sample_type(request, str(seek_id), payload)
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


