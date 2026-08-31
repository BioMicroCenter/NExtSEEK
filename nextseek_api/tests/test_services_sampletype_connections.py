"""Tests for nextseek_api/services/sampletype_connections.py

Covers the selector floor, the three output formats, the superuser gate,
Neo4j failure handling, and the clade-column SVG layout.
"""

import csv
import io
import json
import re
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from rest_framework.test import APIRequestFactory

from nextseek_api.models import SampleTypeConnectionsRequest
from nextseek_api.permissions import IsSuperUser
from nextseek_api.services.sampletype_connections import (
    CONNECTIONS_CYPHER,
    choose_layout,
    hop_depths,
    rows_to_html,
    rows_to_svg_layered,
    rows_to_svg_radial,
    CONNECTIONS_SUBTREE_CYPHER,
    download_name,
    download_name,
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


def _cypher_without_comments(cypher: str) -> str:
    """Strip // comments before checking for write verbs.

    The guard below is a substring scan, so explanatory prose can trip it: a
    comment reading "carries the full set on edges" contains "set ". Comments are
    not query, so they are removed before the check rather than the prose being
    contorted around the test.
    """
    return "\n".join(
        line.split("//")[0] for line in cypher.splitlines()
    ).lower()


def test_query_is_read_only():
    lowered = _cypher_without_comments(CONNECTIONS_CYPHER)
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
    assert "<svg" in resp.content.decode()[:120]


# ---------------------------------------------------------------------------
# SVG layout
# ---------------------------------------------------------------------------


def test_svg_colours_each_node_by_its_clade():
    svg = rows_to_svg(ROWS, CLADES)
    for _clade, color in CLADES.values():
        assert f'fill="{color}"' in svg


def test_self_loop_is_drawn_by_layered_and_skipped_by_radial():
    """CEL -> CEL 'Cell Culture' is real data. It carries no hop depth, so the
    radial layout omits the edge but must still place the node and not crash."""
    loop = [ROWS[1]]
    layered = rows_to_svg_layered(loop, CLADES)
    assert layered.count("<path") >= 1 and ">CEL<" in layered
    radial = rows_to_svg_radial(loop, CLADES)
    assert ">CEL<" in radial

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
    assert "<svg" in svg[:120] and svg.endswith("</svg>")
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
            "application/json", "text/csv", "image/svg+xml", "text/html",
        }

    def test_every_selector_is_documented_as_a_query_param(self):
        op = self._schema()["paths"]["/nextseek_api/sample_types/connections/"]["get"]
        names = {p["name"] for p in op.get("parameters", [])}
        assert names == {
            "graph_inv_id", "seek_inv_id", "name", "sample_type", "all_conns",
            "direct_connections", "layout", "output_format",
        }

    def test_response_model_lands_in_components(self):
        assert "SampleTypeConnectionsResponse" in self._schema()["components"]["schemas"]

    def test_admin_sections_sort_under_assays(self):
        """Order comes from tagsSorter, NOT from the root tags array.

        Swagger UI uses the root "tags" array only for descriptions; section order
        is tagsSorter, falling back to first-appearance-in-paths. Setting TAGS alone
        produced a schema that read correctly and a page that still showed admin
        first. Both were verified by rendering, twice.
        """
        from django.conf import settings as dj
        order = dj.API_TAG_ORDER
        assert order.index("Assays") < order.index("admin") < order.index("Attributes")
        assert order.index("Assays") < order.index("Users (admin)")
        assert "tagsSorter" in dj.SPECTACULAR_SETTINGS["SWAGGER_UI_SETTINGS"], \
            "without tagsSorter the order is ignored and admin returns to the top"

    def test_tag_order_covers_every_tag_actually_in_use(self):
        """A tag missing from the list sorts to the end silently. Three were missed
        the first time -- batch-upload, cc-assistant and schema are derived by
        drf-spectacular from the URL, not written as an explicit tags=[...]."""
        from django.conf import settings as dj
        used = {t for item in self._schema()["paths"].values()
                for op in item.values() if isinstance(op, dict)
                for t in (op.get("tags") or [])}
        assert not (used - set(dj.API_TAG_ORDER)), \
            f"tags used but not ordered: {sorted(used - set(dj.API_TAG_ORDER))}"


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


# ---------------------------------------------------------------------------
# Download filenames
#
# Without an explicit Content-Disposition the client guesses from the content
# type, and the "+xml" in image/svg+xml makes Swagger and browsers save the
# diagram as a .xml file.
# ---------------------------------------------------------------------------

def test_svg_download_is_named_svg_not_xml():
    resp = _call({"graph_inv_id": "2", "output_format": "svg"})
    assert resp["Content-Type"] == "image/svg+xml"
    assert resp["Content-Disposition"].endswith('.svg"')
    assert ".xml" not in resp["Content-Disposition"]


def test_svg_is_inline_so_a_browser_renders_it():
    resp = _call({"graph_inv_id": "2", "output_format": "svg"})
    assert resp["Content-Disposition"].startswith("inline;")


def test_csv_is_an_attachment():
    resp = _call({"graph_inv_id": "2", "output_format": "csv"})
    assert resp["Content-Disposition"].startswith("attachment;")
    assert resp["Content-Disposition"].endswith('.csv"')


def test_svg_document_declares_itself_as_xml():
    svg = rows_to_svg(ROWS, CLADES)
    assert svg.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert rows_to_svg([], CLADES).startswith('<?xml version="1.0" encoding="UTF-8"?>')


@pytest.mark.parametrize("selector,expected", [
    ({"graph_inv_id": 2}, "sampletype_connections_inv2.svg"),
    ({"seek_inv_id": 11}, "sampletype_connections_proj11.svg"),
    ({"name": "Impactb Investigation"}, "sampletype_connections_impactb-investigation.svg"),
    ({"sample_type": "D.IMG"}, "sampletype_connections_d-img.svg"),
    ({"graph_inv_id": 2, "sample_type": "CEL"}, "sampletype_connections_inv2_cel.svg"),
    ({"all_conns": True}, "sampletype_connections_all.svg"),
])
def test_download_name_reflects_the_selector(selector, expected):
    assert download_name(SampleTypeConnectionsRequest.model_validate(selector), "svg") == expected


# ---------------------------------------------------------------------------
# Download filenames
# ---------------------------------------------------------------------------

def test_svg_download_is_named_svg_not_xml():
    """image/svg+xml makes clients guess .xml unless the filename is explicit."""
    resp = _call({"graph_inv_id": "2", "output_format": "svg"})
    assert resp["Content-Disposition"].endswith('.svg"')
    assert ".xml" not in resp["Content-Disposition"]


def test_svg_is_inline_and_csv_is_attachment():
    assert _call({"graph_inv_id": "2", "output_format": "svg"})["Content-Disposition"].startswith("inline;")
    assert _call({"graph_inv_id": "2", "output_format": "csv"})["Content-Disposition"].startswith("attachment;")


def test_svg_document_declares_itself_as_xml():
    assert rows_to_svg(ROWS, CLADES).startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert rows_to_svg([], CLADES).startswith('<?xml version="1.0" encoding="UTF-8"?>')


@pytest.mark.parametrize("selector,expected", [
    ({"graph_inv_id": 2}, "sampletype_connections_inv2.svg"),
    ({"seek_inv_id": 11}, "sampletype_connections_proj11.svg"),
    ({"name": "Impactb Investigation"}, "sampletype_connections_impactb-investigation.svg"),
    ({"sample_type": "D.IMG"}, "sampletype_connections_d-img.svg"),
    ({"graph_inv_id": 2, "sample_type": "CEL"}, "sampletype_connections_inv2_cel.svg"),
    ({"all_conns": True}, "sampletype_connections_all.svg"),
])
def test_download_name_reflects_the_selector(selector, expected):
    assert download_name(SampleTypeConnectionsRequest.model_validate(selector), "svg") == expected


# ---------------------------------------------------------------------------
# direct_connections: the subtree walk
# ---------------------------------------------------------------------------

def test_subtree_cypher_collects_the_tree_once_then_filters_edges():
    """The per-edge EXISTS form is a performance trap under a parameter: measured
    >100s unbounded, versus 0.3s for this inverted form on identical data."""
    assert "collect(DISTINCT elementId(descendant))" in CONNECTIONS_SUBTREE_CYPHER
    assert "elementId(parent) IN subtree" in CONNECTIONS_SUBTREE_CYPHER
    assert "root.type = $sample_type" in CONNECTIONS_SUBTREE_CYPHER


def test_subtree_does_not_use_the_per_edge_exists_form():
    """Guards the regression: this shape times out when $sample_type is a parameter."""
    assert "MATCH (parent)-[:DERIVED_FROM*0..]->(root" not in CONNECTIONS_SUBTREE_CYPHER


def test_subtree_traversal_is_not_depth_bounded():
    """*0..4 answered fast but silently dropped 3 of 39 triples. Completeness wins."""
    import re as _re
    assert not _re.search(r"DERIVED_FROM\*0\.\.\d", CONNECTIONS_SUBTREE_CYPHER)


def test_subtree_includes_the_root_itself():
    """*0.. not *1..: NHP's own direct edges must stay inside NHP's tree."""
    assert "*0.." in CONNECTIONS_SUBTREE_CYPHER and "*1.." not in CONNECTIONS_SUBTREE_CYPHER


def test_direct_cypher_does_not_traverse():
    assert "DERIVED_FROM*" not in CONNECTIONS_CYPHER


def test_both_variants_scope_to_an_investigation_identically():
    for cy in (CONNECTIONS_CYPHER, CONNECTIONS_SUBTREE_CYPHER):
        assert "IN_INVESTIGATION" in cy
        assert "$graph_inv_id IS NULL AND $seek_inv_id IS NULL AND $name IS NULL" in cy


def test_subtree_variant_is_read_only():
    lowered = _cypher_without_comments(CONNECTIONS_SUBTREE_CYPHER)
    for verb in ("create", "delete", "merge", "set ", "remove", "detach"):
        assert verb not in lowered


def test_direct_connections_defaults_to_yes():
    assert SampleTypeConnectionsRequest.model_validate({"sample_type": "NHP"}).direct_connections is True


def test_direct_connections_no_requires_a_sample_type():
    """The flag names a root to walk from; without one it would silently do nothing."""
    with pytest.raises(ValidationError):
        SampleTypeConnectionsRequest.model_validate({"graph_inv_id": 2, "direct_connections": False})


def test_endpoint_selects_the_subtree_query_when_asked():
    captured = {}

    def _fake(selector):
        captured["direct"] = selector.direct_connections
        return ROWS

    with patch(f"{MODULE}.run_connections_query", side_effect=_fake), \
         patch(f"{MODULE}.fetch_clade_map", return_value=CLADES):
        resp = SampleTypeConnectionsViewSet().list(
            _request({"sample_type": "NHP", "direct_connections": "no"}))
    assert resp.status_code == 200 and captured["direct"] is False


def test_default_flag_is_not_echoed_but_a_set_one_is():
    assert "direct_connections" not in _call({"sample_type": "CEL"}).data["filters"]
    with patch(f"{MODULE}.run_connections_query", return_value=ROWS), \
         patch(f"{MODULE}.fetch_clade_map", return_value=CLADES):
        resp = SampleTypeConnectionsViewSet().list(
            _request({"sample_type": "NHP", "direct_connections": "no"}))
    assert resp.data["filters"]["direct_connections"] is False


# ---------------------------------------------------------------------------
# Layout selection and the radial renderer
# ---------------------------------------------------------------------------

def test_layout_is_layered_for_a_bare_sample_type():
    """A sample-type query is a pipeline question, so it reads left to right."""
    sel = SampleTypeConnectionsRequest.model_validate({"sample_type": "CEL"})
    assert choose_layout(sel) == "layered"


@pytest.mark.parametrize("selector", [
    {"graph_inv_id": 2}, {"seek_inv_id": 11}, {"name": "Impactb Investigation"},
    {"all_conns": True}, {"graph_inv_id": 2, "sample_type": "CEL"},
])
def test_layout_is_radial_for_everything_else(selector):
    assert choose_layout(SampleTypeConnectionsRequest.model_validate(selector)) == "radial"


@pytest.mark.parametrize("want", ["radial", "layered"])
def test_explicit_layout_overrides_the_auto_choice(want):
    sel = SampleTypeConnectionsRequest.model_validate({"sample_type": "CEL", "layout": want})
    assert choose_layout(sel) == want


def test_hop_depth_puts_source_clade_at_ring_zero():
    _types, _ch, _pa, depth = hop_depths(ROWS, CLADES)
    assert depth["TIS"] == 0          # Source clade
    assert depth["CEL"] == 1          # TIS -> CEL
    assert depth["D.IMG"] == 2        # TIS -> CEL -> D.IMG


def test_hop_depth_keeps_unreachable_types_visible():
    """An orphan pair must land on an outer ring, never vanish from the diagram."""
    rows = ROWS + [{"parent_sample_type": "ORPH", "child_sample_type": "ORPHB",
                    "internal_assay": "Detached", "n_edges": 1}]
    types, _ch, _pa, depth = hop_depths(rows, CLADES)
    assert "ORPH" in types and "ORPHB" in types
    assert depth["ORPH"] > max(depth[t] for t in ("TIS", "CEL", "D.IMG"))


def test_radial_svg_draws_every_node_and_its_rings():
    svg = rows_to_svg_radial(ROWS, CLADES)
    assert svg.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    for t in ("TIS", "CEL", "D.IMG"):
        assert f">{t}<" in svg
    assert "hop 1" in svg and "hop 2" in svg


def test_node_is_wide_enough_for_its_label():
    """D.ADDCP is 7 characters; a fixed radius clipped it."""
    from nextseek_api.services.sampletype_connections import _node_radius
    assert _node_radius("D.ADDCP") > _node_radius("CEL")
    assert _node_radius("D.ADDCP") >= (7 * 6.6 + 11.0) / 2


def test_both_layouts_are_deterministic():
    """Output is a pure function of the rows, so it is safe to assert on as a string."""
    assert rows_to_svg_radial(ROWS, CLADES) == rows_to_svg_radial(ROWS, CLADES)
    assert rows_to_svg_layered(ROWS, CLADES) == rows_to_svg_layered(ROWS, CLADES)


def test_layered_orders_columns_along_the_clade_pipeline():
    svg = rows_to_svg_layered(ROWS, CLADES)
    assert svg.index(">SOURCE<") < svg.index(">RAW<") < svg.index(">ANALYZED<")


def test_both_layouts_survive_a_sample_type_with_no_clade():
    for fn in (rows_to_svg_radial, rows_to_svg_layered):
        svg = fn(ROWS, {"CEL": ("Raw", "#A2C8F0")})
        assert "Unassigned" in svg and "#D9D9D9" in svg


def test_both_layouts_escape_markup_in_an_assay_title():
    rows = [{"parent_sample_type": "A", "child_sample_type": "B",
             "internal_assay": "<script>x</script>", "n_edges": 1}]
    for fn in (rows_to_svg_radial, rows_to_svg_layered):
        svg = fn(rows, {})
        assert "<script>" not in svg and "&lt;script&gt;" in svg


def test_endpoint_uses_the_chosen_layout():
    resp = _call({"graph_inv_id": "2", "output_format": "svg"})
    assert "hop 1" in resp.content.decode()          # radial
    resp2 = _call({"sample_type": "CEL", "output_format": "svg"})
    assert ">RAW<" in resp2.content.decode()         # layered


# ---------------------------------------------------------------------------
# Clade pipeline order and uniform node sizing
# ---------------------------------------------------------------------------

def test_clade_pipeline_puts_processed_before_raw():
    """Source -> Processed -> Raw -> Analyzed. A processed SPECIMEN (TIS, DNA)
    precedes the RAW DATA FILE measured from it (D.*). The clades table's id
    order says otherwise and is not the pipeline."""
    from nextseek_api.services.sampletype_connections import CLADE_PIPELINE
    assert CLADE_PIPELINE == ["Source", "Processed", "Raw", "Analyzed"]
    assert CLADE_PIPELINE.index("Processed") < CLADE_PIPELINE.index("Raw")


def test_node_size_is_uniform_across_the_graph():
    """One size for every node; a per-label size implied a meaning it did not carry."""
    from nextseek_api.services.sampletype_connections import _uniform_node_radius
    types = ["CEL", "D.ADDCP", "TIS"]
    assert _uniform_node_radius(types) == _uniform_node_radius(list(reversed(types)))
    assert _uniform_node_radius(["CEL"]) < _uniform_node_radius(["D.ADDCP"])


def test_layered_svg_draws_one_node_width_for_everything():
    svg = rows_to_svg_layered(ROWS, CLADES)
    widths = set(re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="(\d+)" height="26"', svg))
    assert len(widths) == 1, f"expected a single node width, got {widths}"


# ---------------------------------------------------------------------------
# Interactive HTML output
# ---------------------------------------------------------------------------

def test_html_uses_the_curation_clade_styles():
    """Same palette and shapes as curation_skill SAMPLE_TREE, so the two read alike."""
    from nextseek_api.services.sampletype_connections import CLADE_STYLES
    assert CLADE_STYLES["Source"] == ("#2E7D32", "ellipse")
    assert CLADE_STYLES["Processed"] == ("#E65100", "round-rectangle")
    assert CLADE_STYLES["Raw"] == ("#42A5F5", "diamond")
    assert CLADE_STYLES["Analyzed"] == ("#1565C0", "hexagon")
    assert list(CLADE_STYLES) == ["Source", "Processed", "Raw", "Analyzed"]


def test_html_is_a_self_contained_cytoscape_page():
    page = rows_to_html(ROWS, CLADES)
    assert page.startswith("<!doctype html>")
    assert "cytoscape.min.js" in page and "cytoscape-dagre" in page
    assert "name:'dagre'" in page
    assert "'width':'96px','height':'62px'" in page   # uniform nodes


def test_html_embeds_every_node_and_collapses_pairs_to_one_edge():
    page = rows_to_html(ROWS, CLADES)
    data = json.loads(re.search(r"var D=(\{.*?\});\n", page, re.S).group(1))
    assert {n["id"] for n in data["nodes"]} == {"CEL", "TIS", "D.IMG"}
    pairs = [(e["source"], e["target"]) for e in data["edges"]]
    assert len(pairs) == len(set(pairs)), "one edge per pair; assays are collapsed into it"


def test_html_edge_carries_every_assay_for_that_pair():
    rows = [
        {"parent_sample_type": "CEL", "child_sample_type": "D.IMG",
         "internal_assay": "Imaging", "n_edges": 10},
        {"parent_sample_type": "CEL", "child_sample_type": "D.IMG",
         "internal_assay": "Comet Chip", "n_edges": 5},
    ]
    data = json.loads(re.search(r"var D=(\{.*?\});\n", rows_to_html(rows, CLADES), re.S).group(1))
    edge = data["edges"][0]
    assert edge["assays"] == ["Comet Chip", "Imaging"]
    assert edge["n"] == 15
    assert edge["label"].endswith("+1")


def test_html_escapes_markup_in_a_title():
    page = rows_to_html(ROWS, CLADES, title="<script>x</script>")
    assert "<title><script>" not in page


def test_endpoint_serves_html():
    resp = _call({"sample_type": "CEL", "output_format": "html"})
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/html")
    assert b"cytoscape" in resp.content


def test_layered_puts_processed_left_of_raw_when_both_are_present():
    """The whole point of the order fix: a processed specimen sits before the raw
    data file measured from it. ROWS has no Processed type, so this uses its own."""
    four = [
        {"parent_sample_type": "NHP", "child_sample_type": "TIS",
         "internal_assay": "Tissue Collection", "n_edges": 5},
        {"parent_sample_type": "TIS", "child_sample_type": "D.SEQ",
         "internal_assay": "Short Read Sequencing", "n_edges": 9},
        {"parent_sample_type": "D.SEQ", "child_sample_type": "A.GEX",
         "internal_assay": "Gene Expression Analysis", "n_edges": 3},
    ]
    clades = {
        "NHP": ("Source", "#A3D46F"), "TIS": ("Processed", "#F4A45E"),
        "D.SEQ": ("Raw", "#A2C8F0"), "A.GEX": ("Analyzed", "#6B8FDD"),
    }
    svg = rows_to_svg_layered(four, clades)
    assert svg.index(">SOURCE<") < svg.index(">PROCESSED<") \
        < svg.index(">RAW<") < svg.index(">ANALYZED<")


# ---------------------------------------------------------------------------
# 422 bodies must actually RENDER
#
# The endpoint shipped returning 500 with an HTML body for both custom-validator
# failures, while these tests were green. Asserting resp.status_code inspects an
# unrendered DRF Response; the crash happens in the renderer, which the test path
# never reached. Pydantic stores the live ValueError from a @model_validator in
# ctx.error, and JSONRenderer raises "Object of type ValueError is not JSON
# serializable" on it. Built-in validator errors carry ctx.error = None, which is
# why bad output_format returned a correct 422 while "no selector" did not.
# ---------------------------------------------------------------------------

def _render(resp):
    """Force the response through the JSON renderer, as a real request would."""
    from rest_framework.renderers import JSONRenderer
    return JSONRenderer().render(resp.data)


@pytest.mark.parametrize("query,label", [
    ({}, "no selector at all"),
    ({"direct_connections": "no"}, "direct_connections=no without a sample_type"),
    ({"sample_type": "CEL", "output_format": "pdf"}, "unknown output_format"),
    ({"sample_type": "CEL", "layout": "spiral"}, "unknown layout"),
    ({"graph_inv_id": "abc"}, "non-numeric graph_inv_id"),
])
def test_every_422_body_is_json_serializable(query, label):
    resp = _call(query)
    assert resp.status_code == 422, label
    body = json.loads(_render(resp))
    assert body["errors"][0]["title"] == "Invalid request"
    assert isinstance(body["errors"][0]["detail"], list)


def test_custom_validator_message_survives_into_the_rendered_body():
    body = json.loads(_render(_call({})))
    blob = json.dumps(body)
    assert "sample_type" in blob and "all_conns" in blob


def test_no_exception_objects_leak_into_the_error_detail():
    """ctx.error is where the unserializable ValueError hid."""
    for query in ({}, {"direct_connections": "no"}):
        for err in _call(query).data["errors"][0]["detail"]:
            assert not isinstance(err.get("ctx", {}).get("error"), BaseException)
