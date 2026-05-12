from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.conf import settings

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.services.common import maybe_v2_error
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
            ),
            # v2 contract examples (task-04)
            OpenApiExample(
                name="Minimal v2 list response",
                value={"results": [], "count": 0, "next": None, "previous": None},
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="Realistic v2 list response",
                value={
                    "results": [{"id": "351", "type": "assays", "attributes": {"title": "Assay-1"}}],
                    "count": 1,
                    "next": None,
                    "previous": None,
                },
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="v2 upstream error (502)",
                value={"errors": [{"status": "502", "title": "Invalid upstream response"}]},
                response_only=True,
                status_codes=["502"],
                media_type="application/vnd.nextseek.v2+json",
            ),
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_assays(request, params=request.query_params)
        if code == 401:
            return maybe_v2_error(HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json'), request)

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json'), request)
            data = json.loads(body or b"{}")
            AssayListResponse.model_validate(data)
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json'), request)

        ct = headers.get('Content-Type', 'application/json')
        return maybe_v2_error(HttpResponse(body, status=code, content_type=ct), request)

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
            ),
            # v2 contract examples (task-04)
            OpenApiExample(
                name="Minimal v2 response",
                value={"data": {"id": "351", "type": "assays", "attributes": {"title": "Assay"}}},
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="Realistic v2 response",
                value={
                    "data": {
                        "id": "351",
                        "type": "assays",
                        "attributes": {"title": "A Maximal experimental Assay"},
                        "relationships": {"study": {"data": {"type": "studies", "id": "434"}}},
                        "links": {"self": "/assays/351"},
                    },
                    "jsonapi": {"version": "1.0"},
                },
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="v2 not found error (404)",
                value={"errors": [{
                    "status": "404",
                    "title": "Assay not found",
                }]},
                response_only=True,
                status_codes=["404"],
                media_type="application/vnd.nextseek.v2+json",
            ),
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Assay not found"}]}', status=404, content_type='application/json'), request)

        body, code, headers, resp = self.client.get_assay(request, str(seek_id))
        if code == 401:
            return maybe_v2_error(HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json'), request)

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json'), request)
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json'), request)

        ct = headers.get('Content-Type', 'application/json')
        return maybe_v2_error(HttpResponse(body, status=code, content_type=ct), request)

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
            ),
            # v2 contract examples (task-04)
            OpenApiExample(
                name="Minimal v2 create request",
                value={"data": {"type": "assays", "attributes": {"title": "Assay-x"}, "relationships": {"study": {"data": {"type": "studies", "id": "434"}}}}},
                request_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="Realistic v2 create response",
                value={
                    "data": {
                        "id": "351",
                        "type": "assays",
                        "attributes": {"title": "A Maximal experimental Assay"},
                        "links": {"self": "/assays/351"},
                    },
                    "jsonapi": {"version": "1.0"},
                },
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="v2 validation error (422)",
                value={"errors": [{
                    "status": "422",
                    "title": "Invalid request",
                    "source": {"pointer": "/data/attributes/title"},
                    "meta": {"pydantic_type": "missing"},
                }]},
                response_only=True,
                status_codes=["422"],
                media_type="application/vnd.nextseek.v2+json",
            ),
        ],
    )
    def create(self, request):
        try:
            payload = AssayCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json'), request)

        body, code, headers, resp = self.client.create_assay(request, payload)
        if code == 401:
            return maybe_v2_error(HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json'), request)

        try:
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json'), request)

        ct = headers.get('Content-Type', 'application/json')
        return maybe_v2_error(HttpResponse(body, status=code, content_type=ct), request)

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
            ),
            # v2 contract examples (task-04)
            OpenApiExample(
                name="Minimal v2 patch request",
                value={"data": {"type": "assays", "id": "351", "attributes": {"description": "x"}}},
                request_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="Realistic v2 patch response",
                value={
                    "data": {
                        "id": "351",
                        "type": "assays",
                        "attributes": {"description": "Revised description"},
                    },
                    "jsonapi": {"version": "1.0"},
                },
                response_only=True,
                media_type="application/vnd.nextseek.v2+json",
            ),
            OpenApiExample(
                name="v2 validation error (422)",
                value={"errors": [{
                    "status": "422",
                    "title": "Payload id does not match path id",
                    "source": {"pointer": "/data/id"},
                }]},
                response_only=True,
                status_codes=["422"],
                media_type="application/vnd.nextseek.v2+json",
            ),
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = AssayUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json'), request)

        path_id = str(uid) if uid is not None else None
        body_id = payload.get('data', {}).get('id')

        seek_id: Optional[str] = None
        if path_id and path_id.isdigit():
            seek_id = path_id
            if body_id is not None and str(body_id) != str(seek_id):
                return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json'), request)
            if body_id is None:
                payload['data']['id'] = str(seek_id)
        else:
            if body_id and str(body_id).isdigit():
                seek_id = str(body_id)

        if seek_id is None:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Assay not found"}]}', status=404, content_type='application/json'), request)

        body, code, headers, resp = self.client.update_assay(request, str(seek_id), payload)
        if code == 401:
            return maybe_v2_error(HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json'), request)

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json'), request)
            data = json.loads(body or b"{}")
            AssaySingleResponse.model_validate(data)
        except Exception:
            return maybe_v2_error(HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json'), request)

        ct = headers.get('Content-Type', 'application/json')
        return maybe_v2_error(HttpResponse(body, status=code, content_type=ct), request)


