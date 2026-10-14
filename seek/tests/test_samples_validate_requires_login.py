"""``/seek/samplesvalidate/`` requires a login before it opens a database connection or parses the upload."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

import seek.views.upload  # noqa: F401  -- so @patch can resolve the target

_MOD = "seek.views.upload"
ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)
MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)


def _post(user, seek_login_ok=True):
    request = RequestFactory().post("/", {"excelfile_upload": SimpleUploadedFile("a.xlsx", b"x")})
    request._dont_enforce_csrf_checks = True
    request.user = user
    request.session = {}
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = {"status": seek_login_ok}
    with patch(f"{_MOD}.SeekDB", return_value=seekdb), patch(f"{_MOD}.MySQLdb.connect") as connect, \
         patch(f"{_MOD}.pd.ExcelFile") as excel:
        body = json.loads(seek.views.upload.samplesValidate(request).content)
    return body, connect, excel


def test_logged_out_post_never_parses_or_queries():
    body, connect, excel = _post(ANONYMOUS)
    assert body["status"] == 0 and body["msg"] == seek.views.upload.LOGIN_REQUIRED
    connect.assert_not_called()
    excel.assert_not_called()


def test_a_login_without_a_seek_login_is_refused_too():
    body, connect, excel = _post(MEMBER, seek_login_ok=False)
    assert body["status"] == 0 and body["msg"] == seek.views.upload.LOGIN_REQUIRED
    connect.assert_not_called()
    excel.assert_not_called()
