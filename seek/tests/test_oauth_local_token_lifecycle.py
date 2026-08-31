"""The NExtSEEK API token's lifetime is tied to the SEEK session (#16, SP3).

A DRF token has no expiry of its own. Choosing it was a deliberate trade -- it
is the one credential that works for async workers and for the container clients
that lose Basic auth at cutover -- and these three moments are the entire
mitigation:

* issued when a SEEK login succeeds,
* revoked at logout,
* revoked when SEEK rejects the refresh token, i.e. when the session that
  justified it is over anyway.

If any of the three stops happening, every OAuth login silently leaves behind a
permanent bearer credential for NExtSEEK's API, and nothing else in the system
would notice. That is what these tests are for.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.authtoken.models import Token

from seek.models.nextseek import SeekOAuthToken
from seek.oauth import client, service

pytestmark = pytest.mark.django_db(databases=["default", "seek"])

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_CLIENT_ID = "client-abc"
    settings.SEEK_OAUTH_CLIENT_SECRET = "secret-xyz"
    settings.SEEK_OAUTH_REDIRECT_URI = "http://testserver/oauth/seek/callback"
    settings.SEEK_OAUTH_AUTHORIZE_URL = "https://seek.public.test/oauth/authorize"
    settings.SEEK_OAUTH_TOKEN_URL = "http://seek:3000/oauth/token"
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


def _tokens(access="at-1", refresh="rt-1"):
    return client.TokenResponse(
        access_token=access, refresh_token=refresh,
        expires_at=timezone.now() + timedelta(hours=1), scope="read",
    )


# -- issued on a successful SEEK sign-in -------------------------------------


def test_signing_in_through_seek_issues_a_nextseek_api_token(
    client_fixture, oauth_on, seek_identity
):
    client_fixture.get("/oauth/seek/login")
    state = client_fixture.session["seek_oauth_state"]

    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        response = client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    assert response.status_code == 302
    user = User.objects.get(username="researcher")
    assert Token.objects.filter(user=user).exists()


def test_a_failure_to_issue_does_not_cost_the_user_their_login(
    client_fixture, oauth_on, seek_identity, monkeypatch
):
    """The token is for the chat stack's benefit. Losing it must not turn a
    successful SEEK authentication into a failed sign-in -- the user should get
    their session, and only the chat stack should be worse off."""
    def _boom(*args, **kwargs):
        raise RuntimeError("db is unhappy")

    monkeypatch.setattr(Token.objects, "get_or_create", _boom)

    client_fixture.get("/oauth/seek/login")
    state = client_fixture.session["seek_oauth_state"]

    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        response = client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    assert response.status_code == 302
    user = User.objects.get(username="researcher")
    assert client_fixture.session["_auth_user_id"] == str(user.pk)
    assert not Token.objects.filter(user=user).exists()


# -- revoked at logout -------------------------------------------------------


def test_logging_out_revokes_the_nextseek_api_token(client_fixture, oauth_on, seek_identity):
    """Logout is what bounds the token's life. Without this, every sign-in
    leaves behind a credential that never expires."""
    client_fixture.get("/oauth/seek/login")
    state = client_fixture.session["seek_oauth_state"]
    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    user = User.objects.get(username="researcher")
    assert Token.objects.filter(user=user).exists()

    # logout_seek shells out to `rm -r <session username>` relative to the
    # working directory (dmac/views.py). Stubbed out here rather than executed:
    # this test is about token revocation, and running that is a side effect no
    # test should have. It is pre-existing behaviour, unrelated to #16.
    with patch("dmac.views.call") as shell_out:
        client_fixture.get("/logout")
    shell_out.assert_called_once()

    assert not Token.objects.filter(user=user).exists()


# -- revoked when SEEK kills the session -------------------------------------


def test_a_dead_refresh_token_also_revokes_the_nextseek_api_token(oauth_on):
    """The third mitigation. Once SEEK has rejected the refresh token there is
    no SEEK session left to justify a NExtSEEK credential issued for it."""
    user = User.objects.create_user(username="researcher", password="x")
    Token.objects.create(user=user)
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=42, access_token="at-old", refresh_token="rt-dead",
        access_token_expires_at=timezone.now() - timedelta(minutes=1),
    )

    with patch("seek.oauth.client.refresh", side_effect=client.InvalidGrant("revoked")):
        assert service.get_valid_access_token(user) is None

    assert not Token.objects.filter(user=user).exists()


def test_a_transient_seek_failure_leaves_the_nextseek_api_token_alone(oauth_on):
    """The asymmetry that runs through this whole sub-project: a SEEK outage
    must not destroy credentials. Revoking here would log every user out of the
    chat stack during a SEEK restart, with nothing to restore them."""
    user = User.objects.create_user(username="researcher", password="x")
    key = Token.objects.create(user=user).key
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=42, access_token="at-old", refresh_token="rt-keep",
        access_token_expires_at=timezone.now() - timedelta(minutes=1),
    )

    with patch("seek.oauth.client.refresh", side_effect=client.TransientError("down")):
        assert service.get_valid_access_token(user) is None

    assert Token.objects.get(user=user).key == key
