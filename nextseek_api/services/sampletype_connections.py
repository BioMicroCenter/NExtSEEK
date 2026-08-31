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
import json
import logging
import math
import re
from collections import defaultdict, deque
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

_CYPHER_TEMPLATE = """
MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
WHERE r.internal_assay_title IS NOT NULL
%(sample_type_predicate)s
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

#: direct_connections=true (default). The type must be one endpoint of the edge.
_DIRECT_PREDICATE = """  AND ($sample_type IS NULL
       OR parent.type = $sample_type
       OR child.type  = $sample_type)"""

#: direct_connections=false. Every edge in a tree rooted at that type, which is
#: what walks NHP -> PAV -> TIS -> DNA all the way down.
#:
#: Written "inverted" - collect the subtree ONCE from the roots, then filter edges
#: by membership - rather than the obvious form, which asks of every edge "can this
#: parent reach a root of type X". That obvious form is
#:
#:     EXISTS { MATCH (parent)-[:DERIVED_FROM*0..]->(root) WHERE root.type = $t }
#:
#: and it is a trap. With a LITERAL type it plans fine (~2s), but with $t as a
#: PARAMETER the planner has no selectivity and runs the traversal per edge:
#: measured >100s unbounded and 38.9s even bounded to *0..6, against 0.3s for the
#: form below on the same data and the same 39 rows. Bounding the depth is not a
#: fix either - *0..4 answered in 1.6s but silently lost 3 of the 39 triples.
_SUBTREE_CYPHER = """
MATCH (root:Sample) WHERE root.type = $sample_type
MATCH (descendant:Sample)-[:DERIVED_FROM*0..]->(root)
WITH collect(DISTINCT elementId(descendant)) AS subtree
MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
WHERE r.internal_assay_title IS NOT NULL
  AND elementId(parent) IN subtree
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

CONNECTIONS_CYPHER = _CYPHER_TEMPLATE % {"sample_type_predicate": _DIRECT_PREDICATE}
CONNECTIONS_SUBTREE_CYPHER = _SUBTREE_CYPHER


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
    cypher = CONNECTIONS_CYPHER if selector.direct_connections else CONNECTIONS_SUBTREE_CYPHER
    with GraphDatabase.driver(neo["URI"], auth=neo["AUTH"]) as driver:
        records, _summary, _keys = driver.execute_query(
            cypher, **params, database_=neo["NAME"]
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


def download_name(selector, extension: str) -> str:
    """Filename for a downloaded connection map, tagged with the selector used.

    Without an explicit Content-Disposition the client guesses, and the "+xml"
    in image/svg+xml makes Swagger and most browsers save the diagram as .xml.
    The selector suffix keeps successive downloads from all colliding on one name.
    """
    parts = []
    if selector.graph_inv_id is not None:
        parts.append(f"inv{selector.graph_inv_id}")
    if selector.seek_inv_id is not None:
        parts.append(f"proj{selector.seek_inv_id}")
    if selector.name:
        parts.append(re.sub(r"[^A-Za-z0-9]+", "-", selector.name).strip("-").lower())
    if selector.sample_type:
        parts.append(re.sub(r"[^A-Za-z0-9]+", "-", selector.sample_type).strip("-").lower())
    if not parts:
        parts.append("all")
    return f"sampletype_connections_{'_'.join(parts)}.{extension}"


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
# Two layouts, because the two ways this endpoint gets used want different
# pictures:
#
#   radial  - an investigation or project. Source-clade types sit at the centre
#             and each ring out is one more DERIVED_FROM hop, so "what grows out
#             of our source material" is the shape you see.
#   layered - one sample type. Clades become left-to-right columns, which reads
#             as a pipeline.
#
# Hand-rolled rather than shelled out to graphviz: the image ships no `dot`
# binary and no graphviz/pydot wheel. Both layouts are pure functions of the
# rows, so they are testable as strings and identical on every run.
# ---------------------------------------------------------------------------

#: Pipeline order, NOT the clades table's id order. The table happens to number
#: them 9 Source / 10 Raw / 11 Processed / 12 Analyzed, but the real flow is
#: Source -> Processed -> Raw -> Analyzed: a processed SPECIMEN (TIS, DNA, RNA)
#: comes before the RAW DATA FILE measured from it (D.*), which comes before the
#: ANALYSIS derived from that (A.*). Sorting by clade id puts Raw second and
#: makes the diagram tell the wrong story. Matches CLADE_STYLES in
#: curation_skill/templates/SAMPLE_TREE.html.j2.
CLADE_PIPELINE = ["Source", "Processed", "Raw", "Analyzed"]

_CHAR_W, _LABEL_PAD, _MIN_NODE_R = 6.6, 11.0, 17.0
_TOP_BAR = 86
_LAYER_W, _LAYER_NODE_H, _LAYER_VGAP = 250.0, 26.0, 12.0


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
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def _uniform_node_radius(types) -> float:
    """One radius for every node, sized to fit the longest label in the graph.

    Deliberately uniform: sizing each node to its own label made a diagram where
    circle size carried no meaning but looked like it did.
    """
    longest = max((len(t) for t in types), default=3)
    return max(_MIN_NODE_R, (longest * _CHAR_W + _LABEL_PAD) / 2)


def _node_radius(label: str) -> float:
    """Per-label radius. Retained only for width math; layouts use the uniform size."""
    return max(_MIN_NODE_R, (len(label) * _CHAR_W + _LABEL_PAD) / 2)


def _clade(clade_map, sample_type):
    return clade_map.get(sample_type, (UNASSIGNED_CLADE, UNASSIGNED_COLOR))[0]


def _colour(clade_map, sample_type):
    return clade_map.get(sample_type, (UNASSIGNED_CLADE, UNASSIGNED_COLOR))[1]


def _adjacency(rows):
    types = sorted(
        {r["parent_sample_type"] for r in rows} | {r["child_sample_type"] for r in rows}
    )
    children, parents = defaultdict(set), defaultdict(set)
    for row in rows:
        p, c = row["parent_sample_type"], row["child_sample_type"]
        if p != c:                      # self-derivation is real but carries no depth
            children[p].add(c)
            parents[c].add(p)
    return types, children, parents


def hop_depths(rows, clade_map):
    """BFS hop distance from the Source-clade types, which become ring 0.

    Falls back to types with no parent when no Source clade is present, so an
    arbitrary slice of the graph still renders instead of collapsing to nothing.
    Types unreachable from any root are pushed to one ring beyond the deepest,
    which keeps them visible rather than silently dropped.
    """
    types, children, parents = _adjacency(rows)
    roots = [t for t in types if _clade(clade_map, t) == "Source"]
    if not roots:
        roots = [t for t in types if not parents[t]] or types[:1]
    depth = {t: 0 for t in roots}
    queue = deque(roots)
    while queue:
        node = queue.popleft()
        for nxt in sorted(children[node]):
            if nxt not in depth:
                depth[nxt] = depth[node] + 1
                queue.append(nxt)
    deepest = max(depth.values()) if depth else 0
    for t in types:
        depth.setdefault(t, deepest + 1)
    return types, children, parents, depth


def _svg_open(width, height, title, subtitle, clade_map, types):
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="Inter,system-ui,-apple-system,sans-serif">',
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#FCFCFD"/>',
        f'<text x="28" y="34" font-size="16" font-weight="700" fill="#1F2328">{_esc(title)}</text>',
        f'<text x="28" y="53" font-size="11.5" fill="#6A737D">{_esc(subtitle)}</text>',
    ]
    present = [c for c in CLADE_PIPELINE if any(_clade(clade_map, t) == c for t in types)]
    if any(_clade(clade_map, t) not in CLADE_PIPELINE for t in types):
        present.append(UNASSIGNED_CLADE)
    for i, clade in enumerate(present):
        colour = next(
            (_colour(clade_map, t) for t in types if _clade(clade_map, t) == clade),
            UNASSIGNED_COLOR,
        )
        out.append(
            f'<circle cx="{33 + i * 112}" cy="70" r="5.5" fill="{_esc(colour)}" stroke="#8A9199"/>'
            f'<text x="{44 + i * 112}" y="74" font-size="11.5" fill="#555">{_esc(clade)}</text>'
        )
    return out


def _edge_weight(n_edges, busiest):
    return 0.55 + 2.4 * (math.log1p(n_edges) / math.log1p(max(1, busiest)))


def _draw_node(x, y, sample_type, clade_map, extra="", radius=None):
    colour = _colour(clade_map, sample_type)
    radius = _node_radius(sample_type) if radius is None else radius
    return (
        f'<g><title>{_esc(sample_type)} \u00b7 {_esc(_clade(clade_map, sample_type))}{_esc(extra)}</title>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{_esc(colour)}" '
        f'stroke="#4E555C" stroke-width="1.1"/>'
        f'<text x="{x:.1f}" y="{y + 3.4:.1f}" text-anchor="middle" font-size="10.5" '
        f'font-weight="600" fill="{_text_on(colour)}">{_esc(sample_type)}</text></g>'
    )


def _empty_svg():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="80" viewBox="0 0 420 80">'
        '<text x="20" y="45" font-family="system-ui,sans-serif" font-size="15" fill="#666">'
        'No connections matched this selector.</text></svg>'
    )


def _radial_layout(types, children, parents, depth, clade_map, uniform):
    """Ring radius fits its own members; angle relaxed toward neighbours.

    The relaxation is what makes this readable: without it, ring members sit in
    alphabetical order and every edge crosses the hub. Each pass reorders a ring
    by the mean angle of its neighbours on other rings, which pulls connected
    types into alignment. Deterministic - no randomness, fixed pass count.
    """
    rings = defaultdict(list)
    for t in types:
        rings[depth[t]].append(t)

    radius, previous = {}, 0.0
    for d in sorted(rings):
        needed = sum(2 * uniform for _t in rings[d]) * 1.28
        # Gap follows the nodes actually on the ring. A fixed minimum made a
        # 13-node graph claim the same canvas as a 163-node one, nearly all empty.
        gap = max(74.0, 2 * uniform + 34)
        r = max(previous + gap, needed / (2 * math.pi))
        if d == 0 and len(rings[d]) == 1:
            r = 0.0
        radius[d] = r
        previous = max(previous, r)

    angle = {}
    for d in sorted(rings):
        members = sorted(rings[d], key=lambda t: (_clade(clade_map, t), t))
        for i, t in enumerate(members):
            angle[t] = 2 * math.pi * i / max(1, len(members))
    for _ in range(40):
        for d in sorted(rings):
            if d == 0:
                continue
            members = rings[d]
            target = {}
            for t in members:
                neighbours = [
                    angle[n] for n in (parents[t] | children[t])
                    if n in angle and depth[n] != d
                ]
                if not neighbours:
                    target[t] = angle[t]
                    continue
                sx = sum(math.cos(a) for a in neighbours) / len(neighbours)
                sy = sum(math.sin(a) for a in neighbours) / len(neighbours)
                target[t] = math.atan2(sy, sx) % (2 * math.pi)
            for i, t in enumerate(sorted(members, key=lambda t: (target[t], t))):
                angle[t] = 2 * math.pi * i / len(members)

    pos = {}
    for t in types:
        r = radius[depth[t]]
        a = angle[t] - math.pi / 2
        pos[t] = (r * math.cos(a), r * math.sin(a)) if r else (0.0, 0.0)
    return pos, radius, rings


def rows_to_svg_radial(rows, clade_map, title="Connection network"):
    if not rows:
        return _empty_svg()
    types, children, parents, depth = hop_depths(rows, clade_map)
    uniform = _uniform_node_radius(types)
    pos, radius, rings = _radial_layout(types, children, parents, depth, clade_map, uniform)

    outermost = max(radius.values())
    pad = 70 + uniform
    cx = cy = outermost + pad
    width, height = cx * 2, cy * 2 + _TOP_BAR
    cy2 = cy + _TOP_BAR

    out = _svg_open(
        width, height, f"{title} \u2014 {len(rows)} connections",
        "rings = derivation hops from a source sample \u00b7 colour = clade "
        "\u00b7 line weight = edge volume",
        clade_map, types,
    )
    for d in sorted(rings):
        if radius[d] <= 0:
            continue
        out.append(
            f'<circle cx="{cx:.0f}" cy="{cy2:.0f}" r="{radius[d]:.0f}" fill="none" '
            f'stroke="#E4E7EB" stroke-width="1" stroke-dasharray="2 5"/>'
            f'<text x="{cx:.0f}" y="{cy2 - radius[d] - 7:.0f}" text-anchor="middle" '
            f'font-size="10" fill="#AEB4BA">hop {d}</text>'
        )

    busiest = max(r["n_edges"] for r in rows)
    for row in rows:
        s, t = row["parent_sample_type"], row["child_sample_type"]
        if s == t or s not in pos or t not in pos:
            continue
        x1, y1 = pos[s][0] + cx, pos[s][1] + cy2
        x2, y2 = pos[t][0] + cx, pos[t][1] + cy2
        qx = cx + ((x1 + x2) / 2 - cx) * 0.58     # bundle gently toward the hub
        qy = cy2 + ((y1 + y2) / 2 - cy2) * 0.58
        out.append(
            f'<path d="M{x1:.1f} {y1:.1f}Q{qx:.1f} {qy:.1f},{x2:.1f} {y2:.1f}" fill="none" '
            f'stroke="{_esc(_colour(clade_map, s))}" '
            f'stroke-width="{_edge_weight(row["n_edges"], busiest):.2f}" stroke-opacity="0.4">'
            f'<title>{_esc(s)} \u2192 {_esc(t)} \u00b7 {_esc(row["internal_assay"])} '
            f'\u00b7 {row["n_edges"]} edges</title></path>'
        )
    for t in sorted(types, key=lambda t: (depth[t], t)):
        out.append(
            _draw_node(pos[t][0] + cx, pos[t][1] + cy2, t, clade_map,
                       f" \u00b7 hop {depth[t]}", radius=uniform)
        )
    out.append("</svg>")
    return "\n".join(out)


def _barycentre(present, columns, rows, passes=6):
    """Order each column by its neighbours' mean position. Cuts crossings sharply."""
    order = {t: i for c in present for i, t in enumerate(sorted(columns[c]))}
    fwd, back = defaultdict(list), defaultdict(list)
    for r in rows:
        fwd[r["parent_sample_type"]].append(r["child_sample_type"])
        back[r["child_sample_type"]].append(r["parent_sample_type"])
    for p in range(passes):
        for c in (present if p % 2 == 0 else present[::-1]):
            source = fwd if p % 2 == 0 else back
            def key(t):
                ns = [order[n] for n in source[t] if n in order]
                return (sum(ns) / len(ns) if ns else order[t], t)
            columns[c] = sorted(columns[c], key=key)
            for i, t in enumerate(columns[c]):
                order[t] = i
    return columns


def rows_to_svg_layered(rows, clade_map, title="Connection map"):
    if not rows:
        return _empty_svg()
    types = sorted(
        {r["parent_sample_type"] for r in rows} | {r["child_sample_type"] for r in rows}
    )
    columns = defaultdict(list)
    for t in types:
        columns[_clade(clade_map, t)].append(t)
    present = [c for c in CLADE_PIPELINE if c in columns]
    present += sorted(c for c in columns if c not in CLADE_PIPELINE)
    columns = _barycentre(present, columns, rows)

    node_w = 2 * _uniform_node_radius(types)
    top = _TOP_BAR + 34
    pos = {}
    for ci, clade in enumerate(present):
        for ri, t in enumerate(columns[clade]):
            pos[t] = (28 + ci * _LAYER_W, top + ri * (_LAYER_NODE_H + _LAYER_VGAP))
    tallest = max(len(v) for v in columns.values())
    width = 56 + len(present) * _LAYER_W
    height = top + tallest * (_LAYER_NODE_H + _LAYER_VGAP) + 40

    out = _svg_open(
        width, height, f"{title} \u2014 {len(rows)} connections",
        "columns = clade \u00b7 colour = clade \u00b7 line weight = edge volume",
        clade_map, types,
    )
    out.append(
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" '
        'markerHeight="5" orient="auto-start-reverse">'
        '<path d="M0 0L10 5L0 10z" fill="#B0B6BE"/></marker></defs>'
    )
    for ci, clade in enumerate(present):
        out.append(
            f'<text x="{28 + ci * _LAYER_W + node_w / 2:.0f}" y="{top - 16:.0f}" '
            f'text-anchor="middle" font-size="12" font-weight="600" fill="#6A737D" '
            f'letter-spacing="0.6">{_esc(clade.upper())}</text>'
        )
    busiest = max(r["n_edges"] for r in rows)
    for row in rows:
        s, t = row["parent_sample_type"], row["child_sample_type"]
        if s not in pos or t not in pos:
            continue
        (sx, sy), (dx, dy) = pos[s], pos[t]
        y1, y2 = sy + _LAYER_NODE_H / 2, dy + _LAYER_NODE_H / 2
        weight = _edge_weight(row["n_edges"], busiest)
        tip = (f'<title>{_esc(s)} \u2192 {_esc(t)} \u00b7 {_esc(row["internal_assay"])} '
               f'\u00b7 {row["n_edges"]} edges</title>')
        if s == t:
            x1 = sx + node_w
            path = f"M{x1} {y1 - 6}C{x1 + 38} {y1 - 22},{x1 + 38} {y1 + 22},{x1} {y1 + 6}"
        else:
            x1, x2 = sx + node_w, dx
            mid = x1 + (x2 - x1) / 2
            path = f"M{x1} {y1}C{mid} {y1},{mid} {y2},{x2} {y2}"
        out.append(
            f'<path d="{path}" fill="none" stroke="{_esc(_colour(clade_map, s))}" '
            f'stroke-width="{weight:.2f}" stroke-opacity="0.5" marker-end="url(#ar)">{tip}</path>'
        )
    for t in types:
        x, y = pos[t]
        colour = _colour(clade_map, t)
        out.append(
            f'<g><title>{_esc(t)} \u00b7 {_esc(_clade(clade_map, t))}</title>'
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{node_w:.0f}" height="{_LAYER_NODE_H:.0f}" '
            f'rx="5" fill="{_esc(colour)}" stroke="#4E555C" stroke-width="0.9"/>'
            f'<text x="{x + node_w / 2:.0f}" y="{y + _LAYER_NODE_H / 2 + 4:.0f}" '
            f'text-anchor="middle" font-size="11" font-weight="600" '
            f'fill="{_text_on(colour)}">{_esc(t)}</text></g>'
        )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Interactive HTML
#
# Same visual language as curation_skill/templates/SAMPLE_TREE.html.j2, so a
# connection map and a curated sample tree read as the same picture: cytoscape
# + dagre, one shape and colour per clade, uniform node boxes, click for detail.
# That template is the prior art for this view; the palette and shapes below are
# copied from its CLADE_STYLES deliberately rather than reinvented.
# ---------------------------------------------------------------------------

#: clade -> (fill, cytoscape shape). Mirrors CLADE_STYLES in the curation template.
CLADE_STYLES = {
    "Source":    ("#2E7D32", "ellipse"),
    "Processed": ("#E65100", "round-rectangle"),
    "Raw":       ("#42A5F5", "diamond"),
    "Analyzed":  ("#1565C0", "hexagon"),
}
_CYTO_CDN = "https://unpkg.com"


def rows_to_html(rows, clade_map, title="SampleType connections") -> str:
    """Interactive cytoscape/dagre page. Nodes are uniform; colour and shape carry clade."""
    types = sorted(
        {r["parent_sample_type"] for r in rows} | {r["child_sample_type"] for r in rows}
    )
    nodes = [
        {"id": t, "clade": _clade(clade_map, t),
         "colour": CLADE_STYLES.get(_clade(clade_map, t), (UNASSIGNED_COLOR, "ellipse"))[0],
         "shape": CLADE_STYLES.get(_clade(clade_map, t), (UNASSIGNED_COLOR, "ellipse"))[1]}
        for t in types
    ]
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[(r["parent_sample_type"], r["child_sample_type"])].append(r)
    edges = []
    for (src, dst), group in sorted(by_pair.items()):
        assays = sorted({g["internal_assay"] for g in group})
        edges.append({
            "source": src, "target": dst,
            "label": assays[0] + (f" +{len(assays) - 1}" if len(assays) > 1 else ""),
            "assays": assays, "n": sum(g["n_edges"] for g in group),
        })
    payload = json.dumps({"nodes": nodes, "edges": edges})
    legend = "".join(
        f'<span class="lg"><i style="background:{c}"></i>{_esc(k)}</span>'
        for k, (c, _shape) in CLADE_STYLES.items()
        if any(n["clade"] == k for n in nodes)
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<script src="{_CYTO_CDN}/cytoscape@3/dist/cytoscape.min.js"></script>
<script src="{_CYTO_CDN}/dagre@0.8/dist/dagre.min.js"></script>
<script src="{_CYTO_CDN}/cytoscape-dagre@2/cytoscape-dagre.js"></script>
<style>
html,body{{margin:0;height:100%;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1F2328}}
header{{padding:10px 18px;border-bottom:1px solid #E1E4E8;background:#fff;display:flex;
gap:16px;align-items:center;flex-wrap:wrap}}
h1{{margin:0;font-size:15px;font-weight:700}}
.sub{{color:#57606A;font-size:12px}}
.lg{{font-size:12px;color:#444;display:inline-flex;align-items:center;gap:5px;margin-right:10px}}
.lg i{{width:11px;height:11px;border-radius:3px;display:inline-block}}
#cy{{position:absolute;top:52px;bottom:0;left:0;right:0;background:#FCFCFD}}
#dp{{position:absolute;right:14px;top:66px;width:250px;background:#fff;border:1px solid #D0D7DE;
border-radius:8px;padding:12px 14px;box-shadow:0 4px 14px rgba(0,0,0,.09);display:none;font-size:12.5px}}
#dp b{{display:block;font-size:14px;margin-bottom:4px}}
#dp ul{{margin:6px 0 0;padding-left:18px}}
</style></head><body>
<header><h1>{_esc(title)}</h1>
<span class="sub">{len(nodes)} sample types &middot; {len(edges)} connections</span>{legend}
<span class="sub">click a node or edge for detail</span></header>
<div id="cy"></div><div id="dp"></div>
<script>
var D={payload};
var els=D.nodes.map(function(n){{return {{group:'nodes',data:{{
  id:n.id,label:n.id,bg:n.colour,shape:n.shape,clade:n.clade}}}};}})
 .concat(D.edges.map(function(e){{return {{group:'edges',data:{{
  id:'e_'+e.source+'__'+e.target,source:e.source,target:e.target,
  label:e.label,assays:e.assays,n:e.n}}}};}}));
var cy=cytoscape({{container:document.getElementById('cy'),elements:els,
 style:[
  {{selector:'node',style:{{'background-color':'data(bg)','shape':'data(shape)','label':'data(label)',
    'text-valign':'center','text-halign':'center','color':'#fff','font-size':'11px','font-weight':'bold',
    'width':'96px','height':'62px','border-width':1,'border-color':'data(bg)',
    'text-outline-color':'data(bg)','text-outline-width':'1px'}}}},
  {{selector:'edge',style:{{'width':1.5,'line-color':'#999','target-arrow-color':'#999',
    'target-arrow-shape':'triangle','arrow-scale':0.9,'curve-style':'bezier','label':'data(label)',
    'font-size':'10px','color':'#333','text-rotation':'autorotate','text-background-color':'#fff',
    'text-background-opacity':0.9,'text-background-padding':'3px','text-margin-y':'-9px'}}}},
  {{selector:'edge:loop',style:{{'curve-style':'bezier','loop-direction':'-90deg','loop-sweep':'-45deg',
    'control-point-step-size':'110px','text-rotation':'none'}}}},
  {{selector:':selected',style:{{'border-color':'#A31F34','border-width':4,
    'line-color':'#A31F34','target-arrow-color':'#A31F34'}}}}
 ],
 layout:{{name:'dagre',rankDir:'TB',rankSep:85,nodeSep:45,edgeSep:18,padding:30,animate:false}},
 minZoom:0.2,maxZoom:3,wheelSensitivity:0.3}});
var dp=document.getElementById('dp');
function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
cy.on('tap','node',function(e){{var d=e.target.data();
  dp.innerHTML='<b>'+esc(d.id)+'</b>clade: '+esc(d.clade)
   +'<br>in: '+e.target.indegree()+' &middot; out: '+e.target.outdegree();
  dp.style.display='block';}});
cy.on('tap','edge',function(e){{var d=e.target.data();
  dp.innerHTML='<b>'+esc(d.source)+' &rarr; '+esc(d.target)+'</b>'+d.n+' edges<ul>'
   +d.assays.map(function(a){{return '<li>'+esc(a)+'</li>';}}).join('')+'</ul>';
  dp.style.display='block';}});
cy.on('tap',function(e){{if(e.target===cy)dp.style.display='none';}});
</script></body></html>"""


def choose_layout(selector) -> str:
    """Auto-pick a layout, honouring an explicit override.

    A sample-type query is a pipeline question ("what does CEL turn into"), which
    reads left to right. An investigation or project query is a "what grew out of
    our source material" question, which reads as rings from the centre.
    """
    if getattr(selector, "layout", None):
        return selector.layout
    if selector.sample_type and not (
        selector.graph_inv_id is not None or selector.seek_inv_id is not None or selector.name
    ):
        return "layered"
    return "radial"


def rows_to_svg(rows, clade_map, layout: str = "radial") -> str:
    if layout == "layered":
        return rows_to_svg_layered(rows, clade_map)
    return rows_to_svg_radial(rows, clade_map)


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
    OpenApiParameter("direct_connections", OpenApiTypes.BOOL, OpenApiParameter.QUERY,
                     description="Default true. False walks the whole subtree rooted at "
                                 "sample_type (NHP -> PAV -> TIS -> DNA ...). Requires sample_type."),
    OpenApiParameter("all_conns", OpenApiTypes.BOOL, OpenApiParameter.QUERY,
                     description="Deliberately return the whole graph unfiltered."),
    OpenApiParameter("layout", OpenApiTypes.STR, OpenApiParameter.QUERY,
                     enum=["radial", "layered"],
                     description="SVG layout. Default is radial for an investigation or project "
                                 "and layered for a sample_type; this overrides that."),
    OpenApiParameter("output_format", OpenApiTypes.STR, OpenApiParameter.QUERY,
                     enum=["json", "csv", "svg", "html"], description="Default json."),
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
            (200, "text/html"): OpenApiResponse(
                response=OpenApiTypes.STR,
                description="Interactive cytoscape/dagre network",
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
            "direct_connections": _truthy(params.get("direct_connections", "yes")),
            "all_conns": _truthy(params.get("all_conns")),
            "layout": (params.get("layout") or None),
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

        # direct_connections is excluded from the generic pass and added back only when
        # it is False: the falsy filter below would otherwise swallow exactly the case
        # worth echoing, and silently report a subtree query as a direct one.
        applied = {k: v for k, v in selector.model_dump().items()
                   if k not in ("output_format", "direct_connections", "layout")
                   and v not in (None, False, "")}
        if not selector.direct_connections:
            applied["direct_connections"] = False

        if selector.output_format == "csv":
            response = HttpResponse(rows_to_csv(rows), content_type="text/csv")
            response["Content-Disposition"] = (
                f'attachment; filename="{download_name(selector, "csv")}"'
            )
            return response

        clade_map = fetch_clade_map()

        if selector.output_format == "html":
            return HttpResponse(
                rows_to_html(rows, clade_map), content_type="text/html; charset=utf-8"
            )

        if selector.output_format == "svg":
            svg = rows_to_svg(rows, clade_map, layout=choose_layout(selector))
            response = HttpResponse(svg, content_type="image/svg+xml")
            # inline, not attachment: the diagram should render in a browser tab,
            # but still carry a .svg name when the viewer chooses to save it.
            response["Content-Disposition"] = (
                f'inline; filename="{download_name(selector, "svg")}"'
            )
            return response

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
