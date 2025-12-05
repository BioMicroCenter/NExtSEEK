from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.models import (
    DataFileListResponse,
    DataFileSingleResponse,
    DataFileCreateRequest,
    DataFileUpdateRequest,
)
from seek.dbtable_data_files import DBtable_data_files
from django.db.models import Q


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        dbdf = DBtable_data_files("DEFAULT")
        # 1) Exact title match
        exact = dbdf.queryRecordsByConstraint({"title": s}) or []
        if isinstance(exact, list) and len(exact) == 1 and exact[0].get('id') is not None:
            return str(exact[0]['id'])

        # 2) If input looks like a sample UID (no underscore), try prefix match on title
        if "_" not in s:
            prefix_rows = dbdf.queryRecordsCustom(Q(title__startswith=s + "_")) or []
            if len(prefix_rows) == 1 and prefix_rows[0].get('id') is not None:
                return str(prefix_rows[0]['id'])
            if len(prefix_rows) > 1:
                # Deterministic tie-breaker: choose the highest numeric id
                def _to_int(v):
                    try:
                        return int(v)
                    except Exception:
                        return -1
                best = max(prefix_rows, key=lambda r: _to_int(r.get('id')))
                if best and best.get('id') is not None:
                    return str(best['id'])
    except Exception:
        return None
    return None


class DataFileProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /data_files
    @extend_schema(
        operation_id="List DataFiles",
        description="Retrieve all data files you have access to, including spreadsheets, images, sequencing outputs, and other research data. Returns file titles, descriptions, content types, and associated projects or assays. Examples: 'Show me all uploaded data files'; 'What sequencing results are available for download?'",
        responses={200: DataFileListResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="List DataFiles",
                value={
                    "data": [
                        {
                            "id": "560",
                            "type": "data_files",
                            "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                            "links": {"self": "/data_files/560"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/data_files?page[number]=1&page[size]=100"},
                    "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_data_files(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # GET /data_files/{uid}?version=int
    @extend_schema(
        description="Fetch metadata for a specific data file by ID or name. Returns title, description, file type, download URL, version history, and linked samples or assays. Specify version number to retrieve historical file revisions. Examples: 'Get the CT scan images for this monkey'; 'Show me the flow cytometry results file for sample NHP-220630FLY-1-PUB'",
        operation_id="Fetch a DataFile",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)'),
            OpenApiParameter(name='version', type=int, location=OpenApiParameter.QUERY, required=True, description='Required DataFile version to fetch', default=1),
        ],
        responses={200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Get DataFile",
                value={
                    "data": {
                        "id": "560",
                        "type": "data_files",
                        "attributes": {"title": "DF-20240101-01_Sample-X.csv"},
                        "relationships": {},
                        "links": {"self": "/data_files/560"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        version = request.query_params.get('version')
        try:
            # Default to 1 if not provided; still documented as required with default
            version_int = int(version) if version is not None else 1
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"version must be an integer"}]}', status=422, content_type='application/json')

        seek_id = _resolve_uid_to_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"DataFile not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_data_file(request, seek_id, version_int)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /data_files
    @extend_schema(
        operation_id="Create a DataFile",
        description="Register a new data file by providing a URL or uploading content. Associate with projects, assays, or samples. Returns the created file record with its assigned ID and metadata. Examples: 'Upload the microscopy images from today's experiment'; 'Register a new sequencing output file for the water study'",
        request=DataFileCreateRequest,
        responses={201: DataFileSingleResponse, 200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Create DataFile",
                value={
                    "data": {
                        "type": "data_files",
                        "attributes": {
                            "title": "DF-20240101-01_Sample-X.csv",
                            "content_blobs": [
                                {"url": "https://example.com/file.csv", "content_type": "text/csv"}
                            ]
                        },
                        "relationships": {"projects": {"data": [{"id": "560", "type": "projects"}]}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = DataFileCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_data_file(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # PATCH /data_files/{uid}
    @extend_schema(
        operation_id="Update a DataFile",
        description="Update an existing data file's metadata by ID or name. Modify title, description, or project/assay associations. Returns the updated file record with all current metadata. Examples: 'Add a description to the microscopy images'; 'Change the project association for this sequencing file'",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request=DataFileUpdateRequest,
        responses={200: DataFileSingleResponse},
        tags=['DataFiles'],
        examples=[
            OpenApiExample(
                name="Patch DataFile",
                value={
                    "data": {
                        "type": "data_files",
                        "id": "560",
                        "attributes": {"description": "Updated description"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = DataFileUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        seek_id = _resolve_uid_to_seek_id(uid)

        if seek_id is not None:
            body_id = payload.get('data', {}).get('id')
            if body_id is None:
                payload['data']['id'] = seek_id
            elif str(body_id) != str(seek_id):
                return HttpResponse(b'{"errors":[{"title":"Payload id does not match resolved uid"}]}', status=422, content_type='application/json')
        else:
            if not payload.get('data', {}).get('id'):
                return HttpResponse(b'{"errors":[{"title":"DataFile not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_data_file(request, payload['data']['id'], payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            DataFileSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


