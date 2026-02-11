from typing import Optional

import json
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.endpoint_descriptions import (
    PEOPLE_LIST_DESC,
    PEOPLE_FETCH_DESC,
    PEOPLE_CREATE_DESC,
    PEOPLE_UPDATE_DESC,
)
from nextseek_api.models import (
    PersonListResponse,
    PersonSingleResponse,
    PersonCreateRequest,
    PersonUpdateRequest,
)
def _validate_seek_id(id_or_uid: str) -> Optional[str]:
    s = str(id_or_uid)
    return s if s.isdigit() else None


class PeopleProxyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    client = SeekAPIClient()
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="List People",
        description=PEOPLE_LIST_DESC,
        responses={200: PersonListResponse},
        tags=['People'],
        examples=[
            OpenApiExample(
                name="List People",
                value={
                    "data": [
                        {
                            "id": "1652",
                            "type": "people",
                            "attributes": {"title": "Doe, John"},
                            "links": {"self": "/people/1652"}
                        }
                    ],
                    "jsonapi": {"version": "1.0"},
                    "links": {"self": "/people?page[number]=1&page[size]=100"},
                    "meta": {"base_url": "http://localhost:3000", "api_version": "v1"}
                }
            )
        ],
    )
    def list(self, request):
        body, code, headers, resp = self.client.list_people(request, params=request.query_params)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            PersonListResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        description=PEOPLE_FETCH_DESC,
        operation_id="Fetch a Person",
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric)')
        ],
        responses={200: PersonSingleResponse},
        tags=['People'],
        examples=[
            OpenApiExample(
                name="Get Person",
                value={
                    "data": {
                        "id": "1652",
                        "type": "people",
                        "attributes": {"title": "Doe, John"},
                        "relationships": {
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
                            "events": {"data": []},
                            "presentations": {"data": []},
                            "collections": {"data": []},
                            "workflows": {"data": []}
                        },
                        "links": {"self": "/people/1652"},
                        "meta": {}
                    },
                    "jsonapi": {"version": "1.0"}
                }
            )
        ],
    )
    def retrieve(self, request, uid=None, pk=None):
        uid = uid or pk
        seek_id = _validate_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Invalid id: must be numeric SEEK id"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.get_person(request, str(seek_id))
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            PersonSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Create a Person",
        description=PEOPLE_CREATE_DESC,
        request=PersonCreateRequest,
        responses={201: PersonSingleResponse, 200: PersonSingleResponse},
        tags=['People'],
        examples=[
            OpenApiExample(
                name="Create Person",
                value={
                    "data": {
                        "type": "people",
                        "attributes": {"first_name": "Post", "last_name": "User", "email": "x@y"}
                    }
                }
            )
        ],
    )
    def create(self, request):
        try:
            payload = PersonCreateRequest.model_validate(request.data).to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.create_person(request, payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            PersonSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)

    @extend_schema(
        operation_id="Update a Person",
        description=PEOPLE_UPDATE_DESC,
        parameters=[
            OpenApiParameter(name='uid', type=str, location=OpenApiParameter.PATH, description='SEEK id (numeric)')
        ],
        request=PersonUpdateRequest,
        responses={200: PersonSingleResponse},
        tags=['People'],
        examples=[
            OpenApiExample(
                name="Patch Person",
                value={
                    "data": {
                        "type": "people",
                        "id": "1652",
                        "attributes": {"first_name": "Patched"}
                    }
                }
            )
        ],
    )
    def partial_update(self, request, uid=None, pk=None):
        uid = uid or pk
        try:
            update_req = PersonUpdateRequest.model_validate(request.data)
            payload = update_req.to_seek_payload()
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid request"}]}', status=422, content_type='application/json')

        seek_id = _validate_seek_id(uid)
        if seek_id is None:
            return HttpResponse(b'{"errors":[{"title":"Invalid id: must be numeric SEEK id"}]}', status=422, content_type='application/json')

        body_id = payload.get('data', {}).get('id')
        if body_id is None:
            payload['data']['id'] = str(seek_id)
        elif str(body_id) != str(seek_id):
            return HttpResponse(b'{"errors":[{"title":"Payload id does not match path id"}]}', status=422, content_type='application/json')

        body, code, headers, resp = self.client.update_person(request, str(seek_id), payload)
        if code == 401:
            return HttpResponse(b'{"detail":"Authentication required"}', status=401, content_type='application/json')

        try:
            ct = (headers.get('Content-Type') or '').lower()
            if 'text/html' in ct or (isinstance(body, (bytes, bytearray)) and b'<html' in (body or b'')):
                return HttpResponse(b'{"errors":[{"title":"Upstream returned HTML (likely unauthenticated to SEEK)","detail":"Verify SEEK credentials/session for this request context."}]}', status=502, content_type='application/json')
            data = json.loads(body or b"{}")
            PersonSingleResponse.model_validate(data)
        except Exception:
            return HttpResponse(b'{"errors":[{"title":"Invalid upstream response"}]}', status=502, content_type='application/json')

        ct = headers.get('Content-Type', 'application/json')
        return HttpResponse(body, status=code, content_type=ct)


