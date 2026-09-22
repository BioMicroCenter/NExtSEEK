"""The legacy sample exports need a login and read only the caller's samples.

``/seek/samples/download/`` (``sampleDownload``), ``/seek/samples/export/`` (``sampleExport``, the ImmPort export) and
``/seek/samplefind/`` (``sampleFindAjax``, the same export driven by a workbook of UIDs) write the metadata of the
samples they are given, by SEEK id or UID, into a workbook and answer with its link. None of them checked a login or a
project: any caller, one not logged in included, could export any sample, and with ``includeSampleTree=1`` its whole
lineage.

The rule every other sample surface applies (``seek.views.samples._sampleVisible`` for the sample page, H2): a caller
who is not a superuser sees only samples in one of their projects, an anonymous caller sees nothing, and a superuser is
unscoped. Membership is ``graph_search.scope.resolve_scope`` (``is_superuser`` alone, never ``is_staff``), a sample's
projects are ``projects_samples`` through ``nextseek_api.views._samples_visible_to_projects``. A requested id outside
the caller's projects is dropped exactly as an unknown one is, before anything is read, and the lineage the export
walks keeps only the caller's samples (``DBtable_sample.restrictToProjects``).

The login is ``request.user``, not ``getSeekLogin``'s status: on a POST that status comes from ``username`` and
``password`` fields in the body and is not checked against SEEK, so it is True for anyone who sends two non-empty
fields.

Hermetic: SEEK, MySQL and the workbook writers are stubbed.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

import seek.views.samples  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.graph_search.scope import Scope, ScopeUnavailable

_MOD = "seek.views.samples"
MEMBER_SCOPE = Scope(is_admin=False, person_id=7, project_ids=(2, 13))
ADMIN_SCOPE = Scope(is_admin=True, person_id=None, project_ids=())
VISIBLE_ID, FOREIGN_ID, MISSING_ID = 101, 202, 999999

ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)
MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)
ADMIN = SimpleNamespace(is_authenticated=True, is_superuser=True, username="admin", pk=1)

EXPORTED = json.dumps({"msg": "okay", "status": 1, "link": "/somewhere"})


def _scope_of(user):
    """resolve_scope as it behaves: a superuser short-circuits, a login with no SEEK person raises."""
    if user.is_superuser:
        return ADMIN_SCOPE
    if not user.username:
        raise ScopeUnavailable("Cannot determine project scope for this caller")
    return MEMBER_SCOPE


def _visible(sample_ids, project_ids):
    """projects_samples as the fixture has it: only VISIBLE_ID is in project 2; MISSING_ID does not exist."""
    return {str(s) for s in sample_ids if str(s) == str(VISIBLE_ID) and 2 in tuple(project_ids)}


@pytest.fixture
def world():
    seekdb = MagicMock()
    # True even for an anonymous caller: a POST with username/password fields gets exactly this from getSeekLogin.
    seekdb.getSeekLogin.return_value = {"status": True, "username": "someone", "password": "x", "server": "s"}
    dbsample = MagicMock()
    dbsample.parseSampleIDs.side_effect = lambda ids: {"TIS": list(ids)} if ids else {}
    dbsample.downloadSamples_new.return_value = EXPORTED
    dbsample.downloadSamples_noTree.return_value = EXPORTED
    dbsample.exportSamples.return_value = EXPORTED
    dbsample.findSamplesForExport.return_value = EXPORTED
    with patch(f"{_MOD}.SeekDB", return_value=seekdb), \
            patch(f"{_MOD}.DBtable_sample", return_value=dbsample), \
            patch(f"{_MOD}.resolve_scope", side_effect=_scope_of) as scope, \
            patch("nextseek_api.views._samples_visible_to_projects", side_effect=_visible) as visible:
        yield SimpleNamespace(seekdb=seekdb, dbsample=dbsample, scope=scope, visible=visible)


def _request(path, params, user, method="post", files=None):
    factory = RequestFactory()
    if method == "get":
        request = factory.get(path, params)
    else:
        request = factory.post(path, dict(params, **(files or {})))
    request.user = user
    request.session = {}
    return request


def _download(user, ids, method="post", **extra):
    params = {"includeSampleTree": "0", "allids": json.dumps(ids), "sampletype_id": "1", **extra}
    return seek.views.samples.sampleDownload(_request("/seek/samples/download/", params, user, method))


def _export(user, ids, method="post", **extra):
    params = {"allids": json.dumps(ids), "sampletype_id": "1", **extra}
    return seek.views.samples.sampleExport(_request("/seek/samples/export/", params, user, method))


def _find(user):
    sheet = SimpleUploadedFile("uids.xlsx", b"PK\x03\x04not-really", content_type="application/vnd.ms-excel")
    return seek.views.samples.sampleFindAjax(_request("/seek/samplefind/", {}, user, files={"excelfile_find": sheet}))


def _body(response):
    return json.loads(response.content)


def _nothing_exported(world):
    for name in ("parseSampleIDs", "downloadSamples_new", "downloadSamples_noTree", "exportSamples",
                 "findSamplesForExport"):
        getattr(world.dbsample, name).assert_not_called()


# --------------------------------------------------------------------------- anonymous


@pytest.mark.parametrize("method", ["get", "post"])
def test_an_anonymous_download_is_refused_before_anything_is_read(world, method):
    body = _body(_download(ANONYMOUS, [VISIBLE_ID], method=method))

    assert body["status"] == 0
    assert body["msg"] == seek.views.samples.LOGIN_REQUIRED
    assert body["link"] == ""
    _nothing_exported(world)


def test_an_anonymous_download_with_seek_credentials_in_the_body_is_still_refused(world):
    body = _body(_download(ANONYMOUS, [VISIBLE_ID], username="someone", password="guess"))

    assert body["status"] == 0 and body["msg"] == seek.views.samples.LOGIN_REQUIRED
    _nothing_exported(world)


@pytest.mark.parametrize("method", ["get", "post"])
def test_an_anonymous_export_is_refused_before_anything_is_read(world, method):
    body = _body(_export(ANONYMOUS, [VISIBLE_ID], method=method))

    assert body["status"] == 0 and body["msg"] == seek.views.samples.LOGIN_REQUIRED
    _nothing_exported(world)


def test_an_anonymous_workbook_lookup_is_refused_before_anything_is_read(world):
    body = _body(_find(ANONYMOUS))

    assert body["status"] == 0 and body["msg"] == seek.views.samples.LOGIN_REQUIRED
    _nothing_exported(world)


# --------------------------------------------------------------------------- a member


def test_a_member_downloads_only_the_samples_in_their_projects(world):
    _download(MEMBER, [VISIBLE_ID, FOREIGN_ID])

    assert world.dbsample.downloadSamples_new.call_args.args[3] == [VISIBLE_ID]
    world.visible.assert_called_once_with([VISIBLE_ID, FOREIGN_ID], MEMBER_SCOPE.project_ids)


def test_a_members_download_walks_only_their_projects(world):
    _download(MEMBER, [VISIBLE_ID], includeSampleTree="1")

    world.dbsample.restrictToProjects.assert_called_once_with(MEMBER_SCOPE.project_ids)
    assert world.dbsample.method_calls[0][0] == "restrictToProjects"


def test_the_filtered_tree_download_also_drops_foreign_ids(world):
    _download(MEMBER, [VISIBLE_ID, FOREIGN_ID], includeSampleTree="1", attributeFilter="TIS:Organ,")

    assert world.dbsample.downloadSamples_noTree.call_args.args[3] == [VISIBLE_ID]


def test_a_foreign_id_downloads_exactly_as_a_missing_one(world):
    foreign = _body(_download(MEMBER, [FOREIGN_ID]))
    missing = _body(_download(MEMBER, [MISSING_ID]))

    assert foreign == missing
    assert foreign["status"] == 0
    _nothing_exported(world)


def test_a_mixed_download_answers_as_if_the_foreign_id_were_missing(world):
    with_foreign = _body(_download(MEMBER, [VISIBLE_ID, FOREIGN_ID]))
    first = world.dbsample.downloadSamples_new.call_args
    with_missing = _body(_download(MEMBER, [VISIBLE_ID, MISSING_ID]))

    assert with_foreign == with_missing
    assert first.args[3] == world.dbsample.downloadSamples_new.call_args.args[3] == [VISIBLE_ID]


def test_a_member_exports_only_the_samples_in_their_projects(world):
    _export(MEMBER, [VISIBLE_ID, FOREIGN_ID])

    assert world.dbsample.exportSamples.call_args.args[3] == [VISIBLE_ID]
    world.dbsample.restrictToProjects.assert_called_once_with(MEMBER_SCOPE.project_ids)


def test_a_foreign_id_exports_exactly_as_a_missing_one(world):
    foreign = _body(_export(MEMBER, [FOREIGN_ID]))
    missing = _body(_export(MEMBER, [MISSING_ID]))

    assert foreign == missing and foreign["status"] == 0
    _nothing_exported(world)


def test_a_members_workbook_lookup_is_limited_to_their_projects(world):
    _find(MEMBER)

    world.dbsample.restrictToProjects.assert_called_once_with(MEMBER_SCOPE.project_ids)
    world.dbsample.findSamplesForExport.assert_called_once()
    assert world.dbsample.method_calls[0][0] == "restrictToProjects"


def test_a_member_of_no_project_exports_nothing(world):
    world.scope.side_effect = lambda user: Scope(is_admin=False, person_id=8, project_ids=())

    assert _body(_download(MEMBER, [VISIBLE_ID]))["status"] == 0
    assert _body(_export(MEMBER, [VISIBLE_ID]))["status"] == 0
    _nothing_exported(world)


@pytest.mark.parametrize("failure", [ScopeUnavailable("no person"), RuntimeError("membership read failed")])
def test_a_caller_whose_scope_cannot_be_resolved_exports_nothing(world, failure):
    world.scope.side_effect = failure

    assert _body(_download(MEMBER, [VISIBLE_ID]))["status"] == 0
    assert _body(_export(MEMBER, [VISIBLE_ID]))["status"] == 0
    _find(MEMBER)
    _nothing_exported(world)


# --------------------------------------------------------------------------- a superuser


def test_a_superuser_downloads_any_sample_unscoped(world):
    _download(ADMIN, [VISIBLE_ID, FOREIGN_ID, MISSING_ID])

    assert world.dbsample.downloadSamples_new.call_args.args[3] == [VISIBLE_ID, FOREIGN_ID, MISSING_ID]
    world.dbsample.restrictToProjects.assert_called_once_with(None)
    world.visible.assert_not_called()


def test_a_superuser_exports_any_sample_unscoped(world):
    _export(ADMIN, [FOREIGN_ID])

    assert world.dbsample.exportSamples.call_args.args[3] == [FOREIGN_ID]
    world.dbsample.restrictToProjects.assert_called_once_with(None)
    world.visible.assert_not_called()
