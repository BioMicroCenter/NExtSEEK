"""Request handling on the legacy sample views under ``seek/views/``.

* Sample deletion accepts POST only, so the CSRF middleware checks its token, and
  only for a logged-in account acting through its own SEEK session. A sample is
  deleted for its contributor or for a superuser (``is_superuser``).
* The sheet upload view requires a login.
* The sample retrieval view behind the sample-type grid requires a login.
* Every page that calls the deletion route posts to it with the CSRF token.

Hermetic: users are stand-ins, ``SeekDB`` and ``DBtable_sample`` are patched in the
module that calls them (``seek/views/`` is a package; see ``seek/views/__init__.py``),
and ``DBtable_sample`` is built with ``__new__`` because its ``__init__`` opens a cursor.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.template import engines
from django.test import RequestFactory

import seek.views.samples  # noqa: F401  -- so @patch can resolve the targets
import seek.views.upload  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "seek" / "templates" / "pages"


def _user(username="demo", superuser=False):
    return SimpleNamespace(is_authenticated=True, is_superuser=superuser, username=username)


def _logged_out():
    """What ``request.user`` is when nobody is logged in."""
    return SimpleNamespace(is_authenticated=False, is_superuser=False, username="")


def _seek_login(username="demo", status=True):
    return {"status": status, "err": [] if status else ["No valid username or password"],
            "username": username, "user_id": 7}


def _post(path, data, user):
    req = RequestFactory().post(path, data)
    req.user = user
    req.session = {}
    return req


def _get(path, data, user):
    req = RequestFactory().get(path, data)
    req.user = user
    req.session = {}
    return req


def _envelope(resp):
    return json.loads(resp.content.decode())


# --------------------------------------------------------------------------- #
# Sample deletion: the view
# --------------------------------------------------------------------------- #

DELETE = "/seek/samples/delete/"


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_accepts_post_only(mock_db, mock_table):
    resp = seek.views.samples.sampleDelete(_get(DELETE, {"allids": "[1]"}, _user()))
    assert resp.status_code == 405
    mock_table.return_value.deleteSamples.assert_not_called()


def test_sample_deletion_is_covered_by_the_csrf_check():
    view = seek.views.samples.sampleDelete
    assert not getattr(view, "csrf_exempt", False)
    middleware = CsrfViewMiddleware(lambda request: HttpResponse())

    no_token = _post(DELETE, {"allids": "[1]"}, _user())
    assert middleware.process_view(no_token, view, (), {}).status_code == 403

    secret = "a" * 32
    with_token = _post(DELETE, {"allids": "[1]"}, _user())
    with_token.COOKIES["csrftoken"] = secret
    with_token.META["HTTP_X_CSRFTOKEN"] = secret
    assert middleware.process_view(with_token, view, (), {}) is None


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_requires_a_login(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    resp = seek.views.samples.sampleDelete(_post(DELETE, {"allids": "[1]"}, _logged_out()))
    assert resp.status_code == 200
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.deleteSamples.assert_not_called()


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_requires_a_seek_login(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login(status=False)
    resp = seek.views.samples.sampleDelete(_post(DELETE, {"allids": "[1]"}, _user()))
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.deleteSamples.assert_not_called()


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_acts_as_the_logged_in_account(mock_db, mock_table):
    """The SEEK identity the deletion runs under is the logged-in account's own."""
    mock_db.return_value.getSeekLogin.return_value = _seek_login(username="someone-else")
    resp = seek.views.samples.sampleDelete(_post(DELETE, {"allids": "[1]"}, _user("demo")))
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.deleteSamples.assert_not_called()


@pytest.mark.parametrize("superuser", [False, True])
@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_passes_the_superuser_flag(mock_db, mock_table, superuser):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    mock_table.return_value.deleteSamples.return_value = '{"status": 1}'
    resp = seek.views.samples.sampleDelete(
        _post(DELETE, {"allids": "[1, 2]"}, _user(superuser=superuser)))
    assert resp.status_code == 200
    call = mock_table.return_value.deleteSamples.call_args
    assert call.args[3] == [1, 2]
    assert call.kwargs["is_superuser"] is superuser


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_deletion_reads_uids_from_the_post_body(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    mock_table.return_value.getSampleID.side_effect = {"U-1": 11, "U-2": 12}.get
    mock_table.return_value.deleteSamples.return_value = '{"status": 1}'
    seek.views.samples.sampleDelete(_post(DELETE, {"alluids": '["U-1", "U-2"]'}, _user()))
    assert mock_table.return_value.deleteSamples.call_args.args[3] == [11, 12]


# --------------------------------------------------------------------------- #
# Sample deletion: who may delete a sample
# --------------------------------------------------------------------------- #

def _deletion_table():
    from seek.dbtable_sample import DBtable_sample

    table = DBtable_sample.__new__(DBtable_sample)
    table.db = MagicMock()
    table.db.retrieveFieldValue.return_value = 1  # the account holds a SEEK role
    table._retrieveSampleByID = lambda sample_id: {
        "contributor_id": 7, "policy_id": 3, "uuid": "U-%s" % sample_id}
    table._getSampleChildren = lambda uid: []
    table._deleteOneSample = MagicMock(return_value=("", True))
    return table


@patch("seek.sample.table.saveDiclistIntoExcel")
def test_the_contributor_may_delete_their_sample(_save):
    table = _deletion_table()
    diclist, _msg, status = table._deleteSampleList({"user_id": 7}, [1], "out.xls")
    assert status == 1
    assert diclist[0]["statusi"] == "DELETED"
    table._deleteOneSample.assert_called_once_with(1, 3)


@patch("seek.sample.table.saveDiclistIntoExcel")
def test_deletion_is_limited_to_the_contributor_or_a_superuser(_save):
    table = _deletion_table()
    diclist, _msg, status = table._deleteSampleList({"user_id": 5}, [1], "out.xls")
    assert status == 0
    assert diclist[0]["statusi"] != "DELETED"
    table._deleteOneSample.assert_not_called()


@patch("seek.sample.table.saveDiclistIntoExcel")
def test_a_superuser_may_delete_any_sample(_save):
    table = _deletion_table()
    diclist, _msg, status = table._deleteSampleList(
        {"user_id": 5}, [1], "out.xls", is_superuser=True)
    assert status == 1
    assert diclist[0]["statusi"] == "DELETED"


@patch("seek.sample.table.saveDiclistIntoExcel")
def test_delete_samples_forwards_the_superuser_flag(_save):
    table = _deletion_table()
    data = json.loads(table.deleteSamples({"user_id": 5}, "out.xls", "/link", [1],
                                          is_superuser=True))
    assert data["status"] == 1


# --------------------------------------------------------------------------- #
# Sheet upload
# --------------------------------------------------------------------------- #

UPLOAD = "/seek/sampleupload/"


def _sheet():
    """A POST body that, logged in, would reach ``DBtable_sample.batchUpload``."""
    return {"excelfile_upload": SimpleUploadedFile("sheet.xlsx", b"PK\x03\x04"),
            "instituion_id": "1", "people_id": "0"}


@patch("seek.views.upload.handle_uploaded_file")
@patch("seek.views.upload.DBtable_sample")
@patch("seek.views.upload.SeekDB")
def test_the_upload_view_requires_a_login(mock_db, mock_table, _save):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    mock_table.return_value.batchUpload.return_value = ("ok", 1)
    resp = seek.views.upload.sampleUploadAjax(_post(UPLOAD, _sheet(), _logged_out()))
    assert resp.status_code == 200
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.batchUpload.assert_not_called()


@patch("seek.views.upload.handle_uploaded_file")
@patch("seek.views.upload.DBtable_sample")
@patch("seek.views.upload.SeekDB")
def test_the_upload_view_requires_a_seek_login(mock_db, mock_table, _save):
    mock_db.return_value.getSeekLogin.return_value = _seek_login(status=False)
    mock_table.return_value.batchUpload.return_value = ("ok", 1)
    resp = seek.views.upload.sampleUploadAjax(_post(UPLOAD, _sheet(), _user()))
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.batchUpload.assert_not_called()


@patch("seek.views.upload.handle_uploaded_file")
@patch("seek.views.upload.DBtable_sample")
@patch("seek.views.upload.SeekDB")
def test_the_upload_view_uploads_for_a_logged_in_account(mock_db, mock_table, _save):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    mock_table.return_value.batchUpload.return_value = ("ok", 1)
    resp = seek.views.upload.sampleUploadAjax(_post(UPLOAD, _sheet(), _user()))
    assert _envelope(resp)["status"] == 1
    mock_table.return_value.batchUpload.assert_called_once()


@patch("seek.views.upload.DBtable_sample")
@patch("seek.views.upload.SeekDB")
def test_the_upload_view_still_answers_a_logged_in_get(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    resp = seek.views.upload.sampleUploadAjax(_get(UPLOAD, {}, _user()))
    body = _envelope(resp)
    assert body["status"] == 0
    assert body["msg"] == "Error: Not a valid http POST request"


# --------------------------------------------------------------------------- #
# Sample retrieval behind the sample-type grid
# --------------------------------------------------------------------------- #

RETRIEVE = "/seek/retrieve/samples/"


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_retrieval_requires_a_login(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    resp = seek.views.samples.retrieveSamples(_get(RETRIEVE, {}, _logged_out()))
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.processRecords.assert_not_called()


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_retrieval_requires_a_seek_login(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login(status=False)
    resp = seek.views.samples.retrieveSamples(_get(RETRIEVE, {}, _user()))
    assert _envelope(resp)["status"] == 0
    mock_table.return_value.processRecords.assert_not_called()


@patch("seek.views.samples.DBtable_sample")
@patch("seek.views.samples.SeekDB")
def test_sample_retrieval_serves_a_logged_in_account(mock_db, mock_table):
    mock_db.return_value.getSeekLogin.return_value = _seek_login()
    mock_table.return_value.processRecords.return_value = '{"total": 0, "rows": []}'
    resp = seek.views.samples.retrieveSamples(_get(RETRIEVE, {}, _user()))
    assert json.loads(resp.content.decode()) == {"total": 0, "rows": []}


# --------------------------------------------------------------------------- #
# The pages that call the deletion route
# --------------------------------------------------------------------------- #

EMBEDS = ["samples_stable.embed.html", "searchAdvanced_stable.embed.html",
          "searchAdvanced_deletion.embed.html"]


@pytest.mark.parametrize("name", EMBEDS)
def test_each_legacy_delete_button_posts_with_the_csrf_token(name):
    text = (PAGES / name).read_text()
    assert "$.get(url_delete" not in text
    assert "$.post(url_delete" in text
    assert "'csrfmiddlewaretoken': '{{ csrf_token }}'" in text


def test_the_new_search_page_posts_deletions_with_the_csrf_token():
    text = (ROOT / "seek" / "templates" / "newSearch.html").read_text()
    start = text.index("async function deleteSamplesExecute(")
    block = text[start:text.index("async function deleteSamples(", start)]
    assert "/seek/samples/delete/?" not in block
    assert 'fetch("/seek/samples/delete/"' in block
    assert 'method: "POST"' in block
    assert "body: params" in block
    assert '"X-CSRFToken": getCookie("csrftoken")' in block


def test_the_deletion_tab_renders_the_csrf_token_into_its_request():
    request = RequestFactory().get("/seek/search/")
    template = engines["django"].from_string(
        (PAGES / "searchAdvanced_deletion.embed.html").read_text())
    html = template.render({}, request)
    script = html[:html.index("</script>")]
    assert "'csrfmiddlewaretoken': '{{ csrf_token }}'" not in script
    token = script.split("'csrfmiddlewaretoken': '", 1)[1].split("'", 1)[0]
    assert len(token) == 64 and token.isalnum()
