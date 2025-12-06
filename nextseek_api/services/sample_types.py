from typing import Optional, List

import json
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
# from drf_spectacular.types import OpenApiTypes
from django.conf import settings
from pydantic import ValidationError

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.services.samples import _resolve_uid_to_seek_id as _resolve_sample_uid_to_seek_id
from nextseek_api.models import (
    SampleTypeListResponse,
    SampleTypeSingleResponse,
    SampleTypeCreateRequest,
    SampleTypeUpdateRequest,
)
from nextseek_api.models import SamplesByChildTypesRequest, SampleUIDItem, SamplesByChildTypesResponse
from seek.dbtable_sampletype import DBtable_sampletype
from seek.models import Sample_types

# Neo4j
import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError


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
        description="Retrieve all sample type definitions available in the system. Returns a list of sample type schemas including their titles, descriptions, and attribute configurations for categories like tissue samples, cell lines, animals, imaging data, and sequencing data. Examples: 'What types of samples can I register?'; 'Show me all available sample categories'",
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
        description="Fetch details for a specific sample type by ID or name. Returns the full schema definition including title, description, and all attribute specifications that samples of this type must have. Examples: 'What fields are required for tissue samples?'; 'Show me the schema for imaging data samples'",
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
        description="Register a new sample type with custom attributes and validation rules. Returns the created sample type with its assigned ID and full schema definition. Examples: 'Create a new sample type for flow cytometry data'; 'Add a custom sample category for behavioral observations'",
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
        description="Modify an existing sample type's attributes, description, or validation rules. Returns the updated sample type with all current settings. Examples: 'Add a new required field to the tissue sample type'; 'Update the description for sequencing data samples'",
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


class SampleTypeChildrenViewSet(viewsets.GenericViewSet):
    """Return unique child sample type titles for a given Sample (by id or UID)."""
    permission_classes = [IsAuthenticated]
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="Get Child Sample Types",
        description="Get the distinct types of all samples derived from a given sample. Traverses the sample hierarchy to find all related samples below it. Returns a list of unique type titles (e.g., PAV, D.SEQ, A.GEX). Examples: 'What types of samples were collected from NHP-220630FLY-1-PUB?'; 'Does this monkey have any imaging data?'",
        parameters=[
            OpenApiParameter(
                name='uid',
                type=str,
                location=OpenApiParameter.PATH,
                description='SEEK id (numeric) or Sample UID (string)'
            )
        ],
        responses={200: List[str]},
        examples=[
            OpenApiExample(name="By numeric id", value={"uid": "123"}),
            OpenApiExample(name="By UID", value={"uid": "ABC-DEF-UUID"}),
        ],
        tags=['Samples'],
    )
    @action(detail=True, methods=["get"], url_path="child_types")
    def child_types(self, request, uid=None, pk=None):
        # Auth gate: allow BASIC header or session; otherwise 401
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Resolve uid/id to numeric SEEK id
        path_value = uid or pk
        seek_id = _resolve_sample_uid_to_seek_id(str(path_value))
        if seek_id is None:
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)

        NEO4J_DATABASE = settings.NEO4J_DATABASE
        try:
            with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
                query = (
                    """
                    MATCH (s: Sample {id: toInteger($id)})
                    MATCH (s)<-[:CHILD_OF*1..]-(child)
                    RETURN DISTINCT child.type AS type
                    ORDER BY type
                    """
                )
                records, summary, keys = driver.execute_query(
                    query,
                    id=int(seek_id),
                    database_=NEO4J_DATABASE['NAME']
                )
                types_list = [r["type"] for r in records if r["type"] is not None]
                return Response(types_list, status=status.HTTP_200_OK)
        except Neo4jError:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)


class SamplesByChildTypesViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Get samples with children of specified sample types",
        description="Find parent samples that have derived samples of all specified types. Uses AND logic across types, so only samples with descendants matching every requested type are returned. Optionally filter by parent sample type. Returns a list of matching sample IDs, UIDs, sample types, and descriptions. Examples: 'Which monkeys have both imaging data and sequencing results?'; 'Find all animals that have tissue samples and cell line derivatives'",
        request=SamplesByChildTypesRequest,
        responses={200: SamplesByChildTypesResponse},
        examples=[
            OpenApiExample(
                name="By child type titles",
                value={
                    "child_sample_types": ["D.IMG", "TIS"]
                },
            ),
            OpenApiExample(
                name="With parent type filter",
                value={
                    "child_sample_types": ["D.IMG", "D.SEQ"],
                    "parent_sample_type_filters": ["NHP"]
                },
            ),
        ],
        tags=["SampleTypes"],
    )
    @action(detail=False, methods=["post"], url_path="parents_by_child_types")
    def parents_by_child_types(self, request):
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Validation using Pydantic V2
        try:
            req = SamplesByChildTypesRequest.model_validate(request.data)
        except ValidationError as e:
            return Response({"errors": [{"title": "Invalid request", "detail": e.errors()}]}, status=422)

        # Normalize child sample types (titles or IDs -> titles)
        child_titles: List[str] = []
        if req.child_sample_types:
            for item in req.child_sample_types:
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                    # Resolve ID to title via ORM
                    try:
                        st = Sample_types.objects.filter(id=int(item)).values_list('title', flat=True).first()
                        if st:
                            child_titles.append(st)
                    except Exception:
                        pass
                elif isinstance(item, str):
                    child_titles.append(item)

        input_types = sorted({t.strip().upper() for t in child_titles if t and isinstance(t, str)})
        if not input_types:
            return Response({"errors": [{"title": "Invalid request", "detail": "Provide at least one valid child sample type."}]}, status=422)

        # Normalize parent sample type filters (titles or IDs -> titles)
        parent_types: List[str] = []
        if req.parent_sample_type_filters:
            for item in req.parent_sample_type_filters:
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                    # Resolve ID to title via ORM
                    try:
                        st = Sample_types.objects.filter(id=int(item)).values_list('title', flat=True).first()
                        if st:
                            parent_types.append(st.strip().upper())
                    except Exception:
                        pass
                elif isinstance(item, str):
                    parent_types.append(item.strip().upper())

        # Build Cypher query with optional parent type filtering
        if parent_types:
            cypher = """
                WITH $types AS input_types, $parent_types AS parent_types
                MATCH (p:Sample)
                WHERE p.type IN parent_types
                MATCH (c:Sample)-[:CHILD_OF*1..]->(p)
                WHERE c.type IN input_types
                WITH p, collect(DISTINCT c.type) AS matched_types, input_types
                WHERE size(matched_types) = size(input_types)
                RETURN p.id AS id, p.uuid AS uuid, p.type AS type
                ORDER BY id
            """
        else:
            cypher = """
                WITH $types AS input_types
                MATCH (p:Sample)
                MATCH (c:Sample)-[:CHILD_OF*1..]->(p)
                WHERE c.type IN input_types
                WITH p, collect(DISTINCT c.type) AS matched_types, input_types
                WHERE size(matched_types) = size(input_types)
                RETURN p.id AS id, p.uuid AS uuid, p.type AS type
                ORDER BY id
            """

        NEO4J_DATABASE = settings.NEO4J_DATABASE
        try:
            with GraphDatabase.driver(NEO4J_DATABASE["URI"], auth=NEO4J_DATABASE["AUTH"]) as driver:
                query_params = {"types": input_types}
                if parent_types:
                    query_params["parent_types"] = parent_types
                records, summary, keys = driver.execute_query(
                    cypher,
                    **query_params,
                    database_=NEO4J_DATABASE["NAME"],
                )
        except Neo4jError:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            return Response({"errors": [{"title": "Invalid upstream response"}]}, status=status.HTTP_502_BAD_GATEWAY)

        # Collect unique sample types from results for description lookup
        unique_types = set()
        for r in records:
            rtype = r.get("type")
            if rtype:
                unique_types.add(rtype)

        # Lookup sample type descriptions from database
        desc_lookup: dict = {}
        if unique_types:
            try:
                qs = Sample_types.objects.filter(title__in=unique_types).values('title', 'description')
                for row in qs:
                    desc_lookup[row['title']] = row.get('description')
            except Exception:
                pass  # Continue without descriptions if lookup fails

        # Build response items using Pydantic models
        items = []
        for r in records:
            rid = r["id"]
            ruid = r["uuid"]
            rtype = r.get("type")
            if rid is not None and ruid is not None:
                items.append(SampleUIDItem(
                    id=str(rid),
                    uuid=str(ruid),
                    sample_type=rtype or "",
                    sample_type_description=desc_lookup.get(rtype) if rtype else None
                ))

        response = SamplesByChildTypesResponse(samples=items)
        return Response(response.model_dump(), status=status.HTTP_200_OK)
