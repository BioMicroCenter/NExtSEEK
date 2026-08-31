"""The OAUTH source in ``resolve_seek_auth`` (#16, sub-project 2).

Two properties matter more than the rest.

**Flag-off behaviour is unchanged.** The default order gained an entry, and
production runs on the flag-off path, so the order matrix below asserts that an
OAuth-less request resolves to exactly what it resolved to before.

**OAUTH and TOKEN emit different schemes, on purpose.** TOKEN forwards a
credential the *caller* presented and has always gone out as ``Token``; OAUTH is
an OAuth2 access token NExtSEEK holds on the user's behalf, which Doorkeeper
only accepts as ``Bearer``. A test pins each, because "tidying up" the
inconsistency would break one of the two.

The precedence is BASIC, SESSION, OAUTH, TOKEN: an explicitly presented
credential still wins over a stored one.
"""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nextseek_api import helpers

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def _request(basic=None, token_header=None, user=None):
    meta = {}
    if basic:
        raw = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
        meta["HTTP_AUTHORIZATION"] = f"Basic {raw}"
    if token_header:
        meta["HTTP_X_SEEK_AUTHORIZATION"] = f"Token {token_header}"
    return SimpleNamespace(
        META=meta,
        session={},
        method="GET",
        user=user or SimpleNamespace(is_authenticated=False, pk=None),
    )


def _authed_user(pk=1):
    return SimpleNamespace(is_authenticated=True, pk=pk)


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


@pytest.fixture
def oauth_off(settings):
    settings.SEEK_OAUTH_ENABLED = False
    return settings


def _stub_token(value):
    return patch("seek.oauth.service.get_valid_access_token", return_value=value)


def _no_session_auth():
    """Neutralise the SESSION source, which would otherwise hit SeekDB."""
    return patch("nextseek_api.helpers.get_auth", return_value=None)


# -- the flag gates everything -----------------------------------------------


def test_no_oauth_credential_while_the_flag_is_off(oauth_off):
    """The reason the default-order change is safe: with the flag off this
    source contributes nothing and the loop falls through as it always did."""
    with _stub_token("at-1") as stub:
        assert helpers.get_oauth_auth(_request(user=_authed_user())) is None
    stub.assert_not_called()


def test_an_anonymous_request_has_no_oauth_credential(oauth_on):
    with _stub_token("at-1") as stub:
        assert helpers.get_oauth_auth(_request()) is None
    stub.assert_not_called()


def test_a_user_with_no_usable_token_yields_none(oauth_on):
    with _stub_token(None):
        assert helpers.get_oauth_auth(_request(user=_authed_user())) is None


def test_a_failure_in_the_token_service_never_propagates(oauth_on):
    """resolve_seek_auth runs on every proxied request. A SEEK outage during a
    refresh must degrade to "no credential" -- and so to the caller's existing
    401 -- rather than 500 the request."""
    with patch("seek.oauth.service.get_valid_access_token",
               side_effect=RuntimeError("SEEK exploded")):
        assert helpers.get_oauth_auth(_request(user=_authed_user())) is None


# -- wire format -------------------------------------------------------------


def test_oauth_is_sent_as_bearer(oauth_on):
    with _stub_token("at-1"), _no_session_auth():
        basic, headers = helpers.resolve_seek_auth(_request(user=_authed_user()))
    assert basic is None
    assert headers == {"Authorization": "Bearer at-1"}


def test_an_inbound_token_header_is_still_sent_as_token(oauth_off):
    """Unchanged, and deliberately different from OAUTH. Forwarding it as
    Bearer would change the meaning of a credential the caller chose."""
    with _no_session_auth():
        basic, headers = helpers.resolve_seek_auth(_request(token_header="caller-token"))
    assert basic is None
    assert headers == {"Authorization": "Token caller-token"}


# -- precedence --------------------------------------------------------------


def test_an_explicit_basic_header_beats_a_stored_oauth_token(oauth_on):
    """A credential the caller presented outranks one we hold for them."""
    with _stub_token("at-1"), _no_session_auth():
        basic, headers = helpers.resolve_seek_auth(
            _request(basic=("alice", "pw"), user=_authed_user())
        )
    assert basic == ("alice", "pw")
    assert headers == {}


def test_a_password_session_beats_a_stored_oauth_token(oauth_on):
    with _stub_token("at-1"), \
         patch("nextseek_api.helpers.get_auth", return_value=("bob", "pw")):
        basic, headers = helpers.resolve_seek_auth(_request(user=_authed_user()))
    assert basic == ("bob", "pw")


def test_oauth_beats_an_inbound_token_header(oauth_on):
    """OAUTH is ahead of TOKEN: during coexistence a signed-in user's own
    credential is the more specific one."""
    with _stub_token("at-1"), _no_session_auth():
        _, headers = helpers.resolve_seek_auth(
            _request(token_header="caller-token", user=_authed_user())
        )
    assert headers == {"Authorization": "Bearer at-1"}


def test_an_explicit_order_can_still_exclude_oauth(oauth_on):
    """Call sites pass restricted orders; omitting OAUTH must omit it."""
    with _stub_token("at-1") as stub, _no_session_auth():
        basic, headers = helpers.resolve_seek_auth(
            _request(user=_authed_user()), ["BASIC", "SESSION"]
        )
    assert (basic, headers) == (None, None)
    stub.assert_not_called()


def test_nothing_available_still_resolves_to_nothing(oauth_on):
    with _stub_token(None), _no_session_auth():
        assert helpers.resolve_seek_auth(_request(user=_authed_user())) == (None, None)
