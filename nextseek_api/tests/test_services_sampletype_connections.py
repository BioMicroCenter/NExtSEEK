"""Tests for nextseek_api/services/sampletype_connections.py

Covers the selector floor, the three output formats, the superuser gate,
Neo4j failure handling, and the clade-column SVG layout.
"""

import csv
import io
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from rest_framework.test import APIRequestFactory

from nextseek_api.models import SampleTypeConnectionsRequest
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.sampletype_connections import (
    CONNECTIONS_CYPHER,
    _bezier_at,
    _inside_any_node,
    SampleTypeConnectionsViewSet,
    _text_on,
    _truthy,
    rows_to_csv,
    rows_to_svg,
)

MODULE = "nextseek_api.services.sampletype_connections"

ROWS = [
    {"parent_sample_type": "CEL", "child_sample_type": "D.IMG",
     "internal_assay": "Imaging", "n_edges": 1932},
    {"parent_sample_type": "CEL", "child_sample_type": "CEL",
     "internal_assay": "Cell Culture", "n_edges": 433},
    {"parent_sample_type": "TIS", "child_sample_type": "CEL",
     "internal_assay": "Tissue Collection", "n_edges": 14},
]

CLADES = {
    "TIS": ("Source", "#A3D46F"),
    "CEL": ("Raw", "#A2C8F0"),
    "D.IMG": ("Analyzed", "#6B8FDD"),
}


def _request(query, superuser=True):
    """APIRequestFactory yields a WSGIRequest, which has no ``query_params``.

    DRF adds that attribute when it wraps the request for a real view; calling
    the action directly skips that, so it is set by hand here — same shape as
    the entity_tree tests.
    """
    req = APIRequestFactory().get("/sample_types/connections/", query)
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = superuser
    req.user = user
    req.query_params = req.GET
    return req


def _call(query, rows=ROWS, clades=CLADES):
    with patch(f"{MODULE}.run_connections_query", return_value=rows), \
         patch(f"{MODULE}.fetch_clade_map", return_value=clades):
        return SampleTypeConnectionsViewSet().list(_request(query))


# ---------------------------------------------------------------------------
# Selector floor
# ---------------------------------------------------------------------------

def test_no_selector_is_rejected():
    """An empty querystring must not dump the whole graph."""
    with pytest.raises(ValidationError):
        SampleTypeConnectionsRequest.model_validate(
            {"graph_inv_id": None, "seek_inv_id": None, "name": None,
             "sample_type": None, "all_conns": False, "output_format": "json"}
        )


@pytest.mark.parametrize("selector", [
    {"graph_inv_id": 2}, {"seek_inv_id": 11}, {"name": "Impactb Investigation"},
    {"sample_type": "CEL"}, {"all_conns": True},
])
def test_each_selector_alone_satisfies_the_floor(selector):
    assert SampleTypeConnectionsRequest.model_validate(selector) is not None


def test_endpoint_returns_422_without_a_selector():
    resp = _call({})
    assert resp.status_code == 422
    assert resp.data["errors"][0]["title"] == "Invalid request"


@pytest.mark.parametrize("raw,expected", [
    ("yes", True), ("YES", True), ("true", True), ("1", True), ("on", True),
    ("no", False), ("false", False), ("", False), (None, False), (True, True),
])
def test_truthy_spellings(raw, expected):
    assert _truthy(raw) is expected


def test_all_conns_yes_string_reaches_the_model():
    resp = _call({"all_conns": "yes"})
    assert resp.status_code == 200


def test_unknown_output_format_is_rejected():
    assert _call({"sample_type": "CEL", "output_format": "pdf"}).status_code == 422


# ---------------------------------------------------------------------------
# Cypher shape
# ---------------------------------------------------------------------------

def test_investigation_hop_is_an_exists_subquery_not_a_join():
    """A hard MATCH would inflate n_edges and drop samples with no IN_STUDY edge."""
    assert "EXISTS {" in CONNECTIONS_CYPHER
    assert CONNECTIONS_CYPHER.count("MATCH (child)-[:IN_STUDY]") == 1


def test_cypher_short_circuits_when_no_investigation_selector_given():
    assert "$graph_inv_id IS NULL AND $seek_inv_id IS NULL AND $name IS NULL" in CONNECTIONS_CYPHER


def test_sample_type_matches_either_endpoint():
    assert "parent.type = $sample_type" in CONNECTIONS_CYPHER
    assert "child.type  = $sample_type" in CONNECTIONS_CYPHER


def test_query_is_read_only():
    lowered = CONNECTIONS_CYPHER.lower()
    for verb in ("create", "delete", "merge", "set ", "remove", "detach"):
        assert verb not in lowered


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

def test_json_payload_carries_clades_and_echoed_filters():
    resp = _call({"graph_inv_id": "2"})
    assert resp.status_code == 200
    assert resp.data["total"] == 3
    assert resp.data["filters"] == {"graph_inv_id": 2}
    first = resp.data["connections"][0]
    assert first["parent_clade"] == "Raw"
    assert first["child_clade"] == "Analyzed"


def test_csv_is_the_triple_format():
    resp = _call({"sample_type": "CEL", "output_format": "csv"})
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    rows = list(csv.reader(io.StringIO(resp.content.decode())))
    assert rows[0] == ["SampleType", "isParent", "SampleType", "InternalAssay", "Edges"]
    assert rows[1] == ["CEL", "isParent", "D.IMG", "Imaging", "1932"]
    assert all(r[1] == "isParent" for r in rows[1:])


def test_csv_escapes_a_comma_in_an_assay_title():
    out = rows_to_csv([{"parent_sample_type": "A", "child_sample_type": "B",
                        "internal_assay": "Flow, sorted", "n_edges": 1}])
    assert '"Flow, sorted"' in out


def test_svg_response_headers():
    resp = _call({"sample_type": "CEL", "output_format": "svg"})
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/svg+xml"
    assert resp.content.decode().startswith("<svg")


# ---------------------------------------------------------------------------
# SVG layout
# ---------------------------------------------------------------------------

def test_svg_places_clades_in_pipeline_order():
    svg = rows_to_svg(ROWS, CLADES)
    assert svg.index(">Source<") < svg.index(">Raw<") < svg.index(">Analyzed<")


def test_svg_colours_each_node_by_its_clade():
    svg = rows_to_svg(ROWS, CLADES)
    for _clade, color in CLADES.values():
        assert f'fill="{color}"' in svg


def test_svg_renders_a_self_loop_without_crashing():
    """CEL -> CEL 'Cell Culture' is real data, not a malformed row."""
    svg = rows_to_svg([ROWS[1]], CLADES)
    assert svg.count("<path") >= 1
    assert "CEL" in svg


def test_svg_handles_a_sample_type_with_no_clade():
    svg = rows_to_svg(ROWS, {"CEL": ("Raw", "#A2C8F0")})
    assert "Unassigned" in svg
    assert "#D9D9D9" in svg


def test_svg_escapes_markup_in_an_assay_title():
    svg = rows_to_svg(
        [{"parent_sample_type": "A", "child_sample_type": "B",
          "internal_assay": "<script>x</script>", "n_edges": 1}], {})
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_svg_on_empty_rows_is_still_valid_svg():
    svg = rows_to_svg([], CLADES)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "No connections matched" in svg


def test_label_text_contrasts_with_fill():
    assert _text_on("#A3D46F") == "#111111"   # light clade -> dark text
    assert _text_on("#6B8FDD") == "#FFFFFF"   # dark clade  -> light text
    assert _text_on("not-a-colour") == "#111111"


# ---------------------------------------------------------------------------
# Gate and failure handling
# ---------------------------------------------------------------------------

def test_viewset_is_superuser_gated():
    assert IsSuperUser in SampleTypeConnectionsViewSet.permission_classes


def test_is_staff_alone_does_not_pass_the_gate():
    """is_staff is set on every SEEK user at login, so it must not admit anyone."""
    user = MagicMock(is_authenticated=True, is_superuser=False, is_staff=True)
    assert IsSuperUser().has_permission(MagicMock(user=user), None) is False


def test_neo4j_error_becomes_502():
    from neo4j.exceptions import Neo4jError
    with patch(f"{MODULE}.run_connections_query", side_effect=Neo4jError("boom")):
        resp = SampleTypeConnectionsViewSet().list(_request({"sample_type": "CEL"}))
    assert resp.status_code == 502


def test_clade_lookup_failure_still_returns_connections():
    """The clade table is decoration; losing it must not fail the request."""
    resp = _call({"sample_type": "CEL"}, clades={})
    assert resp.status_code == 200
    assert resp.data["total"] == 3
    assert resp.data["connections"][0]["parent_clade"] is None


# ---------------------------------------------------------------------------
# Edge-label placement
# ---------------------------------------------------------------------------

def test_bezier_endpoints_are_exact():
    assert _bezier_at(0.0, 0, 0, 1, 1, 2, 2, 3, 3) == (0.0, 0.0)
    assert _bezier_at(1.0, 0, 0, 1, 1, 2, 2, 3, 3) == (3.0, 3.0)


def test_inside_any_node_detects_containment():
    pos = {"CEL": (100.0, 200.0)}
    assert _inside_any_node((110.0, 210.0), pos) is True
    assert _inside_any_node((10.0, 10.0), pos) is False


def test_edge_labels_are_emitted_after_nodes():
    """Nodes paint opaque boxes; a label emitted first would be clipped by them."""
    svg = rows_to_svg(ROWS, CLADES)
    last_node = svg.rindex("<g><title>")
    first_label = svg.index('paint-order="stroke"')
    assert first_label > last_node


def test_edge_labels_avoid_landing_inside_a_node():
    import re
    svg = rows_to_svg(ROWS, CLADES)
    # Reconstruct node boxes from the rendered rects, then assert no label sits in one.
    boxes = [(float(x), float(y)) for x, y in
             re.findall(r'<rect x="([\d.]+)" y="([\d.]+)" width="170"', svg)]
    labels = [(float(x), float(y)) for x, y in
              re.findall(r'<text x="([\d.]+)" y="([\d.]+)"[^>]*paint-order="stroke"', svg)]
    assert labels, "expected at least one edge label"
    assert not _inside_any_node(labels[0], {i: b for i, b in enumerate(boxes)})


def test_labels_are_suppressed_on_a_dense_graph():
    """Above the limit, assay names live in tooltips only, or the diagram is a smear."""
    dense = [
        {"parent_sample_type": f"P{i}", "child_sample_type": f"C{i}",
         "internal_assay": f"Assay {i}", "n_edges": 1}
        for i in range(60)
    ]
    svg = rows_to_svg(dense, {})
    assert 'paint-order="stroke"' not in svg
    assert svg.count("<title>") >= 60  # every edge still carries its tooltip


# ---------------------------------------------------------------------------
# Schema generation (nextseek-viewset SKILL.md section 10.2)
#
# The endpoint's own tests all passed while this route was returning a 500,
# because they call the viewset method directly and never build the schema.
# A description-only OpenApiResponse emits no `content` block and drf-spectacular
# raises KeyError('content') merging it, taking /nextseek_api/schema/ down with it.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectionsSchema:
    def _schema(self):
        from drf_spectacular.generators import SchemaGenerator
        return SchemaGenerator().get_schema(request=None, public=True)

    def test_schema_generates_at_all(self):
        assert "paths" in self._schema()

    def test_connections_path_is_registered_once_without_a_doubled_segment(self):
        paths = [p for p in self._schema()["paths"] if "connections" in p]
        assert paths == ["/nextseek_api/sample_types/connections/"]

    def test_all_three_content_types_are_advertised(self):
        op = self._schema()["paths"]["/nextseek_api/sample_types/connections/"]["get"]
        assert set(op["responses"]["200"]["content"]) == {
            "application/json", "text/csv", "image/svg+xml",
        }

    def test_every_selector_is_documented_as_a_query_param(self):
        op = self._schema()["paths"]["/nextseek_api/sample_types/connections/"]["get"]
        names = {p["name"] for p in op.get("parameters", [])}
        assert names == {
            "graph_inv_id", "seek_inv_id", "name", "sample_type", "all_conns", "output_format",
        }

    def test_response_model_lands_in_components(self):
        assert "SampleTypeConnectionsResponse" in self._schema()["components"]["schemas"]

    def test_admin_sections_sort_under_assays(self):
        tags = [t if isinstance(t, str) else t["name"] for t in self._schema().get("tags", [])]
        assert tags.index("Assays") < tags.index("admin") < tags.index("Assistant")
        assert tags.index("Assays") < tags.index("Users (admin)")


def test_unauthenticated_caller_is_refused():
    """401, not 403: the gate must challenge rather than leak that the route exists."""
    from rest_framework.permissions import IsAuthenticated
    user = MagicMock()
    user.is_authenticated = False
    user.is_superuser = False
    req = MagicMock(user=user)
    assert IsAuthenticated().has_permission(req, None) is False
    assert IsSuperUser().has_permission(req, None) is False
    assert SampleTypeConnectionsViewSet.permission_classes == [IsAuthenticated, IsSuperUser]


def test_endpoint_is_tagged_admin_because_it_is_superuser_only():
    """The swagger tag tracks the privilege gate, not the URL prefix."""
    from drf_spectacular.generators import SchemaGenerator
    schema = SchemaGenerator().get_schema(request=None, public=True)
    op = schema["paths"]["/nextseek_api/sample_types/connections/"]["get"]
    assert op["tags"] == ["admin"]
