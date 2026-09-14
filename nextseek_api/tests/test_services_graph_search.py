"""POST /nextseek_api/samples/graph_search/: the ViewSet and the service behind it.

Hermetic: no MySQL and no Neo4j. The scope resolver, the catalog, the sample type resolver and hydration are
patched; the graph is a fake driver that records every statement and hands back canned records. The request
shapes mirror nextseek_api/tests/test_advanced_search_needs_a_filter.py (APIRequestFactory, a MagicMock user,
create() called directly), plus one request through the real DRF dispatch for the authentication gate.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from rest_framework.test import APIRequestFactory

from nextseek_api.graph_search import catalog_cache
from nextseek_api.graph_search import service
from nextseek_api.graph_search.query import Catalog
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable
from nextseek_api.models import GraphSearchRequest, SampleAdvancedSearchResult

PATH = "/nextseek_api/samples/graph_search/"
SCOPE = "nextseek_api.services.graph_search.resolve_scope"
NEO4J = "nextseek_api.services.graph_search._neo4j"
HYDRATE = "nextseek_api.graph_search.hydrate.hydrate"
CATALOG = "nextseek_api.graph_search.catalog_cache.get_catalog"
RESOLVE = "nextseek_api.graph_search.service.resolve_sampletype_to_seek_id"

ADMIN = Scope(is_admin=True, person_id=None, project_ids=())
MEMBER = Scope(is_admin=False, person_id=144, project_ids=(2, 16))
NOBODY = Scope(is_admin=False, person_id=144, project_ids=())

TYPE_IDS = {"TIS": 26, "D.SEQ": 31}

FAKE_CATALOG = Catalog(
    type_title_by_id={v: k for k, v in TYPE_IDS.items()},
    label_by_title={"TIS": "T_TIS", "D.SEQ": "T_D_SEQ"},
    titles_by_type={
        "TIS": frozenset({"UID", "Organ", "CellCount"}),
        "D.SEQ": frozenset({"UID", "Parent", "Reads"}),
    },
    value_type={("TIS", "CellCount"): "float", ("D.SEQ", "Reads"): "integer"},
)


def _resolver(title):
    value = TYPE_IDS.get(title)
    return None if value is None else str(value)


def _row(sample_id, sample_type="TIS"):
    return {
        "id": sample_id, "title": f"sample {sample_id}", "uuid": f"220119FLY-{sample_id}",
        "sample_type_id": TYPE_IDS[sample_type], "contributor_id": 145,
        "created_at": "2024-01-02 03:04:05", "json_metadata": {"Organ": "Lung"},
        "sample_type": sample_type, "first_name": "Demo", "assays": None, "attributeValue": "",
    }


# --------------------------------------------------------------------------------------------------------------
# A fake neo4j driver: records sessions, transaction timeouts and statements; answers from canned records.
# --------------------------------------------------------------------------------------------------------------


class _Result:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self, strict=False):
        assert len(self._records) == 1
        return self._records[0]


class _Tx:
    def __init__(self, driver):
        self.driver = driver

    def run(self, text, parameters=None, **kwargs):
        self.driver.statements.append((text, dict(parameters or {})))
        if self.driver.error is not None:
            raise self.driver.error
        if "count(s) AS total" in text:
            return _Result([{"total": self.driver.total, "types": list(self.driver.types)}])
        return _Result([{"id": i} for i in self.driver.ids])


class _Session:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, work, *args, **kwargs):
        self.driver.timeouts.append(getattr(work, "timeout", None))
        return work(_Tx(self.driver), *args, **kwargs)

    def execute_write(self, *args, **kwargs):  # pragma: no cover - a failure if ever reached
        raise AssertionError("graph_search must never open a write transaction")

    def run(self, *args, **kwargs):  # pragma: no cover - a failure if ever reached
        raise AssertionError("graph_search must run inside execute_read, not an auto-commit transaction")


class FakeDriver:
    def __init__(self, ids=(), total=0, types=(), error=None):
        self.ids = list(ids)
        self.total = total
        self.types = list(types)
        self.error = error
        self.sessions = []
        self.statements = []
        self.timeouts = []

    def session(self, **kwargs):
        self.sessions.append(kwargs)
        return _Session(self)


# --------------------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_caches():
    catalog_cache.clear()
    yield
    catalog_cache.clear()


def _request(body, query="", superuser=True, authenticated=True):
    req = APIRequestFactory().post(PATH + (f"?{query}" if query else ""),
                                   data=json.dumps(body), content_type="application/json")
    user = MagicMock()
    user.is_authenticated = authenticated
    user.is_superuser = superuser
    user.username = "demo"
    req.user = user
    req.data = body
    req.query_params = req.GET
    return req


def _post(body, query="", superuser=True, authenticated=True):
    from nextseek_api.services.graph_search import GraphSearchViewSet

    return GraphSearchViewSet().create(_request(body, query, superuser, authenticated))


def _json(resp):
    return json.loads(resp.content)


# --------------------------------------------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------------------------------------------


@patch(NEO4J)
@patch(SCOPE)
def test_an_unauthenticated_caller_is_401(mock_scope, mock_neo4j):
    resp = _post({"filter_searchText": "lung"}, authenticated=False)

    assert resp.status_code == 401, resp.content
    mock_scope.assert_not_called()
    mock_neo4j.assert_not_called()


@patch(NEO4J)
@patch(SCOPE)
def test_an_anonymous_request_through_drf_dispatch_is_401_with_a_challenge(mock_scope, mock_neo4j):
    """Session auth is listed first, and DRF asks only the first authenticator for a challenge.

    SessionAuthentication has none, so without the ViewSet's override DRF turns NotAuthenticated into a 403,
    the same code as "no SEEK person".
    """
    from nextseek_api.services.graph_search import GraphSearchViewSet

    req = APIRequestFactory().post(PATH, data={"filter_searchText": "lung"}, format="json")
    resp = GraphSearchViewSet.as_view({"post": "create"})(req)

    assert resp.status_code == 401
    assert resp["WWW-Authenticate"].startswith("Basic")
    mock_scope.assert_not_called()
    mock_neo4j.assert_not_called()


def test_the_viewset_uses_session_then_basic_authentication_and_is_authenticated():
    from rest_framework.authentication import BasicAuthentication
    from rest_framework.permissions import IsAuthenticated

    from nextseek_api.authentication import CsrfExemptSessionAuthentication
    from nextseek_api.services.graph_search import GraphSearchViewSet

    assert GraphSearchViewSet.authentication_classes == [CsrfExemptSessionAuthentication, BasicAuthentication]
    assert GraphSearchViewSet.permission_classes == [IsAuthenticated]


# --------------------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"filter_searchText": "lung", "no_such_key": 1},
    {"filter_searchText": "lung", "extensions": {"where": [], "bogus": True}},
    {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "IN", "value": "Lung"}]}},
    {"sampletype": "TIS"},
    ["lung"],
])
@patch(NEO4J)
@patch(SCOPE)
def test_a_body_the_model_rejects_is_422(mock_scope, mock_neo4j, body):
    resp = _post(body)

    assert resp.status_code == 422, resp.content
    assert _json(resp)["errors"][0]["title"] == "Invalid request"
    mock_scope.assert_not_called()
    mock_neo4j.assert_not_called()


@patch(NEO4J)
@patch(SCOPE)
def test_the_422_names_what_the_model_rejected(mock_scope, mock_neo4j):
    resp = _post({"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "IN", "value": "Lung"}]}})

    assert resp.status_code == 422
    assert "op 'IN' requires a list value" in _json(resp)["errors"][0]["detail"]


@pytest.mark.parametrize("body", [
    {"filter_searchText": ""},
    {"filter_searchText": "   "},
    {"filter_searchText": []},
    {"filter_searchText": "", "sampletype": "NO-SUCH-TYPE"},
    {"filter_searchText": "", "extensions": {"where": []}},
    {"filter_searchText": "", "extensions": {"lineage": {"direction": "descendant", "sample_type": "D.SEQ"}}},
])
@patch(RESOLVE, side_effect=_resolver)
@patch(NEO4J)
@patch(SCOPE)
def test_a_search_with_nothing_to_search_on_is_refused(mock_scope, mock_neo4j, _resolve, body):
    resp = _post(body)

    assert resp.status_code == 422, resp.content
    assert b"sampletype" in resp.content, f"the refusal must say what to add: {resp.content!r}"
    assert b"extensions.where" in resp.content
    mock_scope.assert_not_called()
    mock_neo4j.assert_not_called()


@pytest.mark.parametrize("query", ["page=0", "page=-1", "page=abc"])
@patch(NEO4J)
@patch(SCOPE)
def test_a_page_that_is_not_a_positive_integer_is_422(mock_scope, mock_neo4j, query):
    resp = _post({"filter_searchText": "lung"}, query=query)

    assert resp.status_code == 422, resp.content
    assert "page" in _json(resp)["errors"][0]["detail"]
    mock_neo4j.assert_not_called()


# --------------------------------------------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------------------------------------------


@patch(NEO4J)
@patch(SCOPE, side_effect=ScopeUnavailable("Cannot determine project scope for this caller"))
def test_a_caller_with_no_seek_person_is_403(mock_scope, mock_neo4j):
    resp = _post({"filter_searchText": "lung"}, superuser=False)

    assert resp.status_code == 403, resp.content
    assert _json(resp) == {"errors": [{"title": "Cannot determine project scope for this caller"}]}
    mock_neo4j.assert_not_called()


@patch(NEO4J)
@patch(SCOPE, side_effect=RuntimeError("MySQL has gone away"))
def test_a_scope_lookup_failure_returns_no_rows(mock_scope, mock_neo4j):
    resp = _post({"filter_searchText": "lung"}, superuser=False)

    assert resp.status_code == 502, resp.content
    assert "rows" not in _json(resp)
    mock_neo4j.assert_not_called()


@patch(HYDRATE)
@patch(CATALOG)
@patch(NEO4J)
@patch(SCOPE, return_value=NOBODY)
def test_a_member_of_no_project_gets_total_0_and_no_cypher(mock_scope, mock_neo4j, mock_catalog, mock_hydrate):
    resp = _post({"filter_searchText": "lung"}, superuser=False)

    assert resp.status_code == 200, resp.content
    data = _json(resp)
    assert data["total"] == 0
    assert data["rows"] == []
    SampleAdvancedSearchResult.model_validate(data)
    mock_neo4j.assert_not_called()
    mock_catalog.assert_not_called()
    mock_hydrate.assert_not_called()


# --------------------------------------------------------------------------------------------------------------
# The happy path, with a fake driver
# --------------------------------------------------------------------------------------------------------------


def _happy(body, query="", scope=ADMIN, driver=None, rows=None):
    driver = driver or FakeDriver(ids=[3, 7], total=2, types=["TIS"])
    rows = rows if rows is not None else [_row(3), _row(7)]
    with patch(SCOPE, return_value=scope), \
            patch(NEO4J, return_value=(driver, "neo4j")), \
            patch(CATALOG, return_value=FAKE_CATALOG), \
            patch(RESOLVE, side_effect=_resolver), \
            patch(HYDRATE, return_value=rows) as mock_hydrate:
        resp = _post(body, query=query, superuser=scope.is_admin)
    return resp, driver, mock_hydrate


def test_the_happy_path_returns_advanced_searchs_envelope():
    resp, driver, mock_hydrate = _happy({"sampletype": "TIS", "filter_searchText": "lung"})

    assert resp.status_code == 200, resp.content
    data = _json(resp)
    SampleAdvancedSearchResult.model_validate(data)
    assert data["total"] == 2
    assert [r["id"] for r in data["rows"]] == [3, 7]
    assert data["sampleTypes"] == ["TIS"]
    assert data["noSampleTypes"] == 1
    assert data["msg"] == "okay"
    assert data["status"] == 1
    assert data["footer"] == []
    mock_hydrate.assert_called_once_with([3, 7])


def test_both_statements_run_in_read_transactions_with_a_60_second_timeout():
    _resp, driver, _ = _happy({"sampletype": "TIS", "filter_searchText": "lung"})

    assert driver.sessions == [{"database": "neo4j", "default_access_mode": "READ"}]
    assert driver.timeouts == [60, 60]
    texts = [text for text, _ in driver.statements]
    assert len(texts) == 2
    assert all(text.startswith("CYPHER 25") for text in texts)
    assert "SKIP $skip LIMIT $limit" in texts[0]
    assert "count(s) AS total" in texts[1]


def test_page_and_page_size_become_skip_and_limit():
    _resp, driver, _ = _happy({"filter_searchText": "lung"}, query="page=3&page_size=10")

    params = driver.statements[0][1]
    assert params["skip"] == 20
    assert params["limit"] == 10


@pytest.mark.parametrize("query, limit", [
    ("", 100),
    ("page_size=5000", 1000),
    ("page_size=0", 100),
    ("page_size=abc", 100),
])
def test_page_size_follows_advanced_searchs_paginator(query, limit):
    _resp, driver, _ = _happy({"filter_searchText": "lung"}, query=query)

    assert driver.statements[0][1]["limit"] == limit


def test_a_non_admin_is_scoped_by_the_query_builder():
    _resp, driver, _ = _happy({"filter_searchText": "lung"}, scope=MEMBER)

    for text, params in driver.statements:
        assert "any(p IN s.project_ids WHERE p IN $projects)" in text
        assert params["projects"] == [2, 16]


def test_sample_types_are_sorted():
    driver = FakeDriver(ids=[3], total=2, types=["TIS", "D.SEQ"])
    resp, _driver, _ = _happy({"filter_searchText": "lung"}, driver=driver, rows=[_row(3)])

    assert _json(resp)["sampleTypes"] == ["D.SEQ", "TIS"]
    assert _json(resp)["noSampleTypes"] == 2


def test_extensions_where_searches_without_a_term():
    body = {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "Organ", "op": "=", "value": "Lung"},
        {"sample_type": "TIS", "attribute": "CellCount", "op": ">=", "value": 10000000},
    ]}}
    resp, driver, _ = _happy(body)

    assert resp.status_code == 200, resp.content
    text, params = driver.statements[0]
    assert "MATCH (s:`T_TIS`)" in text
    assert params["w0"] == "Lung"
    assert params["w1"] == 10000000.0


def test_an_out_of_range_page_returns_empty_rows_and_the_total():
    driver = FakeDriver(ids=[], total=2, types=["TIS"])
    resp, _driver, _ = _happy({"filter_searchText": "lung"}, query="page=99", driver=driver, rows=[])

    assert resp.status_code == 200, resp.content
    data = _json(resp)
    assert data["total"] == 2
    assert data["rows"] == []
    SampleAdvancedSearchResult.model_validate(data)


def test_debug_meta_appends_the_timings_to_the_footer():
    resp, _driver, _ = _happy({"filter_searchText": "lung"}, query="debug_meta=1")

    data = _json(resp)
    SampleAdvancedSearchResult.model_validate(data)
    debug = data["footer"][-1]["debug"]
    assert set(debug) == {"cypher_ms", "count_ms", "hydrate_ms", "total_ms"}
    assert all(isinstance(v, (int, float)) and v >= 0 for v in debug.values())


def test_no_debug_meta_leaves_the_footer_empty():
    resp, _driver, _ = _happy({"filter_searchText": "lung"}, query="debug_meta=0")

    assert _json(resp)["footer"] == []


def test_a_request_the_catalog_rejects_is_422_with_its_message():
    body = {"filter_searchText": "", "extensions": {"where": [
        {"sample_type": "TIS", "attribute": "NoSuchAttribute", "op": "=", "value": "x"}]}}
    resp, driver, mock_hydrate = _happy(body)

    assert resp.status_code == 422, resp.content
    error = _json(resp)["errors"][0]
    assert error["title"] == "Invalid request"
    assert "'NoSuchAttribute' does not exist on sample type 'TIS'" in error["detail"]
    assert driver.statements == []
    mock_hydrate.assert_not_called()


def test_a_graph_outage_is_502():
    resp, _driver, mock_hydrate = _happy({"filter_searchText": "lung"},
                                         driver=FakeDriver(error=ServiceUnavailable("graph is down")))

    assert resp.status_code == 502, resp.content
    assert _json(resp)["errors"][0]["title"] == "Invalid upstream response"
    mock_hydrate.assert_not_called()


def test_a_transaction_timeout_is_504():
    error = Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
        message="The transaction has been terminated.",
    )
    resp, _driver, mock_hydrate = _happy({"filter_searchText": "lung"}, driver=FakeDriver(error=error))

    assert resp.status_code == 504, resp.content
    mock_hydrate.assert_not_called()


def test_a_hydration_failure_is_502():
    with patch(SCOPE, return_value=ADMIN), \
            patch(NEO4J, return_value=(FakeDriver(ids=[3], total=1, types=["TIS"]), "neo4j")), \
            patch(CATALOG, return_value=FAKE_CATALOG), \
            patch(HYDRATE, side_effect=RuntimeError("MySQL has gone away")):
        resp = _post({"filter_searchText": "lung"})

    assert resp.status_code == 502, resp.content


# --------------------------------------------------------------------------------------------------------------
# The service, directly
# --------------------------------------------------------------------------------------------------------------


def _req(body):
    return GraphSearchRequest.model_validate(body)


@patch(CATALOG, return_value=FAKE_CATALOG)
@patch(RESOLVE, side_effect=_resolver)
def test_search_returns_ids_total_types_and_timings(_resolve, _catalog):
    driver = FakeDriver(ids=[3, 7], total=12, types=["TIS", "D.SEQ"])

    out = service.search(_req({"sampletype": "TIS", "filter_searchText": "lung"}), ADMIN, 1, 100,
                         driver=driver, db="neo4j")

    assert out["total"] == 12
    assert out["ids"] == [3, 7]
    assert out["sample_types"] == ["D.SEQ", "TIS"]
    assert set(out["timings"]) == {"cypher_ms", "count_ms"}
    assert driver.statements[0][1]["types"] == ["TIS"]


@patch(CATALOG)
def test_search_runs_nothing_for_a_member_of_no_project(mock_catalog):
    driver = FakeDriver(ids=[3], total=1, types=["TIS"])

    out = service.search(_req({"filter_searchText": "lung"}), NOBODY, 1, 100, driver=driver, db="neo4j")

    assert out["total"] == 0
    assert out["ids"] == []
    assert out["sample_types"] == []
    assert driver.sessions == []
    mock_catalog.assert_not_called()


@patch(CATALOG, return_value=FAKE_CATALOG)
def test_search_uses_filters_it_is_given_instead_of_resolving_again(_catalog):
    driver = FakeDriver(ids=[], total=0)
    req = _req({"sampletype": "TIS", "filter_searchText": "lung"})
    filters = req.to_db_filters(sampletype_resolver=_resolver)

    with patch(RESOLVE) as mock_resolve:
        service.search(req, ADMIN, 1, 100, driver=driver, db="neo4j", filters=filters)

    mock_resolve.assert_not_called()


@patch(CATALOG, return_value=FAKE_CATALOG)
@patch(RESOLVE, side_effect=_resolver)
def test_all_ids_runs_the_unpaged_statement(_resolve, _catalog):
    driver = FakeDriver(ids=[3, 7, 11], total=3)

    ids = service.all_ids(_req({"filter_searchText": "lung"}), MEMBER, driver=driver, db="neo4j")

    assert ids == [3, 7, 11]
    assert len(driver.statements) == 1
    text, params = driver.statements[0]
    assert text.endswith("RETURN s.id AS id ORDER BY id")
    assert "SKIP" not in text
    assert params["projects"] == [2, 16]
    assert driver.sessions == [{"database": "neo4j", "default_access_mode": "READ"}]


def test_all_ids_is_empty_for_a_member_of_no_project():
    driver = FakeDriver(ids=[3])

    assert service.all_ids(_req({"filter_searchText": "lung"}), NOBODY, driver=driver, db="neo4j") == []
    assert driver.sessions == []


# --------------------------------------------------------------------------------------------------------------
# Routing and the published schema
# --------------------------------------------------------------------------------------------------------------


def test_the_route_resolves_to_graph_search_not_to_a_sample_detail():
    from django.urls import resolve, reverse

    from nextseek_api.services.graph_search import GraphSearchViewSet

    assert reverse("nextseek_api:samples-graph-search-list") == PATH
    assert resolve(PATH).func.cls is GraphSearchViewSet


@pytest.mark.django_db
def test_the_path_and_the_request_model_are_in_the_openapi_schema():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    op = schema["paths"][PATH]["post"]
    assert op["operationId"] == "Graph Search Samples"
    assert "Samples" in op["tags"]
    assert "GraphSearchRequest" in schema["components"]["schemas"]
    examples = op["requestBody"]["content"]["application/json"]["examples"]
    assert len(examples) >= 3
    params = {p["name"] for p in op.get("parameters", [])}
    assert {"page", "page_size", "debug_meta"} <= params
