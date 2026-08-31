"""``getSeekLogin`` resolves an OAuth token when the session has no password.

This is Layer B's chokepoint. 29 of the ~40 ``SeekDB`` constructions in the tree
are ``SeekDB(None, None, None)`` and resolve their credential through here, so
retrofitting this one method is what makes them all work without editing any of
them.

The important shape: what is stored is a **provider**, not a token. A ``SeekDB``
can be held across a long request, and a token captured once would be served
after it expired. ``test_the_stored_provider_resolves_a_fresh_token_per_call``
is the guard against someone flattening the callable into a value.

Everything here must be inert while ``SEEK_OAUTH_ENABLED`` is off, which
``test_the_flag_gates_the_whole_branch`` asserts directly: production runs on
that path, and this method is on every authenticated request.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from seek.seekdb import SeekDB

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


@pytest.fixture
def oauth_off(settings):
    settings.SEEK_OAUTH_ENABLED = False
    return settings


def _request(username="researcher", password=None, authed=True):
    """A GET carrying an OAuth-shaped session: username set, password absent."""
    session = {"server": "http://seek:3000", "username": username}
    if password is not None:
        session["password"] = password
    return SimpleNamespace(
        method="GET",
        session=session,
        user=SimpleNamespace(is_authenticated=authed, pk=7),
    )


def _stub_token(value):
    return patch("seek.oauth.service.get_valid_access_token", return_value=value)


def _login(request):
    """getSeekLogin without the SEEK round trip that full info would need."""
    return SeekDB(None, None, None).getSeekLogin(request, whetherFullInfo=False)


# -- the flag gates everything -----------------------------------------------


def test_the_flag_gates_the_whole_branch(oauth_off):
    """With the flag off a password-less session fails exactly as it did before
    this branch existed, and the token service is never consulted."""
    with _stub_token("at-1") as stub:
        result = _login(_request())

    assert result["status"] is False
    assert result["token_provider"] is None
    stub.assert_not_called()


def test_an_anonymous_request_gets_no_provider(oauth_on):
    with _stub_token("at-1") as stub:
        result = _login(_request(authed=False))
    assert result["status"] is False
    stub.assert_not_called()


# -- the OAuth branch --------------------------------------------------------


def test_a_password_less_session_with_a_token_authenticates(oauth_on):
    """The point of the whole sub-project: no password anywhere, and the login
    succeeds."""
    with _stub_token("at-1"):
        result = _login(_request())

    assert result["status"] is True
    assert result["password"] is None
    assert result["token_provider"] is not None
    assert result["token_provider"]() == "at-1"


def test_a_user_without_a_usable_token_still_fails(oauth_on):
    """Being signed in is not the same as holding a SEEK credential. A user
    whose token was revoked must fail here, not proceed unauthenticated."""
    with _stub_token(None):
        result = _login(_request())

    assert result["status"] is False
    assert result["token_provider"] is None


def test_a_failure_in_the_token_service_is_not_a_500(oauth_on):
    """getSeekLogin is on the ordinary request path, so a SEEK outage during a
    refresh has to look like "no credential"."""
    with patch("seek.oauth.service.get_valid_access_token",
               side_effect=RuntimeError("SEEK exploded")):
        result = _login(_request())

    assert result["status"] is False
    assert result["token_provider"] is None


def test_the_stored_provider_resolves_a_fresh_token_per_call(oauth_on):
    """A SeekDB can outlive the token it was built with. Flattening this
    callable into a captured string reintroduces exactly that bug."""
    tokens = iter(["at-1", "at-2", "at-3"])
    with patch("seek.oauth.service.get_valid_access_token",
               side_effect=lambda user: next(tokens)):
        result = _login(_request())
        provider = result["token_provider"]
        assert provider() == "at-2"  # at-1 was consumed proving the token exists
        assert provider() == "at-3"


# -- the password path is untouched ------------------------------------------


def test_a_password_session_never_consults_the_token_service(oauth_on):
    """Coexistence: a session that still has a password behaves exactly as it
    always did, even with the flag on."""
    with _stub_token("at-1") as stub:
        result = _login(_request(password="hunter2"))

    assert result["password"] == "hunter2"
    assert result["token_provider"] is None
    stub.assert_not_called()


def test_the_command_line_path_is_untouched(oauth_on):
    """getSeekLogin(None) has no request to carry a user, so it can never
    acquire a provider -- and must not crash looking for one."""
    result = SeekDB(None, None, None).getSeekLogin(None, whetherFullInfo=False)
    assert result["token_provider"] is None
    assert result["status"] is False
