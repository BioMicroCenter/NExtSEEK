from typing import Any, Dict, Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.models import (
    SopCreateRequest,
    SopUpdateRequest,
    SopSingleResponse,
    SopListResponse,
)
from seek.dbtable_sops import DBtable_sops
from django.conf import settings


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    """Resolve a path segment that may be a numeric SEEK id or a NExtSEEK SOP UID.
    Returns a SEEK id as string, or None if not found/ambiguous.
    """
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        dbsop = DBtable_sops("DEFAULT")
        records = dbsop.queryRecordsByConstraint({"title": s})
        if isinstance(records, list) and len(records) == 1 and records[0].get('id') is not None:
            return str(records[0]['id'])
    except Exception:
        return None
    return None


class SopProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    # Ensure router renders /sops/{uid}
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /sops
    @extend_schema(
        operation_id="List SOPs",
        description="Retrieve all standard operating procedures (protocols) you have access to. Returns protocol titles, descriptions, associated projects, and file attachment metadata. Examples: 'Show me all available protocols'; 'What procedures are documented for sample collection?'",
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
                            "attributes": {"title": "This Sop"},
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
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

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
        return HttpResponse(body, status=code, content_type=ct)

    # GET /sops/{uid}
    @extend_schema(
        description="Fetch details for a specific protocol document by ID or name. Returns full metadata including title, description, version history, attached files, and linked projects. Optionally specify a version to retrieve historical revisions. Examples: 'Show me the tissue processing protocol'; 'What is the current version of the blood draw procedure?'",
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
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_sop(request, seek_id)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

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
        description="Upload a new standard operating procedure document. Attach protocol files (PDF, Word, etc.) and associate with projects. Returns the created protocol with its assigned ID and metadata. Examples: 'Add a new necropsy protocol for the primate study'; 'Upload the updated sample handling procedure'",
        request=SopCreateRequest,
        responses={201: SopSingleResponse, 200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Create Sop",
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
                }
            )
        ],
    )
    def create(self, request):
        # Validate request
        try:
            payload = SopCreateRequest.model_validate(request.data).model_dump(exclude_none=True)
        except Exception as e:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_sop(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        # Validate response
        try:
            data = json.loads(body or b"{}")
            SopSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # PATCH /sops/{uid}
    @extend_schema(
        operation_id="Update a SOP",
        description="Revise an existing protocol document by ID or name. Update title, description, attached files, or project associations. Returns the updated protocol with all current metadata. Examples: 'Update the description for the imaging protocol'; 'Change the project association for the cell culture procedure'",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request=SopUpdateRequest,
        responses={200: SopSingleResponse},
        tags=['SOPs'],
        examples=[
            OpenApiExample(
                name="Patch Sop",
                value={
                    "data": {
                        "type": "sops",
                        "id": "132",
                        "attributes": {"title": "A Maximally Patched SOP"},
                        "relationships": {"projects": {"data": [{"id": "2653", "type": "projects"}]}}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        # Validate and normalize body (allows uid in body)
        try:
            update_req = SopUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        # Resolve path param to SEEK id
        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None and isinstance(update_req.data.id, str):
            seek_id = update_req.data.id
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"SOP not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_sop(request, seek_id, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        # Validate response
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


