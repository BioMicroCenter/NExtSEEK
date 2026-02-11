from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    PROJECT_LIST_DESC,
    PROJECT_FETCH_DESC,
    PROJECT_CREATE_DESC,
    PROJECT_UPDATE_DESC,
)
from nextseek_api.models import (
    ProjectListResponse,
    ProjectSingleResponse,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)
from seek.dbtable_projects import DBtable_projects


def _resolve_uid_to_seek_id(uid_or_id: str) -> Optional[str]:
    s = str(uid_or_id)
    if s.isdigit():
        return s
    try:
        dbp = DBtable_projects("DEFAULT")
        records = dbp.queryRecordsByConstraint({"title": s}) or []
        if isinstance(records, list) and len(records) == 1 and records[0].get('id') is not None:
            return str(records[0]['id'])
    except Exception:
        return None
    return None


class ProjectProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    # GET /projects
    @extend_schema(
        operation_id="List Projects",
        description=PROJECT_LIST_DESC,
        responses={200: ProjectListResponse},
        tags=['Projects'],
        examples=[
            OpenApiExample(
                name="List Projects",
                value={
                    "data": [
                        {
                            "id": "2558",
                            "type": "projects",
                            "attributes": {"title": "A Project"},
                            "links": {"self": "/projects/2558"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/projects?page[number]=1&page[size]=100"},
                    "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_projects(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            ProjectListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # GET /projects/{uid}
    @extend_schema(
        description=PROJECT_FETCH_DESC,
        operation_id="Fetch a Project",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        responses={200: ProjectSingleResponse},
        tags=['Projects'],
        examples=[
            OpenApiExample(
                name="Get Project",
                value={
                    "data": {
                        "id": "2558",
                        "type": "projects",
                        "attributes": {"title": "A Project"},
                        "relationships": {
                            "people": {"data": []},
                            "projects": {"data": []},
                            "institutions": {"data": []},
                            "investigations": {"data": []},
                            "studies": {"data": []},
                            "assays": {"data": []},
                            "data_files": {"data": []},
                            "documents": {"data": []},
                            "models": {"data": []},
                            "sops": {"data": []},
                            "publications": {"data": []},
                            "presentations": {"data": []},
                            "events": {"data": []},
                            "workflows": {"data": []},
                            "collections": {"data": []}
                        },
                        "links": {"self": "/projects/2558"},
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
            return HttpResponse(b'{"errors":[{"title":"Project not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.get_project(request, seek_id)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            ProjectSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # POST /projects
    @extend_schema(
        operation_id="Create a Project",
        description=PROJECT_CREATE_DESC,
        request=ProjectCreateRequest,
        responses={201: ProjectSingleResponse, 200: ProjectSingleResponse},
        tags=['Projects'],
        examples=[
            OpenApiExample(
                name="Create Project",
                value={
                    "data": {
                        "type": "projects",
                        "attributes": {"title": "A Project"},
                        "relationships": {"programmes": {"data": []}, "organisms": {"data": []}}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = ProjectCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_project(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            ProjectSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    # PATCH /projects/{uid}
    @extend_schema(
        operation_id="Update a Project",
        description=PROJECT_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric) or NExtSEEK UID (string)')
        ],
        request=ProjectUpdateRequest,
        responses={200: ProjectSingleResponse},
        tags=['Projects'],
        examples=[
            OpenApiExample(
                name="Patch Project",
                value={
                    "data": {
                        "type": "projects",
                        "id": "2558",
                        "attributes": {"title": "Updated title"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = ProjectUpdateRequest.model_validate(request.data)
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
                return HttpResponse(b'{"errors":[{"title":"Project not found"}]}', status=404, content_type='application/json')

        body, code, headers, resp = self.client.update_project(request, payload['data']['id'], payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            ProjectSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


