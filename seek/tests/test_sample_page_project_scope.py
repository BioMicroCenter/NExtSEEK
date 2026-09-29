"""The sample page, ``/seek/sample/id=N/`` (and ``/seek/sample/uid=X/``), is project-scoped.

It prints every metadata value of the sample, read straight from MySQL by id (``getSampleInfo``), so it must apply the
rule every other sample surface applies: a caller who is not a superuser sees only a sample in one of their projects,
and a sample outside them answers exactly as a sample that does not exist, so the page tells nobody which ids are
real. A superuser is unscoped. Membership is ``graph_search.scope.resolve_scope`` (the same helper graph_search and
the lineage endpoints use); a sample's projects are ``projects_samples``, through
``nextseek_api.views._samples_visible_to_projects``. Sample Search links every result row here, so a sample in the
caller's projects must render as before.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import Http404
from django.test import RequestFactory

import seek.views.samples  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

_MOD = "seek.views.samples"
MEMBER = Scope(is_admin=False, person_id=7, project_ids=(2, 13))
ADMIN = Scope(is_admin=True, person_id=None, project_ids=())
VISIBLE_ID, FOREIGN_ID, MISSING_ID = 101, 202, 999999

INFO = ({"UID": "TIS-1", "Organ": "Lung"},
        [{"attrname": "UID", "attrvalue": "TIS-1"}, {"attrname": "Organ", "attrvalue": "Lung"}])


def _request(superuser=False):
    request = RequestFactory().get("/seek/sample/id=1/")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=superuser, username="member")
    request.session = {}
    return request


def _visible(sample_ids, project_ids):
    """projects_samples as the fixture has it: only VISIBLE_ID is in project 2."""
    return {str(s) for s in sample_ids if str(s) == str(VISIBLE_ID) and 2 in tuple(project_ids)}


@pytest.fixture
def page():
    """Run the view with SEEK, MySQL and the template stubbed; hand back what it did."""
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True}
    seekdb.getPageRequests.return_value = "<p>seek page</p>"
    dbsample = MagicMock()
    dbsample.getSampleInfo.side_effect = lambda sid: INFO if int(sid or 0) in (VISIBLE_ID, FOREIGN_ID) else (None, None)
    dbsample.getSampleID.side_effect = {"TIS-1": VISIBLE_ID, "TIS-FOREIGN": FOREIGN_ID}.get
    rendered = {}

    def _render(request, template, context):
        rendered["template"], rendered["context"] = template, context
        return SimpleNamespace(status_code=200, context=context)

    with patch(f"{_MOD}.SeekDB", return_value=seekdb), \
            patch(f"{_MOD}.DBtable_sample", return_value=dbsample), \
            patch(f"{_MOD}.render", side_effect=_render), \
            patch(f"{_MOD}.resolve_scope", create=True) as scope, \
            patch("nextseek_api.views._samples_visible_to_projects", side_effect=_visible) as visible:
        scope.return_value = MEMBER
        yield SimpleNamespace(seekdb=seekdb, dbsample=dbsample, rendered=rendered, scope=scope, visible=visible)


def _outcome(call):
    """('page', context) when the view rendered, ('404', message) when it raised Http404."""
    try:
        return "page", call().context
    except Http404 as exc:
        return "404", str(exc)


def test_member_sees_a_sample_in_their_projects_as_before(page):
    resp = seek.views.samples.sample(_request(), VISIBLE_ID)

    assert resp.status_code == 200
    assert page.rendered["context"]["report"]["sampleinfo"] == INFO[1]
    assert page.rendered["context"]["bodyhtml"] == "<p>seek page</p>"
    page.visible.assert_called_once_with([VISIBLE_ID], MEMBER.project_ids)


def test_member_asking_for_a_sample_outside_their_projects_is_not_found(page):
    with pytest.raises(Http404):
        seek.views.samples.sample(_request(), FOREIGN_ID)

    page.dbsample.getSampleInfo.assert_not_called()
    page.seekdb.getPageRequests.assert_not_called()
    assert page.rendered == {}


def test_a_foreign_sample_answers_exactly_as_a_missing_one(page):
    foreign = _outcome(lambda: seek.views.samples.sample(_request(), FOREIGN_ID))
    missing = _outcome(lambda: seek.views.samples.sample(_request(), MISSING_ID))

    assert foreign == missing
    assert foreign[0] == "404"
    page.dbsample.getSampleInfo.assert_not_called()
    page.seekdb.getPageRequests.assert_not_called()


def test_by_uid_a_foreign_sample_answers_exactly_as_an_unknown_uid(page):
    foreign = _outcome(lambda: seek.views.samples.sampleTree(_request(), "TIS-FOREIGN"))
    unknown = _outcome(lambda: seek.views.samples.sampleTree(_request(), "TIS-NOPE"))

    assert foreign == unknown
    assert foreign[0] == "404"
    page.dbsample.getSampleInfo.assert_not_called()


def test_by_uid_a_member_still_reaches_a_sample_in_their_projects(page):
    resp = seek.views.samples.sampleTree(_request(), "TIS-1")

    assert resp.status_code == 200
    assert page.rendered["context"]["report"]["sample_id"] == VISIBLE_ID


def test_a_member_of_no_project_sees_nothing(page):
    page.scope.return_value = Scope(is_admin=False, person_id=8, project_ids=())

    with pytest.raises(Http404):
        seek.views.samples.sample(_request(), VISIBLE_ID)
    page.dbsample.getSampleInfo.assert_not_called()


@pytest.mark.parametrize("failure", [ScopeUnavailable("no person"), RuntimeError("membership read failed")])
def test_a_caller_whose_scope_cannot_be_resolved_is_not_found(page, failure):
    page.scope.side_effect = failure

    with pytest.raises(Http404):
        seek.views.samples.sample(_request(), VISIBLE_ID)
    page.dbsample.getSampleInfo.assert_not_called()


def test_a_superuser_sees_any_sample_unscoped(page):
    page.scope.return_value = ADMIN

    resp = seek.views.samples.sample(_request(superuser=True), FOREIGN_ID)

    assert resp.status_code == 200
    assert page.rendered["context"]["report"]["sampleinfo"] == INFO[1]
    page.visible.assert_not_called()


def test_the_login_redirect_is_unchanged(page):
    page.seekdb.getSeekLogin.return_value = {"status": False, "err": "no login"}

    resp = seek.views.samples.sample(_request(), FOREIGN_ID)

    assert resp.status_code == 302
    assert resp.url == f"/login/?next=/seek/sample/id={FOREIGN_ID}/"
    page.scope.assert_not_called()
