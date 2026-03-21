"""
Comprehensive tests for nextseek_api/services/entity_tree.py

Covers:
- EntityTreePagination
- _run_sql_query helper
- EntityTreeViewSet: list_nodes, list_edges, list_edge_attributes, lineage
- _get_clade_color, _fetch_lineage_enrichment, _fetch_single_lineage helpers
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from django.test import override_settings
from rest_framework.test import APIRequestFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_request(method="get", path="/", data=None, user=None, query=None):
    """Build a mock request that works with ViewSet methods accessing request.data / request.GET."""
    factory = APIRequestFactory()
    fn = getattr(factory, method)
    if data is not None:
        req = fn(path, data=json.dumps(data), content_type="application/json")
    else:
        req = fn(path)
    if user is None:
        user = MagicMock()
        user.is_authenticated = True
    req.user = user
    if data is not None:
        req.data = data
    if query:
        req.GET = query
    req.query_params = req.GET
    return req


def _get_inner(resp):
    """Extract inner data from a DRF paginated or plain Response.

    DRF PageNumberPagination wraps the payload under ``results``.
    EntityTreeViewSet passes ``model.model_dump()`` to ``get_paginated_response``,
    so the inner dict lives at ``resp.data["results"]``.
    """
    data = resp.data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def _make_neo4j_node(node_id, uuid, node_type):
    """Create a mock Neo4j Node object."""
    node = MagicMock()
    node._properties = {"id": node_id, "uuid": uuid, "type": node_type}
    return node


def _make_neo4j_rel(child_id, child_uuid, child_type, parent_id, parent_uuid, parent_type, **rel_props):
    """Create a mock Neo4j Relationship object."""
    rel = MagicMock()
    props = {
        "internal_assay_id": None,
        "internal_assay_title": None,
        "protocol_title": None,
        "protocol_id": None,
    }
    props.update(rel_props)
    rel._properties = props
    rel.start_node = MagicMock()
    rel.start_node._properties = {"id": child_id, "uuid": child_uuid, "type": child_type}
    rel.end_node = MagicMock()
    rel.end_node._properties = {"id": parent_id, "uuid": parent_uuid, "type": parent_type}
    return rel


def _make_graph_result(nodes, rels):
    """Create a mock graph result with .nodes and .relationships."""
    result = MagicMock()
    result.nodes = nodes
    result.relationships = rels
    return result


# ============================================================================
# EntityTreePagination
# ============================================================================


class TestEntityTreePagination:
    def test_defaults(self):
        from nextseek_api.services.entity_tree import EntityTreePagination
        p = EntityTreePagination()
        assert p.page_size == 100
        assert p.page_size_query_param == "page_size"
        assert p.max_page_size == 1000


# ============================================================================
# _run_sql_query
# ============================================================================


class TestRunSqlQuery:
    @patch("nextseek_api.services.entity_tree.MySQLdb")
    def test_returns_dicts(self, mock_mysql):
        from nextseek_api.services.entity_tree import _run_sql_query
        mock_conn = MagicMock()
        mock_mysql.connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "Alice"), (2, "Bob")]
        result = _run_sql_query("SELECT id, name FROM t")
        assert len(result) == 2
        assert result[0] == {"id": 1, "name": "Alice"}
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("nextseek_api.services.entity_tree.MySQLdb")
    def test_with_params(self, mock_mysql):
        from nextseek_api.services.entity_tree import _run_sql_query
        mock_conn = MagicMock()
        mock_mysql.connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.description = [("id",)]
        mock_cursor.fetchall.return_value = [(1,)]
        result = _run_sql_query("SELECT id FROM t WHERE x=%s", [42])
        mock_cursor.execute.assert_called_once_with("SELECT id FROM t WHERE x=%s", [42])
        assert result == [{"id": 1}]


# ============================================================================
# EntityTreeViewSet.list_nodes
# ============================================================================


class TestListNodes:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        vs = EntityTreeViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(None, None))
    def test_list_nodes_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(user=user)
        resp = vs.list_nodes(req)
        assert resp.status_code == 401

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_200_primary_source(self, mock_sql, mock_auth):
        """Success via primary SQL source (dmac.sample_types_context)."""
        vs = self._viewset()
        mock_sql.return_value = [
            {"node": "NHP", "id": 41, "description": "Non Human Primate", "clade": "Subject"},
        ]
        with patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_sa.return_value.getAttributeTitlesBySampleTypeIds.return_value = {
                41: ["Sex", "Species"],
            }
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 200

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_502_attribute_lookup_fail(self, mock_sql, _):
        """When attribute lookup fails, returns 502."""
        vs = self._viewset()
        mock_sql.return_value = [
            {"node": "NHP", "id": 41, "description": "NHP", "clade": "Subject"},
        ]
        with patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_sa.side_effect = RuntimeError("can't load")
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 502

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_502_missing_metadata_fields(self, mock_sql, _):
        """Sample type with no attributes returns 502."""
        vs = self._viewset()
        mock_sql.return_value = [
            {"node": "NHP", "id": 41, "description": "NHP", "clade": "Subject"},
        ]
        with patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_sa.return_value.getAttributeTitlesBySampleTypeIds.return_value = {
                41: [],  # empty attributes
            }
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 502
        errors = resp.data.get("errors", [])
        assert any("Missing sample type attributes" in str(e) for e in errors)

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_fallback_path(self, mock_sql, _):
        """Fallback via DBtable_sample_types_clades when primary SQL fails."""
        vs = self._viewset()
        mock_sql.side_effect = RuntimeError("table not found")
        with patch("dmac.dbtable_sampletypesclades.DBtable_sample_types_clades") as mock_clades, \
             patch("seek.models.Sample_types") as mock_st, \
             patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_clades.return_value.getAllWithTitles.return_value = [
                {"sample_type_id": 41, "sample_type_title": "NHP", "clade_title": "Subject"},
            ]
            mock_st.objects.filter.return_value.values.return_value = [
                {"id": 41, "title": "NHP", "description": "Non Human Primate"},
            ]
            mock_sa.return_value.getAttributeTitlesBySampleTypeIds.return_value = {
                41: ["Sex", "Species"],
            }
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 200

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_fallback_also_fails_502(self, mock_sql, _):
        """Both primary and fallback fail -> 502."""
        vs = self._viewset()
        mock_sql.side_effect = RuntimeError("table not found")
        with patch("dmac.dbtable_sampletypesclades.DBtable_sample_types_clades") as mock_clades:
            mock_clades.return_value.getAllWithTitles.side_effect = RuntimeError("also broken")
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 502
        errors = resp.data.get("errors", [])
        assert any("Database error" in str(e) for e in errors)

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_fallback_with_non_int_ids(self, mock_sql, _):
        """Fallback path with non-integer sample_type_id should skip those items."""
        vs = self._viewset()
        mock_sql.side_effect = RuntimeError("table not found")
        with patch("dmac.dbtable_sampletypesclades.DBtable_sample_types_clades") as mock_clades, \
             patch("seek.models.Sample_types") as mock_st, \
             patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_clades.return_value.getAllWithTitles.return_value = [
                {"sample_type_id": 41, "sample_type_title": "NHP", "clade_title": "Subject"},
                {"sample_type_id": "not-a-number", "sample_type_title": "BAD", "clade_title": "Bad"},
                {"sample_type_id": None, "sample_type_title": "NONE", "clade_title": "None"},
            ]
            mock_st.objects.filter.return_value.values.return_value = [
                {"id": 41, "title": "NHP", "description": "Non Human Primate"},
            ]
            mock_sa.return_value.getAttributeTitlesBySampleTypeIds.return_value = {
                41: ["Sex", "Species"],
            }
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 200

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_list_nodes_empty_rows(self, mock_sql, _):
        """Empty primary SQL result with no rows -> no items, triggers unpaginated path."""
        vs = self._viewset()
        mock_sql.return_value = []  # empty result
        with patch("seek.dbtable_sampleattribute.DBtable_sampleattribute") as mock_sa:
            mock_sa.return_value.getAttributeTitlesBySampleTypeIds.return_value = {}
            resp = vs.list_nodes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["total"] == 0


# ============================================================================
# EntityTreeViewSet.list_edges
# ============================================================================


class TestListEdges:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        vs = EntityTreeViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(None, None))
    def test_list_edges_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        resp = vs.list_edges(_auth_request(user=user))
        assert resp.status_code == 401

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_list_edges_200(self, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"source": "NHP", "target": "PAV", "annotation": "Patient Visit"},
                {"source": "PAV", "target": "TIS", "annotation": "Tissue Collection"},
            ],
            MagicMock(),
            ["source", "target", "annotation"],
        )
        resp = vs.list_edges(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["total"] == 2

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_list_edges_filters_incomplete(self, mock_gd, _):
        """Records missing source/target/annotation are skipped."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"source": "NHP", "target": "PAV", "annotation": "Visit"},
                {"source": None, "target": "TIS", "annotation": "Bad"},
                {"source": "PAV", "target": None, "annotation": "Bad"},
                {"source": "PAV", "target": "TIS", "annotation": None},
            ],
            MagicMock(),
            ["source", "target", "annotation"],
        )
        resp = vs.list_edges(_auth_request())
        inner = _get_inner(resp)
        assert inner["total"] == 1

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_list_edges_502_neo4j_error(self, mock_gd, _):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.list_edges(_auth_request())
        assert resp.status_code == 502
        errors = resp.data.get("errors", [])
        assert any("Neo4j error" in str(e) for e in errors)

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_list_edges_502_generic(self, mock_gd, _):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.list_edges(_auth_request())
        assert resp.status_code == 502

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_list_edges_empty(self, mock_gd, _):
        """Empty Neo4j result triggers unpaginated response path."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = ([], MagicMock(), [])
        resp = vs.list_edges(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["total"] == 0


# ============================================================================
# EntityTreeViewSet.list_edge_attributes
# ============================================================================


class TestListEdgeAttributes:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        vs = EntityTreeViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(None, None))
    def test_edge_attrs_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        resp = vs.list_edge_attributes(_auth_request(user=user))
        assert resp.status_code == 401

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_edge_attrs_200_with_enrichment(self, mock_sql, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {
                    "source": "NHP", "target": "PAV",
                    "annotation": "Patient Visit",
                    "internal_assay_id": "16",
                },
            ],
            MagicMock(),
            ["source", "target", "annotation", "internal_assay_id"],
        )
        mock_sql.return_value = [
            {
                "internal_assay_id": 16,
                "description": "Visit data",
                "study_titles": "MIT_SRP",
            }
        ]
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["total"] == 1
        edge = inner["edges"][0]
        assert edge["description"] == "Visit data"
        assert edge["study_titles"] == "MIT_SRP"

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_edge_attrs_title_to_id_mapping(self, mock_sql, mock_gd, _):
        """When Neo4j doesn't have internal_assay_id, map from title."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {
                    "source": "NHP", "target": "PAV",
                    "annotation": "Patient Visit",
                    "internal_assay_id": None,
                },
            ],
            MagicMock(),
            ["source", "target", "annotation", "internal_assay_id"],
        )
        mock_sql.side_effect = [
            [{"internal_assay_title": "Patient Visit", "id": 16}],
            [{"internal_assay_id": 16, "description": "Visit", "study_titles": "MIT"}],
        ]
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        edge = inner["edges"][0]
        assert edge["internal_assay_id"] == "16"

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_edge_attrs_enrichment_failure(self, mock_sql, mock_gd, _):
        """Enrichment SQL failure doesn't break response."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"source": "NHP", "target": "PAV", "annotation": "Visit", "internal_assay_id": "16"}],
            MagicMock(),
            ["source", "target", "annotation", "internal_assay_id"],
        )
        mock_sql.side_effect = RuntimeError("DB down")
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["edges"][0]["description"] is None

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_edge_attrs_502_neo4j_error(self, mock_gd, _):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 502

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_edge_attrs_502_generic(self, mock_gd, _):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 502

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_edge_attrs_incomplete_records_filtered(self, mock_sql, mock_gd, _):
        """Records missing source/target/annotation are skipped."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"source": "NHP", "target": "PAV", "annotation": "Visit", "internal_assay_id": "16"},
                {"source": None, "target": "PAV", "annotation": "Bad", "internal_assay_id": None},
            ],
            MagicMock(),
            ["source", "target", "annotation", "internal_assay_id"],
        )
        mock_sql.return_value = [
            {"internal_assay_id": 16, "description": "D", "study_titles": "S"},
        ]
        resp = vs.list_edge_attributes(_auth_request())
        inner = _get_inner(resp)
        assert inner["total"] == 1

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_edge_attrs_empty(self, mock_gd, _):
        """Empty Neo4j result triggers unpaginated response path."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = ([], MagicMock(), [])
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["total"] == 0

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_edge_attrs_title_mapping_failure(self, mock_sql, mock_gd, _):
        """Title-to-ID SQL failure falls back gracefully."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"source": "NHP", "target": "PAV", "annotation": "Visit", "internal_assay_id": None}],
            MagicMock(),
            ["source", "target", "annotation", "internal_assay_id"],
        )
        mock_sql.side_effect = RuntimeError("SQL fail")
        resp = vs.list_edge_attributes(_auth_request())
        assert resp.status_code == 200
        inner = _get_inner(resp)
        assert inner["edges"][0]["internal_assay_id"] is None


# ============================================================================
# EntityTreeViewSet._get_clade_color
# ============================================================================


class TestGetCladeColor:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        return EntityTreeViewSet()

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_color_found(self, mock_sql):
        vs = self._viewset()
        mock_sql.return_value = [{"color": "#A3D46F"}]
        assert vs._get_clade_color("NHP") == "#A3D46F"

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_color_not_found(self, mock_sql):
        vs = self._viewset()
        mock_sql.return_value = []
        assert vs._get_clade_color("UNKNOWN") == "#000000"

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_color_none_value(self, mock_sql):
        vs = self._viewset()
        mock_sql.return_value = [{"color": None}]
        assert vs._get_clade_color("NHP") == "#000000"

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_color_exception(self, mock_sql):
        vs = self._viewset()
        mock_sql.side_effect = RuntimeError("DB fail")
        assert vs._get_clade_color("NHP") == "#000000"


# ============================================================================
# EntityTreeViewSet._fetch_lineage_enrichment
# ============================================================================


class TestFetchLineageEnrichment:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        return EntityTreeViewSet()

    def test_empty_ids(self):
        vs = self._viewset()
        assert vs._fetch_lineage_enrichment(set()) == {}

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_enrichment_success(self, mock_sql):
        vs = self._viewset()
        mock_sql.return_value = [
            {"internal_assay_id": 16, "description": "Visit", "study_titles": "MIT_SRP"},
        ]
        result = vs._fetch_lineage_enrichment({16})
        assert 16 in result
        assert result[16]["description"] == "Visit"

    @patch("nextseek_api.services.entity_tree._run_sql_query")
    def test_enrichment_exception(self, mock_sql):
        vs = self._viewset()
        mock_sql.side_effect = RuntimeError("DB fail")
        result = vs._fetch_lineage_enrichment({16})
        assert result == {}


# ============================================================================
# EntityTreeViewSet._fetch_single_lineage
# ============================================================================


class TestFetchSingleLineage:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        vs = EntityTreeViewSet()
        return vs

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#A3D46F"}])
    def test_lineage_with_nodes_and_rels(self, _):
        vs = self._viewset()
        driver = MagicMock()
        nodes = [
            _make_neo4j_node(1, "NHP-1", "NHP"),
            _make_neo4j_node(2, "PAV-1", "PAV"),
        ]
        rels = [
            _make_neo4j_rel(2, "PAV-1", "PAV", 1, "NHP-1", "NHP",
                            internal_assay_id="16", internal_assay_title="Visit",
                            protocol_title="P1", protocol_id="99"),
        ]
        driver.execute_query.return_value = _make_graph_result(nodes, rels)
        tree, assay_ids = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_nodes == 2
        assert tree.total_rels == 1
        assert 16 in assay_ids
        assert tree.warning is None

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000000"}])
    def test_lineage_empty_result(self, _):
        vs = self._viewset()
        driver = MagicMock()
        result = MagicMock()
        result.nodes = None
        driver.execute_query.return_value = result
        tree, assay_ids = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_nodes == 0
        assert tree.warning is not None
        assert assay_ids == set()

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000000"}])
    def test_lineage_none_result(self, _):
        vs = self._viewset()
        driver = MagicMock()
        driver.execute_query.return_value = None
        tree, assay_ids = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_nodes == 0

    def test_lineage_exception(self):
        vs = self._viewset()
        driver = MagicMock()
        driver.execute_query.side_effect = RuntimeError("boom")
        tree, assay_ids = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_nodes == 0
        assert "Query failed" in tree.warning
        assert assay_ids == set()

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#AAA"}])
    def test_lineage_duplicate_rels_deduped(self, _):
        """Duplicate child->parent relationships are deduplicated."""
        vs = self._viewset()
        driver = MagicMock()
        nodes = [
            _make_neo4j_node(1, "NHP-1", "NHP"),
            _make_neo4j_node(2, "PAV-1", "PAV"),
        ]
        rel1 = _make_neo4j_rel(2, "PAV-1", "PAV", 1, "NHP-1", "NHP")
        rel2 = _make_neo4j_rel(2, "PAV-1", "PAV", 1, "NHP-1", "NHP")
        driver.execute_query.return_value = _make_graph_result(nodes, [rel1, rel2])
        tree, _ = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_rels == 1

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    def test_lineage_node_missing_uuid_skipped(self, _):
        """Nodes without uuid are skipped."""
        vs = self._viewset()
        driver = MagicMock()
        good_node = _make_neo4j_node(1, "NHP-1", "NHP")
        bad_node = _make_neo4j_node(2, "", "PAV")  # empty uuid
        driver.execute_query.return_value = _make_graph_result([good_node, bad_node], [])
        tree, _ = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_nodes == 1

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    def test_lineage_rel_missing_child_parent_skipped(self, _):
        """Relationships with None child_id or parent_id are skipped."""
        vs = self._viewset()
        driver = MagicMock()
        nodes = [_make_neo4j_node(1, "NHP-1", "NHP")]
        bad_rel = _make_neo4j_rel(None, "PAV-1", "PAV", 1, "NHP-1", "NHP")
        driver.execute_query.return_value = _make_graph_result(nodes, [bad_rel])
        tree, _ = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert tree.total_rels == 0

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    def test_lineage_rel_adds_child_node_if_missing(self, _):
        """If child from rel is not in node_dict, it gets added."""
        vs = self._viewset()
        driver = MagicMock()
        # Only parent node in nodes list
        nodes = [_make_neo4j_node(1, "NHP-1", "NHP")]
        rel = _make_neo4j_rel(2, "PAV-1", "PAV", 1, "NHP-1", "NHP")
        driver.execute_query.return_value = _make_graph_result(nodes, [rel])
        tree, _ = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        # Child should have been added via the relationship processing
        assert tree.total_nodes == 2

    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    def test_lineage_non_numeric_assay_id(self, _):
        """Non-numeric internal_assay_id should not be added to assay_ids set."""
        vs = self._viewset()
        driver = MagicMock()
        nodes = [
            _make_neo4j_node(1, "NHP-1", "NHP"),
            _make_neo4j_node(2, "PAV-1", "PAV"),
        ]
        rel = _make_neo4j_rel(2, "PAV-1", "PAV", 1, "NHP-1", "NHP",
                              internal_assay_id="not-a-number")
        driver.execute_query.return_value = _make_graph_result(nodes, [rel])
        tree, assay_ids = vs._fetch_single_lineage(driver, 1, "testdb", "NHP-1", "1")
        assert len(assay_ids) == 0


# ============================================================================
# EntityTreeViewSet.lineage (endpoint)
# ============================================================================


class TestLineageEndpoint:
    def _viewset(self):
        from nextseek_api.services.entity_tree import EntityTreeViewSet
        vs = EntityTreeViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(None, None))
    def test_lineage_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(method="post", data={"sample_ids": ["1"]}, user=user)
        resp = vs.lineage(req)
        assert resp.status_code == 401

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_lineage_422_invalid_body(self, _):
        vs = self._viewset()
        req = _auth_request(method="post", data={"bad": "data"})
        resp = vs.lineage(req)
        assert resp.status_code == 422

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_lineage_400_empty_sample_ids(self, _):
        vs = self._viewset()
        req = _auth_request(method="post", data={"sample_ids": []})
        resp = vs.lineage(req)
        assert resp.status_code == 400

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value=None)
    def test_lineage_unresolved_sample(self, _, __):
        vs = self._viewset()
        req = _auth_request(method="post", data={"sample_ids": ["UNKNOWN"]})
        with patch("nextseek_api.services.entity_tree.GraphDatabase") as mock_gd:
            mock_driver = MagicMock()
            mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
            mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
            resp = vs.lineage(req)
        assert resp.status_code == 200
        tree = resp.data["trees"][0]
        assert "not found" in tree["warning"]

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#AAA"}])
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_200_with_enrichment(self, mock_gd, mock_sql, mock_resolve, mock_auth):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        # _fetch_single_lineage is called internally, so we mock execute_query for graph result
        nodes = [_make_neo4j_node(42, "NHP-1", "NHP")]
        rels = [
            _make_neo4j_rel(43, "PAV-1", "PAV", 42, "NHP-1", "NHP",
                            internal_assay_id="16", internal_assay_title="Visit"),
        ]
        mock_driver.execute_query.return_value = _make_graph_result(nodes, rels)
        # Enrichment SQL
        mock_sql.side_effect = [
            [{"color": "#AAA"}],  # _get_clade_color for NHP
            [{"color": "#BBB"}],  # _get_clade_color for PAV
            [{"internal_assay_id": 16, "description": "Visit data", "study_titles": "MIT"}],  # enrichment
        ]
        req = _auth_request(method="post", data={
            "sample_ids": ["NHP-1"],
            "include_attributes": True,
        })
        resp = vs.lineage(req)
        assert resp.status_code == 200
        assert resp.data["total_trees"] == 1

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_502_neo4j_error(self, mock_gd, _):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"sample_ids": ["1"]})
        resp = vs.lineage(req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_500_generic_exception(self, mock_gd, _):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"sample_ids": ["1"]})
        resp = vs.lineage(req)
        assert resp.status_code == 500

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_no_enrichment_when_not_requested(self, mock_gd, mock_sql, _, __):
        """include_attributes=False skips enrichment."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        nodes = [_make_neo4j_node(42, "NHP-1", "NHP")]
        rels = [_make_neo4j_rel(43, "PAV-1", "PAV", 42, "NHP-1", "NHP",
                                internal_assay_id="16", internal_assay_title="Visit")]
        mock_driver.execute_query.return_value = _make_graph_result(nodes, rels)
        req = _auth_request(method="post", data={
            "sample_ids": ["NHP-1"],
            "include_attributes": False,
        })
        resp = vs.lineage(req)
        assert resp.status_code == 200
        # Rels should not have study_titles
        for tree in resp.data["trees"]:
            for rel in tree["rels"]:
                assert rel.get("study_titles") is None

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_multiple_samples(self, mock_gd, mock_sql, _, __):
        """Multiple sample_ids produce multiple trees."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        nodes = [_make_neo4j_node(42, "NHP-1", "NHP")]
        mock_driver.execute_query.return_value = _make_graph_result(nodes, [])
        req = _auth_request(method="post", data={"sample_ids": ["42", "43"]})
        resp = vs.lineage(req)
        assert resp.status_code == 200
        assert resp.data["total_trees"] == 2

    @patch("nextseek_api.services.entity_tree.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.entity_tree._run_sql_query", return_value=[{"color": "#000"}])
    @patch("nextseek_api.services.entity_tree.GraphDatabase")
    def test_lineage_with_custom_timeout(self, mock_gd, mock_sql, _, __):
        """Custom timeout is passed to _fetch_single_lineage."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        nodes = [_make_neo4j_node(42, "NHP-1", "NHP")]
        mock_driver.execute_query.return_value = _make_graph_result(nodes, [])
        req = _auth_request(method="post", data={
            "sample_ids": ["42"],
            "timeout": 120.0,
        })
        resp = vs.lineage(req)
        assert resp.status_code == 200
