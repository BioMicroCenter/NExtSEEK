"""The identity the chat stack acts under (#16, sub-project 3).

Every entry point used to end the same four lines: fall back to
``request.session["password"]``. An OAuth session has no password, so the
orchestrator saw a half-supplied pair and refused the request. That refusal was
correct given what it was handed -- ``_credentials_are_complete`` exists
precisely to stop a mixed identity (user A's name, the service account's
password) -- which is why the fix belongs here, in what gets handed to it.

The property worth guarding: **exactly one credential kind is ever populated.**
NExtSEEK rejects competing credentials outright
(``attributes/auth.py::_reject_competing_sources``), so a caller carrying both
Basic and Token is refused rather than falling back to one.

Sub-project 5 then removed the Basic side entirely, so "exactly one" is now
"only ever the token". The first two tests below are inverted from how they
started: they assert that a leftover password in a session, or a Basic header on
the request, resolves nothing rather than taking precedence.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from nextseek_api import helpers, local_tokens

pytestmark = pytest.mark.django_db


def _request(user=None, session=None):
    return SimpleNamespace(
        META={}, method="GET",
        session=session if session is not None else {},
        user=user or SimpleNamespace(is_authenticated=False, username=None, pk=None),
    )


def _db_user(username="researcher"):
    return User.objects.create_user(username=username, password="x")


def _exactly_one_kind(creds):
    basic = bool(creds["api_user"] and creds["api_pass"])
    token = bool(creds["api_token"])
    return basic != token


# -- password credentials no longer resolve ----------------------------------


def test_a_password_session_no_longer_yields_a_basic_pair():
    """Inverted by the cutover (#16, sub-project 5). This used to return the
    session's Basic pair; there is no password in a session now, and even a
    stale one is ignored -- the caller's DRF token is the only identity."""
    creds = helpers.chat_credentials_for(
        _request(session={"username": "researcher", "password": "leftover"})
    )
    assert creds["api_pass"] is None
    assert creds["api_token"] is None  # no token issued for this user


def test_an_inbound_basic_header_is_ignored():
    import base64

    request = _request(session={"username": "researcher"})
    request.META["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"alice:pw").decode()

    creds = helpers.chat_credentials_for(request)
    assert creds["api_user"] == "researcher"
    assert creds["api_pass"] is None


# -- OAuth sessions ----------------------------------------------------------


def test_an_oauth_session_yields_the_users_drf_token():
    """The fix. Before it, this produced api_pass=None and the orchestrator
    refused the request as a half-supplied identity."""
    user = _db_user()
    key = local_tokens.ensure_for(user)

    creds = helpers.chat_credentials_for(
        _request(user=user, session={"username": "researcher"})
    )

    assert creds["api_token"] == key
    assert creds["api_pass"] is None
    assert creds["api_user"] == "researcher"
    assert _exactly_one_kind(creds)


def test_a_missing_token_is_not_invented():
    """get_for never mints. Minting is confined to the OAuth callback so the
    token's life is exactly the SEEK session's -- a token revoked at logout must
    not come back on the next request."""
    user = _db_user()

    creds = helpers.chat_credentials_for(
        _request(user=user, session={"username": "researcher"})
    )

    assert creds["api_token"] is None
    assert not _exactly_one_kind(creds)  # nothing to act with; caller refuses


def test_a_revoked_token_stays_revoked():
    user = _db_user()
    local_tokens.ensure_for(user)
    local_tokens.revoke_for(user)

    creds = helpers.chat_credentials_for(
        _request(user=user, session={"username": "researcher"})
    )
    assert creds["api_token"] is None


# -- the orchestrator's view of completeness ---------------------------------


def test_the_orchestrator_accepts_a_token_as_a_complete_identity():
    from chat_nextseek.orchestrator import _credentials_are_complete

    assert _credentials_are_complete(
        {"api_user": "researcher", "api_pass": None, "api_token": "abc123"}
    )


def test_the_orchestrator_still_rejects_a_half_supplied_pair():
    """Unchanged, and the reason this function exists: applying one half and
    leaving the other on the service account produces a mixed identity."""
    from chat_nextseek.orchestrator import _credentials_are_complete

    assert not _credentials_are_complete({"api_user": "researcher", "api_pass": None})
    assert not _credentials_are_complete({"api_user": None, "api_pass": "pw"})
    assert not _credentials_are_complete(None)


def test_applying_a_token_clears_the_service_accounts_password():
    """Otherwise a request could carry the caller's token alongside the service
    account's Basic pair -- which NExtSEEK refuses outright.

    The complete-credentials branch returns before touching session or
    send_event, so both are None here.
    """
    from chat_nextseek.orchestrator import _identity_gate

    config = SimpleNamespace(API_USER="service", API_PASS="service-pw", API_TOKEN=None)
    applied, refusal = _identity_gate(
        None, config,
        {"api_user": "researcher", "api_pass": None, "api_token": "abc123"},
        None,
    )

    assert refusal is None
    assert applied.API_TOKEN == "abc123"
    assert applied.API_PASS is None
    assert applied.API_USER == "researcher"
    # The shared singleton must not have been mutated.
    assert config.API_PASS == "service-pw"


def test_applying_a_basic_pair_leaves_no_stale_token():
    from chat_nextseek.orchestrator import _identity_gate

    config = SimpleNamespace(API_USER="service", API_PASS="service-pw", API_TOKEN="stale")
    applied, refusal = _identity_gate(
        None, config,
        {"api_user": "alice", "api_pass": "pw", "api_token": None},
        None,
    )

    assert refusal is None
    assert applied.API_TOKEN is None
    assert (applied.API_USER, applied.API_PASS) == ("alice", "pw")
