"""``GET /nextseek_api/definitions/export/``: the whole sample type and assay catalog in one call.

Native read, any logged-in user: the catalog is the same for everyone, so nothing is scoped by project. It replaces
the ETL's TEMPORARY ``remote_export.py`` (which assembled the same pieces over ssh) and keeps that bundle's keys and
shapes, so ``scripts/definitions_parity.py`` can compare the two field for field:

- ``sample_types`` and ``assays``: the two context tables as stored, without their ``id``. Their derived columns are
  kept current by graph_sync's context step (``graph_sync/context_derived.py``).
- ``connections``: the observed triples, read from ``sample_type_connections`` (the step's table), not by scanning
  the graph's 1.8 M DERIVED_FROM edges on each call. ``licensed_connections`` adds the designed ones and both flags.
- ``attributes`` and ``graph_meta``: the graph's Attribute catalog and GraphMeta stamp, the same statements as before.
- ``needs_definition``: SEEK sample types and internal assays with no definition row.
"""
from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings
from django.db import connections
from drf_spectacular.utils import OpenApiExample, extend_schema
from neo4j import READ_ACCESS, GraphDatabase, unit_of_work
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.authentication import SeekSessionAuthentication
from nextseek_api.endpoint_descriptions import DEFINITIONS_EXPORT_DESC
from nextseek_api.graph_sync import sources
from nextseek_api.graph_sync.context_derived import needs_definition
from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow
from nextseek_api.models import DefinitionsExportResponse, JsonApiErrorResponse

GRAPH_TIMEOUT_S = 120
GRAPH_META = ("MATCH (m:GraphMeta) RETURN m.schema_version AS schema_version, m.catalog_hash AS catalog_hash, "
              "toString(m.synced_at) AS synced_at LIMIT 1")
# Every attribute of every type, filled or not (graph_catalog.TYPES_ADMIN keeps only the filled ones).
ATTRIBUTES = """
MATCH (t:SampleType)-[:HAS_ATTRIBUTE]->(a:Attribute)
RETURN t.title AS sample_type, a.key AS key, a.title AS title, a.pos AS pos, a.required AS required,
       a.is_title AS is_title, a.value_type AS value_type, a.base_type AS base_type, a.unit_key AS unit_key,
       a.role AS role, a.declared AS declared, a.seek_description AS seek_description, a.meaning AS meaning,
       a.sample_count AS sample_count
ORDER BY sample_type, coalesce(a.pos, 100000), title
""".strip()


def _plain(cursor) -> list[dict]:
    """JSON-safe rows: the table's own ``id`` dropped, bytes decoded, dates as ISO text."""
    names = [d[0] for d in cursor.description]
    out = []
    for values in cursor.fetchall():
        row = {}
        for name, value in zip(names, values):
            if name == "id":
                continue
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8", "replace")
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            row[name] = value
        out.append(row)
    return out


@unit_of_work(timeout=GRAPH_TIMEOUT_S)
def _rows(tx, statement: str) -> list[dict]:
    return [record.data() for record in tx.run(statement)]


def read_graph(statements: dict[str, str]) -> dict[str, list[dict]]:
    """Each statement's rows, read in one READ session of the stack's Neo4j."""
    neo = settings.NEO4J_DATABASE
    with GraphDatabase.driver(neo["URI"], auth=neo["AUTH"]) as driver:
        with driver.session(database=neo["NAME"], default_access_mode=READ_ACCESS) as session:
            return {name: session.execute_read(_rows, q) for name, q in statements.items()}


def build_export(graph_reader=read_graph) -> dict:
    """The export document. ``graph_reader`` is the seam tests replace; a graph error propagates (a 502)."""
    with connections[settings.NEXTSEEK_DATABASE].cursor() as cur:
        cur.execute("SELECT * FROM sample_types_context ORDER BY sample_type")
        sample_types = _plain(cur)
        cur.execute("SELECT * FROM assay_context ORDER BY id")
        assays = _plain(cur)
        cur.execute("SELECT id, internal_assay_title FROM internal_assays ORDER BY id")
        internal = [{"id": i, "internal_assay_title": t} for i, t in cur.fetchall()]
    graph = graph_reader({"meta": GRAPH_META, "attributes": ATTRIBUTES})
    clade = {r.get("sample_type"): r.get("clade") for r in sample_types}
    rows = list(SampleTypeConnectionRow.objects.using(settings.NEXTSEEK_DATABASE)
                .order_by("parent_type", "assay", "child_type"))
    observed = sorted((r.assay, r.parent_type, r.child_type) for r in rows if r.observed)
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_types": sample_types,
        "assays": assays,
        "connections": [{"assay": a, "parent_type": p, "child_type": c} for a, p, c in observed],
        "licensed_connections": [{"parent": r.parent_type, "assay": r.assay, "child": r.child_type,
                                  "designed": r.designed, "observed": r.observed,
                                  "parent_clade": clade.get(r.parent_type), "child_clade": clade.get(r.child_type)}
                                 for r in rows],
        "attributes": graph["attributes"],
        "graph_meta": (graph["meta"] or [{}])[0],
        "needs_definition": needs_definition([t["title"] for t in sources.sample_types()], sample_types, internal,
                                             assays),
    }


class DefinitionsExportViewSet(viewsets.GenericViewSet):
    """The definitions export (module docstring)."""

    authentication_classes = [SeekSessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get_authenticate_header(self, request):
        # DRF takes the challenge from the first authenticator, and the session one has none, so an anonymous call
        # would read 403; Basic's challenge makes it the 401 a script can act on.
        return BasicAuthentication().authenticate_header(request)

    @extend_schema(
        operation_id="Export Definitions",
        description=DEFINITIONS_EXPORT_DESC,
        responses={200: DefinitionsExportResponse, 502: JsonApiErrorResponse},
        tags=["SampleTypes"],
        examples=[
            OpenApiExample(
                name="Definitions export (trimmed)",
                value={
                    "exported_at": "2026-11-23T17:11:58+00:00",
                    "sample_types": [{"sample_type": "BAC", "name": "Bacteria Sample", "clade": "Source",
                                      "parent_sampletypes": "TIS", "child_sampletypes": "DNA, D.BSRA"}],
                    "assays": [{"assay_name": "DNA Extraction", "Required_Parent_Sample_Types": "BAC or CEL",
                                "Children_Sample_Types": "DNA", "internal_assay_id": 5}],
                    "connections": [{"assay": "DNA Extraction", "parent_type": "BAC", "child_type": "DNA"}],
                    "licensed_connections": [{"parent": "BAC", "assay": "DNA Extraction", "child": "DNA",
                                              "designed": True, "observed": True,
                                              "parent_clade": "Source", "child_clade": "Processed"}],
                    "attributes": [{"sample_type": "BAC", "key": "7:Species", "title": "Species", "pos": 3,
                                    "required": True, "role": "data"}],
                    "graph_meta": {"schema_version": "1.3", "synced_at": "2026-11-23T15:46:31.795Z"},
                    "needs_definition": {"sample_types": ["A.ATAC"], "assays": ["Library Prep"]},
                },
                response_only=True,
            ),
        ],
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        try:
            return Response(build_export())
        except Exception as exc:  # noqa: BLE001 - the graph or a table could not be read; say so, never a 500 page
            return Response({"errors": [{"title": "Definitions export failed", "detail": str(exc)}]},
                            status=status.HTTP_502_BAD_GATEWAY)
