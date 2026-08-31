import json
import io
import datetime
import logging
import os
import MySQLdb
from django.conf import settings
from django.http import FileResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.openapi import OpenApiExample
from pydantic import ValidationError

# Import Neo4j
import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError

# Import legacy classes and functions needed for core logic
from seek.seekdb import SeekDB
from seek.dbtable_sample import DBtable_sample
from seek.timeline.services.timeline_service import run_All, get_event_data
from seek.timeline.services.nhp_service import save_nhp_info_to_json, get_timeline_data, save_nhp_data
from .batch_upload.views import BatchUploadViewSet

logger = logging.getLogger(__name__)

NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
SEEK_DATABASE = settings.SEEK_DATABASE

# Import serializers
from .serializers import (
    SampleTreeSerializer,
    SampleNodeSerializer,
    SampleRetrieveRequestSerializer,
    SampleAdvancedRetrieveRequestSerializer,
)
from .services.sops import SopProxyViewSet as SopViewSet
from .services.data_files import DataFileProxyViewSet as DataFileViewSet
from .services.projects import ProjectProxyViewSet as ProjectViewSet
from .services.people import PeopleProxyViewSet as PeopleViewSet
from .services.users import UsersViewSet
from .services.investigations import InvestigationProxyViewSet as InvestigationViewSet
from .services.studies import StudyProxyViewSet as StudyViewSet
from .attributes.views import AttributeViewSet
from .services.assays import AssayProxyViewSet as AssayViewSet
from .services.sample_types import SampleTypeProxyViewSet as SampleTypeViewSet
from .services.sample_types import SampleTypeChildrenViewSet as SampleTypeChildrenViewSet
from .services.sample_types import SamplesByChildTypesViewSet as SamplesByChildTypesViewSet
from .services.sampletype_connections import SampleTypeConnectionsViewSet as SampleTypeConnectionsViewSet
from .services.samples import SampleProxyViewSet as SampleViewSet
from .services.samples import _resolve_uid_to_seek_id
from .services.samples import SampleAdvancedSearchViewSet as SampleAdvancedSearchViewSet
from .services.schema_rag import SchemaRAGViewSet
from .services.assistant import AssistantViewSet
# Additive dmac_assistant integration (router + Container-Claude-Code).
from .services.cc_assistant import CCAssistantViewSet
from .services.evaluator import EvaluatorViewSet
from .services.entity_tree import EntityTreeViewSet
from .services.project_export import ProjectExportViewSet
from .helpers import resolve_seek_auth
from nextseek_api.helpers import StandardResultsSetPagination
from nextseek_api.endpoint_descriptions import SAMPLE_TREE_GET_DESC, ADMIN_SAMPLE_RETRIEVE_DESC
from nextseek_api.models import (
    SampleTreeResponse,
    SampleTreeNode,
    SampleTreeRel,
    AdminSampleRetrieveRequest,
    AdminSampleRetrieveResponse,
    AdminSampleGroup,
)


def get_clade_color(sample_type):
    """Extract core logic from seek.views.get_clade_color"""
    db = settings.DATABASES[SEEK_DATABASE]
    nextseekdb = settings.DATABASES[NEXTSEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()
    # sample_type is caller-supplied and MUST stay parameterized. Only the
    # schema names (from settings) are interpolated, matching the already-safe
    # twin at services/entity_tree.py::EntityTreeViewSet._get_clade_color.
    query = f"""
    SELECT c.color FROM {nextseekdb["NAME"]}.clades c
    JOIN {nextseekdb["NAME"]}.sample_types_clades stc ON stc.clade_id = c.id
    JOIN {db["NAME"]}.sample_types st ON stc.sample_type_id = st.id
    WHERE st.title = %s
    """

    cursor.execute(query, [sample_type])
    try:
        color = cursor.fetchone()[0]
    except Exception:
        color = "#000000"
    
    cursor.close()
    conn.close()
    return color


def _caller_seek_project_ids(basic_tuple):
    """SEEK project ids the caller belongs to, as a list of str.

    Same derivation as AdminSampleViewSet.admin_retrieve_samples: SeekDB has to
    be built from real credentials, because the username-is-None branch
    (seek/seekdb.py:31) never calls getSeekLogin() and so leaves __server unset,
    after which getCurrentUser() raises.

    Returns [] when the caller's projects cannot be resolved. Callers must treat
    that as "sees nothing", not as "sees everything".
    """
    if not basic_tuple or not basic_tuple[0] or not basic_tuple[1]:
        return []
    try:
        seekdb = SeekDB(None, basic_tuple[0], basic_tuple[1])
        user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
        return [str(p['id']) for p in user_projects]
    except Exception:
        logger.exception("Could not resolve SEEK projects for the caller")
        return []


def _samples_visible_to_projects(sample_ids, project_ids):
    """Subset of ``sample_ids`` (SEEK ``samples.id``) that belongs to one of
    ``project_ids``, as a set of str.

    Fails closed: an empty project list, an unresolvable id, or a DB error all
    yield an empty set, i.e. nothing is visible.
    """
    numeric = []
    for sid in sample_ids:
        try:
            numeric.append(int(sid))
        except (TypeError, ValueError):
            continue
    scoped_projects = [str(pid) for pid in (project_ids or []) if str(pid).strip()]
    if not numeric or not scoped_projects:
        return set()

    db = settings.DATABASES[SEEK_DATABASE]
    try:
        conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    except Exception:
        logger.exception("Could not open SEEK DB connection for a project-scope check")
        return set()
    try:
        cursor = conn.cursor()
        # Both lists are bound, never interpolated; only the schema name (from
        # settings) is formatted in.
        sample_placeholders = ', '.join(['%s'] * len(numeric))
        project_placeholders = ', '.join(['%s'] * len(scoped_projects))
        cursor.execute(
            f"""
            SELECT DISTINCT sample_id
            FROM {db["NAME"]}.projects_samples
            WHERE sample_id IN ({sample_placeholders})
              AND project_id IN ({project_placeholders})
            """,
            numeric + scoped_projects,
        )
        rows = cursor.fetchall()
        cursor.close()
        return {str(r[0]) for r in rows if r and r[0] is not None}
    except Exception:
        logger.exception("Project-scope check failed")
        return set()
    finally:
        try:
            conn.close()
        except Exception:
            pass


class SampleTreeViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample tree retrieval by SEEK id (numeric) or Sample UID (string).
    Mirrors SampleProxyViewSet resolution behavior.
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'uid'
    lookup_url_kwarg = 'uid'
    lookup_value_regex = r'[^/]+'

    @extend_schema(
        operation_id="Get Sample Tree by id or uid",
        description=SAMPLE_TREE_GET_DESC,
        tags=['Samples'],
        responses={200: SampleTreeResponse},
        parameters=[
            OpenApiParameter(
                name='uid',
                type=str,
                location=OpenApiParameter.PATH,
                description='SEEK id (numeric) or Sample UID (string)'
            )
        ],
        examples=[
            OpenApiExample(
                name="Tree by numeric id",
                value={"uid": "123"},
                request_only=True,
            ),
            OpenApiExample(
                name="Tree by UID",
                value={"uid": "ABC-DEF-UUID"},
                request_only=True,
            ),
            OpenApiExample(
                name="Sample tree response",
                value={
                    "total_nodes": 2,
                    "total_rels": 1,
                    "nodes": [
                        {
                            "id": "1525",
                            "uuid": "PAV-230522GRI-7-PUB",
                            "sample_type": "PAV",
                            "color": "#A3D46F",
                            "parentIds": ["1506"],
                        },
                        {
                            "id": "1506",
                            "uuid": "PAT-230522GRI-7-PUB",
                            "sample_type": "PAT",
                            "color": "#A3D46F",
                            "parentIds": [],
                        },
                    ],
                    "rels": [
                        {
                            "child_id": "1525",
                            "parent_id": "1506",
                            "internal_assay_id": "380",
                            "internal_assay_title": "Patient Visit",
                            "protocol_title": "P.GRI-230228-V1_2.1-Study-Approval.docx",
                            "protocol_id": "145",
                        }
                    ],
                },
                response_only=True,
            ),
        ]
    )
    @action(detail=True, methods=["get"], url_path="tree")
    def get_tree(self, request, uid=None, pk=None):
        """Resolve uid to SEEK id when needed, then run the Neo4j tree query."""
        # Validate user authentication: allow BASIC header or session; no token here
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Resolve to numeric sample_id
        path_value = uid or pk
        seek_id = _resolve_uid_to_seek_id(str(path_value))
        if seek_id is None:
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)

        # Project scope (#60). The traversal below resolves ANY uid and walks
        # DERIVED_FROM from it, so without this gate any authenticated caller
        # could read the lineage of any sample in the instance by UID.
        #
        # is_superuser ALONE is the admin predicate, deliberately. is_staff is
        # set to 1 on every SEEK user at login (dmac/views.py:80 and :97, both
        # the create and the update branch), so including it -- as
        # admin_retrieve_samples still does, see the SECURITY comment there --
        # would make this scoping a no-op for every account. Same predicate as
        # nextseek_api.permissions.IsSuperUser and seek.views.verifySuperUser.
        is_admin = bool(getattr(request.user, 'is_superuser', False))
        project_ids = [] if is_admin else _caller_seek_project_ids(basic_tuple)
        if not is_admin and str(seek_id) not in _samples_visible_to_projects([seek_id], project_ids):
            # Same 404 as an unknown UID: do not confirm the sample exists to a
            # caller who may not see it.
            return Response({"detail": "Sample not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            NEO4J_DATABASE = settings.NEO4J_DATABASE
            with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
                # Mirror legacy SEEK behavior (seek/views.py sampleTreeNew/sampleTreeNewUID):
                # Traverse DERIVED_FROM in both directions and return nodes + relationships for visualization.
                r = driver.execute_query(
                    """
                    MATCH (s: Sample {id: toInteger($id)})
                    MATCH parents=(s)-[r1:DERIVED_FROM*0..]->(parent)
                    MATCH children=(s)<-[r2:DERIVED_FROM*0..]-(child)
                    RETURN collect(DISTINCT s) + collect(DISTINCT parent) + collect(DISTINCT child) AS nodes, r1 + r2 AS relationships
                    """,
                    id=int(seek_id),
                    result_transformer_=neo4j.Result.graph,
                    database_=NEO4J_DATABASE['NAME']
                )

                nodeDict = {}
                for node in r.nodes:
                    nodeProps = node._properties
                    nodeUid = nodeProps['uuid']
                    nodeId = nodeProps['id']
                    nodeType = nodeProps['type']
                    nodeDict[nodeUid] = {'id': str(nodeId), 'type': nodeType, 'parents': []}

                rel_props = []
                # Endpoint ids taken from the graph itself, in lockstep with
                # rel_props, so pruning below does not depend on the
                # relationship carrying child_id/parent_id properties.
                rel_endpoints = []
                for rel in r.relationships:
                    relProps = rel._properties
                    startRelProps = rel.start_node._properties
                    endRelProps = rel.end_node._properties

                    nodeUid = startRelProps['uuid']
                    parentId = str(endRelProps['id'])

                    if nodeDict.get(nodeUid):
                        nodeDict[nodeUid]['parents'].append(parentId)
                    else:
                        # Should not happen if r.nodes includes all relationship endpoints,
                        # but keep this defensive to avoid KeyError.
                        nodeDict[nodeUid] = {
                            'id': str(startRelProps.get('id', '')),
                            'type': startRelProps.get('type', ''),
                            'parents': [parentId]
                        }

                    # Normalize relationship id fields to numeric strings for parity with SampleTreeNode.id
                    relProps_norm = dict(relProps or {})
                    for k in ("child_id", "parent_id", "internal_assay_id", "protocol_id"):
                        if k in relProps_norm and relProps_norm[k] is not None:
                            relProps_norm[k] = str(relProps_norm[k])
                    rel_props.append(relProps_norm)
                    rel_endpoints.append((str(startRelProps.get('id', '')), parentId))

                # Prune anything outside the caller's projects (#60). The
                # traversal reaches ancestors and descendants that may live in
                # other projects, so the root gate above is not sufficient on
                # its own. parentIds and rels are pruned in lockstep: a node
                # whose parent was dropped simply becomes a root, so the
                # response stays referentially consistent for the D3 consumer
                # (static/js/dag/dag.js, whose graphStratify throws on a
                # dangling parent id).
                if not is_admin:
                    visible_ids = _samples_visible_to_projects(
                        [v['id'] for v in nodeDict.values()], project_ids
                    )
                    nodeDict = {
                        uid_key: v for uid_key, v in nodeDict.items()
                        if str(v['id']) in visible_ids
                    }
                    for v in nodeDict.values():
                        v['parents'] = [p for p in v['parents'] if p in visible_ids]
                    rel_props = [
                        props for props, (child_id, parent_id) in zip(rel_props, rel_endpoints)
                        if child_id in visible_ids and parent_id in visible_ids
                    ]

                nodes = []
                for k, v in nodeDict.items():
                    color = get_clade_color(v['type'])
                    nodes.append(SampleTreeNode(
                        id=v['id'],
                        uuid=k,
                        sample_type=v['type'],
                        color=color,
                        parentIds=v['parents'],
                    ))

                response = SampleTreeResponse(
                    total_nodes=len(nodes),
                    total_rels=len(rel_props),
                    nodes=nodes,
                    rels=[SampleTreeRel.model_validate(p) for p in rel_props],
                )
                return Response(response.model_dump(), status=status.HTTP_200_OK)
        except AuthError as e:
            return Response(
                {"detail": f"Neo4j authentication failed: {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Neo4jError as e:
            return Response(
                {"detail": f"Neo4j query failed: {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Exception as e:
            return Response(
                {"detail": f"Tree query failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class NHPViewSet(viewsets.GenericViewSet):
    """
    ViewSet for NHP (Non-Human Primate) data operations.
    Extracts core logic from NHP functions without decorator bypass issues.
    """
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id="Get NHP Info",
        description="Get NHP info by UID",
        tags=['Timeline'],
        responses={200: OpenApiTypes.OBJECT},
        parameters=[
            OpenApiParameter(
                name='id',
                type=str,
                location=OpenApiParameter.PATH,
                description='NHP identifier/name'
            )
        ],
        examples=[
            OpenApiExample(
                name="NHP Info Response",
                value={"id": "FLY001", "metadata": {"info": "example"}}
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path="info")
    def info(self, request, pk=None):
        """Extract core logic from nhp_info function."""
        try:
            nhp_info = save_nhp_info_to_json(pk)
            if nhp_info:
                return Response(nhp_info, status=status.HTTP_200_OK)
            else:
                return Response({"detail": "NHP Info not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @extend_schema(
        operation_id="Get NHP Events",
        description="Get NHP events by UID, event type, and date",
        tags=['Timeline'],
        responses={200: OpenApiTypes.OBJECT},
        parameters=[
            OpenApiParameter(
                name='id',
                type=str,
                location=OpenApiParameter.PATH,
                description='NHP identifier/name'
            ),
            OpenApiParameter(
                name='event_type',
                type=str,
                location=OpenApiParameter.PATH,
                description='Type of event (e.g., feeding, medication)'
            ),
            OpenApiParameter(
                name='date',
                type=str,
                location=OpenApiParameter.PATH,
                description='Date in YYYY-MM-DD format'
            )
        ],
        examples=[
            OpenApiExample(
                name="Event Data Response",
                value={"event_type": "feeding", "date": "2023-01-01", "data": {}}
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path=r"events/(?P<event_type>[^/]+)/(?P<date>[^/]+)")
    def events(self, request, pk=None, event_type=None, date=None):
        """Extract core logic from fetch_event_data function."""
        if not pk:
            return Response({"detail": "NHP data not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            event_data = get_event_data(pk, event_type, date)
            if event_data:
                return Response(event_data, status=status.HTTP_200_OK)
            else:
                return Response({"detail": "Event data not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @extend_schema(
        operation_id="Get NHP Timeline Data",
        description="Get NHP timeline by UID",
        tags=['Timeline'],
        responses={200: OpenApiTypes.OBJECT},
        parameters=[
            OpenApiParameter(
                name='id',
                type=str,
                location=OpenApiParameter.PATH,
                description='NHP identifier/name'
            )
        ],
        examples=[
            OpenApiExample(
                name="Timeline Data Response",
                value={"timeline": "data"}
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        """Extract core logic from get_nhp_data function."""
        try:
            timeline_data = run_All(pk)
            if timeline_data:
                return Response(timeline_data, status=status.HTTP_200_OK)
            else:
                return Response({"detail": "Event Data not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @extend_schema(
        operation_id="Download NHP Data",
        description="Download NHP data as Excel file by UID",
        tags=['Timeline'],
        responses={200: OpenApiTypes.STR},
        parameters=[
            OpenApiParameter(
                name='id',
                type=str,
                location=OpenApiParameter.PATH,
                description='NHP identifier/name'
            )
        ],
    )
    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        """Extract core logic from download_nhp_data function."""
        try:
            timeline_data = get_timeline_data(pk)
            if not timeline_data:
                return Response({"detail": "NHP data not found"}, status=status.HTTP_404_NOT_FOUND)
            
            # Convert to Excel
            excel_data = save_nhp_data(timeline_data)
            
            # Create a streaming response with proper content type
            response = FileResponse(
                io.BytesIO(excel_data),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                filename=f"{pk}_data.xlsx"
            )
            return response
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SampleQueryViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample query operations with pagination.
    Extracts core logic from retrieveSamples and converts HttpResponse to DRF Response.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    @extend_schema(
        responses={200: OpenApiTypes.OBJECT},
        operation_id="Simple Sample Search",
        description="Simple sample retrieval via stable SQL path (FILTERING).",
        request=SampleAdvancedRetrieveRequestSerializer,
        tags=['Samples'],
        examples=[
            OpenApiExample(
                name="Keyword within json_metadata",
                value={
                    "sampletype_id": 12,
                    "attribute": "none",
                    "filter_valueFrom": "SARS-CoV-2",
                    "project_id": 0
                }
            ),
            OpenApiExample(
                name="Attribute-based range",
                value={
                    "sampletype_id": 12,
                    "attribute": "CollectionDate",
                    "filter_rule": "range",
                    "filter_valueFrom": "2023-01-01",
                    "filter_valueTo": "2023-12-31",
                    "project_id": 0
                }
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="retrieve")
    def retrieve_samples(self, request):
        """
        Extract core logic from retrieveSamples function.
        Convert HttpResponse to DRF Response and add pagination.
        """
        # Core logic extracted from retrieveSamples (lines 892-896)
        # Gate using BASIC header or session only (no token)
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple:
            return Response(
                {"detail": "Authentication required"}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        dbsample = DBtable_sample()
        try:
            # Validate advanced search body
            params = SampleAdvancedRetrieveRequestSerializer(data=request.data)
            params.is_valid(raise_exception=True)
            body = params.validated_data

            # Build filters expected by __parseSearchFilters for searchType "FILTERING"
            filters = {
                'sampletype_id': body.get('sampletype_id'),
                'attribute': body.get('attribute', 'none') or 'none',
                'filter_rule': body.get('filter_rule'),
                'filter_valueFrom': body.get('filter_valueFrom'),
                'filter_valueTo': body.get('filter_valueTo'),
            }
            project_id = int(body.get('project_id') or 0)

            # Execute advanced retrieval (stable SQL path) and return JSON
            # Reconstruct minimal user_seek dict required by legacy code when using BASIC header
            if isinstance(basic_tuple, tuple):
                user_seek = {'status': True, 'username': basic_tuple[0], 'password': basic_tuple[1]}
            else:
                seekdb = SeekDB(None, None, None)
                user_seek = seekdb.getSeekLogin(request, False)

            reportData = dbsample.searchAdvanced(user_seek, filters, 'FILTERING', project_id)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Convert underlying JSON string to DRF Response
        try:
            payload = json.loads(reportData)
        except json.JSONDecodeError:
            return Response({"data": reportData}, status=status.HTTP_200_OK)

        # Paginate rows array if present
        if isinstance(payload, dict) and isinstance(payload.get('rows'), list):
            paginator = self.pagination_class()
            page = paginator.paginate_queryset(payload['rows'], request)
            # Keep other keys (e.g., msg/status) while replacing rows with the page
            paginated_payload = {k: v for k, v in payload.items() if k != 'rows'}
            paginated_payload['rows'] = page
            return paginator.get_paginated_response(paginated_payload['rows'])

        return Response(payload, status=status.HTTP_200_OK)


class AdminSampleViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample retrieval and export.
    Supports JSON export (default) and Excel export (opt-in) of sample metadata,
    optionally including parent/child (derived) samples.

    The `admin/` in the route is historical. This is the single download API
    behind every sample-download control in the UI, so it is gated on
    authentication only; data scope is enforced per caller further down.
    """
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id="Admin Sample Retrieval",
        tags=['Samples'],
        request=AdminSampleRetrieveRequest,
        description=ADMIN_SAMPLE_RETRIEVE_DESC,
        responses={
            (200, "application/json"): AdminSampleRetrieveResponse,
            (200, "application/vnd.ms-excel"): OpenApiResponse(
                response=OpenApiTypes.BINARY,
                description="Excel workbook (XLSX) containing sample metadata grouped by sample type.",
            ),
        },
        examples=[
            OpenApiExample(
                name="JSON output (default)",
                value={"identifiers": ["NHP-220630FLY-1-PUB", "TIS-230324BOO-39-PUB"]},
                request_only=True,
            ),
            OpenApiExample(
                name="Excel output with mixed IDs",
                value={"identifiers": ["NHP-220630FLY-1-PUB", "12345"], "output_format": "excel"},
                request_only=True,
            ),
            OpenApiExample(
                name="SEEK IDs only",
                value={"identifiers": ["12345", "67890"], "output_format": "json"},
                request_only=True,
            ),
        ]
    )
    @action(detail=False, methods=["post"], url_path="retrieve")
    def admin_retrieve_samples(self, request):
        """Admin export: accepts sample UIDs/SEEK ids, returns JSON (default) or an Excel workbook (opt-in)."""
        # Gate using BASIC header or session only (no token)
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Normalize request payload for pydantic validation (support legacy field names as input)
        body = request.data or {}
        if isinstance(body, dict):
            output_format = body.get("output_format", "json")
            # Form-encoded callers send "false"/"0"; pydantic coerces both.
            include_tree = body.get("include_tree", True)
            raw_identifiers = body.get("identifiers")
            if raw_identifiers is None:
                raw_identifiers = body.get("retrieval_uids") or body.get("uids") or body.get("retrieval_uids_text") or ""
        else:
            output_format = "json"
            include_tree = True
            raw_identifiers = ""

        if isinstance(raw_identifiers, list):
            identifiers = [str(u).strip() for u in raw_identifiers if str(u).strip()]
        else:
            identifiers = str(raw_identifiers or "").strip().split()

        try:
            req = AdminSampleRetrieveRequest.model_validate(
                {
                    "identifiers": identifiers,
                    "output_format": output_format,
                    "include_tree": include_tree,
                }
            )
        except ValidationError as e:
            return Response(
                {"detail": "Invalid request", "errors": e.errors()},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if not req.identifiers:
            return Response({"detail": "identifiers required"}, status=status.HTTP_400_BAD_REQUEST)

        # Project scope for this caller.
        #
        # This used to be SeekDB(None, None, None), which takes the username-is-None
        # branch (seek/seekdb.py:31), never calls getSeekLogin(), and so leaves
        # __server None. getCurrentUser() then did None + "/people/current"
        # (seek/seekapi.py:188) -> TypeError -> swallowed by the bare except below ->
        # user_project_ids was ALWAYS []. Build it from the credentials
        # resolve_seek_auth already returned at the top of this method instead.
        seekdb = SeekDB(None, basic_tuple[0], basic_tuple[1])
        try:
            user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
            user_project_ids = list(map(lambda x: x['id'], user_projects))
        except Exception:
            logger.exception("Could not resolve SEEK projects for the caller")
            user_project_ids = []
        # Project membership is the data-scope boundary for this endpoint (#74).
        #
        # `is_staff` must NOT widen it: dmac/views.py:80,97 set is_staff = 1 on every
        # SEEK user at login, so `or is_staff` made project scope a no-op for everyone
        # and handed every authenticated account the unfiltered branch of
        # getChildrenUIDs. is_superuser is never assigned by any live application path,
        # so it is the only trustworthy admin signal here — same predicate as the legacy
        # path this mirrors, seek/views.py:1249 (verifySuperUser).
        #
        # FUTURE (deliberately not built): a scoped miss and a nonexistent UID are
        # currently indistinguishable — both return the 404 below, because the
        # project-id sentinel keeps the SQL valid and matching nothing. We COULD
        # distinguish them by re-querying unscoped on an empty result and returning 403
        # "exists but not in your projects". Not done on purpose: that response confirms
        # a UID is real to someone not authorised to see it. Revisit only if the
        # ambiguous 404 actually causes support load. See #74.
        is_superuser = bool(getattr(request.user, 'is_superuser', False))
        
        dbs = DBtable_sample()

        # Resolve numeric SEEK ids → sample UUIDs (used by downstream retrieval)
        requested_uids: list[str] = []
        numeric_ids: list[str] = []
        for it in req.identifiers:
            s = str(it or "").strip()
            if not s:
                continue
            if s.isdigit():
                numeric_ids.append(s)
            else:
                requested_uids.append(s)

        unresolved_numeric = 0
        if numeric_ids:
            try:
                db = settings.DATABASES[SEEK_DATABASE]
                conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
                cursor = conn.cursor()
                ids_str = ", ".join(str(int(x)) for x in numeric_ids)
                cursor.execute(f"SELECT id, uuid FROM {db["NAME"]}.samples WHERE id IN ({ids_str})")
                rows = cursor.fetchall()
                id_to_uuid = {str(r[0]): str(r[1]) for r in rows if r and r[0] is not None and r[1] is not None}
                for sid in numeric_ids:
                    u = id_to_uuid.get(str(sid))
                    if u:
                        requested_uids.append(u)
                    else:
                        unresolved_numeric += 1
                cursor.close()
                conn.close()
            except Exception:
                unresolved_numeric = len(numeric_ids)

        # Dedupe while preserving order
        seen = set()
        requested_uids = [u for u in requested_uids if not (u in seen or seen.add(u))]

        # Build dataset and write Excel to a temp path under MEDIA_ROOT/download
        try:
            if req.include_tree:
                children_uids_df = dbs.getChildrenUIDs(requested_uids, user_project_ids, is_superuser)
            else:
                # No graph expansion: fetch exactly what was asked for. Raising here
                # reuses the handler below, which is already the project-scoped MySQL
                # query this path needs.
                raise Neo4jError("include_tree=False")
        except IndexError:
            return Response({"detail": "No samples found for provided UIDs"}, status=status.HTTP_404_NOT_FOUND)
        except (AuthError, Neo4jError):
            # Fallback: proceed without Neo4j expansion; export provided UIDs only
            try:
                import pandas as pd  # local import to minimize surface area
                db = settings.DATABASES[SEEK_DATABASE]
                conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
                cursor = conn.cursor()

                # requested_uids comes straight from the caller's `identifiers`
                # POST body (models.py:1974 is an unvalidated List[str]), so it
                # MUST stay parameterized -- it used to be inlined as
                # "'%s'" % uid and a single quote broke out of the literal.
                # Only the schema name (from settings) is interpolated, matching
                # get_clade_color and services/entity_tree.py.
                uid_placeholders = ', '.join(['%s'] * len(requested_uids))

                if is_superuser:
                    query = f"""
                    SELECT id, sample_type_id, uuid, json_metadata
                    FROM {db["NAME"]}.samples
                    WHERE uuid IN ({uid_placeholders})
                    """
                    params = list(requested_uids)
                else:
                    # Sentinel keeps the statement valid, and matching nothing,
                    # when the caller has no mapped projects.
                    scoped_project_ids = [str(pid) for pid in user_project_ids] or ['']
                    project_placeholders = ', '.join(['%s'] * len(scoped_project_ids))
                    query = f"""
                    SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
                    FROM {db["NAME"]}.samples s
                    JOIN {db["NAME"]}.projects_samples ps
                    ON s.id = ps.sample_id
                    WHERE s.uuid IN ({uid_placeholders}) AND ps.sample_id = s.id AND ps.project_id IN ({project_placeholders})
                    """
                    params = list(requested_uids) + scoped_project_ids

                cursor.execute(query, params)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                children_uids_df = pd.DataFrame(rows, columns=columns)
                cursor.close()
                conn.close()
            except Exception as e:
                return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        if getattr(children_uids_df, 'empty', False):
            return Response({"detail": "No samples found for provided UIDs"}, status=status.HTTP_404_NOT_FOUND)

        # JSON output (default)
        if req.output_format == "json":
            # Compute failure count: unresolved numeric ids + requested UIDs that didn't appear in results
            try:
                returned_uids = set(str(u) for u in list(children_uids_df.get("uuid", [])))
            except Exception:
                returned_uids = set()
            missing_requested = len(set(requested_uids) - returned_uids) if requested_uids else 0
            failed_uids = int(unresolved_numeric + missing_requested)

            # Group by sample_type (same extraction used for Excel sheet naming)
            try:
                df = children_uids_df.copy()
                df["uuid"] = df["uuid"].astype(str)
                df["sample_type"] = df["uuid"].str.extract(r"([A-Z]+\.[A-Z]+|[A-Z]+)", expand=False).fillna("UNKNOWN")
            except Exception:
                df = children_uids_df

            def _parse_meta(val):
                try:
                    if isinstance(val, str):
                        return json.loads(val) if val else {}
                    if isinstance(val, dict):
                        return val
                except Exception:
                    pass
                return {}

            groups: list[AdminSampleGroup] = []
            try:
                for st, gdf in df.groupby("sample_type"):
                    records = []
                    for row in gdf.to_dict("records"):
                        records.append(
                            {
                                "id": str(row.get("id")) if row.get("id") is not None else None,
                                "uuid": str(row.get("uuid")) if row.get("uuid") is not None else None,
                                "sample_type_id": row.get("sample_type_id"),
                                "metadata": _parse_meta(row.get("json_metadata")),
                            }
                        )
                    groups.append(AdminSampleGroup(sample_type=str(st), samples=records, n_samples=len(records)))
            except Exception:
                # Fallback: treat as a single group
                records = []
                try:
                    for row in children_uids_df.to_dict("records"):
                        records.append(
                            {
                                "id": str(row.get("id")) if row.get("id") is not None else None,
                                "uuid": str(row.get("uuid")) if row.get("uuid") is not None else None,
                                "sample_type_id": row.get("sample_type_id"),
                                "metadata": _parse_meta(row.get("json_metadata")),
                            }
                        )
                except Exception:
                    records = []
                groups = [AdminSampleGroup(sample_type="UNKNOWN", samples=records, n_samples=len(records))]

            total_samples = int(getattr(children_uids_df, "shape", [0])[0] or 0)
            total_sample_types = int(len(groups))
            requested_found = len(set(requested_uids) & returned_uids) if requested_uids else 0
            total_children = int(max(0, total_samples - requested_found))

            resp = AdminSampleRetrieveResponse(
                data=groups,
                total_samples=total_samples,
                total_sample_types=total_sample_types,
                total_children=total_children,
                failed_uids=failed_uids,
            )
            return Response(resp.model_dump(mode="json", exclude_none=True), status=status.HTTP_200_OK)

        datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"download-samples-{datenow}.xlsx"
        download_dir = os.path.join(settings.MEDIA_ROOT, "download")
        os.makedirs(download_dir, exist_ok=True)
        downloadfile = os.path.join(download_dir, filename)

        dbs.sampleRetrievalData(children_uids_df, downloadfile)

        # Stream the file (let FileResponse manage the file handle)
        try:
            fh = open(downloadfile, 'rb')
            response = FileResponse(
                fh,
                content_type="application/vnd.ms-excel",
                as_attachment=True,
                filename=filename,
            )
            return response
        except FileNotFoundError:
            return Response({"detail": "Export failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
