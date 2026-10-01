"""
Comprehensive tests for nextseek_api/views.py ViewSets.

Covers:
- get_clade_color(): raw MySQLdb query for sample type color
- SampleTreeViewSet.get_tree(): Neo4j tree traversal
- NHPViewSet: info(), events(), timeline(), download()
- SampleQueryViewSet.retrieve_samples(): advanced search with pagination
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

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    def test_sample_type_is_parameterized_not_interpolated(self, mock_settings, mock_mysql):
        """sample_type is caller-supplied; it must never be inlined into SQL.

        The query used to build `WHERE st.title = '{sample_type}'` with an
        f-string, so a single quote in sample_type breaks out of the literal.
        Mirrors the already-safe twin in services/entity_tree.py.
        """
        _setup_settings(mock_settings)
        from nextseek_api.views import get_clade_color

        conn, cur = _mysql_cursor(fetchone_val=("#FF5733",))
        mock_mysql.connect.return_value = conn

        payload = "NHP' UNION SELECT '#DEADBE'-- "
        assert get_clade_color(payload) == "#FF5733"

        cur.execute.assert_called_once()
        args, kwargs = cur.execute.call_args
        sql = args[0]
        params = args[1] if len(args) > 1 else kwargs.get("args")

        # The value travels as a bound parameter, never in the statement.
        assert params == [payload], f"sample_type was not bound: {params!r}"
        assert payload not in sql
        assert "UNION" not in sql.upper()
        assert "st.title = %s" in sql

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    def test_quote_in_sample_type_does_not_change_statement(self, mock_settings, mock_mysql):
        """Two different sample_types must produce the identical SQL text."""
        _setup_settings(mock_settings)
        from nextseek_api.views import get_clade_color

        seen = []
        for value in ("NHP_blood", "O'Brien'; DROP TABLE clades; --"):
            conn, cur = _mysql_cursor(fetchone_val=("#FF5733",))
            mock_mysql.connect.return_value = conn
            get_clade_color(value)
            seen.append(cur.execute.call_args[0][0])

        assert seen[0] == seen[1]


# ===========================================================================
# SampleTreeViewSet
# ===========================================================================


class TestSampleTreeViewSet:
    """NOTE: these tests run as a superuser (``_admin_user()``). get_tree is
    project-scoped (#60) and a non-superuser is gated on SEEK project
    membership before the traversal runs, which is covered separately by
    TestSampleTreeProjectScope below."""


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

        req = _auth_request("get", "/", user=_admin_user())
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
        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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

        req = _auth_request("get", "/", user=_admin_user())
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
# AdminSampleViewSet: the sample download API and its data path are covered end to end in
# nextseek_api/tests/test_sample_retrieve.py (both routes, scope, lineage, graph failure, SQL binding).
# ===========================================================================


# ===========================================================================
# SampleTreeViewSet — project scoping (#60)
# ===========================================================================


class TestSampleTreeProjectScope:
    """get_tree resolves ANY uid and walks DERIVED_FROM from it with no
    membership predicate, so before this fix any authenticated caller could
    read the lineage of any sample in the instance by UID.

    The admin bypass is is_superuser ALONE, deliberately: dmac/views.py:80,:97
    set is_staff = 1 on every SEEK user at login, so an is_staff bypass would
    make this scoping a no-op for every account.
    """

    _VISIBLE_SQL_TABLE = "projects_samples"

    def _vs(self):
        from nextseek_api.views import SampleTreeViewSet
        vs = SampleTreeViewSet()
        vs.format_kwarg = None
        return vs

    def _user(self, is_staff=False, is_superuser=False):
        u = MagicMock()
        u.is_authenticated = True
        u.is_staff = is_staff
        u.is_superuser = is_superuser
        return u

    @staticmethod
    def _scope_calls(cur):
        """Every projects_samples membership query run on this cursor."""
        out = []
        for c in cur.execute.call_args_list:
            sql = c[0][0]
            if TestSampleTreeProjectScope._VISIBLE_SQL_TABLE in sql:
                params = c[0][1] if len(c[0]) > 1 else c[1].get("args")
                out.append((sql, params))
        return out

    def test_endpoint_requires_authentication(self):
        from nextseek_api.views import SampleTreeViewSet
        from rest_framework.permissions import IsAuthenticated

        assert IsAuthenticated in SampleTreeViewSet.permission_classes

    # -- root gate ----------------------------------------------------------

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_non_member_root_is_404_and_never_reaches_neo4j(
        self, mock_s, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])
        conn, cur = _mysql_cursor(fetchall_val=[])  # sample 42 is in no project of the caller's
        mock_mysql.connect.return_value = conn

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="NHP-1")

        assert resp.status_code == 404
        mock_gdb.driver.assert_not_called()

        # The filter really was applied, with the caller's own project ids bound.
        calls = self._scope_calls(cur)
        assert calls, "no projects_samples membership query was issued"
        sql, params = calls[0]
        assert "project_id IN (%s)" in sql
        assert "sample_id IN (%s)" in sql
        assert list(params) == [42, "7"]

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_member_root_is_served(
        self, mock_s, mock_color, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])
        conn, cur = _mysql_cursor(fetchall_val=[(42,)])
        mock_mysql.connect.return_value = conn
        node = _make_node({"uuid": "NHP-1", "id": 42, "type": "NHP"})
        mock_gdb.driver.return_value = _driver(_graph([node], []))

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="NHP-1")

        assert resp.status_code == 200
        assert {n["uuid"] for n in resp.data["nodes"]} == {"NHP-1"}

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_caller_with_no_projects_sees_nothing(
        self, mock_s, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        """Fails closed: an unresolvable/empty project list must not mean
        'unfiltered'."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([])
        conn, cur = _mysql_cursor(fetchall_val=[(42,)])
        mock_mysql.connect.return_value = conn

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="NHP-1")

        assert resp.status_code == 404
        # No project ids => no query at all, and certainly no traversal.
        assert self._scope_calls(cur) == []
        mock_gdb.driver.assert_not_called()

    # -- the admin predicate ------------------------------------------------

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.settings")
    def test_is_staff_alone_does_not_bypass_scoping(
        self, mock_s, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        """Every synced SEEK user is is_staff (dmac/views.py:80,:97). If staff
        bypassed the filter this whole fix would be a no-op."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])
        conn, cur = _mysql_cursor(fetchall_val=[])
        mock_mysql.connect.return_value = conn

        req = _auth_request("get", "/", user=self._user(is_staff=True))
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="NHP-1")

        assert resp.status_code == 404
        assert self._scope_calls(cur), "staff skipped the membership query"

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="42")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_superuser_bypasses_scoping_entirely(
        self, mock_s, mock_color, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        _setup_settings(mock_s)
        conn, cur = _mysql_cursor(fetchall_val=[])
        mock_mysql.connect.return_value = conn
        node = _make_node({"uuid": "NHP-1", "id": 42, "type": "NHP"})
        mock_gdb.driver.return_value = _driver(_graph([node], []))

        req = _auth_request("get", "/", user=self._user(is_superuser=True))
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="NHP-1")

        assert resp.status_code == 200
        assert self._scope_calls(cur) == []
        mock_seekdb.assert_not_called()

    # -- lineage pruning ----------------------------------------------------

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="200")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_foreign_lineage_nodes_are_pruned_consistently(
        self, mock_s, mock_color, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        """A sample the caller owns can have a parent in a project they cannot
        see. That parent must not leak, and the surviving child must not keep a
        dangling parentId (static/js/dag/dag.js graphStratify throws on one)."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])

        # Root 200 is visible; ancestor 100 is not.
        conn, cur = _mysql_cursor()
        cur.fetchall.side_effect = [[(200,)], [(200,)]]
        mock_mysql.connect.return_value = conn

        parent = _make_node({"uuid": "PAT-1", "id": 100, "type": "PAT"})
        child = _make_node({"uuid": "TIS-1", "id": 200, "type": "TIS"})
        rel = _make_rel(
            {"uuid": "TIS-1", "id": 200, "type": "TIS"},
            {"uuid": "PAT-1", "id": 100, "type": "PAT"},
            {"child_id": 200, "parent_id": 100},
        )
        mock_gdb.driver.return_value = _driver(_graph([parent, child], [rel]))

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="TIS-1")

        assert resp.status_code == 200
        uuids = {n["uuid"] for n in resp.data["nodes"]}
        assert uuids == {"TIS-1"}, f"foreign lineage leaked: {uuids}"
        assert resp.data["total_rels"] == 0, "relationship to a pruned node survived"

        # No dangling parent ids: every parentId must name a returned node.
        returned_ids = {n["id"] for n in resp.data["nodes"]}
        for n in resp.data["nodes"]:
            assert set(n["parentIds"]) <= returned_ids

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="200")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_wholly_visible_lineage_is_returned_intact(
        self, mock_s, mock_color, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])

        conn, cur = _mysql_cursor()
        cur.fetchall.side_effect = [[(200,)], [(100,), (200,)]]
        mock_mysql.connect.return_value = conn

        parent = _make_node({"uuid": "PAT-1", "id": 100, "type": "PAT"})
        child = _make_node({"uuid": "TIS-1", "id": 200, "type": "TIS"})
        rel = _make_rel(
            {"uuid": "TIS-1", "id": 200, "type": "TIS"},
            {"uuid": "PAT-1", "id": 100, "type": "PAT"},
            {"child_id": 200, "parent_id": 100},
        )
        mock_gdb.driver.return_value = _driver(_graph([parent, child], [rel]))

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="TIS-1")

        assert resp.status_code == 200
        assert {n["uuid"] for n in resp.data["nodes"]} == {"PAT-1", "TIS-1"}
        assert resp.data["total_rels"] == 1
        tis = next(n for n in resp.data["nodes"] if n["uuid"] == "TIS-1")
        assert tis["parentIds"] == ["100"]

    @patch(f"{_VIEWS}.GraphDatabase")
    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    @patch(f"{_VIEWS}._resolve_uid_to_seek_id", return_value="200")
    @patch(f"{_VIEWS}.get_clade_color", return_value="#A3D46F")
    @patch(f"{_VIEWS}.settings")
    def test_pruning_does_not_depend_on_rel_properties(
        self, mock_s, mock_color, mock_resolve, mock_auth, mock_seekdb, mock_mysql, mock_gdb
    ):
        """Relationship visibility is decided from the graph endpoints, not from
        child_id/parent_id properties, which a DERIVED_FROM edge need not carry."""
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([7])

        conn, cur = _mysql_cursor()
        cur.fetchall.side_effect = [[(200,)], [(100,), (200,)]]
        mock_mysql.connect.return_value = conn

        parent = _make_node({"uuid": "PAT-1", "id": 100, "type": "PAT"})
        child = _make_node({"uuid": "TIS-1", "id": 200, "type": "TIS"})
        rel = _make_rel(
            {"uuid": "TIS-1", "id": 200, "type": "TIS"},
            {"uuid": "PAT-1", "id": 100, "type": "PAT"},
            {},  # no child_id / parent_id properties at all
        )
        mock_gdb.driver.return_value = _driver(_graph([parent, child], [rel]))

        req = _auth_request("get", "/", user=self._user())
        vs = self._vs()
        vs.request = req
        resp = vs.get_tree(req, uid="TIS-1")

        assert resp.status_code == 200
        assert resp.data["total_rels"] == 1


# ===========================================================================
# DBtable_sample.getChildrenUIDs — SQL injection in the graph-expanded query (#78)
# ===========================================================================


_DBTS = "seek.dbtable_sample"


class TestGetChildrenUIDsSQLInjection:
    """``getChildrenUIDs`` built BOTH ``IN`` lists by string interpolation
    (``', '.join(f"'{uid}'" ...)``) and handed the finished statement to
    ``__runQuery``, which called ``cursor.execute(query)`` with no parameters.
    A single quote in either list breaks out of the literal.

    This is the twin of the site fixed by 7698848. The download API no longer
    calls it (nextseek_api/services/sample_retrieve.py reads rows by primary key
    with bound parameters); it stays guarded while the method exists. The uuids interpolated are not the request body but
    ``r[0]['uuids']`` returned by Neo4j, so the value has to be written into
    the graph first — second-order, but reachable. ``user_project_ids`` is the
    other half of the same pair of lines and became load-bearing for every
    non-superuser in ca1c9d9 (#74).
    """

    _PAYLOAD = "NHP-1' UNION SELECT id, sample_type_id, uuid, json_metadata FROM testdb.samples WHERE '1'='1"

    @staticmethod
    def _instance():
        """Build the instance without __init__, which touches Django models."""
        from seek.dbtable_sample import DBtable_sample

        return DBtable_sample.__new__(DBtable_sample)

    @staticmethod
    def _executed(cur):
        """Return (sql, params) for the SELECT __runQuery issued."""
        args, kwargs = cur.execute.call_args
        sql = args[0]
        params = args[1] if len(args) > 1 else kwargs.get("args")
        return sql, params

    def _run(self, mock_graph, mock_mysql, graph_uids, project_ids, admin, rows=None):
        # The graph query is what supplies the uuids that get interpolated.
        mock_graph.driver.return_value = _driver(
            graph_result=([{"uuids": list(graph_uids)}], None, None)
        )
        conn, cur = _mysql_cursor(
            fetchall_val=[(1, 12, "NHP-1", "{}")] if rows is None else rows,
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        df = self._instance().getChildrenUIDs(["NHP-1"], project_ids, admin)
        return df, cur

    @patch(f"{_DBTS}.MySQLdb")
    @patch(f"{_DBTS}.GraphDatabase")
    @patch(f"{_DBTS}.settings")
    def test_uids_from_the_graph_are_bound_not_interpolated(self, mock_s, mock_graph, mock_mysql):
        _setup_settings(mock_s)
        uids = [self._PAYLOAD, "TIS-2"]

        df, cur = self._run(mock_graph, mock_mysql, uids, [1], True)

        sql, params = self._executed(cur)
        assert params is not None, "graph uuids were interpolated into the statement, not bound"
        assert list(params) == uids
        assert self._PAYLOAD not in sql
        assert "UNION" not in sql.upper()
        assert "uuid IN (%s, %s)" in sql
        assert not df.empty

    @patch(f"{_DBTS}.MySQLdb")
    @patch(f"{_DBTS}.GraphDatabase")
    @patch(f"{_DBTS}.settings")
    def test_quote_in_uid_does_not_change_the_statement(self, mock_s, mock_graph, mock_mysql):
        """Two runs over same-length uuid lists must produce identical SQL."""
        _setup_settings(mock_s)
        seen = []
        for value in ("NHP-1", "O'Brien'; DROP TABLE samples; --"):
            _, cur = self._run(mock_graph, mock_mysql, [value, "TIS-2"], [1], True)
            seen.append(self._executed(cur)[0])
        assert seen[0] == seen[1]

    @patch(f"{_DBTS}.MySQLdb")
    @patch(f"{_DBTS}.GraphDatabase")
    @patch(f"{_DBTS}.settings")
    def test_project_scoped_branch_binds_uids_and_project_ids(self, mock_s, mock_graph, mock_mysql):
        """The non-superuser branch interpolated the project ids too."""
        _setup_settings(mock_s)
        uids = [self._PAYLOAD, "TIS-2"]

        df, cur = self._run(mock_graph, mock_mysql, uids, ["7", "8"], False)

        sql, params = self._executed(cur)
        assert params is not None, "uuids/project ids were interpolated, not bound"
        assert list(params) == [self._PAYLOAD, "TIS-2", "7", "8"]
        assert self._PAYLOAD not in sql
        assert "'7'" not in sql and "'8'" not in sql
        assert "s.uuid IN (%s, %s)" in sql
        assert "ps.project_id IN (%s, %s)" in sql

    @patch(f"{_DBTS}.MySQLdb")
    @patch(f"{_DBTS}.GraphDatabase")
    @patch(f"{_DBTS}.settings")
    def test_no_project_ids_still_matches_nothing(self, mock_s, mock_graph, mock_mysql):
        """A caller with no mapped projects produced ``IN ()`` — a MySQL syntax
        error that __runQuery's bare except swallowed into None. Bind a sentinel
        instead, exactly as views.py:841 does: valid SQL that matches nothing."""
        _setup_settings(mock_s)

        df, cur = self._run(mock_graph, mock_mysql, ["NHP-1"], [], False, rows=[])

        sql, params = self._executed(cur)
        assert params is not None
        assert list(params) == ["NHP-1", ""]
        assert "IN ()" not in sql
        assert "ps.project_id IN (%s)" in sql
        assert df.empty
        assert list(df.columns) == ["id", "sample_type_id", "uuid", "json_metadata"]
