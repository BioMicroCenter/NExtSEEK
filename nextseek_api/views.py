import json
import io
import datetime
import os
import MySQLdb
from django.conf import settings
from django.http import FileResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.openapi import OpenApiExample

# Import Neo4j
import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError

# Import legacy classes and functions needed for core logic
from seek.seekdb import SeekDB
from seek.dbtable_sample import DBtable_sample
from seek.timeline.services.timeline_service import run_All, get_event_data
from seek.timeline.services.nhp_service import save_nhp_info_to_json, get_timeline_data, save_nhp_data
from seek.views import get_children_uids, sample_retrieval_data

# Constants from legacy code
SEEK_DATABASE = 'default'

# Import serializers
from .serializers import SampleTreeSerializer, SampleNodeSerializer, AdminRetrieveRequestSerializer
from .services.sops import SopProxyViewSet as SopViewSet
from .services.data_files import DataFileProxyViewSet as DataFileViewSet
from .services.projects import ProjectProxyViewSet as ProjectViewSet
from .services.people import PeopleProxyViewSet as PeopleViewSet
from .services.investigations import InvestigationProxyViewSet as InvestigationViewSet
from .services.assays import AssayProxyViewSet as AssayViewSet
from .services.sample_types import SampleTypeProxyViewSet as SampleTypeViewSet
from .services.samples import SampleProxyViewSet as SampleViewSet


def get_clade_color(sample_type):
    """Extract core logic from seek.views.get_clade_color"""
    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()
    query = f"""
    SELECT c.color FROM dmac.clades c
    JOIN dmac.sample_types st
    ON st.clade_id = c.id
    WHERE st.title = '{sample_type}'
    """
    
    cursor.execute(query)
    try:
        color = cursor.fetchone()[0]
    except Exception:
        color = "#000000"
    
    cursor.close()
    conn.close()
    return color


class SampleTreeByIDViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample tree retrieval by numeric ID.
    Extracts core logic from sampleTreeNew function to avoid authentication bypass.
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'
    
    @extend_schema(
        responses={200: SampleTreeSerializer()},
        parameters=[
            OpenApiParameter(
                name='id',
                type=int,
                location=OpenApiParameter.PATH,
                description='Numeric sample ID'
            )
        ],
        examples=[
            OpenApiExample(
                name="Sample Tree Response",
                value=[
                    {
                        "id": "123",
                        "uuid": "abc-def-123",
                        "type": "Sample",
                        "color": "#FF0000",
                        "parentIds": ["456"]
                    }
                ]
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path="tree")
    def get_tree(self, request, pk=None):
        """
        Extract core Neo4j logic from legacy sampleTreeNew function.
        Handle authentication at ViewSet level instead of function decorators.
        """
        # Validate user authentication (handled by ViewSet permission_classes)
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request, False)
        # Fallback: if SEEK session missing but DRF auth is valid, allow read-only
        if not user_seek.get('status') and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Core Neo4j logic extracted from sampleTreeNew (lines 1883-1917)
        NEO4J_DATABASE = settings.NEO4J_DATABASE
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            r = driver.execute_query("""
            MATCH (s: Sample {id: """ + str(pk) + """})
            MATCH parents=(s)-[r1:CHILD_OF*0..]->(parent)
            MATCH children=(s)<-[r2:CHILD_OF*0..]-(child)
            RETURN collect(DISTINCT s) + collect(DISTINCT parent) + collect(DISTINCT child) AS nodes, r1 + r2 AS relationships
            """,
            result_transformer_=neo4j.Result.graph,
            database_=NEO4J_DATABASE['NAME'])

            nodeDict = {}
            for node in r.nodes:
                nodeUid = node._properties['uuid']
                nodeId = node._properties['id']
                nodeType = node._properties['type']
                nodeDict[nodeUid] = {'id': str(nodeId), 'type': nodeType, 'parents': []}

            for rel in r.relationships:
                nodeId = str(rel.start_node._properties['id'])
                nodeUid = rel.start_node._properties['uuid']
                nodeType = rel.start_node._properties['type']
                parentId = str(rel.end_node._properties['id'])

                if nodeDict.get(nodeUid) is not None:
                    nodeDict[nodeUid]['parents'].append(parentId)
                else:
                    nodeDict[nodeUid]['parents'] = [parentId]

            data = []
            for k, v in nodeDict.items():
                color = get_clade_color(v['type'])
                data.append({
                    "id": v['id'], 
                    "uuid": k, 
                    "type": v['type'], 
                    "color": color, 
                    "parentIds": v['parents']
                })

            return Response(data, status=status.HTTP_200_OK)


class SampleTreeByUUIDViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample tree retrieval by UUID.
    Separate ViewSet to avoid routing conflicts with numeric IDs.
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'uuid'
    lookup_value_regex = '[0-9A-Fa-f-]{36}'
    
    @extend_schema(
        responses={200: SampleTreeSerializer()},
        parameters=[
            OpenApiParameter(
                name='uuid',
                type=str,
                location=OpenApiParameter.PATH,
                description='Sample UUID (36-character format)'
            )
        ],
        examples=[
            OpenApiExample(
                name="Sample Tree Response", 
                value=[
                    {
                        "id": "123",
                        "uuid": "abc-def-123",
                        "type": "Sample",
                        "color": "#FF0000",
                        "parentIds": ["456"]
                    }
                ]
            )
        ]
    )
    @action(detail=True, methods=["get"], url_path="tree")
    def get_tree(self, request, uuid=None):
        """
        Extract core logic from legacy sampleTreeNewUID function.
        Convert UUID to sample_id then execute Neo4j query.
        """
        # Validate user authentication
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request, False)
        # Fallback: if SEEK session missing but DRF auth is valid, allow read-only
        if not user_seek.get('status') and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Core logic extracted from sampleTreeNewUID (lines 1928-1965)
        dbsample = DBtable_sample()
        sample_id = dbsample.getSampleID(uuid)
        
        NEO4J_DATABASE = settings.NEO4J_DATABASE
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            r = driver.execute_query("""
            MATCH (s: Sample {id: """ + str(sample_id) + """})
            MATCH parents=(s)-[r1:CHILD_OF*0..]->(parent)
            MATCH children=(s)<-[r2:CHILD_OF*0..]-(child)
            RETURN collect(DISTINCT s) + collect(DISTINCT parent) + collect(DISTINCT child) AS nodes, r1 + r2 AS relationships
            """,
            result_transformer_=neo4j.Result.graph,
            database_=NEO4J_DATABASE['NAME'])

            nodeDict = {}
            for node in r.nodes:
                nodeUid = node._properties['uuid']
                nodeId = node._properties['id']
                nodeType = node._properties['type']
                nodeDict[nodeUid] = {'id': str(nodeId), 'type': nodeType, 'parents': []}

            for rel in r.relationships:
                nodeId = str(rel.start_node._properties['id'])
                nodeUid = rel.start_node._properties['uuid']
                nodeType = rel.start_node._properties['type']
                parentId = str(rel.end_node._properties['id'])

                if nodeDict.get(nodeUid) is not None:
                    nodeDict[nodeUid]['parents'].append(parentId)
                else:
                    nodeDict[nodeUid]['parents'] = [parentId]

            data = []
            for k, v in nodeDict.items():
                color = get_clade_color(v['type'])
                data.append({
                    "id": v['id'], 
                    "uuid": k, 
                    "type": v['type'], 
                    "color": color, 
                    "parentIds": v['parents']
                })

            return Response(data, status=status.HTTP_200_OK)


class NHPViewSet(viewsets.GenericViewSet):
    """
    ViewSet for NHP (Non-Human Primate) data operations.
    Extracts core logic from NHP functions without decorator bypass issues.
    """
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
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
        responses={200: OpenApiTypes.STR},
        parameters=[
            OpenApiParameter(
                name='id',
                type=str,
                location=OpenApiParameter.PATH,
                description='NHP identifier/name'
            )
        ],
        description="Downloads NHP data as Excel file"
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


class StandardResultsSetPagination(PageNumberPagination):
    """Custom pagination class for large datasets."""
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000


class SampleQueryViewSet(viewsets.GenericViewSet):
    """
    ViewSet for sample query operations with pagination.
    Extracts core logic from retrieveSamples and converts HttpResponse to DRF Response.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    @extend_schema(
        responses={200: OpenApiTypes.OBJECT},
        description="Retrieve samples with pagination"
    )
    @action(detail=False, methods=["get"], url_path="retrieve")
    def retrieve_samples(self, request):
        """
        Extract core logic from retrieveSamples function.
        Convert HttpResponse to DRF Response and add pagination.
        """
        # Core logic extracted from retrieveSamples (lines 892-896)
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request, False)
        if not user_seek['status']:
            return Response(
                {"detail": "Authentication required"}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        dbsample = DBtable_sample()
        try:
            reportData = dbsample.processRecords(request, user_seek, "retrieve")
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Convert HttpResponse data to DRF Response with pagination
        try:
            data = json.loads(reportData)  # Parse the JSON string
            
            # Apply pagination
            paginator = self.pagination_class()
            if isinstance(data, list):
                page = paginator.paginate_queryset(data, request)
                return paginator.get_paginated_response(page)
            else:
                return Response(data, status=status.HTTP_200_OK)
                
        except json.JSONDecodeError:
            # If reportData is not JSON, return as-is
            return Response({"data": reportData}, status=status.HTTP_200_OK)


class AdminSampleViewSet(viewsets.GenericViewSet):
    """
    ViewSet for admin-only sample operations.
    Requires admin permissions.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    @extend_schema(
        request=AdminRetrieveRequestSerializer,
        description="Admin sample retrieval: POST UIDs and receive an Excel file",
        responses={200: OpenApiTypes.STR},
        examples=[
            OpenApiExample(name="UID list", value={"retrieval_uids": ["UID1", "UID2"]}),
            OpenApiExample(name="Whitespace-separated", value={"retrieval_uids_text": "UID1 UID2 UID3"})
        ]
    )
    @action(detail=False, methods=["post"], url_path="retrieve")
    def admin_retrieve_samples(self, request):
        """Admin export: accepts UIDs, returns an Excel workbook of metadata."""
        seekdb = SeekDB(None, None, None)
        user_seek = seekdb.getSeekLogin(request, False)
        if not user_seek.get('status'):
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Extract UIDs from JSON body or form-encoded fallbacks
        body = request.data or {}
        raw_uids = body.get("retrieval_uids") or body.get("uids") or body.get("retrieval_uids_text") or ""
        if isinstance(raw_uids, list):
            uids = [str(u).strip() for u in raw_uids if str(u).strip()]
        else:
            uids = str(raw_uids).strip().split()

        if not uids:
            return Response({"detail": "retrieval_uids required"}, status=status.HTTP_400_BAD_REQUEST)

        # Project scope and admin flag mirroring legacy behavior
        try:
            user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
            user_project_ids = list(map(lambda x: x['id'], user_projects))
        except Exception:
            user_project_ids = []
        # Treat Django staff as admin for data scope, matching IsAdminUser
        is_superuser = bool(getattr(request.user, 'is_superuser', False) or getattr(request.user, 'is_staff', False))

        # Build dataset and write Excel to a temp path under MEDIA_ROOT/download
        try:
            children_uids_df = get_children_uids(uids, user_project_ids, is_superuser)
        except IndexError:
            return Response({"detail": "No samples found for provided UIDs"}, status=status.HTTP_404_NOT_FOUND)
        except (AuthError, Neo4jError):
            # Fallback: proceed without Neo4j expansion; export provided UIDs only
            try:
                import pandas as pd  # local import to minimize surface area
                db = settings.DATABASES[SEEK_DATABASE]
                conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
                cursor = conn.cursor()

                uids_str = ', '.join(["'%s'" % uid for uid in uids])

                if is_superuser:
                    query = f"""
                    SELECT id, sample_type_id, uuid, json_metadata
                    FROM seek_production.samples
                    WHERE uuid IN ({uids_str})
                    """
                else:
                    # Avoid SQL syntax error when user has no mapped projects
                    project_ids_str = ', '.join(["'%s'" % pid for pid in user_project_ids]) if user_project_ids else "''"
                    query = f"""
                    SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
                    FROM seek_production.samples s
                    JOIN seek_production.projects_samples ps
                    ON s.id = ps.sample_id
                    WHERE s.uuid IN ({uids_str}) AND ps.sample_id = s.id AND ps.project_id IN ({project_ids_str})
                    """

                cursor.execute(query)
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
        datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"download-samples-{datenow}.xlsx"
        download_dir = os.path.join(settings.MEDIA_ROOT, "download")
        os.makedirs(download_dir, exist_ok=True)
        downloadfile = os.path.join(download_dir, filename)

        sample_retrieval_data(children_uids_df, downloadfile)

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
