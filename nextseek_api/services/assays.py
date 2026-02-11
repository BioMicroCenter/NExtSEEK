from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    ASSAY_LIST_DESC,
    ASSAY_FETCH_DESC,
    ASSAY_CREATE_DESC,
    ASSAY_UPDATE_DESC,
)
from nextseek_api.models import (
    AssayListResponse,
    AssaySingleResponse,
    AssayCreateRequest,
    AssayUpdateRequest,
)


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment to a SEEK assay id.
    - If numeric, use as-is.
    - Else: DB resolver not available in this codebase; return None.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    return None


class AssayProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List Assays",
        description=ASSAY_LIST_DESC,
        responses={200: AssayListResponse},
        tags=['Assays'],
        examples=[
            OpenApiExample(
                name="List Assays",
                value={
                    "data": [
                        {
                            "id": "351",
                            "type": "assays",
                            "attributes": {"title": "A Maximal experimental Assay"},
                            "links": {"self": "/assays/351"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/assays?page[number]=1&page[size]=100"},
                    "meta": {"base_url": settings.SEEK_URL, "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_assays(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            AssayListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        description=ASSAY_FETCH_DESC,
        operation_id="Fetch an Assay",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.'),
        ],
        responses={200: AssaySingleResponse},
        tags=['Assays'],
        examples=[
            OpenApiExample(
                name="Get Assay",
                value={
                    "data": {
                        "id": "351",
                        "type": "assays",
                        "attributes": {"title": "A Maximal experimental Assay"},
                        "relationships": {"study": {"data": {"type": "studies", "id": "434"}}},
                        "links": {"self": "/assays/351"},
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
            return HttpResponse(b'{"errors":[{"title":"Assay not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_assay(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create an Assay",
        description=ASSAY_CREATE_DESC,
        request=AssayCreateRequest,
        responses={201: AssaySingleResponse, 200: AssaySingleResponse},
        tags=['Assays'],
        examples=[
            OpenApiExample(
                name="Create Assay",
                value={
                    "data": {
                        "type": "assays",
                        "attributes": {
                            "title": "A Maximal experimental Assay",
                            "assay_class": {"key": "EXP"},
                            "assay_type": {"uri": "http://jermontology.org/ontology/JERMOntology#Transcriptomics"},
                            "technology_type": {"uri": "http://jermontology.org/ontology/JERMOntology#RNA-Seq"}
                        },
                        "relationships": {"study": {"data": {"type": "studies", "id": "434"}}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = AssayCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_assay(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update an Assay",
        description=ASSAY_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric). Non-numeric UID resolution not configured.')
        ],
        request=AssayUpdateRequest,
        responses={200: AssaySingleResponse},
        tags=['Assays'],
        examples=[
            OpenApiExample(
                name="Patch Assay",
                value={
                    "data": {
                        "type": "assays",
                        "id": "351",
                        "attributes": {"description": "Revised description"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = AssayUpdateRequest.model_validate(request.data)
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
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)

        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Assay not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_assay(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


