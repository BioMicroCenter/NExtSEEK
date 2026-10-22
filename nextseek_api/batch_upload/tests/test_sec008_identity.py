"""SEC-008: an upload is filed under the authenticated user, never under a login named in a header.

DRF authenticates the session before a Basic header, and _resolve_user_context used to read that header's login
without its password, so a signed-in user could send `Authorization: Basic <victim>:x` and upload as the victim.
"""
from unittest.mock import MagicMock, patch

from nextseek_api.batch_upload.views import _resolve_user_context

PERSON = {"alice": 1, "bob": 2}


def _request(username):
    request = MagicMock()
    request.data = {}
    request.user.is_authenticated = True
    request.user.is_superuser = False
    request.user.username = username
    return request


def _resolve(request, header_login):
    users = MagicMock()
    users.objects.using.return_value.get.side_effect = lambda login: MagicMock(person_id=PERSON[login])
    seekdb = MagicMock()
    # The session belongs to alice: SEEK's own login check reports her person.
    seekdb.getSeekLogin.return_value = {"status": True, "person_id": 1, "lababbv": "MIT"}
    seekdb.getUserInfo.return_value = ({"lababbv": "MIT"}, True, "")
    with patch("nextseek_api.helpers.resolve_seek_auth", return_value=((header_login, "x"), {})), \
         patch("seek.models.Users", users), \
         patch("seek.seekdb.SeekDB", return_value=seekdb):
        return _resolve_user_context(request)


def test_a_header_naming_another_login_is_ignored():
    assert _resolve(_request("alice"), "bob")["contributor_id"] == PERSON["alice"]


def test_the_users_own_login_in_a_header_still_counts():
    assert _resolve(_request("alice"), "alice")["contributor_id"] == PERSON["alice"]
