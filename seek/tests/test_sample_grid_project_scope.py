"""The sample-type grid and the NHP timeline are project-scoped (SEC-009).

``/seek/retrieve/samples/`` (``retrieveSamples``) and ``/seek/samples/retrieveType/`` (``getSampleType``) used to hand
any logged-in caller every project's samples with their full metadata, and the ``/seek/nhp*/`` and ``/seek/eventdata/``
routes any project's NHP by uuid. Now a caller who is not a superuser gets only the samples of their projects, and an
NHP outside them answers exactly as one that does not exist. A superuser is unscoped. Fails closed: a caller whose
projects cannot be resolved gets no rows and no NHP.

Hermetic: SEEK and MySQL are stubbed; the SQL and the ORM filter the views build are inspected, never run.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate

import seek.views.samples  # noqa: F401  -- so @patch can resolve the target
import seek.views.timeline  # noqa: F401
from seek.dbtable_sample import DBtable_sample

_MOD = "seek.views.samples"
MEMBER = SimpleNamespace(is_authenticated=True, is_active=True, is_superuser=False, username="member", pk=7)


def _grid(view, project_ids, captured):
    """Call a grid view as MEMBER whose projects are ``project_ids``, with a real DBtable_sample whose database calls
    are captured instead of run."""
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "username": "member", "password": "x", "server": "s"}
    with patch("dmac.dbconn_django.DBconn_django.__init__", return_value=None):  # no database connection
        dbsample = DBtable_sample()
    dbsample.db = MagicMock()
    dbsample.db.queryToListDics.side_effect = lambda sql, headers, alias, params: captured.update(
        sql=sql, params=params) or []
    dbsample.db.retrieve_table_list.side_effect = lambda table, pk, filtersdic, mapping: captured.update(
        filtersdic=filtersdic) or ([], [], 0)
    request = RequestFactory().get("/", {"sampletype_id": "1", "attribute": "none"})
    request.user = MEMBER
    request.session = {}
    with patch(f"{_MOD}.SeekDB", return_value=seekdb), patch(f"{_MOD}.DBtable_sample", return_value=dbsample), \
            patch(f"{_MOD}._callerProjectIds", return_value=project_ids):
        return getattr(seek.views.samples, view)(request)


def test_the_sample_type_rows_are_only_the_callers_projects():
    captured = {}
    _grid("getSampleType", (5,), captured)

    assert "projects_samples" in captured["sql"]
    assert "5" in captured["params"]


def test_the_sample_type_rows_are_none_when_the_projects_cannot_be_resolved():
    captured = {}
    _grid("getSampleType", (), captured)

    assert "1=0" in captured["sql"]


def test_a_superuser_gets_every_sample_of_the_type():
    captured = {}
    _grid("getSampleType", None, captured)

    assert "projects_samples" not in captured["sql"] and "1=0" not in captured["sql"]


def _scope_projects(filtersdic):
    """The project ids of the grid's row scope: the ``id__in`` subquery over projects_samples, or None if unscoped."""
    scope = filtersdic.get("scope")
    if scope is None:
        return None
    (lookup, inner), = scope.children
    assert lookup == "id__in" and inner.model._meta.db_table == "projects_samples"
    (condition,) = inner.query.where.children
    return [str(p) for p in condition.rhs]


def test_the_grid_rows_are_only_the_callers_projects():
    captured = {}
    _grid("retrieveSamples", (5, 9), captured)

    assert _scope_projects(captured["filtersdic"]) == ["5", "9"]


def test_the_grid_rows_are_none_when_the_projects_cannot_be_resolved():
    captured = {}
    _grid("retrieveSamples", (), captured)

    assert _scope_projects(captured["filtersdic"]) == []


def test_a_superuser_gets_every_grid_row():
    captured = {}
    _grid("retrieveSamples", None, captured)

    assert _scope_projects(captured["filtersdic"]) is None


def test_the_grid_scope_applies_to_the_page_and_the_total():
    from django.db.models import Q

    from dmac.dbconn_django import DBconn_django

    conn = DBconn_django.__new__(DBconn_django)
    scope = Q(id__in=[1])
    with patch.object(DBconn_django, "retrieveJoint", return_value=[]) as joint:
        conn.retrieve_table_list("samples", "id", {"orderby": " ", "startNo": " ", "endNo": " ", "limit": " ",
                                                   "filterRules": [], "scope": scope}, {})

    assert [call.args[2] for call in joint.call_args_list] == [Q() & scope, scope]


# --------------------------------------------------------------------------- the NHP timeline

NHP = "NHP-0001"


@pytest.fixture
def timeline():
    samples = MagicMock()
    samples.objects.filter.return_value.values_list.return_value = [42]
    data = [{"uuid": NHP}]
    with patch("seek.views.timeline.Samples", samples, create=True), \
            patch("seek.views.timeline.save_nhp_info_to_json", return_value=data), \
            patch("seek.views.timeline.run_All", return_value=data), \
            patch("seek.views.timeline.get_event_data", return_value=data), \
            patch("seek.views.timeline.get_timeline_data", return_value=data), \
            patch("seek.views.timeline.save_nhp_data", return_value=b"xlsx"):
        yield samples


_ROUTES = {
    "nhp_info": ((NHP,), "/seek/nhpinfo/%s/" % NHP),
    "get_nhp_data": ((NHP,), "/seek/nhpdata/%s/" % NHP),
    "download_nhp_data": ((NHP,), "/seek/nhpdata/%s/download/" % NHP),
    "fetch_event_data": ((NHP, "imaging", "2026-01-01"), "/seek/eventdata/%s/imaging/2026-01-01/" % NHP),
}


def _nhp(view, project_ids, visible):
    args, path = _ROUTES[view]
    request = APIRequestFactory().get(path)
    force_authenticate(request, user=MEMBER)
    with patch("seek.views.timeline._callerProjectIds", return_value=project_ids, create=True), \
            patch("nextseek_api.views._samples_visible_to_projects", return_value=visible):
        return getattr(seek.views.timeline, view)(request, *args)


@pytest.mark.parametrize("view", sorted(_ROUTES))
def test_an_nhp_outside_the_callers_projects_is_not_found(timeline, view):
    response = _nhp(view, (5,), set())

    assert response.status_code == 404
    timeline.objects.filter.assert_called_with(uuid=NHP)


@pytest.mark.parametrize("view", sorted(_ROUTES))
def test_an_nhp_is_not_found_when_the_projects_cannot_be_resolved(timeline, view):
    assert _nhp(view, (), {"42"}).status_code == 404


@pytest.mark.parametrize("view", sorted(_ROUTES))
def test_an_nhp_in_the_callers_projects_is_served(timeline, view):
    assert _nhp(view, (5,), {"42"}).status_code == 200


@pytest.mark.parametrize("view", sorted(_ROUTES))
def test_a_superuser_reads_every_nhp(timeline, view):
    assert _nhp(view, None, set()).status_code == 200


_SERVICE_OF = {"nhp_info": "save_nhp_info_to_json", "get_nhp_data": "run_All",
               "download_nhp_data": "get_timeline_data", "fetch_event_data": "get_event_data"}


@pytest.mark.parametrize("view", sorted(_ROUTES))
@pytest.mark.parametrize("project_ids", [(5,), None])
def test_each_view_hands_the_callers_scope_to_its_service(timeline, view, project_ids):
    """A member's project ids (None only for a superuser) reach the service that reads the descendants."""
    service = _SERVICE_OF[view]
    with patch(f"seek.views.timeline.{service}", return_value=[{"uuid": NHP}]) as called:
        assert _nhp(view, project_ids, {"42"}).status_code == 200
    assert called.call_args.args[-1] == project_ids
