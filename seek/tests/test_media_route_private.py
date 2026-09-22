"""``/media/`` needs a login and serves only the tree the application links to; upload feedback is its maker's alone.

``dmac/urls.py`` routed ``/media/`` to Django's static serve over all of ``MEDIA_ROOT``, with no login; nginx has no
``/media`` location, so every request reached it. ``MEDIA_ROOT`` holds the application's working files, named by the
minute, the second or the user id: the sample upload page's feedback workbook (``download/<sheet>_feedback-<minute>
.xls``, whose link the page opens) and its copy of the uploaded sheet (``uploads/<sheet>_v<minute>.<ext>``), batch
upload's files, reports, checkpoints and per-user job index, and the chat upload staging. A name was the only thing
standing in front of any of them.

Now:

* ``/media/`` sends an anonymous caller to the login page;
* a logged-in caller gets only the legacy data-file store the code builds ``/media/`` links to
  (``SEEK_DATAFILE_ROOT_WEBLINK``); every other path answers 404, as a missing file does, a superuser's included;
* the upload page's feedback workbook goes to the private export store (``seek.views.exports``), so its link streams
  only to the caller who uploaded the sheet, or to a superuser.

Hermetic: MEDIA_ROOT and the export root are temporary directories; SEEK and the sheet upload are stubbed.
"""

import json
import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, override_settings

import seek.views.upload  # noqa: F401  -- so @patch can resolve the target
from seek.views.exports import exportFile

OWNER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)
OTHER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="other", pk=8)
ADMIN = SimpleNamespace(is_authenticated=True, is_superuser=True, username="admin", pk=1)
ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)

LINK = re.compile(r"^/seek/exports/(?P<token>[0-9a-f]{32})/(?P<name>[\w.-]+)$")

PRIVATE_FILES = (
    "download/samples_feedback-2026-09-18_22-00.xls",
    "uploads/samples_v2026-09-18_22-00.xlsx",
    "batch_upload_uploads/1790000000_samples.xlsx",
    "batch_upload_reports/summary_00000000-0000-4000-8000-000000000000.csv",
    "celery_jobs/7.json",
    "cc_upload_staging/1790000000000_notes.txt",
    "reserved/SAMPLE_TEMPLATE.xlsx",
)
SHARED_FILE = "uploads/production/Project_A/LAB/TIS-1_data.csv"


def _request(user, path, method="get", data=None):
    factory = RequestFactory()
    request = factory.post(path, data or {}) if method == "post" else factory.get(path, data or {})
    request.user = user
    request.session = {}
    return request


@pytest.fixture
def media(tmp_path):
    """A MEDIA_ROOT holding one file in every tree the application writes, and the private export root."""
    root, exports = tmp_path / "media", tmp_path / "exports"
    for name in PRIVATE_FILES + (SHARED_FILE,):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"contents of " + name.encode())
    with override_settings(MEDIA_ROOT=str(root), MEDIA_URL="/media/", NEXTSEEK_EXPORT_ROOT=str(exports),
                           SEEK_DATAFILE_ROOT=str(root / "uploads" / "production") + "/",
                           SEEK_DATAFILE_ROOT_WEBLINK="/media/uploads/production/"):
        yield SimpleNamespace(root=root, exports=exports)


def _serve(user, name):
    from dmac.media import serve_media
    return serve_media(_request(user, "/media/" + name), name)


def _body(response):
    return b"".join(response.streaming_content)


# --------------------------------------------------------------------------- /media/


def test_the_media_route_is_the_login_checked_view():
    from django.urls import resolve

    from dmac.media import serve_media

    assert resolve("/media/download/anything.xls").func is serve_media


@pytest.mark.parametrize("name", PRIVATE_FILES + (SHARED_FILE,))
def test_an_anonymous_caller_is_sent_to_log_in(media, name):
    response = _serve(ANONYMOUS, name)

    assert response.status_code == 302
    assert response["Location"].startswith("/login/?next=/media/")


@pytest.mark.parametrize("user", [OWNER, ADMIN], ids=["member", "superuser"])
@pytest.mark.parametrize("name", PRIVATE_FILES)
def test_no_logged_in_caller_can_fetch_a_working_file(media, user, name):
    with pytest.raises(Http404):
        _serve(user, name)


@pytest.mark.parametrize("name", [
    "uploads/production/../../celery_jobs/7.json",
    "uploads/production/../samples_v2026-09-18_22-00.xlsx",
    "/celery_jobs/7.json",
    "uploads/production",
])
def test_the_shared_tree_cannot_be_walked_out_of(media, name):
    with pytest.raises(Http404):
        _serve(OWNER, name)


def test_a_logged_in_caller_still_gets_the_legacy_data_file_store(media):
    response = _serve(OWNER, SHARED_FILE)

    assert response.status_code == 200
    assert _body(response) == b"contents of " + SHARED_FILE.encode()


def test_nothing_is_served_when_the_data_file_links_are_not_under_media(media):
    with override_settings(SEEK_DATAFILE_ROOT_WEBLINK="/uploads/"):
        with pytest.raises(Http404):
            _serve(OWNER, SHARED_FILE)


# --------------------------------------------------------------------------- the upload page's feedback workbook


@pytest.fixture
def upload(media):
    """POST /seek/sampleupload/ as OWNER; the sheet upload writes the feedback workbook it is handed."""
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": True, "username": "member", "password": "x", "server": "s"}
    written = []

    def _batch_upload(_excel, feedbackfile, _seekdb):
        written.append(feedbackfile)
        with open(feedbackfile, "wb") as fh:
            fh.write(b"feedback workbook")
        return "uploaded", 1

    dbsample = MagicMock()
    dbsample.batchUpload.side_effect = _batch_upload
    sheet = SimpleUploadedFile("my samples (v2).xlsx", b"PK\x03\x04sheet")
    request = _request(OWNER, "/seek/sampleupload/", method="post",
                       data={"excelfile_upload": sheet, "instituion_id": "1", "people_id": "7"})
    with patch("seek.views.upload.SeekDB", return_value=seekdb), \
            patch("seek.views.upload.DBtable_sample", return_value=dbsample), \
            patch("seek.views.upload.verifySuperUser", return_value=0), \
            patch("seek.views.upload.DOWNLOAD_DIRECTORY", str(media.root / "download") + "/", create=True), \
            patch("seek.views.upload.UPLOAD_DIRECTORY", str(media.root / "uploads") + "/"):
        response = seek.views.upload.sampleUploadAjax(request)
    return SimpleNamespace(body=json.loads(response.content), written=written, media=media)


def test_the_feedback_workbook_is_written_where_media_does_not_reach(upload):
    (path,) = upload.written
    assert not os.path.realpath(path).startswith(os.path.realpath(str(upload.media.root)) + os.sep)
    assert os.path.realpath(path).startswith(os.path.realpath(str(upload.media.exports)) + os.sep)


def test_the_feedback_link_is_a_private_export_with_a_fetchable_name(upload):
    match = LINK.match(upload.body["link"])
    assert match, upload.body["link"]
    assert match["name"] == os.path.basename(upload.written[0])
    assert match["name"] in upload.body["msg"]


@pytest.mark.parametrize("user", [OWNER, ADMIN], ids=["uploader", "superuser"])
def test_the_uploader_and_a_superuser_can_fetch_the_feedback(upload, user):
    match = LINK.match(upload.body["link"])

    response = exportFile(_request(user, upload.body["link"]), match["token"], match["name"])

    assert b"".join(response.streaming_content) == b"feedback workbook"


def test_another_member_cannot_fetch_the_feedback(upload):
    match = LINK.match(upload.body["link"])

    with pytest.raises(Http404):
        exportFile(_request(OTHER, upload.body["link"]), match["token"], match["name"])


def test_an_anonymous_caller_is_sent_to_log_in_for_the_feedback(upload):
    match = LINK.match(upload.body["link"])

    response = exportFile(_request(ANONYMOUS, upload.body["link"]), match["token"], match["name"])

    assert response.status_code == 302
