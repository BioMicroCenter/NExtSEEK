"""Self-service NExtSEEK API tokens and the logout exemption (#16, sub-project 5).

This endpoint is the missing middle of "remove passwords, use tokens": after
cutover, HTTP Basic against NExtSEEK's own API stops working, and until now
nothing issued the DRF token that replaces it.

Three properties are worth more than the rest.

**The key is never readable.** ``list`` reports only whether a token exists.
A DRF token is shown once, at issue, so that read access to this endpoint --
through a leaked session, a mistaken permission, an XSS -- cannot hand over a
working unattended credential.

**Session authentication only.** Allowing ``TokenAuthentication`` would let a
leaked token mint its own replacement and read it back, turning one compromised
credential into a permanent one. ``test_a_token_cannot_be_used_to_mint_a_token``
is that guard.

**The exemption is from logout, not from losing SEEK access.** A script token
must survive the user closing a tab, or it is useless. It must not survive SEEK
revoking their refresh token, because nearly every NExtSEEK endpoint is
SEEK-backed and could no longer answer for them anyway.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from nextseek_api import local_tokens
from nextseek_api.assistant.models_db import SelfServiceApiToken

pytestmark = pytest.mark.django_db

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
URL = "/nextseek_api/me/api-token/"


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


def _user(username="researcher"):
    return User.objects.create_user(username=username, password="pw")


# -- the exemption -----------------------------------------------------------


def test_a_session_token_is_revoked_at_logout():
    """Unchanged from SP3: a token the user never asked for dies with the
    session that justified it."""
    user = _user()
    local_tokens.ensure_for(user)

    assert local_tokens.revoke_for(user) is True
    assert not Token.objects.filter(user=user).exists()


def test_a_self_service_token_survives_logout():
    """The decision this sub-project implements. A token issued for a script
    would be useless if closing a browser tab destroyed it."""
    user = _user()
    key = local_tokens.ensure_for(user)
    local_tokens.mark_self_service(user)

    assert local_tokens.revoke_for(user) is False
    assert Token.objects.get(user=user).key == key


def test_a_self_service_token_does_not_survive_losing_seek_access():
    """The exemption is from logout only. Once SEEK rejects the refresh token,
    the user's SEEK access is gone and the SEEK-backed API cannot answer for
    them, so the credential goes too."""
    user = _user()
    local_tokens.ensure_for(user)
    local_tokens.mark_self_service(user)

    assert local_tokens.revoke_for(user, force=True) is True
    assert not Token.objects.filter(user=user).exists()


def test_revoking_clears_the_marker_too():
    """A stale marker would exempt the *next* token the user is issued -- one
    they never asked to be long-lived."""
    user = _user()
    local_tokens.ensure_for(user)
    local_tokens.mark_self_service(user)
    local_tokens.revoke_for(user, force=True)

    assert not SelfServiceApiToken.objects.filter(user=user).exists()

    local_tokens.ensure_for(user)
    assert local_tokens.is_self_service(user) is False
    assert local_tokens.revoke_for(user) is True  # session-bound again


def test_an_unreadable_marker_fails_closed(monkeypatch):
    """Revoking a script token by mistake costs one re-issue. Keeping a token
    alive by mistake leaves a credential we meant to destroy."""
    user = _user()
    local_tokens.ensure_for(user)
    local_tokens.mark_self_service(user)

    def _boom(*args, **kwargs):
        raise RuntimeError("db is unhappy")

    monkeypatch.setattr(SelfServiceApiToken.objects, "filter", _boom)
    assert local_tokens.is_self_service(user) is False


# -- the endpoint ------------------------------------------------------------


def test_issuing_returns_the_key_once_and_marks_it_self_service(client):
    user = _user()
    client.force_login(user)

    response = client.post(URL)

    assert response.status_code == 201
    body = response.json()
    assert body["token"] == Token.objects.get(user=user).key
    assert body["self_service"] is True
    assert local_tokens.is_self_service(user) is True


def test_the_key_is_never_readable_afterwards(client):
    """Shown once, at issue. Read access to this endpoint must not be a way to
    obtain a working credential."""
    user = _user()
    client.force_login(user)
    issued = client.post(URL).json()["token"]

    response = client.get(URL)

    assert response.json()["has_token"] is True
    assert response.json()["self_service"] is True
    # The key must appear nowhere in the payload, under any field name.
    assert issued not in response.content.decode()


def test_issuing_twice_rotates_rather_than_returning_the_old_key(client):
    """Rotation is the honest answer to both "I lost it" and "it leaked", and
    it keeps this endpoint from ever being a retrieval path."""
    user = _user()
    client.force_login(user)

    first = client.post(URL).json()["token"]
    second = client.post(URL).json()["token"]

    assert first != second
    assert Token.objects.get(user=user).key == second
    assert Token.objects.filter(user=user).count() == 1


def test_revoking_destroys_the_token(client):
    user = _user()
    client.force_login(user)
    client.post(URL)

    response = client.delete(f"{URL}revoke/")

    assert response.status_code == 200
    assert response.json()["revoked"] is True
    assert not Token.objects.filter(user=user).exists()


def test_an_anonymous_caller_is_refused(client):
    assert client.get(URL).status_code in (401, 403)
    assert client.post(URL).status_code in (401, 403)


def test_a_token_cannot_be_used_to_mint_a_token(client):
    """Session auth only. Otherwise a single leaked token could rotate itself
    forever and read the replacement back -- a permanent compromise from a
    temporary one."""
    user = _user()
    key = local_tokens.ensure_for(user)

    response = client.post(URL, HTTP_AUTHORIZATION=f"Token {key}")

    assert response.status_code in (401, 403)


def test_basic_credentials_cannot_mint_a_token(client):
    """The endpoint exists to remove the password dependency; accepting a
    password here would reintroduce it."""
    import base64

    _user()
    raw = base64.b64encode(b"researcher:pw").decode()

    response = client.post(URL, HTTP_AUTHORIZATION=f"Basic {raw}")

    assert response.status_code in (401, 403)


def test_a_failure_to_issue_reports_service_unavailable(client, monkeypatch):
    user = _user()
    client.force_login(user)
    monkeypatch.setattr(local_tokens, "ensure_for", lambda u: None)

    assert client.post(URL).status_code == 503
