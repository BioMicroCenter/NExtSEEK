"""The Simple/Advanced search UI must scope to EVERY project the caller belongs to.

Observed on production 2026-08-21: charlie-test-3 searched sample type NHP on both
the Simple and the Advanced tab and got "No Samples", while 706 of the 725 NHP
samples sit in IMPAcTb, a project that account belongs to.

Root cause is upstream of the search itself. ``SeekDB.__getFeatureInfo`` takes
``featureData[defaultIndex]`` with ``defaultIndex=0`` (seek/seekdb.py:56), so
``user_seek['projectid']`` is only ever the FIRST project SEEK lists.
``GET /people/current`` for that account returns ``['13', '2']`` -- project 13 is
TestProject_250820, which holds zero samples -- so the UI scoped every search to an
empty project and returned nothing for every sample type, not just NHP.

The list of all projects was already sitting in ``user_seek['projectOptions']``
(seek/seekdb.py:64-77) and ``searchAdvanced`` already accepts a
``scoped_project_ids`` list applied as EXISTS. The UI path simply never used
either one.

Scoping by the list also drops the UI's exposure to the many-to-many row
inflation that forced EXISTS over the ``projects_samples`` join for the API
(measured 2026-08-20 on prod data: project_id=3 gave 5134 rows against 5122
unscoped). EXISTS cannot multiply rows.
"""

from types import SimpleNamespace
from unittest import mock

import pytest


def _request():
    """A request stub. ``runSampleSearch`` only reads ``.GET``; the admin decision
    comes from ``verifySuperUser``, which every test here patches explicitly."""
    return SimpleNamespace(GET={}, user=SimpleNamespace(is_authenticated=True))


def _user_seek(project_ids, first=None):
    """Shape a ``getSeekLogin`` return the way SeekDB builds it.

    ``projectid`` is deliberately the FIRST id, mirroring seekdb.py:56, so a test
    that only satisfies ``projectid`` cannot accidentally pass.
    """
    options = [{"id": str(pid), "title": "P%s" % pid} for pid in project_ids]
    return {
        "status": True,
        "projectid": first if first is not None else (project_ids[0] if project_ids else 0),
        "projectOptions": options,
    }


@pytest.fixture
def captured():
    """Patch the three collaborators and hand back the call recorded on searchAdvanced."""
    from seek import views as views_module

    calls = {}

    class _FakeSample:
        def searchAdvanced(self, user_seek, filters, searchType, project_id=0,
                           skip_tree=False, scoped_project_ids=None):
            calls["project_id"] = project_id
            calls["scoped_project_ids"] = scoped_project_ids
            calls["searchType"] = searchType
            return "{}"

    def _run(user_seek, is_supervisor):
        fake_db = mock.Mock()
        fake_db.getSeekLogin.return_value = user_seek
        with mock.patch.object(views_module, "SeekDB", return_value=fake_db), \
             mock.patch.object(views_module, "verifySuperUser", return_value=is_supervisor), \
             mock.patch.object(views_module, "DBtable_sample", _FakeSample):
            views_module.runSampleSearch(_request(), "Advanced")
        return calls

    return _run


def test_multi_project_user_is_scoped_to_all_their_projects(captured):
    """The production repro: two projects, the first of which is empty.

    Scoping to ``projectid`` alone (13) returns nothing. Both ids must reach the
    query for the caller to see their IMPAcTb samples.
    """
    calls = captured(_user_seek(["13", "2"]), is_supervisor=0)

    assert calls["scoped_project_ids"] == ["13", "2"], (
        "the UI dropped every project after the first -- this is the NHP blackout"
    )
    # The legacy single-project argument must not ALSO be applied; it would AND a
    # second, narrower predicate on top of the list and re-create the bug.
    assert calls["project_id"] == 0


def test_single_project_user_still_scoped(captured):
    calls = captured(_user_seek(["2"]), is_supervisor=0)

    assert calls["scoped_project_ids"] == ["2"]
    assert calls["project_id"] == 0


def test_supervisor_is_unscoped(captured):
    """None, not [] -- ``searchAdvanced`` reads [] as 'match nothing'."""
    calls = captured(_user_seek(["13", "2"]), is_supervisor=1)

    assert calls["scoped_project_ids"] is None


def test_caller_with_no_projects_matches_nothing_rather_than_everything(captured):
    """Regression guard for a fail-OPEN hole in the pre-fix code.

    Before the fix a non-supervisor whose SEEK person had no project got
    ``projectid = 0`` (seek/seekdb.py:106), and the builder applies the legacy
    project predicate only ``if int(project_id) > 0`` -- so the search ran with no
    scope at all and that caller read every project. ``runSampleSearch`` never
    checked ``user_seek['status']``, which SeekDB sets False in exactly that case.

    An empty list is the fail-closed signal and must not degrade to None.
    """
    calls = captured(_user_seek([], first=0), is_supervisor=0)

    assert calls["scoped_project_ids"] == []
    assert calls["scoped_project_ids"] is not None


def test_projectOptions_missing_falls_back_to_the_single_project(captured):
    """If SEEK gave us projectid but no options, keep scoping instead of blacking out.

    Returning [] here would turn a transient SEEK hiccup into a total blackout for
    a caller we *can* scope; returning None would silently unscope them. Neither is
    acceptable, so fall back to the one id we have.
    """
    user_seek = {"status": True, "projectid": "2"}
    calls = captured(user_seek, is_supervisor=0)

    assert calls["scoped_project_ids"] == ["2"]


def test_project_ids_are_deduped_and_stringified(captured):
    """searchAdvanced stringifies ids for binding; duplicates would just bloat the IN list."""
    user_seek = {
        "status": True,
        "projectid": 2,
        "projectOptions": [{"id": 2}, {"id": "2"}, {"id": 13}],
    }
    calls = captured(user_seek, is_supervisor=0)

    assert calls["scoped_project_ids"] == ["2", "13"]
