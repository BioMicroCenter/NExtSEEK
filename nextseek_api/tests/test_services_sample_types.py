"""
Comprehensive tests for nextseek_api/services/sample_types.py

Covers:
- _resolve_uid_to_seek_id (sample type resolver)
- SampleTypeProxyViewSet: list, retrieve, create, partial_update
- SampleTypeChildrenViewSet: child_types action
- SamplesByChildTypesViewSet: parents_by_child_types action
"""

import json
import pytest
import requests
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from django.test import override_settings
from rest_framework.test import APIRequestFactory


@pytest.fixture(autouse=True)
def _titles():
    """The create's title check reads SEEK's sample_types table; there is none here. ``.real`` is the unpatched one."""
    from nextseek_api.services import sample_types
    real = getattr(sample_types, "_existing_type_ids", None)
    with patch.object(sample_types, "_existing_type_ids", return_value=[], create=True) as m:
        m.real = real
        yield m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(payload):
    return json.dumps(payload).encode()


def _ok(payload, code=200):
    return (_body(payload), code, {"Content-Type": "application/json"}, Mock())


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
        # A superuser: the lineage endpoints scope anyone else (test_sample_types_lineage_scope.py covers that).
        user.is_superuser = True
    req.user = user
    if data is not None:
        req.data = data
    if query:
        req.GET = query
    req.query_params = req.GET
    return req


# Valid JSON:API responses matching SampleTypeSingleResponse / SampleTypeListResponse models
GOOD_ST_SINGLE = {
    "data": {
        "id": "12",
        "type": "sample_types",
        "attributes": {"title": "TIS"},
        "relationships": {
            "submitter": {"data": []},
            "projects": {"data": []},
            "assays": {"data": []},
            "samples": {"data": []},
        },
        "links": {"self": "/sample_types/12"},
        "meta": {},
    },
    "jsonapi": {"version": "1.0"},
}

GOOD_ST_LIST = {
    "data": [
        {
            "id": "12",
            "type": "sample_types",
            "attributes": {"title": "TIS"},
            "links": {"self": "/sample_types/12"},
        }
    ],
    "jsonapi": {"version": "1.0"},
    "links": {"self": "/sample_types?page[number]=1&page[size]=100"},
    "meta": {"base_url": "http://localhost", "api_version": "v1"},
}

# Valid create request payload matching SampleTypeCreateRequest model
GOOD_ST_CREATE_PAYLOAD = {
    "data": {
        "type": "sample_types",
        "attributes": {
            "title": "NEW",
            "sample_attributes": [
                {
                    "title": "Title",
                    "sample_attribute_type": {"id": "1"},
                    "required": True,
                }
            ],
        },
        "relationships": {
            "projects": {"data": [{"type": "projects", "id": "1"}]},
        },
    }
}

# Valid update request payload matching SampleTypeUpdateRequest model
GOOD_ST_UPDATE_PAYLOAD = {
    "data": {
        "id": "12",
        "type": "sample_types",
        "attributes": {"title": "Revised"},
    }
}


# ============================================================================
# _resolve_uid_to_seek_id (sample type)
# ============================================================================


class TestSampleTypeResolveUid:
    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_numeric_passthrough(self, _):
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("42") == "42"

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_resolved(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = 12
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("TIS") == "12"

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_not_found(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = 0
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("UNKNOWN") is None

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_title_returns_none(self, mock_cls):
        mock_cls.return_value.getSampleTypeID.return_value = None
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("UNKNOWN") is None

    @patch("nextseek_api.services.sample_types.DBtable_sampletype")
    def test_exception_returns_none(self, mock_cls):
        mock_cls.side_effect = RuntimeError("boom")
        from nextseek_api.services.sample_types import _resolve_uid_to_seek_id
        assert _resolve_uid_to_seek_id("TIS") is None


# ============================================================================
# SampleTypeProxyViewSet.list
# ============================================================================


class TestSampleTypeList:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_list_200(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = _ok(GOOD_ST_LIST)
        resp = vs.list(_auth_request())
        assert resp.status_code == 200

    def test_list_401(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b'{}', 401, {}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 401

    def test_list_502_html_content_type(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502
        assert b"HTML" in resp.content

    def test_list_502_html_body(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"<html>login</html>", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502

    def test_list_502_invalid_json(self):
        vs = self._viewset()
        vs.client.list_sample_types.return_value = (b"not json", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.list(_auth_request())
        assert resp.status_code == 502

    def test_list_504_when_seek_times_out(self):
        import requests
        vs = self._viewset()
        vs.client.list_sample_types.side_effect = requests.Timeout()
        resp = vs.list(_auth_request())
        assert resp.status_code == 504
        assert b"did not answer" in resp.content

    def test_list_502_when_seek_unreachable(self):
        import requests
        vs = self._viewset()
        vs.client.list_sample_types.side_effect = requests.ConnectionError()
        assert vs.list(_auth_request()).status_code == 502

    def test_list_asks_for_the_longer_timeout(self):
        from nextseek_api.services.sample_types import LIST_TIMEOUT_S
        vs = self._viewset()
        vs.client.list_sample_types.return_value = _ok(GOOD_ST_LIST)
        vs.list(_auth_request())
        assert vs.client.list_sample_types.call_args.kwargs["timeout_s"] == LIST_TIMEOUT_S > 25
        # N1: under nginx's default 60 s proxy_read_timeout, so the app's own JSON 504 answers first.
        assert LIST_TIMEOUT_S < 60


class TestSeekClientTimeout:
    def _call(self, **kw):
        from nextseek_api.helpers import SeekAPIClient
        c = SeekAPIClient()
        c.session = MagicMock()
        c.session.request.return_value = Mock(content=b"{}", status_code=200, headers={})
        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("u", "p"), {})):
            c.list_sample_types(MagicMock(), **kw)
        return c.session.request.call_args.kwargs["timeout"]

    def test_default_stays_20(self):
        assert self._call() == 20

    def test_list_override_reaches_requests(self):
        assert self._call(timeout_s=60) == 60

    def test_other_value_reaches_requests(self):
        assert self._call(timeout_s=45) == 45

    def test_create_timeout_reaches_requests(self):
        from nextseek_api.helpers import SeekAPIClient
        c = SeekAPIClient()
        c.session = MagicMock()
        c.session.request.return_value = Mock(content=b"{}", status_code=201, headers={})
        with patch("nextseek_api.helpers.resolve_seek_auth", return_value=(("u", "p"), {})):
            c.create_sample_type(MagicMock(), {"data": {}})
            assert c.session.request.call_args.kwargs["timeout"] == 20
            c.create_sample_type(MagicMock(), {"data": {}}, timeout_s=(10, 50))
            assert c.session.request.call_args.kwargs["timeout"] == (10, 50)


# ============================================================================
# SampleTypeProxyViewSet.retrieve
# ============================================================================


class TestSampleTypeRetrieve:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_retrieve_numeric_200(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_retrieve_404_unresolved(self, _):
        vs = self._viewset()
        resp = vs.retrieve(_auth_request(), uid="UNKNOWN")
        assert resp.status_code == 404

    def test_retrieve_401(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b'{}', 401, {}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 401

    def test_retrieve_502_html(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 502

    def test_retrieve_502_invalid_json(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = (b"bad", 200, {"Content-Type": "application/json"}, Mock())
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 502

    def test_retrieve_uses_pk_fallback(self):
        vs = self._viewset()
        vs.client.get_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        resp = vs.retrieve(_auth_request(), uid=None, pk="12")
        assert resp.status_code == 200

    # Measured on fairdata-dev 2026-09-22: SEEK took 23-25 s to answer GET /sample_types/11 (its auth
    # lookup table is stale, so it checks every sample's permissions one by one), the proxy's 20 s
    # timeout raised ReadTimeout, and the uncaught exception reached the caller as a bare 500.
    def test_retrieve_504_when_seek_times_out(self):
        import requests
        vs = self._viewset()
        vs.client.timeout_s = 20
        vs.client.get_sample_type.side_effect = requests.ReadTimeout("read timed out")
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 504
        body = json.loads(resp.content)
        assert body["errors"][0]["title"] == "Upstream timeout"
        assert "20 s" in body["errors"][0]["detail"]

    def test_retrieve_502_when_seek_unreachable(self):
        import requests
        vs = self._viewset()
        vs.client.get_sample_type.side_effect = requests.ConnectionError("refused")
        resp = vs.retrieve(_auth_request(), uid="12")
        assert resp.status_code == 502
        assert json.loads(resp.content)["errors"][0]["title"] == "Upstream connection error"


# ============================================================================
# SampleTypeProxyViewSet.create
# ============================================================================


class TestSampleTypeCreate:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def test_create_201(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = _ok(GOOD_ST_SINGLE, 201)
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 201

    def test_create_422_invalid_body(self):
        vs = self._viewset()
        req = _auth_request(method="post", data={"bad": "data"})
        resp = vs.create(req)
        assert resp.status_code == 422

    def test_create_401(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (b'{}', 401, {}, Mock())
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 401

    def test_create_502_bad_response(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (b"not json", 200, {"Content-Type": "application/json"}, Mock())
        req = _auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD)
        resp = vs.create(req)
        assert resp.status_code == 502


# Issue #140: SEEK finished creating a large type (70+ attributes) after the front proxy had given up, so the caller
# saw a 504 or 500 for a type that exists, and a retry would have made a second type with the same title.
class TestSampleTypeCreateUnconfirmed:
    @pytest.fixture(autouse=True)
    def _seek_login(self):
        with patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {})) as m:
            yield m

    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def _create(self, vs):
        resp = vs.create(_auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD))
        return resp, json.loads(resp.content)

    def test_create_waits_longer_than_the_default_but_answers_before_the_front_proxy(self):
        from nextseek_api.services.sample_types import CREATE_TIMEOUT_S
        vs = self._viewset()
        vs.client.create_sample_type.return_value = _ok(GOOD_ST_SINGLE, 201)
        resp, _ = self._create(vs)
        assert resp.status_code == 201
        assert vs.client.create_sample_type.call_args.kwargs["timeout_s"] == (10, CREATE_TIMEOUT_S)
        # Under nginx's default 60 s proxy_read_timeout, so the app's own JSON answers first.
        assert 20 < CREATE_TIMEOUT_S < 60

    def test_read_timeout_is_202_unconfirmed_with_the_title(self):
        vs = self._viewset()
        vs.client.create_sample_type.side_effect = requests.ReadTimeout("slow")
        resp, body = self._create(vs)
        assert resp.status_code == 202
        assert resp["Content-Type"] == "application/json"
        assert body["status"] == "unconfirmed" and body["title"] == "NEW"
        assert "GET /nextseek_api/sample_types/<title>/" in body["detail"]

    def test_connection_error_is_502_json_with_the_title(self):
        vs = self._viewset()
        vs.client.create_sample_type.side_effect = requests.ConnectionError("down")
        resp, body = self._create(vs)
        assert resp.status_code == 502
        assert resp["Content-Type"] == "application/json"
        assert body["title"] == "NEW" and body["errors"]

    def test_seek_5xx_is_502_json_that_says_the_type_may_exist(self):
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (b"<html>boom</html>", 500, {"Content-Type": "text/html"}, Mock())
        resp, body = self._create(vs)
        assert resp.status_code == 502
        assert resp["Content-Type"] == "application/json"
        assert body["seek_status"] == 500 and body["title"] == "NEW"
        assert "may still have been created" in body["errors"][0]["detail"]

    def test_seek_refusal_keeps_seek_status_and_body(self):
        vs = self._viewset()
        refusal = _body({"errors": [{"title": "Title is invalid"}]})
        vs.client.create_sample_type.return_value = (refusal, 422, {"Content-Type": "application/vnd.api+json"}, Mock())
        resp, body = self._create(vs)
        assert resp.status_code == 422
        assert body["errors"][0]["title"] == "Title is invalid"

    def test_taken_title_is_409_with_the_id_when_visible_and_nothing_is_sent(self, _titles):
        _titles.return_value = ["117"]
        vs = self._viewset()
        vs.client.get_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        resp, body = self._create(vs)
        assert resp.status_code == 409
        assert resp["Content-Type"] == "application/json"
        assert body["id"] == "117" and body["title"] == "NEW"
        _titles.assert_called_once_with("NEW")
        vs.client.create_sample_type.assert_not_called()

    def test_taken_title_is_409_without_the_id_when_not_visible(self, _titles):
        _titles.return_value = ["117", "118"]
        vs = self._viewset()
        vs.client.get_sample_type.side_effect = [(b"{}", 403, {}, Mock()), requests.ReadTimeout("slow")]
        resp, body = self._create(vs)
        assert resp.status_code == 409
        assert "id" not in body
        vs.client.create_sample_type.assert_not_called()

    def test_caller_without_a_seek_login_gets_401_whether_or_not_the_title_is_taken(self, _titles, _seek_login):
        _seek_login.return_value = (None, {})
        vs = self._viewset()
        vs.client.create_sample_type.return_value = (None, 401, {}, None)
        _titles.return_value = ["117"]
        taken = vs.create(_auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD))
        _titles.return_value = []
        free = vs.create(_auth_request(method="post", data=GOOD_ST_CREATE_PAYLOAD))
        assert taken.status_code == free.status_code == 401
        assert taken.content == free.content


class TestExistingTypeIds:
    def test_ids_of_the_types_holding_the_title(self, _titles):
        with patch("nextseek_api.services.sample_types.DBtable_sampletype") as table:
            table.return_value.queryRecordsByConstraint.return_value = [{"id": 117, "title": "D.HEM"}]
            assert _titles.real("D.HEM") == ["117"]
            table.return_value.queryRecordsByConstraint.assert_called_once_with({"title": "D.HEM"})

    def test_an_unreadable_table_lets_the_create_through(self, _titles):
        with patch("nextseek_api.services.sample_types.DBtable_sampletype", side_effect=RuntimeError("no db")):
            assert _titles.real("D.HEM") == []


# ============================================================================
# SampleTypeProxyViewSet.partial_update
# ============================================================================


class TestSampleTypePartialUpdate:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeProxyViewSet
        vs = SampleTypeProxyViewSet()
        vs.client = MagicMock()
        return vs

    def _patch_payload(self, sid="12"):
        return {
            "data": {
                "type": "sample_types",
                "id": sid,
                "attributes": {"title": "Revised"},
            }
        }

    def test_patch_numeric_200(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 200

    def test_patch_id_mismatch(self):
        vs = self._viewset()
        # Path uid=12 resolves to "12", body id="99" => mismatch
        req = _auth_request(method="patch", data=self._patch_payload("99"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 422
        assert b"does not match path id" in resp.content

    def test_patch_body_id_inferred_from_path(self):
        """Body id same as path id, inferred when body id is None."""
        vs = self._viewset()
        # SampleTypeUpdateRequest requires data.id, so we must provide it.
        # But the body_id None branch triggers when to_seek_payload() drops id.
        # Let's provide body id matching path to test the happy path.
        payload = self._patch_payload("12")
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_patch_fallback_body_id_digit(self, _):
        vs = self._viewset()
        payload = self._patch_payload("42")
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="UNKNOWN")
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types._resolve_uid_to_seek_id", return_value=None)
    def test_patch_404_no_id(self, _):
        vs = self._viewset()
        # body id is non-digit "UNKNOWN", path also unresolvable
        payload = self._patch_payload("UNKNOWN")
        req = _auth_request(method="patch", data=payload)
        resp = vs.partial_update(req, uid="UNKNOWN")
        assert resp.status_code == 404

    def test_patch_422_invalid_body(self):
        vs = self._viewset()
        req = _auth_request(method="patch", data={"bad": "data"})
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 422

    def test_patch_401_upstream(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b'{}', 401, {}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 401

    def test_patch_502_html(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b"<html>login</html>", 200, {"Content-Type": "text/html"}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 502

    def test_patch_502_invalid_json(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = (b"bad", 200, {"Content-Type": "application/json"}, Mock())
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid="12")
        assert resp.status_code == 502

    def test_patch_uses_pk_fallback(self):
        vs = self._viewset()
        vs.client.update_sample_type.return_value = _ok(GOOD_ST_SINGLE)
        req = _auth_request(method="patch", data=self._patch_payload("12"))
        resp = vs.partial_update(req, uid=None, pk="12")
        assert resp.status_code == 200


# ============================================================================
# SampleTypeChildrenViewSet.child_types
# ============================================================================


class TestSampleTypeChildren:
    def _viewset(self):
        from nextseek_api.services.sample_types import SampleTypeChildrenViewSet
        vs = SampleTypeChildrenViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_200(self, mock_st_model, mock_gd, mock_resolve, mock_auth):
        vs = self._viewset()
        # Neo4j mock
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": "CEL"}],
            MagicMock(),
            ["type"],
        )
        # ORM mock
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
            {"id": 27, "title": "CEL", "description": "Cell"},
        ]
        req = _auth_request()
        resp = vs.child_types(req, uid="42")
        assert resp.status_code == 200
        data = resp.data
        assert data["total"] == 2
        assert data["msg"] == "OK"

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(None, None))
    def test_child_types_401_unauthenticated(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(user=user)
        resp = vs.child_types(req, uid="42")
        assert resp.status_code == 401

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value=None)
    def test_child_types_404(self, _, __):
        vs = self._viewset()
        resp = vs.child_types(_auth_request(), uid="UNKNOWN")
        assert resp.status_code == 404

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_child_types_502_neo4j_error(self, mock_gd, _, __):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_child_types_502_generic_exception(self, mock_gd, _, __):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_partial_desc_lookup(self, mock_st_model, mock_gd, _, __):
        """Some types not found in DB gives warning message."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": "UNKNOWN_TYPE"}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
        ]
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert "not available for 1" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_desc_lookup_failed(self, mock_st_model, mock_gd, _, __):
        """DB exception in description lookup gives warning."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.side_effect = RuntimeError("DB fail")
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert "not available" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_empty_result(self, mock_st_model, mock_gd, _, __):
        """No child types returns total=0."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = ([], MagicMock(), ["type"])
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert resp.data["total"] == 0

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types._resolve_sample_uid_to_seek_id", return_value="42")
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_child_types_none_type_filtered(self, mock_st_model, mock_gd, _, __):
        """None type values from Neo4j should be filtered out."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"type": "TIS"}, {"type": None}],
            MagicMock(),
            ["type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"id": 26, "title": "TIS", "description": "Tissue"},
        ]
        resp = vs.child_types(_auth_request(), uid="42")
        assert resp.status_code == 200
        assert resp.data["total"] == 1


# ============================================================================
# SamplesByChildTypesViewSet.parents_by_child_types
# ============================================================================


class TestSamplesByChildTypes:
    def _viewset(self):
        from nextseek_api.services.sample_types import SamplesByChildTypesViewSet
        vs = SamplesByChildTypesViewSet()
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_200_titles(self, mock_st_model, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "Non Human Primate"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS", "CEL"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert resp.data["total"] == 1
        assert resp.data["msg"] == "OK"

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(None, None))
    def test_parents_401(self, _):
        vs = self._viewset()
        user = MagicMock()
        user.is_authenticated = False
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]}, user=user)
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 401

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    def test_parents_422_invalid_body(self, _):
        vs = self._viewset()
        # The model accepts None for child_sample_types, but code requires at least one valid
        req = _auth_request(method="post", data={"child_sample_types": []})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 422

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_parent_filter(self, mock_st_model, mock_gd, _):
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "Non Human Primate"},
        ]
        req = _auth_request(method="post", data={
            "child_sample_types": ["TIS"],
            "parent_sample_type_filters": ["NHP"],
        })
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_id_resolution(self, mock_st_model, mock_gd, _):
        """IDs in child_sample_types should be resolved to titles via ORM."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        # First call: resolve child_sample_types ID -> title
        # Second call: lookup descriptions
        mock_st_model.objects.filter.return_value.values_list.return_value.first.return_value = "TIS"
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["26"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_parents_502_neo4j_error(self, mock_gd, _):
        from neo4j.exceptions import Neo4jError
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=Neo4jError("fail"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    def test_parents_502_generic_exception(self, mock_gd, _):
        vs = self._viewset()
        mock_gd.driver.return_value.__enter__ = Mock(side_effect=RuntimeError("boom"))
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 502

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_desc_lookup_failed(self, mock_st_model, mock_gd, _):
        """Description lookup failure doesn't break the response."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.side_effect = RuntimeError("DB fail")
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert "not available" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_none_id_uuid_skipped(self, mock_st_model, mock_gd, _):
        """Records with None id/uuid are skipped."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"id": 1, "uuid": "NHP-1", "type": "NHP"},
                {"id": None, "uuid": None, "type": "NHP"},
            ],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert resp.data["total"] == 1

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_partial_desc(self, mock_st_model, mock_gd, _):
        """Some types not in DB gives partial warning."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [
                {"id": 1, "uuid": "NHP-1", "type": "NHP"},
                {"id": 2, "uuid": "TIS-1", "type": "UNKNOWN"},
            ],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={"child_sample_types": ["TIS"]})
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
        assert "not available for 1" in resp.data["msg"]

    @patch("nextseek_api.services.sample_types.resolve_seek_auth", return_value=(("u", "p"), {}))
    @patch("nextseek_api.services.sample_types.GraphDatabase")
    @patch("nextseek_api.services.sample_types.Sample_types")
    def test_parents_with_parent_id_resolution(self, mock_st_model, mock_gd, _):
        """parent_sample_type_filters with numeric IDs resolved to titles."""
        vs = self._viewset()
        mock_driver = MagicMock()
        mock_gd.driver.return_value.__enter__ = Mock(return_value=mock_driver)
        mock_gd.driver.return_value.__exit__ = Mock(return_value=False)
        mock_driver.execute_query.return_value = (
            [{"id": 1, "uuid": "NHP-1", "type": "NHP"}],
            MagicMock(),
            ["id", "uuid", "type"],
        )
        mock_st_model.objects.filter.return_value.values_list.return_value.first.return_value = "NHP"
        mock_st_model.objects.filter.return_value.values.return_value = [
            {"title": "NHP", "description": "NHP desc"},
        ]
        req = _auth_request(method="post", data={
            "child_sample_types": ["TIS"],
            "parent_sample_type_filters": ["41"],
        })
        resp = vs.parents_by_child_types(req)
        assert resp.status_code == 200
