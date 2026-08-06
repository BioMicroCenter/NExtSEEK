"""
Comprehensive tests for nextseek_api/views.py ViewSets.

Covers:
- get_clade_color(): raw MySQLdb query for sample type color
- SampleTreeViewSet.get_tree(): Neo4j tree traversal
- NHPViewSet: info(), events(), timeline(), download()
- SampleQueryViewSet.retrieve_samples(): advanced search with pagination
- AdminSampleViewSet.admin_retrieve_samples(): JSON/Excel export, Neo4j + MySQL fallback
"""

import io
import json
import pytest
from unittest.mock import MagicMock, Mock, patch, call

from rest_framework.test import APIRequestFactory

from neo4j.exceptions import AuthError, Neo4jError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOCK_DB = {
    "default": {
        "HOST": "localhost",
        "USER": "testuser",
        "PASSWORD": "testpass",
        "NAME": "testdb",
    },
    "seek": {
        "HOST": "localhost",
        "USER": "testuser",
        "PASSWORD": "testpass",
        "NAME": "testdb",
    },
}

NEO4J_DB = {
    "URI": "bolt://localhost:7687",
    "AUTH": ("neo4j", "password"),
    "NAME": "neo4j",
}

_VIEWS = "nextseek_api.views"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_settings(mock_settings, media_root="/tmp/test_media"):
    """Configure a mock settings object with required attributes."""
    mock_settings.DATABASES = MOCK_DB
    mock_settings.NEO4J_DATABASE = NEO4J_DB
    mock_settings.MEDIA_ROOT = media_root


def _auth_request(method="get", path="/", data=None, user=None, query=None):
    factory = APIRequestFactory()
    fn = getattr(factory, method)
    if data is not None:
        req = fn(path, data=json.dumps(data), content_type="application/json")
    else:
        req = fn(path)
    if user is None:
        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.is_superuser = False
    req.user = user
    if data is not None:
        req.data = data
    if query:
        req.GET = query
    req.query_params = getattr(req, "GET", {})
    return req


def _admin_user():
    u = MagicMock()
    u.is_authenticated = True
    u.is_staff = True
    u.is_superuser = True
    return u


def _make_node(props):
    n = MagicMock()
    n._properties = props
    return n


def _make_rel(start_props, end_props, rel_props=None):
    r = MagicMock()
    r._properties = rel_props or {}
    r.start_node = MagicMock()
    r.start_node._properties = start_props
    r.end_node = MagicMock()
    r.end_node._properties = end_props
    return r


def _graph(nodes, rels):
    g = MagicMock()
    g.nodes = nodes
    g.relationships = rels
    return g


def _driver(graph_result=None, side_effect=None):
    d = MagicMock()
    d.__enter__ = Mock(return_value=d)
    d.__exit__ = Mock(return_value=False)
    if side_effect:
        d.execute_query.side_effect = side_effect
    else:
        d.execute_query.return_value = graph_result or _graph([], [])
    return d


def _seekdb_mock(project_ids=None):
    """Create SeekDB mock returning given project ids."""
    m = MagicMock()
    pdata = [{"id": str(pid)} for pid in (project_ids or [])]
    m.getCurrentUser.return_value = {"data": {"relationships": {"projects": {"data": pdata}}}}
    return m


def _mysql_cursor(fetchone_val=None, fetchall_val=None, description=None):
    """Build (mock_mysql, mock_conn, mock_cursor) tuple."""
    cur = MagicMock()
    if fetchone_val is not None:
        cur.fetchone.return_value = fetchone_val
    if fetchall_val is not None:
        cur.fetchall.return_value = fetchall_val
    if description is not None:
        cur.description = description
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ===========================================================================
# get_clade_color
# ===========================================================================


class TestGetCladeColor:

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    def test_returns_color_on_match(self, mock_settings, mock_mysql):
        _setup_settings(mock_settings)
        from nextseek_api.views import get_clade_color

        conn, cur = _mysql_cursor(fetchone_val=("#FF5733",))
        mock_mysql.connect.return_value = conn

        assert get_clade_color("NHP_blood") == "#FF5733"
        cur.execute.assert_called_once()
        cur.close.assert_called_once()
        conn.close.assert_called_once()

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    def test_returns_black_on_no_match(self, mock_settings, mock_mysql):
        _setup_settings(mock_settings)
        from nextseek_api.views import get_clade_color

        conn, cur = _mysql_cursor()
        cur.fetchone.return_value = None  # None[0] will raise TypeError -> fallback to #000000
        mock_mysql.connect.return_value = conn

        assert get_clade_color("UnknownType") == "#000000"

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    def test_returns_black_on_exception(self, mock_settings, mock_mysql):
        _setup_settings(mock_settings)
        from nextseek_api.views import get_clade_color

        conn, cur = _mysql_cursor()
        cur.fetchone.side_effect = TypeError("NoneType")
        mock_mysql.connect.return_value = conn

        assert get_clade_color("BadType") == "#000000"


# ===========================================================================
# SampleTreeViewSet
# ===========================================================================


class TestSampleTreeViewSet:

    def _vs(self):
        from nextseek_api.views import SampleTreeViewSet
        vs = SampleTreeViewSet()
        vs.format_kwarg = None
        return vs

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_get_tree_success(self, mock_s, mock_color, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        parent = _make_node({"uuid": "PAT-1", "id": 100, "type": "PAT"})
        child = _make_node({"uuid": "TIS-1", "id": 200, "type": "TIS"})
        rel = _make_rel(
            {"uuid": "TIS-1", "id": 200, "type": "TIS"},
            {"uuid": "PAT-1", "id": 100, "type": "PAT"},
            {"child_id": 200, "parent_id": 100, "internal_assay_id": 5, "protocol_id": None},
        )
        mock_gdb.driver.return_value = _driver(_graph([parent, child], [rel]))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="PAT-1")

        assert resp.status_code == 200
        assert resp.data["total_nodes"] == 2
        assert resp.data["total_rels"] == 1
        assert resp.data["rels"][0]["child_id"] == "200"
        assert resp.data["rels"][0]["parent_id"] == "100"
        assert resp.data["rels"][0]["internal_assay_id"] == "5"

    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value=None)
    def test_sample_not_found(self, mock_resolve, mock_auth):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="INVALID")

        assert resp.status_code == 404

    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(None, None))
    def test_auth_required(self, mock_auth):
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request("get", "/", user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="123")

        assert resp.status_code == 401

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_neo4j_auth_error(self, mock_s, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(side_effect=AuthError("Auth failed"))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 503
        assert "authentication" in resp.data["detail"].lower()

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_neo4j_query_error(self, mock_s, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(side_effect=Neo4jError("Query failed"))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 503

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_generic_exception(self, mock_s, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(side_effect=RuntimeError("Unexpected"))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 500
        assert "tree query failed" in resp.data["detail"].lower()

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#111111")
    @patch(f"{_VIEWS}.settings")
    def test_rel_node_not_in_nodedict(self, mock_s, mock_color, mock_resolve, mock_auth, mock_gdb):
        """Defensive branch: rel start_node not in nodes list."""
        _setup_settings(mock_s)
        parent = _make_node({"uuid": "PAT-1", "id": 100, "type": "PAT"})
        rel = _make_rel(
            {"uuid": "TIS-1", "id": 200, "type": "TIS"},  # NOT in nodes
            {"uuid": "PAT-1", "id": 100, "type": "PAT"},
            {"child_id": 200, "parent_id": 100},
        )
        mock_gdb.driver.return_value = _driver(_graph([parent], [rel]))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 200
        assert resp.data["total_nodes"] == 2
        uuids = {n["uuid"] for n in resp.data["nodes"]}
        assert "TIS-1" in uuids and "PAT-1" in uuids

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#000000")
    @patch(f"{_VIEWS}.settings")
    def test_empty_graph(self, mock_s, mock_color, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(_graph([], []))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 200
        assert resp.data["total_nodes"] == 0
        assert resp.data["total_rels"] == 0

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(None, None))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_session_authenticated_user_passes_auth(self, mock_s, mock_resolve, mock_auth, mock_gdb):
        """User is authenticated via session even when resolve_seek_auth returns None."""
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(side_effect=RuntimeError("no neo4j"))

        user = MagicMock()
        user.is_authenticated = True
        req = _auth_request("get", "/", user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 500  # Past auth, fails in Neo4j

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#AAA")
    @patch(f"{_VIEWS}.settings")
    def test_rel_null_props(self, mock_s, mock_color, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        node = _make_node({"uuid": "NHP-1", "id": 10, "type": "NHP"})
        rel = _make_rel(
            {"uuid": "NHP-1", "id": 10, "type": "NHP"},
            {"uuid": "NHP-1", "id": 10, "type": "NHP"},
            None,
        )
        mock_gdb.driver.return_value = _driver(_graph([node], [rel]))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="42")

        assert resp.status_code == 200

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#000")
    @patch(f"{_VIEWS}.settings")
    def test_pk_fallback(self, mock_s, mock_color, mock_resolve, mock_auth, mock_gdb):
        _setup_settings(mock_s)
        mock_gdb.driver.return_value = _driver(_graph([], []))

        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        vs.get_tree(req, uid=None, pk="99")
        mock_resolve.assert_called_once_with("99")


# ===========================================================================
# NHPViewSet
# ===========================================================================


class TestNHPViewSet:

    def _vs(self):
        from nextseek_api.views import NHPViewSet
        vs = NHPViewSet()
        vs.format_kwarg = None
        return vs

    @patch(f"{_VIEWS}.save_nhp_info_to_json", return_value={"id": "FLY001"})
    def test_info_success(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.info(req, pk="FLY001")
        assert resp.status_code == 200
        assert resp.data["id"] == "FLY001"

    @patch(f"{_VIEWS}.save_nhp_info_to_json", return_value=None)
    def test_info_not_found(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.info(req, pk="X")
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.save_nhp_info_to_json", side_effect=RuntimeError("DB error"))
    def test_info_exception(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.info(req, pk="F")
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.get_event_data", return_value={"event": "feeding"})
    def test_events_success(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.events(req, pk="F", event_type="feeding", date="2023-01-01")
        assert resp.status_code == 200
        mock_fn.assert_called_once_with("F", "feeding", "2023-01-01")

    @patch(f"{_VIEWS}.get_event_data", return_value=None)
    def test_events_not_found(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.events(req, pk="F", event_type="x", date="d")
        assert resp.status_code == 404

    def test_events_no_pk(self):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.events(req, pk=None, event_type="x", date="d")
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.get_event_data", side_effect=ValueError("bad"))
    def test_events_exception(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.events(req, pk="F", event_type="x", date="d")
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.run_All", return_value={"timeline": "data"})
    def test_timeline_success(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.timeline(req, pk="F")
        assert resp.status_code == 200
        assert resp.data["timeline"] == "data"

    @patch(f"{_VIEWS}.run_All", return_value=None)
    def test_timeline_not_found(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.timeline(req, pk="F")
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.run_All", side_effect=RuntimeError("timeout"))
    def test_timeline_exception(self, mock_fn):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.timeline(req, pk="F")
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.save_nhp_data", return_value=b"excel-bytes")
    @patch(f"{_VIEWS}.get_timeline_data", return_value={"t": "raw"})
    def test_download_success(self, mock_tl, mock_xl):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.download(req, pk="F")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp["Content-Type"]

    @patch(f"{_VIEWS}.get_timeline_data", return_value=None)
    def test_download_not_found(self, mock_tl):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.download(req, pk="F")
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.get_timeline_data", side_effect=RuntimeError("fail"))
    def test_download_exception(self, mock_tl):
        req = _auth_request("get", "/")
        vs = self._vs()
        vs.request = req
        resp = vs.download(req, pk="F")
        assert resp.status_code == 500


# ===========================================================================
# SampleQueryViewSet
# ===========================================================================


class TestSampleQueryViewSet:

    def _vs(self):
        from nextseek_api.views import SampleQueryViewSet
        vs = SampleQueryViewSet()
        vs.format_kwarg = None
        vs.kwargs = {}
        return vs

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_retrieve_paginated(self, mock_auth, mock_dbs):
        rows = [{"id": i} for i in range(5)]
        mock_dbs.return_value.searchAdvanced.return_value = json.dumps({"rows": rows})

        data = {"sampletype_id": 12, "attribute": "none", "filter_valueFrom": "X", "project_id": 0}
        req = _auth_request("post", "/", data=data)
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(None, None))
    def test_auth_required(self, mock_auth):
        req = _auth_request("post", "/", data={"sampletype_id": 12})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 401

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_non_json_response(self, mock_auth, mock_dbs):
        mock_dbs.return_value.searchAdvanced.return_value = "plain text"

        req = _auth_request("post", "/", data={"sampletype_id": 12})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["data"] == "plain text"

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_no_rows_key(self, mock_auth, mock_dbs):
        mock_dbs.return_value.searchAdvanced.return_value = json.dumps({"summary": "ok"})

        req = _auth_request("post", "/", data={"sampletype_id": 12})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["summary"] == "ok"

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_exception(self, mock_auth, mock_dbs):
        mock_dbs.return_value.searchAdvanced.side_effect = RuntimeError("DB down")

        req = _auth_request("post", "/", data={"sampletype_id": 12})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth")
    def test_session_auth_path(self, mock_auth, mock_dbs, mock_seekdb):
        mock_auth.return_value = ("session_str", {})
        mock_seekdb.return_value.getSeekLogin.return_value = {"status": True, "username": "u", "password": "p"}
        mock_dbs.return_value.searchAdvanced.return_value = json.dumps({"rows": []})

        req = _auth_request("post", "/", data={"sampletype_id": 12})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        mock_seekdb.assert_called_once_with(None, None, None)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_validation_error(self, mock_auth, mock_dbs):
        req = _auth_request("post", "/", data={"sampletype_id": "not_int"})
        vs = self._vs()
        vs.request = req
        resp = vs.retrieve_samples(req)
        assert resp.status_code == 500


# ===========================================================================
# AdminSampleViewSet
# ===========================================================================


class TestAdminSampleViewSet:

    def _vs(self):
        from nextseek_api.views import AdminSampleViewSet
        vs = AdminSampleViewSet()
        vs.format_kwarg = None
        vs.kwargs = {}
        return vs

    def _df(self, rows):
        import pandas as pd
        return pd.DataFrame(rows)

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_json_export_success(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock([1])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": '{"k":"v"}'},
            {"id": 2, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": '{}'},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1", "TIS-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["total_samples"] == 2
        assert resp.data["total_sample_types"] == 2

    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(None, None))
    def test_auth_required(self, mock_auth):
        req = _auth_request("post", "/", data={"identifiers": ["X"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 401

    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_empty_identifiers(self, mock_auth, mock_seekdb):
        mock_seekdb.return_value = _seekdb_mock()
        req = _auth_request("post", "/", data={"identifiers": []}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 400

    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_validation_error(self, mock_auth, mock_seekdb):
        mock_seekdb.return_value = _seekdb_mock()
        req = _auth_request("post", "/", data={"identifiers": ["X"], "output_format": "bad"}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 422

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_numeric_id_resolution(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        conn, cur = _mysql_cursor(fetchall_val=[(123, "NHP-1")])
        mock_mysql.connect.return_value = conn
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 123, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["123"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_numeric_id_mysql_failure(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_mysql.connect.side_effect = RuntimeError("DB down")
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["123", "NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["failed_uids"] >= 1

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_index_error_returns_404(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.side_effect = IndexError("no data")

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_neo4j_fallback_superuser(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        """AuthError triggers MySQL fallback (superuser path)."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.side_effect = AuthError("Neo4j auth")

        conn, cur = _mysql_cursor(
            fetchall_val=[(1, 12, "NHP-1", '{}')],
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["total_samples"] == 1

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_neo4j_fallback_non_superuser(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        """Neo4jError triggers MySQL fallback with project-scoped query."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([5])
        mock_dbs.return_value.getChildrenUIDs.side_effect = Neo4jError("timeout")

        conn, cur = _mysql_cursor(
            fetchall_val=[(2, 13, "TIS-1", '{}')],
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        user = _admin_user()
        user.is_superuser = False
        req = _auth_request("post", "/", data={"identifiers": ["TIS-1"]}, user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_neo4j_and_mysql_both_fail(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.side_effect = AuthError("Neo4j")
        mock_mysql.connect.side_effect = RuntimeError("MySQL down")

        req = _auth_request("post", "/", data={"identifiers": ["X"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_generic_exception(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.side_effect = RuntimeError("boom")

        req = _auth_request("post", "/", data={"identifiers": ["X"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 500

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_empty_df_404(self, mock_auth, mock_seekdb, mock_dbs):
        import pandas as pd
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = pd.DataFrame()

        req = _auth_request("post", "/", data={"identifiers": ["X"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.os.makedirs")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_excel_export(self, mock_auth, mock_seekdb, mock_dbs, mock_makedirs, mock_s):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        content = b"PK\x03\x04fake-excel"
        with patch("builtins.open", return_value=io.BytesIO(content)):
            req = _auth_request("post", "/", data={"identifiers": ["NHP-1"], "output_format": "excel"}, user=_admin_user())
            vs = self._vs()
            vs.request = req
            resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 200
        assert "ms-excel" in resp["Content-Type"]

    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.os.makedirs")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_excel_file_not_found(self, mock_auth, mock_seekdb, mock_dbs, mock_makedirs, mock_s):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        with patch("builtins.open", side_effect=FileNotFoundError("nope")):
            req = _auth_request("post", "/", data={"identifiers": ["NHP-1"], "output_format": "excel"}, user=_admin_user())
            vs = self._vs()
            vs.request = req
            resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 500
        assert "export failed" in resp.data["detail"].lower()

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_legacy_retrieval_uids_text(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
            {"id": 2, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"retrieval_uids_text": "NHP-1 TIS-1"}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["total_samples"] == 2

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_legacy_uids_field(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"uids": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_legacy_retrieval_uids_field(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"retrieval_uids": ["TIS-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_non_dict_body(self, mock_auth, mock_seekdb):
        mock_seekdb.return_value = _seekdb_mock()
        req = _auth_request("post", "/", data=None, user=_admin_user())
        req.data = "not a dict"
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 400

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_json_metadata_variants(self, mock_auth, mock_seekdb, mock_dbs):
        """Valid JSON, invalid JSON, None, and dict-type metadata."""
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": '{"k":"v"}'},
            {"id": 2, "uuid": "NHP-2", "sample_type_id": 12, "json_metadata": "bad-json"},
            {"id": 3, "uuid": "NHP-3", "sample_type_id": 12, "json_metadata": None},
            {"id": 4, "uuid": "NHP-4", "sample_type_id": 12, "json_metadata": {"already": "dict"}},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1", "NHP-2", "NHP-3", "NHP-4"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_getCurrentUser_exception(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value.getCurrentUser.side_effect = RuntimeError("API down")
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_neo4j_fallback_no_projects(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        """Non-superuser, empty project list, MySQL fallback returns empty."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.side_effect = Neo4jError("fail")

        conn, cur = _mysql_cursor(
            fetchall_val=[],
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        user = _admin_user()
        user.is_superuser = False
        user.is_staff = False
        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 404

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_deduplication(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1", "NHP-1", "NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        uid_arg = mock_dbs.return_value.getChildrenUIDs.call_args[0][0]
        assert len(uid_arg) == 1

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_dotted_sample_type(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "D.IMG-230101MIT-1", "sample_type_id": 14, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["D.IMG-230101MIT-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        types = [g["sample_type"] for g in resp.data["data"]]
        assert "D.IMG" in types

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_children_count(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
            {"id": 2, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": "{}"},
            {"id": 3, "uuid": "TIS-2", "sample_type_id": 13, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["total_samples"] == 3
        assert resp.data["total_children"] == 2

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_empty_string_identifier_skipped(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        """Empty strings and whitespace-only entries in identifiers are skipped (line 607)."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1", "", "  "]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        uid_arg = mock_dbs.return_value.getChildrenUIDs.call_args[0][0]
        assert len(uid_arg) == 1

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_numeric_id_not_found_in_mysql(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        """Numeric IDs that don't exist in MySQL are counted as unresolved (line 628)."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock()
        conn, cur = _mysql_cursor(fetchall_val=[])  # No rows returned
        mock_mysql.connect.return_value = conn
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["999", "NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200
        assert resp.data["failed_uids"] >= 1

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_groupby_exception_fallback(self, mock_auth, mock_seekdb, mock_dbs):
        """When groupby fails, fallback to single UNKNOWN group (lines 726-741)."""
        mock_seekdb.return_value = _seekdb_mock()
        # Create a mock df that has .empty=False, shape, copy(), but groupby raises
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.shape = (1,)
        mock_df_copy = MagicMock()
        mock_df.copy.return_value = mock_df_copy
        mock_df_copy.__setitem__ = Mock()
        mock_df_copy.__getitem__ = Mock(side_effect=Exception("no uuid col"))
        # Make the get method work for returned_uids extraction
        mock_df.get.return_value = ["NHP-1"]
        # Make to_dict work for fallback path
        mock_df.to_dict.return_value = [
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ]
        mock_dbs.return_value.getChildrenUIDs.return_value = mock_df

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_returned_uids_extraction_failure(self, mock_auth, mock_seekdb, mock_dbs):
        """When df.get('uuid') raises, returned_uids defaults to empty set (lines 689-690)."""
        mock_seekdb.return_value = _seekdb_mock()
        import pandas as pd
        # Use a DataFrame with a uuid column but make .get raise on iteration
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.shape = (1,)
        mock_df.get.side_effect = Exception("bad column access")
        # Make copy/groupby work
        df_real = pd.DataFrame([{"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"}])
        mock_df.copy.return_value = df_real.copy()
        mock_df.groupby = df_real.groupby
        mock_dbs.return_value.getChildrenUIDs.return_value = mock_df

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)
        assert resp.status_code == 200


class TestAdminSampleViewSetUnification:
    """Task 4: un-gated, project-scoped, include_tree-aware sample retrieval."""

    def _vs(self):
        from nextseek_api.views import AdminSampleViewSet
        vs = AdminSampleViewSet()
        vs.format_kwarg = None
        vs.kwargs = {}
        return vs

    def _df(self, rows):
        import pandas as pd
        return pd.DataFrame(rows)

    def test_endpoint_is_not_admin_gated(self):
        from nextseek_api.views import AdminSampleViewSet
        from rest_framework.permissions import IsAdminUser, IsAuthenticated

        assert IsAuthenticated in AdminSampleViewSet.permission_classes
        assert IsAdminUser not in AdminSampleViewSet.permission_classes

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    def test_seekdb_is_built_from_the_resolved_credentials(self, mock_auth, mock_seekdb, mock_dbs):
        """SeekDB(None, None, None) never sets __server, so getCurrentUser() always
        raised and every caller fell through to an empty project list."""
        mock_seekdb.return_value = _seekdb_mock([7])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        vs.admin_retrieve_samples(req)

        mock_seekdb.assert_called_once_with(None, "alice", "pw")

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    def test_non_staff_user_gets_project_scoped_rows_not_404(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock([7])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": "{}"},
        ])

        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.is_superuser = False
        req = _auth_request("post", "/", data={"identifiers": ["TIS-1"]}, user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 200
        # The caller's real projects reached getChildrenUIDs instead of [].
        assert mock_dbs.return_value.getChildrenUIDs.call_args[0][1] == ["7"]
        assert mock_dbs.return_value.getChildrenUIDs.call_args[0][2] is False

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_include_tree_defaults_to_true(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock([1])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        vs.admin_retrieve_samples(req)

        mock_dbs.return_value.getChildrenUIDs.assert_called_once()

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_include_tree_false_skips_neo4j(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([1])
        conn, cur = _mysql_cursor(
            fetchall_val=[(1, 12, "NHP-1", "{}")],
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        req = _auth_request(
            "post", "/",
            data={"identifiers": ["NHP-1"], "include_tree": False},
            user=_admin_user(),
        )
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 200
        mock_dbs.return_value.getChildrenUIDs.assert_not_called()
