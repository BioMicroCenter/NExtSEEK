"""The SEEK password is never in the session row in a readable form."""
from unittest.mock import patch

import pytest
from django.contrib.sessions.models import Session
from django.test import Client, RequestFactory

from dmac.session_login import seek_password, store_seek_password
from seek.seekdb import SeekDB

SECRET = "SECRET-" + "pw"


def _accepted():
    return {
        "server": "http://seek:3000", "storage": "http://seek:3000", "storagetype": "SEEK", "username": "u1",
        "password": SECRET, "status": True, "err": [], "noexpire": "no", "user_id": 7,
        "userdata": {"attributes": {"first_name": "A", "last_name": "B"}},
    }


@pytest.mark.django_db
def test_login_stores_no_readable_password():
    from django.contrib.auth.models import User

    User.objects.create_user("u1", "u1@example.org", SECRET)  # Django password already matches: no sync needed
    with patch.object(SeekDB, "getSeekLogin", return_value=_accepted()):
        response = Client().post("/login/", {"username": "u1", "password": SECRET})
    assert response.status_code == 302
    rows = [Session.objects.get(pk=pk).get_decoded() for pk in Session.objects.values_list("pk", flat=True)]
    assert rows and all(SECRET not in str(r) for r in rows)
    assert all(SECRET not in s.session_data for s in Session.objects.all())
    assert "password" not in rows[0]


def test_session_reader_returns_the_password():
    session = {"server": "x", "username": "u1"}
    store_seek_password(session, "u1", SECRET)
    request = RequestFactory().get("/")
    request.session = session
    request.user = type("U", (), {"is_authenticated": True})()
    assert SeekDB(None, None, None).getSeekLogin(request, False)["password"] == SECRET


def test_legacy_plain_key_is_dropped_and_reads_as_none():
    session = {"username": "u1", "password": SECRET}
    assert seek_password(session, "u1") is None
    assert "password" not in session


def test_wrong_username_cannot_decrypt():
    session = {}
    store_seek_password(session, "u1", SECRET)
    assert SECRET not in str(session)
    assert seek_password(session, "someone-else") is None
    assert seek_password(session, "u1") == SECRET


def test_garbage_ciphertext_reads_as_none():
    assert seek_password({"seek_password_enc": "not base64 !!"}, "u1") is None
