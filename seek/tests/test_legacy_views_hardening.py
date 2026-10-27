"""Legacy /seek/ views: search scope is the caller's current projects, AJAX lookups need a login, uploads are
size-checked before parse (SEC-H05, SEC-H20, SEC-H12)."""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

import seek.views.samples as samples_views
import seek.views.search as search_views
import seek.views.upload as upload_views
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)
MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)


def _search(user, scope=None, exc=None, options=("13", "2")):
    """Run runSampleSearch; SEEK's option list says ``options``, the caller's current scope says ``scope``."""
    calls = {}

    class _Sample:
        def searchAdvanced(self, user_seek, filters, searchType, project_id=0, skip_tree=False, scoped_project_ids=None):
            calls["scoped"] = scoped_project_ids
            return "{}"

    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "projectid": options[0],
                                       "projectOptions": [{"id": o} for o in options]}
    resolve = MagicMock(side_effect=exc) if exc else MagicMock(return_value=scope)
    with patch.object(search_views, "SeekDB", return_value=seekdb), \
            patch.object(search_views, "DBtable_sample", _Sample), \
            patch.object(samples_views, "resolve_scope", resolve):
        response = search_views.runSampleSearch(_get_adv(user), "Advanced")
    return response, calls


def _get_adv(user):
    request = RequestFactory().get("/seek/searchAdvanced/", {
        "filter_searchText": "x", "sampletype_id": "1", "attribute": "none", "filter_matchType": "any"})
    request.user = user
    request.session = {}
    return request


def test_search_uses_current_projects_not_the_seek_option_list():
    # SEEK still lists project 13 (a project the caller has left); their current scope is project 2 only.
    _, calls = _search(MEMBER, scope=Scope(is_admin=False, person_id=7, project_ids=(2,)))
    assert calls["scoped"] == ["2"]


def test_search_fails_closed_when_the_scope_cannot_be_resolved():
    _, calls = _search(MEMBER, exc=ScopeUnavailable("down"))
    assert calls["scoped"] == []


def test_search_for_a_superuser_is_unscoped():
    _, calls = _search(MEMBER, scope=Scope(is_admin=True, person_id=None, project_ids=()))
    assert calls["scoped"] is None


def test_search_refuses_an_anonymous_caller_before_querying():
    response, calls = _search(ANONYMOUS, exc=ScopeUnavailable("anon"))
    assert "scoped" not in calls
    assert json.loads(response.content)["status"] == 0


@pytest.mark.parametrize("view,args", [
    (samples_views.getAttributes, (1,)),
    (samples_views.getOperators, ()),
    (samples_views.getStudiesOptions, (1,)),
    (samples_views.getAssaysOptions, (1,)),
    (samples_views.getInstituionUsers, (1,)),
])
def test_ajax_lookups_refuse_a_caller_without_a_seek_login(view, args):
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": False, "err": "Error: Please log in first."}
    reached = MagicMock()
    request = RequestFactory().get("/seek/x/", {"sampletype_id": "1", "attribute": "a"})
    request.user = ANONYMOUS
    request.session = {}
    with patch("seek.decorators.SeekDB", return_value=seekdb), \
            patch.object(samples_views, "SeekDB", reached), \
            patch.object(samples_views, "DBtable_sampleattribute", reached), \
            patch.object(samples_views, "DBtable_sampletype", reached):
        body = json.loads(view(request, *args).content)
    assert body["status"] == 0
    reached.assert_not_called()


def _bomb():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (150 * 1024 * 1024))
    return buf.getvalue()


def test_samples_validate_refuses_a_bomb_before_connecting_or_parsing():
    request = RequestFactory().post("/", {"excelfile_upload": SimpleUploadedFile("a.xlsx", _bomb())})
    request.user = MEMBER
    request.session = {}
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True}
    with patch.object(upload_views, "SeekDB", return_value=seekdb), \
            patch.object(upload_views.MySQLdb, "connect") as connect, \
            patch.object(upload_views.pd, "ExcelFile") as excel:
        body = json.loads(upload_views.samplesValidate(request).content)
    assert body["status"] == 0
    connect.assert_not_called()
    excel.assert_not_called()


def test_sample_find_refuses_a_bomb_before_building_an_export():
    request = RequestFactory().post("/", {"excelfile_find": SimpleUploadedFile("a.xlsx", _bomb())})
    request.user = MEMBER
    request.session = {}
    with patch.object(samples_views, "_exportLogin", return_value={"status": True}), \
            patch.object(samples_views, "newExport") as new_export, \
            patch.object(samples_views, "DBtable_sample") as table:
        body = json.loads(samples_views.sampleFindAjax(request).content)
    assert body["status"] == 0
    new_export.assert_not_called()
    table.assert_not_called()
