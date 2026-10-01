"""``SeekDB.getSeekLogin`` says "logged in" only for a logged-in Django user, or login-form credentials SEEK accepts.

On a POST it used to build its answer from ``username`` and ``password`` fields in the body, whatever view it served
and whoever sent them, and check nothing: with ``whetherFullInfo=False`` any two non-empty fields gave
``status: True``, and with ``True`` the name alone was enough, because it fetched that person's record from SEEK with
the posted pair and SEEK serves a person's record to anyone, answering a wrong password as an anonymous caller.
Two consequences, both reachable with no session at all:

* ``runSampleSearch`` (``/seek/searchAdvanced/``, ``/seek/samples/searching/``, ``/seek/searchUIDs/``) scopes the
  search by the projects of whoever ``getSeekLogin`` names, so an anonymous POST naming a member searched that
  member's projects;
* the login view (``dmac.views.login_seek``) took the same answer as proof of the password, and on a mismatch with the
  Django password ``userSynchronization`` reset the Django password to the posted one and logged the caller in.

Now every caller but the login form reads the SEEK credentials from the session, and only when ``request.user`` is
authenticated (the login view writes the two together). The login form (``fromLoginForm=True``) still reads the body,
and its credentials count only once SEEK's ``/people/current`` answers, with them, as the person that login belongs to.

Hermetic: SEEK (``SeekAPI``) and the SEEK users table are stubbed; nothing shells out.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

import seek.views.search  # noqa: F401  -- so @patch can resolve the target
from seek.seekdb import SeekDB

ANONYMOUS = SimpleNamespace(is_authenticated=False, is_superuser=False, username="", pk=None)
MEMBER = SimpleNamespace(is_authenticated=True, is_superuser=False, username="member", pk=7)

OTHER_MEMBER = "other-member"
NOT_THEIR_PASSWORD = "not-the-password"
VICTIM_PERSON_ID = 5
SESSION = {"server": "http://seek:3000", "username": "member", "password": "members-password"}
VICTIM_INFO = {
    "user_id": VICTIM_PERSON_ID, "person_id": VICTIM_PERSON_ID, "userdata": {"attributes": {}},
    "projectid": "3", "projectname": "Victim project", "projectOptions": [{"id": "3", "title": "Victim project"}],
    "institutionid": "1", "institutionname": "Lab", "lababbv": "LAB",
}
NOT_LOGGED_IN = {"errors": [{"title": "No user logged in"}]}


class _Session(dict):
    """Just enough of a Django session for getSeekLogin and the login view."""

    def set_expiry(self, value):
        self["_expiry"] = value

    def flush(self):
        self.clear()


def _request(method, user, data=None, session=None, path="/seek/searchAdvanced/"):
    factory = RequestFactory()
    request = factory.post(path, data or {}) if method == "post" else factory.get(path, data or {})
    request.user = user
    request.session = _Session(session or {})
    return request


@pytest.fixture
def seekstub(monkeypatch):
    """SEEK as seen from getSeekLogin: the users table knows OTHER_MEMBER, a person record comes back for any caller, and
    ``/people/current`` answers for the credentials ``current`` maps to a person id. Records every SeekAPI built."""
    built = []
    current = {}

    def _api(server, username, password):
        api = MagicMock()
        api.credentials = (username, password)
        api.getCurrentUser.side_effect = lambda: (
            {"data": {"id": str(current[(username, password)])}} if (username, password) in current else NOT_LOGGED_IN)
        built.append(api)
        return api

    user_info = MagicMock(side_effect=lambda person_id: (dict(VICTIM_INFO), True, ""))
    monkeypatch.setattr("seek.seekdb.SeekAPI", _api)
    monkeypatch.setattr(SeekDB, "_SeekDB__getSeekPersonID",
                        lambda self, username: VICTIM_PERSON_ID if username == OTHER_MEMBER else 7)
    monkeypatch.setattr(SeekDB, "getUserInfo", lambda self, person_id: user_info(person_id))
    return SimpleNamespace(built=built, current=current, user_info=user_info)


def _posted_password_reached_seek(seekstub):
    return any(api.credentials == (OTHER_MEMBER, NOT_THEIR_PASSWORD) for api in seekstub.built)


# --------------------------------------------------------------------------- every caller but the login form


@pytest.mark.parametrize("full_info", [False, True])
def test_an_anonymous_post_naming_a_user_is_not_logged_in(seekstub, full_info):
    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": NOT_THEIR_PASSWORD})

    user_seek = SeekDB(None, None, None).getSeekLogin(request, full_info)

    assert user_seek["status"] is False
    assert user_seek["username"] != OTHER_MEMBER
    assert not _posted_password_reached_seek(seekstub), "the posted password must never reach SEEK (or a shell)"
    seekstub.user_info.assert_not_called()


def test_a_logged_in_post_reads_the_session_not_the_body(seekstub):
    request = _request("post", MEMBER, {"username": OTHER_MEMBER, "password": NOT_THEIR_PASSWORD}, session=SESSION)

    user_seek = SeekDB(None, None, None).getSeekLogin(request, False)

    assert user_seek["status"] is True
    assert (user_seek["username"], user_seek["password"]) == ("member", "members-password")
    assert not _posted_password_reached_seek(seekstub)


def test_a_logged_in_get_is_unchanged(seekstub):
    user_seek = SeekDB(None, None, None).getSeekLogin(_request("get", MEMBER, session=SESSION), False)

    assert user_seek["status"] is True
    assert user_seek["username"] == "member"


def test_session_credentials_without_a_logged_in_user_are_not_a_login(seekstub):
    user_seek = SeekDB(None, None, None).getSeekLogin(_request("get", ANONYMOUS, session=SESSION), False)

    assert user_seek["status"] is False


def test_a_server_built_proof_request_with_no_user_keeps_its_session_credentials(seekstub):
    """The attribute API proves an already-authenticated caller to SEEK with a request-like object it builds itself
    (``SelectedSeekCredential.proof_request``): a session, no body, no ``user``. No HTTP request lacks ``user``."""
    proof = SimpleNamespace(META={}, COOKIES={}, session=dict(SESSION), method="GET")

    user_seek = SeekDB(None, None, None).getSeekLogin(proof, False)

    assert user_seek["status"] is True
    assert user_seek["username"] == "member"


def test_an_anonymous_search_naming_a_member_searches_nothing(seekstub):
    """End to end: an anonymous POST naming a member, with a password that is not theirs, searches nothing."""
    dbsample = MagicMock()
    dbsample.searchAdvanced.return_value = "{}"
    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": NOT_THEIR_PASSWORD})

    with patch("seek.views.search.DBtable_sample", return_value=dbsample):
        seek.views.search.searchingAdvanced(request)

    assert dbsample.searchAdvanced.call_args.kwargs["scoped_project_ids"] == []


# --------------------------------------------------------------------------- the login form


def test_login_form_credentials_seek_rejects_are_not_a_login(seekstub):
    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": NOT_THEIR_PASSWORD}, path="/login/")

    user_seek = SeekDB(None, None, None).getSeekLogin(request, fromLoginForm=True)

    assert user_seek["status"] is False
    seekstub.user_info.assert_not_called()


def test_login_form_credentials_seek_accepts_are_a_login(seekstub):
    seekstub.current[(OTHER_MEMBER, "the-password")] = VICTIM_PERSON_ID
    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": "the-password"}, path="/login/")

    user_seek = SeekDB(None, None, None).getSeekLogin(request, fromLoginForm=True)

    assert user_seek["status"] is True
    assert user_seek["username"] == OTHER_MEMBER
    assert user_seek["projectid"] == "3"


def test_login_form_credentials_of_another_person_are_not_this_login(seekstub):
    """SEEK answering /people/current as someone else is not SEEK accepting this login."""
    seekstub.current[(OTHER_MEMBER, NOT_THEIR_PASSWORD)] = 99
    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": NOT_THEIR_PASSWORD}, path="/login/")

    assert SeekDB(None, None, None).getSeekLogin(request, fromLoginForm=True)["status"] is False


def _login_view(seekstub, password):
    from dmac import views

    request = _request("post", ANONYMOUS, {"username": OTHER_MEMBER, "password": password}, path="/login/")
    user = SimpleNamespace(is_authenticated=True, username=OTHER_MEMBER)
    authenticated = {"the-password": user}
    sync = MagicMock(return_value=(1, "synchronised"))
    login = MagicMock()
    with patch.object(views, "render", return_value=HttpResponse("login page")), \
            patch.object(views, "authenticate", side_effect=lambda username, password: authenticated.get(password)), \
            patch.object(views, "userSynchronization", sync), \
            patch.object(views, "login", login):
        response = views.login_seek(request)
    return response, sync, login


def test_the_login_view_refuses_a_password_seek_rejects(seekstub):
    response, sync, login = _login_view(seekstub, NOT_THEIR_PASSWORD)

    login.assert_not_called()
    sync.assert_not_called()  # a rejected password must never be written over the Django one
    assert response.status_code == 200


def test_the_login_view_still_logs_in_a_password_seek_accepts(seekstub):
    seekstub.current[(OTHER_MEMBER, "the-password")] = VICTIM_PERSON_ID

    response, sync, login = _login_view(seekstub, "the-password")

    login.assert_called_once()
    assert response.status_code == 302
