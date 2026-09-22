"""Sample export files never land where ``/media/`` serves them, and only their maker can fetch them.

``dmac/urls.py`` serves ``MEDIA_ROOT`` to anyone at ``/media/`` (Django's static serve, no login; nginx proxies it). The
exports used to be written there, under ``download/``, with a per-minute name: ``download-samples-<minute>.xlsx``
from the admin retrieve page and ``sampleDownload``, ``samples-export<minute>`` from ``sampleExport`` and
``sampleFindAjax``, ``samples-deletion<minute>.xls`` from ``sampleDelete``, and the ImmPort sheets under fixed names.
The name was the only thing in front of an export, a superuser's unscoped one included, and two exports made in the
same minute shared one file.

Now:

* the admin retrieve page (``adminRetrieveSamples``) writes a private temporary file outside ``MEDIA_ROOT``, answers
  with its bytes and removes it;
* the views whose page opens a returned ``link`` write into a private store (``seek.views.exports.newExport``): one
  directory per export, named by a random token, outside ``MEDIA_ROOT``, and the link is
  ``/seek/exports/<token>/<file>``, which ``exportFile`` streams only to the caller who made the export or to a
  superuser. Anyone else, an unknown token and a bad name all get the same 404; an anonymous caller is sent to log in.

Hermetic: SEEK, MySQL, Neo4j and the workbook writers are stubbed; files go to a temporary directory.
"""

import importlib
import json
import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, override_settings

import seek.views.admin  # noqa: F401  -- so @patch can resolve the target
import seek.views.samples  # noqa: F401
from nextseek_api.graph_search.scope import Scope
from seek.sample.table import DBtable_sample

OWNER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)
OTHER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="other", pk=8)
ADMIN = SimpleNamespace(is_authenticated=True, is_superuser=True, username="admin", pk=1)
ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)

LINK = re.compile(r"^/seek/exports/(?P<token>[0-9a-f]{32})/(?P<name>[\w.-]+)$")
WORKBOOK = b"PK\x03\x04a-workbook"


@pytest.fixture
def roots(tmp_path):
    """MEDIA_ROOT and the private export root, both temporary; hands back (media, exports)."""
    media, exports = tmp_path / "media", tmp_path / "exports"
    (media / "download").mkdir(parents=True)
    with override_settings(MEDIA_ROOT=str(media), NEXTSEEK_EXPORT_ROOT=str(exports)):
        yield media, exports


def _inside(path, root):
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(str(root))]) == os.path.realpath(str(root))


def _request(user, path="/", method="post", data=None):
    factory = RequestFactory()
    request = factory.get(path, data or {}) if method == "get" else factory.post(path, data or {})
    request.user = user
    request.session = {}
    return request


# --------------------------------------------------------------------------- the admin retrieve page


def _retrieve_page(media, written):
    """POST /seek/admin/retrieve/ as a superuser; the workbook writer records the path it is handed."""
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True}
    seekdb.getCurrentUser.return_value = {"data": {"relationships": {"projects": {"data": [{"id": "2"}]}}}}

    def _write(_df, path):
        written.append(path)
        with open(path, "wb") as fh:
            fh.write(WORKBOOK)

    frame = pd.DataFrame([{"id": 1, "sample_type_id": 1, "uuid": "TIS-1", "json_metadata": "{}"}])
    with patch("seek.views.admin.SeekDB", return_value=seekdb), \
            patch("seek.views.admin.verifySuperUser", return_value=1), \
            patch("seek.views.admin.get_children_uids", return_value=frame), \
            patch("seek.views.admin.sample_retrieval_data", side_effect=_write), \
            patch("seek.views.admin.DOWNLOAD_DIRECTORY", str(media / "download") + "/", create=True):
        return seek.views.admin.adminRetrieveSamples(
            _request(ADMIN, "/seek/admin/retrieve/", data={"retrieval_uids": "TIS-1"}))


def test_the_retrieve_page_leaves_no_workbook_where_media_is_served(roots):
    media, _ = roots
    written = []

    first = _retrieve_page(media, written)
    second = _retrieve_page(media, written)

    assert first.content == second.content == WORKBOOK
    assert len(set(written)) == 2, "two exports in the same minute must not share a file"
    for path in written:
        assert not _inside(path, media)
        assert not os.path.exists(path), "the workbook must not outlive the response"
    assert list((media / "download").iterdir()) == []


# --------------------------------------------------------------------------- the link-returning views


@pytest.fixture
def export_world(roots):
    media, exports = roots
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "username": "member", "password": "x", "server": "s"}
    dbsample = MagicMock()
    calls = []

    def _record(name, path_index, link_index):
        def _export(*args, **_kwargs):
            path, link = args[path_index], args[link_index]
            calls.append(SimpleNamespace(view=name, path=path, link=link))
            with open(path, "wb") as fh:
                fh.write(WORKBOOK)
            return json.dumps({"msg": "okay", "status": 1, "link": link})
        return _export

    dbsample.parseSampleIDs.side_effect = lambda ids: {"TIS": list(ids)}
    dbsample.downloadSamples_new.side_effect = _record("download", 1, 2)
    dbsample.exportSamples.side_effect = _record("export", 1, 2)
    dbsample.findSamplesForExport.side_effect = _record("find", 1, 2)
    dbsample.deleteSamples.side_effect = _record("delete", 1, 2)
    with patch("seek.views.samples.SeekDB", return_value=seekdb), \
            patch("seek.views.samples.DBtable_sample", return_value=dbsample), \
            patch("seek.views.samples.resolve_scope", return_value=Scope(False, 7, (2,))), \
            patch("nextseek_api.views._samples_visible_to_projects", return_value={"101"}), \
            patch("seek.views.samples.DOWNLOAD_DIRECTORY", str(media / "download") + "/", create=True):
        yield SimpleNamespace(media=media, exports=exports, calls=calls)


def _run(view, user=OWNER):
    if view == "download":
        return seek.views.samples.sampleDownload(_request(user, data={
            "includeSampleTree": "0", "allids": "[101]", "sampletype_id": "1"}))
    if view == "export":
        return seek.views.samples.sampleExport(_request(user, data={"allids": "[101]", "sampletype_id": "1"}))
    if view == "find":
        sheet = SimpleUploadedFile("uids.xlsx", b"PK\x03\x04", content_type="application/vnd.ms-excel")
        return seek.views.samples.sampleFindAjax(_request(user, data={"excelfile_find": sheet}))
    return seek.views.samples.sampleDelete(_request(user, data={"allids": "[101]"}))


@pytest.mark.parametrize("view", ["download", "export", "find", "delete"])
def test_an_export_is_written_to_a_private_store_and_linked_there(export_world, view):
    body = json.loads(_run(view).content)
    call = export_world.calls[-1]

    assert not _inside(call.path, export_world.media), call.path
    assert _inside(call.path, export_world.exports), call.path
    match = LINK.match(body["link"])
    assert match, body["link"]
    assert call.path == os.path.join(str(export_world.exports), match["token"], match["name"])
    assert not body["link"].startswith(settings.MEDIA_URL)
    assert list((export_world.media / "download").iterdir()) == []


@pytest.mark.parametrize("view", ["download", "export", "find", "delete"])
def test_two_exports_in_the_same_minute_never_share_a_file(export_world, view):
    _run(view)
    _run(view)

    first, second = export_world.calls[-2:]
    assert first.path != second.path and first.link != second.link


# --------------------------------------------------------------------------- the ImmPort sheets


def test_the_immport_sheets_are_written_beside_their_zip_not_under_media(roots, tmp_path):
    media, _ = roots
    written = []
    constants = importlib.import_module("seek.sample.constants")
    sheets = {name.upper(): [] for name in constants.IMMPORT_TEMPLATES}

    def _sheet(_self, _user, _mapping, _rows, _filedata, _name, sheetfile, _label, _zf):
        written.append(sheetfile)
        with open(sheetfile, "w") as fh:
            fh.write("sheet")

    target = tmp_path / "private" / "samples-export.zip"
    target.parent.mkdir()
    with patch("seek.sample.immport.os.system"), \
            patch("seek.sample.immport.load_excelfile_asdic", return_value=dict(sheets, sheetnames=list(sheets))), \
            patch("seek.sample.immport.DOWNLOAD_DIRECTORY", str(media / "download") + "/", create=True), \
            patch.object(DBtable_sample, "_exportImmportSheetInfoZip", autospec=True, side_effect=_sheet):
        DBtable_sample.__new__(DBtable_sample)._exportImmportSampleListZip({}, [], [], str(target), "D.MSP", {})

    assert written and all(os.path.dirname(path) == str(target.parent) for path in written), written
    assert list((media / "download").iterdir()) == []


# --------------------------------------------------------------------------- exportFile


def _exports():
    return importlib.import_module("seek.views.exports")


def _made_by(user, name="download-samples-2026-01-01_00-00.xlsx"):
    path, link = _exports().newExport(_request(user), name)
    with open(path, "wb") as fh:
        fh.write(WORKBOOK)
    match = LINK.match(link)
    return match["token"], match["name"], link


def _fetch(user, token, name):
    return _exports().exportFile(_request(user, "/seek/exports/%s/%s" % (token, name), method="get"), token, name)


def _outcome(call):
    try:
        response = call()
    except Http404:
        return 404, None
    if response.status_code == 200:
        return 200, b"".join(response.streaming_content)
    return response.status_code, response.get("Location")


@pytest.mark.parametrize("user", [OWNER, ADMIN], ids=["its-maker", "a-superuser"])
def test_an_export_is_streamed_to_its_maker_and_to_a_superuser(roots, user):
    token, name, _ = _made_by(OWNER)

    assert _outcome(lambda: _fetch(user, token, name)) == (200, WORKBOOK)


def test_another_caller_gets_exactly_what_an_unknown_export_gets(roots):
    token, name, _ = _made_by(OWNER)

    someone_elses = _outcome(lambda: _fetch(OTHER, token, name))
    unknown = _outcome(lambda: _fetch(OTHER, "0" * 32, name))

    assert someone_elses == unknown == (404, None)


def test_an_anonymous_caller_is_sent_to_log_in(roots):
    token, name, link = _made_by(OWNER)

    assert _outcome(lambda: _fetch(ANONYMOUS, token, name)) == (302, "/login/?next=" + link)


@pytest.mark.parametrize("name", [".owner", "..", "../x.xlsx", "missing.xlsx"])
def test_a_bad_or_missing_name_is_not_found(roots, name):
    token, _, _ = _made_by(OWNER)

    assert _outcome(lambda: _fetch(OWNER, token, name)) == (404, None)


def test_the_route_resolves_to_the_export_view():
    from django.urls import resolve

    match = resolve("/seek/exports/%s/download-samples-2026-01-01_00-00.xlsx" % ("a" * 32))

    assert match.func is _exports().exportFile
    assert match.kwargs == {"token": "a" * 32, "filename": "download-samples-2026-01-01_00-00.xlsx"}


def test_exports_older_than_a_day_are_removed_when_the_next_one_is_made(roots):
    _, exports = roots
    old_token, _, _ = _made_by(OWNER)
    stale = os.path.join(str(exports), old_token)
    os.utime(stale, (0, 0))

    _made_by(OWNER)

    assert not os.path.exists(stale)
