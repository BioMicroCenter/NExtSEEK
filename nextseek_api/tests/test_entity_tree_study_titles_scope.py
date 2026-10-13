"""Study titles on ``GET entity_tree/edge_attributes/`` and on ``POST entity_tree/lineage/`` with ``include_attributes``.

They follow the graph catalog's rule for study titles (``chat_nextseek.graph_catalog.VOCAB_STUDIES_SCOPED``): a
superuser reads every one; anyone else reads the title of a study only when it holds a sample in one of their
projects (a Sample asset of one of the study's assays, linked to that project in ``projects_samples``). A caller whose
scope cannot be resolved, or who has no projects, reads none. Edges and descriptions are not scoped. MySQL and Neo4j
are faked, so these pin the statement the enrichment runs; the MySQL syntax itself is not exercised here.

The scope is a ``CASE`` on the title in the select list, never a condition on the outer join to ``studies``: MySQL 8.0
may run an ``EXISTS`` in a ``LEFT JOIN ... ON`` as a semijoin, and its answer then depends on the plan it picks.
The test is an uncorrelated ``s.id IN (SELECT ...)`` so MySQL runs the subquery once; a correlated ``EXISTS`` in the
select list ran once per joined row and table-scanned ``projects_samples`` each time (issue #64 follow-up).
"""

import re
from unittest.mock import MagicMock, Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from nextseek_api.graph_search.scope import Scope, ScopeUnavailable
from nextseek_api.models import LineageEdge, LineageTree

MEMBER = Scope(is_admin=False, person_id=7, project_ids=(2, 5))
NO_PROJECTS = Scope(is_admin=False, person_id=8, project_ids=())
ADMIN = Scope(is_admin=True, person_id=None, project_ids=())
_MOD = "nextseek_api.services.entity_tree"


def _viewset():
    from nextseek_api.services.entity_tree import EntityTreeViewSet
    vs = EntityTreeViewSet()
    vs.kwargs, vs.format_kwarg = {}, None
    return vs


def _request(method="get", data=None, superuser=False):
    factory = APIRequestFactory()
    req = factory.post("/") if method == "post" else factory.get("/")
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = superuser
    req.user = user
    if data is not None:
        req.data = data
    req.query_params = req.GET
    return req


class _Sql:
    """Fake MySQL: answers the internal assay read and the enrichment read, and keeps every statement."""

    def __init__(self):
        self.calls = []

    def __call__(self, query, params=None):
        self.calls.append((" ".join(query.split()), list(params or [])))
        if "internal_assays" in query and "assay_context" not in query:
            return [{"id": 7, "internal_assay_title": "RNA-Seq"}]
        if "assay_context" in query:
            return [{"internal_assay_id": 7, "description": "d", "study_titles": "S"}]
        return [{"color": "#AAA"}]

    def enrichment(self):
        found = [c for c in self.calls if "assay_context" in c[0]]
        assert len(found) == 1, self.calls
        return found[0]


def _study_join(statement):
    """The studies join of the enrichment statement, up to its WHERE."""
    start = statement.index(".studies s ON")
    return statement[start:statement.index(" WHERE ac.internal_assay_id")]


def _titles(statement):
    """The select-list expression the study titles are read through."""
    start = statement.index("GROUP_CONCAT(DISTINCT ")
    return statement[start:statement.index(" AS study_titles")]


def _assert_scoped_to(sql_call, projects):
    statement, params = sql_call
    titles = _titles(statement)
    assert titles.startswith("GROUP_CONCAT(DISTINCT CASE WHEN s.id IN (SELECT va.study_id FROM "), titles
    assert "projects_samples" in titles and "assay_assets" in titles, titles
    assert "asset_type = 'Sample'" in titles and "project_id IN (%s, %s)" in titles, titles
    assert titles.endswith(") THEN s.title END SEPARATOR '; ')"), titles
    assert params == list(projects)
    _assert_uncorrelated(titles)


def _assert_uncorrelated(titles):
    """The IN ( ... ) subquery names no column of the outer query, so MySQL runs it once, not once per joined row."""
    inner = titles[titles.index("s.id IN (") + len("s.id IN ("):titles.index(") THEN s.title END")]
    assert not re.search(r"(?<![\w.])s\.", inner) and "= s.id" not in inner, inner


def _assert_join_is_plain(statement):
    """No scope condition on the outer join itself."""
    assert _study_join(statement).strip().endswith(".studies s ON s.id = a.study_id"), _study_join(statement)


@pytest.fixture
def edges():
    """Neo4j answers one edge carrying internal assay 7; MySQL is a _Sql."""
    sql = _Sql()
    driver = MagicMock()
    driver.execute_query.return_value = (
        [{"source": "Tissue", "target": "RNA", "annotation": "RNA-Seq", "internal_assay_id": 7}], None, None)
    with patch(f"{_MOD}.GraphDatabase") as gd, patch(f"{_MOD}._run_sql_query", side_effect=sql), \
            patch(f"{_MOD}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch(f"{_MOD}.resolve_scope") as scope:
        gd.driver.return_value.__enter__ = Mock(return_value=driver)
        gd.driver.return_value.__exit__ = Mock(return_value=False)
        yield sql, scope


def test_edge_attributes_read_a_members_study_titles_through_their_projects(edges):
    sql, scope = edges
    scope.return_value = MEMBER
    resp = _viewset().list_edge_attributes(_request())
    assert resp.status_code == 200
    _assert_scoped_to(sql.enrichment(), MEMBER.project_ids)
    _assert_join_is_plain(sql.enrichment()[0])


@pytest.mark.parametrize("resolved", [NO_PROJECTS, ScopeUnavailable("no person")], ids=["no-projects", "unresolved"])
def test_edge_attributes_read_no_study_title_for_a_caller_who_sees_nothing(edges, resolved):
    sql, scope = edges
    if isinstance(resolved, Exception):
        scope.side_effect = resolved
    else:
        scope.return_value = resolved
    resp = _viewset().list_edge_attributes(_request())
    assert resp.status_code == 200
    edge = (resp.data.get("results") or resp.data)["edges"][0]
    assert edge["annotation"] == "RNA-Seq" and edge["description"] == "d"
    statement, params = sql.enrichment()
    assert _titles(statement) == "GROUP_CONCAT(DISTINCT NULL SEPARATOR '; ')" and params == []
    _assert_join_is_plain(statement)


def test_edge_attributes_leave_a_superusers_study_titles_unscoped(edges):
    sql, scope = edges
    scope.return_value = ADMIN
    resp = _viewset().list_edge_attributes(_request(superuser=True))
    assert resp.status_code == 200
    statement, params = sql.enrichment()
    assert _titles(statement) == "GROUP_CONCAT(DISTINCT s.title SEPARATOR '; ')" and params == []
    _assert_join_is_plain(statement)
    assert (resp.data.get("results") or resp.data)["edges"][0]["study_titles"] == "S"


def test_lineage_include_attributes_reads_study_titles_through_the_callers_projects():
    sql = _Sql()
    tree = LineageTree(input_id="MINE-1", resolved_id="10", total_nodes=0, total_rels=1, nodes=[],
                       rels=[LineageEdge(child_id="10", parent_id="9", internal_assay_id="7")])
    with patch(f"{_MOD}.GraphDatabase") as gd, patch(f"{_MOD}._run_sql_query", side_effect=sql), \
            patch(f"{_MOD}.resolve_seek_auth", return_value=(("u", "p"), {})), \
            patch(f"{_MOD}.resolve_scope", return_value=MEMBER), \
            patch("nextseek_api.services.samples._resolve_uid_to_seek_id", return_value="10"), \
            patch(f"{_MOD}.EntityTreeViewSet._fetch_single_lineage", return_value=(tree, {7})):
        gd.driver.return_value.__enter__ = Mock(return_value=MagicMock())
        gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = _viewset().lineage(_request("post", {"sample_ids": ["MINE-1"], "include_attributes": True}))
    assert resp.status_code == 200
    _assert_scoped_to(sql.enrichment(), MEMBER.project_ids)
    _assert_join_is_plain(sql.enrichment()[0])


def test_many_internal_assays_still_run_one_enrichment_with_the_projects_once(edges):
    sql, scope = edges
    scope.return_value = MEMBER
    ids = list(range(1, 81))
    driver_rows = [{"source": "A", "target": "B", "annotation": "RNA-Seq", "internal_assay_id": 7 if i == 80 else i} for i in ids]
    with patch(f"{_MOD}.GraphDatabase") as gd:
        driver = MagicMock()
        driver.execute_query.return_value = (driver_rows, None, None)
        gd.driver.return_value.__enter__ = Mock(return_value=driver)
        gd.driver.return_value.__exit__ = Mock(return_value=False)
        resp = _viewset().list_edge_attributes(_request())
    assert resp.status_code == 200
    statement, params = sql.enrichment()
    titles = _titles(statement)
    assert statement.count("project_id IN (") == 1 and titles.count("%s") == 2, statement
    assert params[-2:] == list(MEMBER.project_ids)
    _assert_scoped_to((statement, list(MEMBER.project_ids)), MEMBER.project_ids)
