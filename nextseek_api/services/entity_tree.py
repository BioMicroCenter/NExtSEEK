"""
EntityTree ViewSet for exposing the sample type graph structure.

Provides four endpoints:
- GET /entity_tree/nodes - Node attributes from dmac.sample_types_context
- GET /entity_tree/edges - Edges from Neo4j DERIVED_FROM relationships
- GET /entity_tree/edge_attributes - Edges with enriched metadata
- POST /entity_tree/lineage - Full lineage trees for specific samples
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import MySQLdb
from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.helpers import resolve_seek_auth
from nextseek_api.models import (
    Edge,
    EdgeAttribute,
    EdgeAttributeResponse,
    EdgeResponse,
    LineageEdge,
    LineageRequest,
    LineageResponse,
    LineageTree,
    NodeAttribute,
    NodeAttributeResponse,
    SampleTreeNode,
)

SEEK_DATABASE = "default"


class EntityTreePagination(PageNumberPagination):
    """Custom pagination for entity tree endpoints."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000


def _run_sql_query(query: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SQL query on the configured MySQL DB and return a list of dict rows."""

    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db["HOST"], user=db["USER"], passwd=db["PASSWORD"], db=db["NAME"])
    cursor = conn.cursor()
    try:
        cursor.execute(query, params or [])
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        cursor.close()
        conn.close()


class EntityTreeViewSet(viewsets.GenericViewSet):
    """
    ViewSet for entity tree node and edge retrieval.

    Exposes the sample type graph structure including:
    - Node attributes (sample type metadata)
    - Edges (relationships between sample types)
    - Edge attributes (edges with provenance metadata)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = EntityTreePagination

    # -------------------------------------------------------------------------
    # Endpoint 1: GET /entity_tree/nodes
    # -------------------------------------------------------------------------
    @extend_schema(
        operation_id="List Node Attributes",
        description=(
            "Retrieve all sample type node attributes from the entity graph. "
            "Uses dmac.sample_types_context for description and clade. "
            "Examples: 'What sample types exist in the system?'; "
            "'Show me all node types and their descriptions'"
        ),
        responses={200: NodeAttributeResponse},
        tags=["EntityTree"],
        examples=[
            OpenApiExample(
                name="Node attributes response",
                value={
                    "total": 3,
                    "nodes": [
                        {"node": "NHP", "id": 41, "description": "Non Human Primates", "clade": "Subject"},
                        {"node": "TIS", "id": 26, "description": "Tissue Sample", "clade": "Sample"},
                        {"node": "D.IMG", "id": 40, "description": "Imaging Data", "clade": "Data"},
                    ],
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="nodes")
    def list_nodes(self, request):
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Primary source: dmac.sample_types_context (may not exist in dev yet)
        rows: List[Dict[str, Any]] = []
        try:
            query = """
                SELECT
                    sample_type AS node,
                    sampletype_id AS id,
                    description,
                    clade
                FROM dmac.sample_types_context
                WHERE sample_type IS NOT NULL
                ORDER BY sample_type
            """
            rows = _run_sql_query(query)
        except Exception:
            # Fallback: use existing DBtable + ORM to reconstruct (node, id, description, clade)
            try:
                from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades
                from seek.models import Sample_types

                stc_data = DBtable_sample_types_clades().getAllWithTitles()

                # Collect ids and fetch descriptions via ORM
                st_ids: List[int] = []
                for item in stc_data:
                    sid = item.get("sample_type_id")
                    try:
                        if sid is not None:
                            st_ids.append(int(sid))
                    except Exception:
                        continue
                st_ids = sorted(set(st_ids))

                desc_lookup: Dict[int, Optional[str]] = {}
                title_lookup: Dict[int, Optional[str]] = {}
                if st_ids:
                    for row in Sample_types.objects.filter(id__in=st_ids).values("id", "title", "description"):
                        try:
                            rid = int(row["id"])
                            desc_lookup[rid] = row.get("description")
                            title_lookup[rid] = row.get("title")
                        except Exception:
                            continue

                # Normalize into rows expected by NodeAttribute
                by_id: Dict[int, Dict[str, Any]] = {}
                for item in stc_data:
                    try:
                        sid = int(item.get("sample_type_id"))
                    except Exception:
                        continue

                    node_title = item.get("sample_type_title") or title_lookup.get(sid) or ""
                    by_id[sid] = {
                        "node": node_title,
                        "id": sid,
                        "description": desc_lookup.get(sid),
                        "clade": item.get("clade_title"),
                    }

                rows = sorted(by_id.values(), key=lambda r: str(r.get("node") or ""))
            except Exception as e:
                return Response(
                    {"errors": [{"title": "Database error", "detail": str(e)}]},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        items: List[NodeAttribute] = []
        for row in rows:
            items.append(
                NodeAttribute(
                    node=str(row.get("node") or ""),
                    id=int(row.get("id") or 0),
                    description=row.get("description"),
                    clade=row.get("clade"),
                )
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(items, request)
        if page is not None:
            response = NodeAttributeResponse(total=len(items), nodes=page)
            return paginator.get_paginated_response(response.model_dump())

        response = NodeAttributeResponse(total=len(items), nodes=items)
        return Response(response.model_dump(), status=status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # Endpoint 2: GET /entity_tree/edges
    # -------------------------------------------------------------------------
    @extend_schema(
        operation_id="List Edges",
        description=(
            "Retrieve all edges (relationships) between sample types from the entity graph. "
            "Each edge is a Neo4j DERIVED_FROM relationship annotated by internal_assay_title."
        ),
        responses={200: EdgeResponse},
        tags=["EntityTree"],
        examples=[
            OpenApiExample(
                name="Edges response",
                value={
                    "total": 3,
                    "edges": [
                        {"source": "NHP", "target": "PAV", "annotation": "Patient Visit"},
                        {"source": "PAV", "target": "TIS", "annotation": "Tissue Collection"},
                        {"source": "TIS", "target": "D.IMG", "annotation": "Tissue Imaging"},
                    ],
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="edges")
    def list_edges(self, request):
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        NEO4J_DATABASE = settings.NEO4J_DATABASE
        try:
            with GraphDatabase.driver(NEO4J_DATABASE["URI"], auth=NEO4J_DATABASE["AUTH"]) as driver:
                cypher = """
                    MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
                    WHERE r.internal_assay_title IS NOT NULL
                    RETURN DISTINCT
                        parent.type AS source,
                        child.type AS target,
                        r.internal_assay_title AS annotation
                    ORDER BY source, target, annotation
                """
                records, summary, keys = driver.execute_query(cypher, database_=NEO4J_DATABASE["NAME"])
        except Neo4jError as e:
            return Response(
                {"errors": [{"title": "Neo4j error", "detail": str(e)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            return Response(
                {"errors": [{"title": "Invalid upstream response", "detail": str(e)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        items: List[Edge] = []
        for record in records:
            source = record.get("source")
            target = record.get("target")
            annotation = record.get("annotation")
            if source and target and annotation:
                items.append(Edge(source=str(source), target=str(target), annotation=str(annotation)))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(items, request)
        if page is not None:
            response = EdgeResponse(total=len(items), edges=page)
            return paginator.get_paginated_response(response.model_dump())

        response = EdgeResponse(total=len(items), edges=items)
        return Response(response.model_dump(), status=status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # Endpoint 3: GET /entity_tree/edge_attributes
    # -------------------------------------------------------------------------
    @extend_schema(
        operation_id="List Edge Attributes",
        description=(
            "Retrieve all edges with extended metadata including internal_assay_id, "
            "study_titles, and description. Uses Neo4j for (source, target, annotation) "
            "and dmac.assay_context + seek_production.studies for enrichment."
        ),
        responses={200: EdgeAttributeResponse},
        tags=["EntityTree"],
        examples=[
            OpenApiExample(
                name="Edge attributes response",
                value={
                    "total": 2,
                    "edges": [
                        {
                            "source": "NHP",
                            "target": "PAV",
                            "annotation": "Patient Visit",
                            "internal_assay_id": "16",
                            "study_titles": "Impactb Study",
                            "description": "Visit data of NHP/PAT",
                        },
                        {
                            "source": "TIS",
                            "target": "D.IMG",
                            "annotation": "Tissue Imaging",
                            "internal_assay_id": "11",
                            "study_titles": "MIT_SRP; Impactb Study",
                            "description": "Imaging of Tissues (IHC, RaDR, yH2AX, etc)",
                        },
                    ],
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="edge_attributes")
    def list_edge_attributes(self, request):
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED)

        # Step 1: base edge rows from Neo4j
        NEO4J_DATABASE = settings.NEO4J_DATABASE
        try:
            with GraphDatabase.driver(NEO4J_DATABASE["URI"], auth=NEO4J_DATABASE["AUTH"]) as driver:
                cypher = """
                    MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
                    WHERE r.internal_assay_title IS NOT NULL
                    RETURN DISTINCT
                        parent.type AS source,
                        child.type AS target,
                        r.internal_assay_title AS annotation,
                        r.internal_assay_id AS internal_assay_id
                    ORDER BY source, target, annotation
                """
                records, summary, keys = driver.execute_query(cypher, database_=NEO4J_DATABASE["NAME"])
        except Neo4jError as e:
            return Response(
                {"errors": [{"title": "Neo4j error", "detail": str(e)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            return Response(
                {"errors": [{"title": "Invalid upstream response", "detail": str(e)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Normalize rows and collect keys for enrichment
        edge_rows: List[Dict[str, Any]] = []
        ids_from_neo4j: set[int] = set()
        titles_missing_id: set[str] = set()

        for r in records:
            source = r.get("source")
            target = r.get("target")
            annotation = r.get("annotation")
            internal_assay_id = r.get("internal_assay_id")
            if not (source and target and annotation):
                continue

            internal_id_str: Optional[str] = None
            if internal_assay_id is not None and str(internal_assay_id).isdigit():
                internal_id_str = str(int(internal_assay_id))
                ids_from_neo4j.add(int(internal_assay_id))
            else:
                titles_missing_id.add(str(annotation))

            edge_rows.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "annotation": str(annotation),
                    "internal_assay_id": internal_id_str,
                }
            )

        # Step 2a: if Neo4j doesn't have internal_assay_id, map from title -> id via dmac.internal_assays
        title_to_id: Dict[str, str] = {}
        if titles_missing_id:
            try:
                placeholders = ", ".join(["%s"] * len(titles_missing_id))
                q = f"""
                    SELECT id, internal_assay_title
                    FROM dmac.internal_assays
                    WHERE internal_assay_title IN ({placeholders})
                """
                rows = _run_sql_query(q, list(titles_missing_id))
                for row in rows:
                    t = row.get("internal_assay_title")
                    i = row.get("id")
                    if t is not None and i is not None:
                        title_to_id[str(t)] = str(int(i))
            except Exception:
                # continue without IDs if lookup fails
                title_to_id = {}

        # Apply mapped ids and collect final ids for enrichment
        all_internal_ids: set[int] = set(ids_from_neo4j)
        for er in edge_rows:
            if er["internal_assay_id"] is None:
                mapped = title_to_id.get(er["annotation"])
                if mapped is not None:
                    er["internal_assay_id"] = mapped
                    if mapped.isdigit():
                        all_internal_ids.add(int(mapped))

        # Step 2b: Enrich by internal_assay_id (description + study_titles)
        enrichment: Dict[int, Dict[str, Any]] = {}
        if all_internal_ids:
            try:
                ids_str = ", ".join(str(i) for i in sorted(all_internal_ids))
                q = f"""
                    SELECT
                        ac.internal_assay_id AS internal_assay_id,
                        ac.Description AS description,
                        GROUP_CONCAT(DISTINCT s.title SEPARATOR '; ') AS study_titles
                    FROM dmac.assay_context ac
                    LEFT JOIN dmac.assays_internal_assays aia
                        ON aia.internal_assay_id = ac.internal_assay_id
                    LEFT JOIN dmac.assays a ON a.id = aia.assay_id
                    LEFT JOIN seek_production.studies s ON s.id = a.study_id
                    WHERE ac.internal_assay_id IN ({ids_str})
                    GROUP BY ac.internal_assay_id, ac.Description
                """
                rows = _run_sql_query(q)
                for row in rows:
                    aid = row.get("internal_assay_id")
                    if aid is None:
                        continue
                    try:
                        enrichment[int(aid)] = {
                            "description": row.get("description"),
                            "study_titles": row.get("study_titles"),
                        }
                    except Exception:
                        continue
            except Exception:
                enrichment = {}

        items: List[EdgeAttribute] = []
        for er in edge_rows:
            internal_id = er.get("internal_assay_id")
            enrich = enrichment.get(int(internal_id), {}) if internal_id and internal_id.isdigit() else {}
            items.append(
                EdgeAttribute(
                    source=er["source"],
                    target=er["target"],
                    annotation=er["annotation"],
                    internal_assay_id=internal_id,
                    study_titles=enrich.get("study_titles"),
                    description=enrich.get("description"),
                )
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(items, request)
        if page is not None:
            response = EdgeAttributeResponse(total=len(items), edges=page)
            return paginator.get_paginated_response(response.model_dump())

        response = EdgeAttributeResponse(total=len(items), edges=items)
        return Response(response.model_dump(), status=status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # Helper methods for lineage endpoint
    # -------------------------------------------------------------------------
    def _get_clade_color(self, sample_type: str) -> str:
        """Get hex color for a sample type from clade mapping."""
        try:
            query = """
                SELECT c.color
                FROM dmac.clades c
                JOIN dmac.sample_types_clades stc ON stc.clade_id = c.id
                JOIN seek_production.sample_types st ON stc.sample_type_id = st.id
                WHERE st.title = %s
            """
            rows = _run_sql_query(query, [sample_type])
            if rows:
                return rows[0].get("color") or "#000000"
            return "#000000"
        except Exception:
            return "#000000"

    def _fetch_lineage_enrichment(self, internal_assay_ids: set) -> Dict[int, Dict[str, Any]]:
        """Fetch edge attribute enrichment from MySQL."""
        if not internal_assay_ids:
            return {}

        try:
            ids_str = ", ".join(str(i) for i in sorted(internal_assay_ids))
            query = f"""
                SELECT
                    ac.internal_assay_id AS internal_assay_id,
                    ac.Description AS description,
                    GROUP_CONCAT(DISTINCT s.title SEPARATOR '; ') AS study_titles
                FROM dmac.assay_context ac
                LEFT JOIN dmac.assays_internal_assays aia
                    ON aia.internal_assay_id = ac.internal_assay_id
                LEFT JOIN dmac.assays a ON a.id = aia.assay_id
                LEFT JOIN seek_production.studies s ON s.id = a.study_id
                WHERE ac.internal_assay_id IN ({ids_str})
                GROUP BY ac.internal_assay_id, ac.Description
            """
            rows = _run_sql_query(query)

            enrichment: Dict[int, Dict[str, Any]] = {}
            for row in rows:
                aid = row.get("internal_assay_id")
                if aid is not None:
                    enrichment[int(aid)] = {
                        "description": row.get("description"),
                        "study_titles": row.get("study_titles"),
                    }
            return enrichment
        except Exception:
            return {}

    def _fetch_single_lineage(
        self,
        driver,
        sample_id: int,
        db_name: str,
        input_id: str,
        resolved_id: str,
    ) -> Tuple[LineageTree, set]:
        """Fetch a single sample's full lineage from Neo4j.

        Returns tuple of (LineageTree, set of internal_assay_ids for enrichment).
        """
        try:
            cypher = """
                MATCH (s:Sample {id: toInteger($id)})
                OPTIONAL MATCH parents_path=(s)-[r1:DERIVED_FROM*0..]->(parent)
                OPTIONAL MATCH children_path=(s)<-[r2:DERIVED_FROM*0..]-(child)
                WITH s,
                     collect(DISTINCT parent) AS parents_list,
                     collect(DISTINCT child) AS children_list,
                     [p IN collect(DISTINCT parents_path) | relationships(p)] AS parent_rels_nested,
                     [c IN collect(DISTINCT children_path) | relationships(c)] AS child_rels_nested
                RETURN
                    [s] + parents_list + children_list AS nodes,
                    reduce(acc = [], rels IN parent_rels_nested | acc + rels) +
                    reduce(acc = [], rels IN child_rels_nested | acc + rels) AS relationships
            """
            records, summary, keys = driver.execute_query(
                cypher, id=sample_id, database_=db_name
            )

            if not records:
                return (
                    LineageTree(
                        input_id=input_id,
                        resolved_id=resolved_id,
                        total_nodes=0,
                        total_rels=0,
                        nodes=[],
                        rels=[],
                        warning=f"No data found for sample ID: {sample_id}",
                    ),
                    set(),
                )

            record = records[0]
            raw_nodes = record.get("nodes", [])
            raw_rels = record.get("relationships", [])

            # Process nodes - build lookup for parent relationships
            node_dict: Dict[int, Dict[str, Any]] = {}
            for node in raw_nodes:
                if node is None:
                    continue
                node_id = node.get("id")
                node_uuid = node.get("uuid", "")
                node_type = node.get("type", "")
                if node_id is not None:
                    node_dict[int(node_id)] = {
                        "id": str(node_id),
                        "uuid": node_uuid or "",
                        "type": node_type or "",
                        "parents": [],
                    }

            # Process relationships and collect internal_assay_ids
            rel_list: List[LineageEdge] = []
            internal_assay_ids: set = set()
            seen_rels: set = set()

            for rel in raw_rels:
                if rel is None:
                    continue

                # Extract relationship properties
                child_id = rel.start_node.get("id") if rel.start_node else None
                parent_id = rel.end_node.get("id") if rel.end_node else None

                if child_id is None or parent_id is None:
                    continue

                # Deduplicate relationships
                rel_key = (int(child_id), int(parent_id))
                if rel_key in seen_rels:
                    continue
                seen_rels.add(rel_key)

                # Update parent tracking for node
                if int(child_id) in node_dict:
                    node_dict[int(child_id)]["parents"].append(str(parent_id))

                # Get relationship properties
                internal_assay_id = rel.get("internal_assay_id")
                internal_assay_title = rel.get("internal_assay_title")
                protocol_title = rel.get("protocol_title")
                protocol_id = rel.get("protocol_id")

                internal_assay_id_str = None
                if internal_assay_id is not None:
                    internal_assay_id_str = str(int(internal_assay_id))
                    internal_assay_ids.add(int(internal_assay_id))

                edge = LineageEdge(
                    child_id=str(child_id),
                    parent_id=str(parent_id),
                    internal_assay_id=internal_assay_id_str,
                    internal_assay_title=internal_assay_title,
                    protocol_title=protocol_title,
                    protocol_id=str(int(protocol_id)) if protocol_id is not None else None,
                )
                rel_list.append(edge)

            # Build SampleTreeNode list with colors
            nodes: List[SampleTreeNode] = []
            for nid, v in node_dict.items():
                color = self._get_clade_color(v["type"])
                nodes.append(
                    SampleTreeNode(
                        id=v["id"],
                        uuid=v["uuid"],
                        sample_type=v["type"],
                        color=color,
                        parentIds=v["parents"],
                    )
                )

            return (
                LineageTree(
                    input_id=input_id,
                    resolved_id=resolved_id,
                    total_nodes=len(nodes),
                    total_rels=len(rel_list),
                    nodes=nodes,
                    rels=rel_list,
                    warning=None,
                ),
                internal_assay_ids,
            )

        except Exception as e:
            return (
                LineageTree(
                    input_id=input_id,
                    resolved_id=resolved_id,
                    total_nodes=0,
                    total_rels=0,
                    nodes=[],
                    rels=[],
                    warning=f"Query failed: {str(e)}",
                ),
                set(),
            )

    # -------------------------------------------------------------------------
    # Endpoint 4: POST /entity_tree/lineage
    # -------------------------------------------------------------------------
    @extend_schema(
        operation_id="Get Sample Lineage Trees",
        description=(
            "Retrieve full lineage trees for one or more samples. "
            "For each input sample, returns all upstream (parents via DERIVED_FROM) "
            "and downstream (children) samples. Accepts sample UIDs or SEEK IDs. "
            "Optionally includes edge attributes (study_titles, description). "
            "Examples: 'Show me all samples derived from NHP-220630FLY-1-PUB'; "
            "'Get the full lineage for tissue samples TIS-230324BOO-39-PUB and TIS-230324BOO-40-PUB'"
        ),
        request=LineageRequest,
        responses={200: LineageResponse},
        tags=["EntityTree"],
        parameters=[
            OpenApiParameter(
                name="page",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Page number for node pagination within each tree (1-indexed)",
            ),
            OpenApiParameter(
                name="page_size",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Number of nodes per page within each tree (default 100, max 1000)",
            ),
        ],
        examples=[
            OpenApiExample(
                name="Single sample by UID",
                value={
                    "sample_ids": ["NHP-220630FLY-1-PUB"],
                    "include_attributes": False,
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Multiple samples with attributes",
                value={
                    "sample_ids": ["NHP-220630FLY-1-PUB", "12345"],
                    "include_attributes": True,
                },
                request_only=True,
            ),
            OpenApiExample(
                name="Lineage response",
                value={
                    "total_trees": 1,
                    "trees": [
                        {
                            "input_id": "NHP-220630FLY-1-PUB",
                            "resolved_id": "1506",
                            "total_nodes": 5,
                            "total_rels": 4,
                            "nodes": [
                                {
                                    "id": "1506",
                                    "uuid": "NHP-220630FLY-1-PUB",
                                    "sample_type": "NHP",
                                    "color": "#A3D46F",
                                    "parentIds": [],
                                },
                                {
                                    "id": "1525",
                                    "uuid": "PAV-230522GRI-7-PUB",
                                    "sample_type": "PAV",
                                    "color": "#A3D46F",
                                    "parentIds": ["1506"],
                                },
                            ],
                            "rels": [
                                {
                                    "child_id": "1525",
                                    "parent_id": "1506",
                                    "internal_assay_id": "380",
                                    "internal_assay_title": "Patient Visit",
                                    "study_titles": "MIT_SRP; Impactb Study",
                                    "description": "Visit data of NHP/PAT",
                                }
                            ],
                            "warning": None,
                        }
                    ],
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="lineage")
    def lineage(self, request):
        """Return full lineage trees for the provided sample identifiers."""
        # Auth gate
        basic_tuple, _ = resolve_seek_auth(request, ["BASIC", "SESSION"])
        if not basic_tuple and not request.user.is_authenticated:
            return Response(
                {"detail": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED
            )

        # Validate request body
        try:
            req = LineageRequest.model_validate(request.data)
        except Exception as e:
            return Response(
                {"errors": [{"title": "Invalid request", "detail": str(e)}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if not req.sample_ids:
            return Response(
                {"errors": [{"title": "sample_ids required"}]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Import ID resolver
        from nextseek_api.services.samples import _resolve_uid_to_seek_id

        # Build trees for each input sample
        trees: List[LineageTree] = []
        all_internal_assay_ids: set = set()

        NEO4J_DATABASE = settings.NEO4J_DATABASE

        try:
            with GraphDatabase.driver(
                NEO4J_DATABASE["URI"], auth=NEO4J_DATABASE["AUTH"]
            ) as driver:
                for input_id in req.sample_ids:
                    # Resolve to SEEK ID
                    resolved_id = _resolve_uid_to_seek_id(str(input_id))

                    if resolved_id is None:
                        # Sample not found - return empty tree with warning
                        trees.append(
                            LineageTree(
                                input_id=str(input_id),
                                resolved_id=None,
                                total_nodes=0,
                                total_rels=0,
                                nodes=[],
                                rels=[],
                                warning=f"Sample not found: {input_id}",
                            )
                        )
                        continue

                    # Fetch lineage from Neo4j
                    tree_result, assay_ids = self._fetch_single_lineage(
                        driver,
                        int(resolved_id),
                        NEO4J_DATABASE["NAME"],
                        str(input_id),
                        resolved_id,
                    )

                    # Collect internal_assay_ids for batch enrichment
                    all_internal_assay_ids.update(assay_ids)

                    trees.append(tree_result)

        except Neo4jError as e:
            return Response(
                {"errors": [{"title": "Neo4j error", "detail": str(e)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            return Response(
                {"errors": [{"title": "Query failed", "detail": str(e)}]},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Enrich edges with attributes if requested
        if req.include_attributes and all_internal_assay_ids:
            enrichment = self._fetch_lineage_enrichment(all_internal_assay_ids)
            for tree in trees:
                for rel in tree.rels:
                    if rel.internal_assay_id and str(rel.internal_assay_id).isdigit():
                        aid = int(rel.internal_assay_id)
                        if aid in enrichment:
                            rel.study_titles = enrichment[aid].get("study_titles")
                            rel.description = enrichment[aid].get("description")

        # Apply pagination to nodes within each tree
        paginator = self.pagination_class()
        for tree in trees:
            # Store total before pagination
            total_nodes = tree.total_nodes
            page = paginator.paginate_queryset(tree.nodes, request)
            if page is not None:
                tree.nodes = page
            # Restore total (pagination shouldn't change it)
            tree.total_nodes = total_nodes

        response = LineageResponse(total_trees=len(trees), trees=trees)

        return Response(response.model_dump(), status=status.HTTP_200_OK)

