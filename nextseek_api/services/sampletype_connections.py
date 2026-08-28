"""Unique SampleType -> SampleType assay connections, per investigation or sample type.

Superuser-only. Answers "what connects to what, and by which assay" for one
investigation, one sample type, or the whole graph, as JSON, CSV or a
clade-coloured SVG.

Two things about the Cypher are deliberate:

* the Investigation hop is an ``EXISTS {}`` subquery, not a second ``MATCH``.
  A hard join would multiply rows whenever a sample sits in more than one
  study (inflating ``n_edges``), and — worse — would silently drop every
  sample carrying no ``IN_STUDY`` edge. 727 of 50,889 samples were in that
  state on the reference graph, so a hard join makes "across all projects"
  quietly mean "across all projects, minus the unlinked".

* the whole predicate short-circuits when no investigation selector is given,
  so a ``sample_type``-only query genuinely spans everything.

``seek_inv_id`` maps to ``Investigation.project_id``, which is a SEEK *project*
id: one such id covers every investigation in that project. The parameter name
is the caller-facing vocabulary, not a claim about the underlying column.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import connections
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api.endpoint_descriptions import SAMPLETYPE_CONNECTIONS_DESC
from nextseek_api.models import (
    SampleTypeConnection,
    SampleTypeConnectionsRequest,
    SampleTypeConnectionsResponse,
)
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

logger = logging.getLogger(__name__)

# Clade order is the pipeline order, and doubles as the SVG's column order.
# Anything unmapped lands in a trailing "Unassigned" column rather than being
# dropped, so an incomplete sample_types_clades table degrades visibly.
CLADE_ORDER = ["Source", "Raw", "Processed", "Analyzed"]
UNASSIGNED_CLADE = "Unassigned"
UNASSIGNED_COLOR = "#D9D9D9"

CONNECTIONS_CYPHER = """
MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
WHERE r.internal_assay_title IS NOT NULL
  AND ($sample_type IS NULL
       OR parent.type = $sample_type
       OR child.type  = $sample_type)
  AND ($graph_inv_id IS NULL AND $seek_inv_id IS NULL AND $name IS NULL
       OR EXISTS {
            MATCH (child)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(i:Investigation)
            WHERE ($graph_inv_id IS NULL OR i.id         = $graph_inv_id)
              AND ($seek_inv_id  IS NULL OR i.project_id = $seek_inv_id)
              AND ($name         IS NULL OR toLower(i.title) = toLower($name))
          })
RETURN
  parent.type            AS parent_sample_type,
  child.type             AS child_sample_type,
  r.internal_assay_title AS internal_assay,
  count(*)               AS n_edges
ORDER BY parent_sample_type, child_sample_type, internal_assay
"""


def _truthy(value: Any) -> bool:
    """Accept the spellings a human types into a querystring for a boolean."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def fetch_clade_map() -> Dict[str, Tuple[str, str]]:
    """Map every sample type title -> (clade title, hex colour), in one query.

    ``entity_tree._get_clade_color`` does this one sample type at a time; a
    connection map touches dozens, so it is fetched once here.

    Never raises: an unreachable clade table yields an empty map, and every
    node then renders as Unassigned rather than failing the request.
    """
    seekdb = settings.DATABASES[settings.SEEK_DATABASE]["NAME"]
    nextseekdb = settings.DATABASES[settings.NEXTSEEK_DATABASE]["NAME"]
    sql = f"""
        SELECT st.title AS sample_type, c.title AS clade, c.color AS color
        FROM {nextseekdb}.sample_types_clades stc
        JOIN {nextseekdb}.clades c ON c.id = stc.clade_id
        JOIN {seekdb}.sample_types st ON st.id = stc.sample_type_id
    """
    try:
        with connections[settings.NEXTSEEK_DATABASE].cursor() as cursor:
            cursor.execute(sql)
            return {
                str(row[0]): (str(row[1]), str(row[2] or UNASSIGNED_COLOR))
                for row in cursor.fetchall()
                if row[0]
            }
    except Exception:
        logger.exception("Clade lookup failed; rendering every sample type as unassigned")
        return {}


def run_connections_query(selector: SampleTypeConnectionsRequest) -> List[Dict[str, Any]]:
    """Run the connection query. Raises Neo4jError to the caller for a 502."""
    neo = settings.NEO4J_DATABASE
    params = {
        "sample_type": selector.sample_type or None,
        "graph_inv_id": selector.graph_inv_id,
        "seek_inv_id": selector.seek_inv_id,
        "name": selector.name or None,
    }
    with GraphDatabase.driver(neo["URI"], auth=neo["AUTH"]) as driver:
        records, _summary, _keys = driver.execute_query(
            CONNECTIONS_CYPHER, **params, database_=neo["NAME"]
        )
    rows: List[Dict[str, Any]] = []
    for record in records:
        parent = record.get("parent_sample_type")
        child = record.get("child_sample_type")
        assay = record.get("internal_assay")
        if not (parent and child and assay):
            continue
        rows.append(
            {
                "parent_sample_type": str(parent),
                "child_sample_type": str(child),
                "internal_assay": str(assay),
                "n_edges": int(record.get("n_edges") or 0),
            }
        )
    return rows


def rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Emit the triple format: SampleType, isParent, SampleType, InternalAssay, Edges."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["SampleType", "isParent", "SampleType", "InternalAssay", "Edges"])
    for row in rows:
        writer.writerow(
            [
                row["parent_sample_type"],
                "isParent",
                row["child_sample_type"],
                row["internal_assay"],
                row["n_edges"],
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# SVG rendering
#
# Hand-rolled rather than shelled out to graphviz: the image ships no `dot`
# binary and no graphviz/pydot wheel, and adding either means a Dockerfile
# change for one endpoint. The clade vocabulary is a pipeline (Source -> Raw ->
# Processed -> Analyzed), which is already a layer assignment, so the layout
# reduces to "one column per clade" and needs no layout engine. Output is a
# pure function of the rows, which also makes it testable as a string.
# ---------------------------------------------------------------------------

_SVG_MARGIN = 40
_COL_WIDTH = 280
_NODE_W = 170
_NODE_H = 36
_V_GAP = 18
_HEADER_H = 64
_LABEL_LIMIT = 40  # above this many edges, assay names live in tooltips only


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _text_on(hex_color: str) -> str:
    """Black or white body text, whichever survives on this fill."""
    try:
        raw = hex_color.lstrip("#")
        r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#111111"
    # Rec. 601 luma; the clade palette is mid-tone, so the threshold matters.
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def _inside_any_node(point, pos):
    """True if a candidate label point falls within any node's box."""
    px, py = point
    return any(
        nx <= px <= nx + _NODE_W and ny <= py <= ny + _NODE_H
        for nx, ny in pos.values()
    )


def _bezier_at(t, x0, y0, x1, y1, x2, y2, x3, y3):
    """Point on a cubic bezier at t, so an edge label can sit anywhere along its curve."""
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3)


def rows_to_svg(rows: List[Dict[str, Any]], clade_map: Dict[str, Tuple[str, str]]) -> str:
    """Render the connection map as a self-contained, clade-coloured SVG."""
    sample_types = sorted(
        {r["parent_sample_type"] for r in rows} | {r["child_sample_type"] for r in rows}
    )
    if not sample_types:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="80" '
            'viewBox="0 0 420 80"><text x="20" y="45" font-family="system-ui,sans-serif" '
            'font-size="15" fill="#666">No connections matched this selector.</text></svg>'
        )

    # Column assignment: clade order, unmapped types trailing.
    columns: Dict[str, List[str]] = {}
    for st in sample_types:
        clade = clade_map.get(st, (UNASSIGNED_CLADE, UNASSIGNED_COLOR))[0]
        columns.setdefault(clade, []).append(st)
    present = [c for c in CLADE_ORDER if c in columns]
    present += sorted(c for c in columns if c not in CLADE_ORDER)

    pos: Dict[str, Tuple[float, float]] = {}
    for col_i, clade in enumerate(present):
        x = _SVG_MARGIN + col_i * _COL_WIDTH
        for row_i, st in enumerate(sorted(columns[clade])):
            pos[st] = (x, _HEADER_H + row_i * (_NODE_H + _V_GAP))

    tallest = max(len(v) for v in columns.values())
    width = _SVG_MARGIN * 2 + max(1, len(present)) * _COL_WIDTH
    height = _HEADER_H + tallest * (_NODE_H + _V_GAP) + _SVG_MARGIN * 2
    show_labels = len(rows) <= _LABEL_LIMIT

    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,-apple-system,sans-serif">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#7A7A7A"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
    ]

    # Column headers double as the clade legend, so there is no separate key.
    for col_i, clade in enumerate(present):
        x = _SVG_MARGIN + col_i * _COL_WIDTH
        color = next(
            (clade_map[s][1] for s in columns[clade] if s in clade_map), UNASSIGNED_COLOR
        )
        out.append(
            f'<rect x="{x}" y="{_SVG_MARGIN - 12}" width="{_NODE_W}" height="24" rx="4" '
            f'fill="{_esc(color)}" opacity="0.35"/>'
            f'<text x="{x + _NODE_W / 2}" y="{_SVG_MARGIN + 4}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="#333">{_esc(clade)}</text>'
        )

    # Edges first, so nodes paint over the curve ends.
    # Labels are staggered along the curve rather than all pinned to the midpoint:
    # parallel edges between the same two columns share a midpoint, and stacking
    # every assay name there renders them as one illegible smear.
    fanout: Dict[str, int] = {}
    labels: List[str] = []
    for row in rows:
        src, dst = row["parent_sample_type"], row["child_sample_type"]
        if src not in pos or dst not in pos:
            continue
        seq = fanout.get(src, 0)
        fanout[src] = seq + 1
        sx, sy = pos[src]
        dx_, dy = pos[dst]
        y1, y2 = sy + _NODE_H / 2, dy + _NODE_H / 2
        tip = f'{_esc(src)} -[{_esc(row["internal_assay"])}]-> {_esc(dst)} ({row["n_edges"]})'
        if src == dst:
            # Self-derivation is real (e.g. CEL -> CEL, "Cell Culture"): loop out right.
            x1 = sx + _NODE_W
            path = f"M {x1} {y1 - 8} C {x1 + 54} {y1 - 30}, {x1 + 54} {y1 + 30}, {x1} {y1 + 8}"
        else:
            x1, x2 = sx + _NODE_W, dx_
            bow = max(40.0, abs(x2 - x1) / 2)
            path = f"M {x1} {y1} C {x1 + bow} {y1}, {x2 - bow} {y2}, {x2} {y2}"
        out.append(
            f'<path d="{path}" fill="none" stroke="#9AA0A6" stroke-width="1.3" '
            f'marker-end="url(#arrow)" opacity="0.75"><title>{tip}</title></path>'
        )
        if show_labels and src != dst:
            # Prefer a point on the curve that does not land inside a node box;
            # a label drawn over a node is unreadable no matter how it is haloed.
            spot = None
            # Start at this edge's own stagger slot so parallel edges separate,
            # then sweep the rest of the curve for the first clear spot.
            slots = [0.20 + 0.075 * i for i in range(9)]
            start = (seq * 2) % len(slots)
            for i in range(len(slots)):
                t = slots[(start + i) % len(slots)]
                cand = _bezier_at(t, x1, y1, x1 + bow, y1, x2 - bow, y2, x2, y2)
                if not _inside_any_node(cand, pos):
                    spot = cand
                    break
            lx, ly = spot or _bezier_at(0.5, x1, y1, x1 + bow, y1, x2 - bow, y2, x2, y2)
            labels.append(
                f'<text x="{lx:.1f}" y="{ly - 5:.1f}" text-anchor="middle" font-size="10" '
                f'fill="#3C4043" stroke="#FFFFFF" stroke-width="3" paint-order="stroke" '
                f'stroke-linejoin="round">{_esc(row["internal_assay"])}</text>'
            )

    for st in sample_types:
        if st not in pos:
            continue
        x, y = pos[st]
        clade, color = clade_map.get(st, (UNASSIGNED_CLADE, UNASSIGNED_COLOR))
        out.append(
            f'<g><title>{_esc(st)} ({_esc(clade)})</title>'
            f'<rect x="{x}" y="{y}" width="{_NODE_W}" height="{_NODE_H}" rx="6" '
            f'fill="{_esc(color)}" stroke="#4A4A4A" stroke-width="1"/>'
            f'<text x="{x + _NODE_W / 2}" y="{y + _NODE_H / 2 + 4}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="{_text_on(color)}">{_esc(st)}</text></g>'
        )

    # Labels last: the node pass paints opaque boxes, and an edge label emitted
    # before it would be silently clipped by whichever node it happened to cross.
    out.extend(labels)
    out.append("</svg>")
    return "\n".join(out)


_QUERY_PARAMS = [
    OpenApiParameter("graph_inv_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                     description="Investigation.id in the graph."),
    OpenApiParameter("seek_inv_id", OpenApiTypes.INT, OpenApiParameter.QUERY,
                     description="Investigation.project_id — a SEEK project id, so it spans "
                                 "every investigation in that project."),
    OpenApiParameter("name", OpenApiTypes.STR, OpenApiParameter.QUERY,
                     description="Investigation.title, exact and case-insensitive."),
    OpenApiParameter("sample_type", OpenApiTypes.STR, OpenApiParameter.QUERY,
                     description="Sample type code; matches either endpoint. Alone, spans all projects."),
    OpenApiParameter("all_conns", OpenApiTypes.BOOL, OpenApiParameter.QUERY,
                     description="Deliberately return the whole graph unfiltered."),
    OpenApiParameter("output_format", OpenApiTypes.STR, OpenApiParameter.QUERY,
                     enum=["json", "csv", "svg"], description="Default json."),
]


class SampleTypeConnectionsViewSet(viewsets.GenericViewSet):
    """Unique SampleType -> SampleType assay connections (superuser-only)."""

    authentication_classes = [
        TokenAuthentication,
        CsrfExemptSessionAuthentication,
        BasicAuthentication,
    ]
    # IsAuthenticated first so anonymous callers get a 401 challenge rather than a
    # bare 403. IsSuperUser, not IsAdminUser — is_staff is set on every SEEK user
    # at login (dmac/views.py:80,97), so IsAdminUser would gate nothing.
    permission_classes = [IsAuthenticated, IsSuperUser]

    @extend_schema(
        operation_id="List SampleType assay connections",
        description=SAMPLETYPE_CONNECTIONS_DESC,
        # "admin", not "SampleTypes": the tag tracks the gate, not the URL. Every
        # other superuser-only endpoint is tagged admin, and SPECTACULAR_SETTINGS
        # orders that section directly under Assays.
        tags=["admin"],
        parameters=_QUERY_PARAMS,
        responses={
            (200, "application/json"): SampleTypeConnectionsResponse,
            # response= is required, not decorative: a description-only OpenApiResponse
            # emits no `content` block, and drf-spectacular then raises KeyError('content')
            # while merging it with the JSON variant, taking the whole /schema/ route down.
            (200, "text/csv"): OpenApiResponse(
                response=OpenApiTypes.STR,
                description="CSV: SampleType,isParent,SampleType,InternalAssay,Edges",
            ),
            (200, "image/svg+xml"): OpenApiResponse(
                response=OpenApiTypes.STR,
                description="Clade-coloured connection diagram",
            ),
        },
        examples=[
            OpenApiExample(
                name="Connections response",
                value={
                    "total": 2,
                    "filters": {"graph_inv_id": 2},
                    "connections": [
                        {
                            "parent_sample_type": "CEL",
                            "child_sample_type": "D.IMG",
                            "internal_assay": "Imaging",
                            "n_edges": 1932,
                            "parent_clade": "Raw",
                            "child_clade": "Analyzed",
                        },
                        {
                            "parent_sample_type": "CEL",
                            "child_sample_type": "DNA",
                            "internal_assay": "DNA Extraction",
                            "n_edges": 68,
                            "parent_clade": "Raw",
                            "child_clade": "Raw",
                        },
                    ],
                },
                response_only=True,
            ),
        ],
    )
    def list(self, request):
        """GET /nextseek_api/sample_types/connections/

        Named ``list`` rather than decorated with ``@action``: the router maps
        ``list`` onto the prefix root, giving ``sample_types/connections/``. An
        action would have appended its own segment on top of the prefix and
        produced ``sample_types/connections/connections/``, which is how the
        sibling ``sample_types/get_parents/parents_by_child_types/`` reads.
        """
        params = request.query_params
        raw = {
            "graph_inv_id": params.get("graph_inv_id") or None,
            "seek_inv_id": params.get("seek_inv_id") or None,
            "name": params.get("name") or None,
            "sample_type": params.get("sample_type") or None,
            "all_conns": _truthy(params.get("all_conns")),
            "output_format": (params.get("output_format") or "json").lower(),
        }
        try:
            selector = SampleTypeConnectionsRequest.model_validate(raw)
        except ValidationError as exc:
            return Response(
                {"errors": [{"title": "Invalid request", "detail": exc.errors()}]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        try:
            rows = run_connections_query(selector)
        except Neo4jError as exc:
            return Response(
                {"errors": [{"title": "Neo4j error", "detail": str(exc)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            return Response(
                {"errors": [{"title": "Invalid upstream response", "detail": str(exc)}]},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        applied = {k: v for k, v in selector.model_dump().items()
                   if k != "output_format" and v not in (None, False, "")}

        if selector.output_format == "csv":
            response = HttpResponse(rows_to_csv(rows), content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="sampletype_connections.csv"'
            return response

        clade_map = fetch_clade_map()

        if selector.output_format == "svg":
            return HttpResponse(rows_to_svg(rows, clade_map), content_type="image/svg+xml")

        payload = SampleTypeConnectionsResponse(
            total=len(rows),
            filters=applied,
            connections=[
                SampleTypeConnection(
                    **row,
                    parent_clade=clade_map.get(row["parent_sample_type"], (None, None))[0],
                    child_clade=clade_map.get(row["child_sample_type"], (None, None))[0],
                )
                for row in rows
            ],
        )
        return Response(payload.model_dump(), status=status.HTTP_200_OK)
