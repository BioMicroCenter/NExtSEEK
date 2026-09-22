"""The login view must never write a password to a log.

dmac/views.py logged the whole SEEK credential dict at DEBUG on every login
attempt, password included, and production runs with DEBUG logging reaching
`docker logs nextseek`: each login left the user's plaintext SEEK password in
the container log (found 2026-09-11 while tracing a batch upload).
"""
import logging
from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.test import RequestFactory

SECRET = "hunter2-not-a-real-password"


def _credentials(status):
    return {
        "server": "http://seek:3000", "storage": "http://seek:3000", "storagetype": "SEEK",
        "username": "someuser", "password": SECRET, "status": status,
        "err": "" if status else "Error: bad login", "noexpire": "no",
    }


def _login(caplog, status):
    from dmac import views

    request = RequestFactory().post("/login/")
    request.session = MagicMock()
    seekdb = MagicMock()
    seekdb.getSeekLogin.return_value = _credentials(status)
    with caplog.at_level(logging.DEBUG), \
            patch.object(views, "SeekDB", return_value=seekdb), \
            patch.object(views, "render", return_value=HttpResponse()), \
            patch.object(views, "authenticate", return_value=None), \
            patch.object(views, "userSynchronization", return_value=(0, "failed")):
        views.login_seek(request)
    return "\n".join(r.getMessage() for r in caplog.records)


def test_failed_login_logs_no_password(caplog):
    logged = _login(caplog, status=False)
    assert SECRET not in logged


def test_successful_seek_login_logs_no_password(caplog):
    logged = _login(caplog, status=True)
    assert SECRET not in logged


def test_the_attempt_is_still_logged_by_username(caplog):
    """Dropping the line altogether would lose the audit trail the old line
    gave; the username stays."""
    logged = _login(caplog, status=False)
    assert "someuser" in logged
