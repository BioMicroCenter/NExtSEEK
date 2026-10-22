import datetime
import logging
import os
import secrets
import MySQLdb
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from drf_spectacular.openapi import OpenApiExample
from pydantic import ValidationError

# Import Neo4j
import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError

# Import legacy classes and functions needed for core logic
from seek.seekdb import SeekDB
from .batch_upload.views import BatchUploadViewSet

logger = logging.getLogger(__name__)

NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
SEEK_DATABASE = settings.SEEK_DATABASE

# Import serializers
from .serializers import (
    SampleTreeSerializer,
    SampleNodeSerializer,
    SampleRetrieveRequestSerializer,
)
from .services.sops import SopProxyViewSet as SopViewSet
from .services.data_files import DataFileProxyViewSet as DataFileViewSet
from .services.projects import ProjectProxyViewSet as ProjectViewSet
from .services.people import PeopleProxyViewSet as PeopleViewSet
from .services.users import UsersViewSet
from .services.investigations import InvestigationProxyViewSet as InvestigationViewSet
from .services.studies import StudyProxyViewSet as StudyViewSet
from .attributes.views import AttributeViewSet
from .assay_registration.views import AssayRegistrationViewSet  # noqa: F401
from .services.sample_shares import SampleShareViewSet  # noqa: F401
from .services.assays import AssayProxyViewSet as AssayViewSet
from .services.sample_types import SampleTypeProxyViewSet as SampleTypeViewSet
from .services.sample_types import SampleTypeChildrenViewSet as SampleTypeChildrenViewSet
from .services.sample_types import SamplesByChildTypesViewSet as SamplesByChildTypesViewSet
from .services.templates import TemplatesViewSet
from .services.sampletype_connections import SampleTypeConnectionsViewSet as SampleTypeConnectionsViewSet
from .services.samples import SampleProxyViewSet as SampleViewSet
from .services.samples import _resolve_uid_to_seek_id
from .services.samples import SampleAdvancedSearchViewSet as SampleAdvancedSearchViewSet
from .services.graph_search import GraphSearchViewSet as GraphSearchViewSet
from .services.sample_retrieve import SampleRetrieveViewSet, handle_retrieve, RETRIEVE_EXAMPLES, RETRIEVE_RESPONSES
from .services.schema_rag import SchemaRAGViewSet
from .services.assistant import AssistantViewSet
# Additive dmac_assistant integration (router + Container-Claude-Code).
from .services.cc_assistant import CCAssistantViewSet
from .services.evaluator import EvaluatorViewSet
from .services.entity_tree import EntityTreeViewSet
from .services.project_export import ProjectExportViewSet
from .services.graph_sync_status import GraphSyncStatusViewSet
from .services.nessie import NessieChatViewSet, NessieViewSet
from .helpers import resolve_seek_auth
from nextseek_api.endpoint_descriptions import SAMPLE_TREE_GET_DESC, ADMIN_SAMPLE_RETRIEVE_DESC
from nextseek_api.models import (
    SampleTreeResponse,
    SampleTreeNode,
    SampleTreeRel,
    SampleRetrieveRequest,
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

    The sample-tree route's derivation (the download API reads the same membership
    from MySQL instead: nextseek_api/services/sample_retrieve.py). SeekDB has to
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


def _tree_error(where, exc, http_status):
    """A fixed error text plus a reference. The exception text (it can carry the graph host) goes to the log, with
    the same reference, never to the caller."""
    ref = secrets.token_hex(3)
    logger.error("%s failed (ref %s): %s", where, ref, exc, exc_info=exc)
    return Response({"detail": f"{where} failed (ref {ref})"}, status=http_status)


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
        # the create and the update branch), so including it would make this
        # scoping a no-op for every account. Same predicate as
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
            return _tree_error("Neo4j authentication", e, status.HTTP_503_SERVICE_UNAVAILABLE)
        except Neo4jError as e:
            return _tree_error("Neo4j query", e, status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            return _tree_error("Tree query", e, status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminSampleViewSet(viewsets.GenericViewSet):
    """The deprecated route ``admin/samples/retrieve/``: the same handler as ``samples/retrieve/``.

    Kept because saved Nessie chats replay the endpoint they stored, browsers may hold a cached
    ``ns_sample_download.js``, and scripts outside this repo post here. The data path lives in
    ``nextseek_api/services/sample_retrieve.py``; nothing is decided here.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="Admin Sample Retrieval",
        tags=['Samples'],
        request=SampleRetrieveRequest,
        description=ADMIN_SAMPLE_RETRIEVE_DESC,
        responses=RETRIEVE_RESPONSES,
        examples=RETRIEVE_EXAMPLES,
        deprecated=True,
    )
    @action(detail=False, methods=["post"], url_path="retrieve")
    def admin_retrieve_samples(self, request):
        """Deprecated alias of POST samples/retrieve/."""
        return handle_retrieve(request)
