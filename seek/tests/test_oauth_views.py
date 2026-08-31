"""The "Log in with SEEK" views.

The flag-off tests come first and matter most: they are the evidence that this
whole feature ships dark. The routes are registered unconditionally and the
views 404, precisely so this can be asserted in the same process that exercises
the flag-on path, rather than inferred from a conditional urlconf that the test
run never sees.

After that, the callback's four guards, each corresponding to a way this is
attacked or misused rather than merely broken:

* a replayed ``state`` -- popped before comparison, so a second use fails even
  with the right value;
* a forged or missing ``state`` -- rejected *before* any code is exchanged;
* an off-site ``next`` -- validated against the host, which ``login_seek``
  notably does not do (``dmac/views.py:153-158``);
* a declined authorization -- a normal outcome, not a traceback.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.oauth import client, views

pytestmark = pytest.mark.django_db(databases=["default", "seek"])

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

OAUTH_ON = dict(
    SEEK_OAUTH_ENABLED=True,
    SEEK_OAUTH_CLIENT_ID="client-abc",
    SEEK_OAUTH_CLIENT_SECRET="secret-xyz",
    SEEK_OAUTH_REDIRECT_URI="http://testserver/oauth/seek/callback",
    SEEK_OAUTH_SCOPE="read write",
    SEEK_OAUTH_AUTHORIZE_URL="https://seek.public.test/oauth/authorize",
    SEEK_OAUTH_TOKEN_URL="http://seek:3000/oauth/token",
    SEEK_OAUTH_TOKEN_KEYS=KEY_A,
    SEEK_OAUTH_HTTP_TIMEOUT=10,
)


@pytest.fixture
def oauth_on(settings):
    for name, value in OAUTH_ON.items():
        setattr(settings, name, value)
    return settings


@pytest.fixture
def oauth_off(settings):
    settings.SEEK_OAUTH_ENABLED = False
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


def _tokens(access="at-1", refresh="rt-1"):
    return client.TokenResponse(
        access_token=access, refresh_token=refresh,
        expires_at=timezone.now() + timedelta(hours=1), scope="read write",
    )


# -- shipped dark ------------------------------------------------------------


def test_the_routes_exist_even_while_the_flag_is_off(oauth_off):
    """Registered unconditionally on purpose: a conditional urlconf could not be
    exercised in both states in one process."""
    assert reverse("seek_oauth_login") == "/oauth/seek/login"
    assert reverse("seek_oauth_callback") == "/oauth/seek/callback"


@pytest.mark.parametrize("route", ["/oauth/seek/login", "/oauth/seek/callback"])
def test_the_views_404_while_the_flag_is_off(client_fixture, oauth_off, route):
    assert client_fixture.get(route).status_code == 404


def test_the_login_page_does_not_offer_seek_while_the_flag_is_off(client_fixture, oauth_off):
    body = client_fixture.get("/login").content.decode()
    assert "Log in with SEEK" not in body


# -- starting the flow -------------------------------------------------------


def test_login_redirects_to_seek_with_a_state_it_remembers(client_fixture, oauth_on):
    response = client_fixture.get("/oauth/seek/login")

    assert response.status_code == 302
    assert response["Location"].startswith("https://seek.public.test/oauth/authorize?")
    state = client_fixture.session[views.STATE_SESSION_KEY]
    assert state and f"state={state}" in response["Location"]


def test_login_refuses_to_remember_an_off_site_next(client_fixture, oauth_on):
    """Validated on the way in as well as on the way out, so a hostile `next`
    never reaches the session in the first place."""
    client_fixture.get("/oauth/seek/login?next=https://evil.test/steal")
    assert client_fixture.session[views.NEXT_SESSION_KEY] == "/"


def test_login_keeps_a_local_next(client_fixture, oauth_on):
    client_fixture.get("/oauth/seek/login?next=/samples/42")
    assert client_fixture.session[views.NEXT_SESSION_KEY] == "/samples/42"


# -- the callback's guards ---------------------------------------------------


def _start(client_fixture):
    client_fixture.get("/oauth/seek/login")
    return client_fixture.session[views.STATE_SESSION_KEY]


def test_a_missing_state_is_rejected_before_any_code_is_exchanged(client_fixture, oauth_on):
    with patch("seek.oauth.client.exchange_code") as exchange:
        response = client_fixture.get("/oauth/seek/callback?code=c")
    assert response.status_code == 200  # the login page, with an error
    exchange.assert_not_called()


def test_a_forged_state_is_rejected_before_any_code_is_exchanged(client_fixture, oauth_on):
    _start(client_fixture)
    with patch("seek.oauth.client.exchange_code") as exchange:
        client_fixture.get("/oauth/seek/callback?code=c&state=not-the-one")
    exchange.assert_not_called()


def test_a_state_cannot_be_replayed(client_fixture, oauth_on, seek_identity):
    """Popped before it is compared, so the second use of a *correct* value
    fails too. Without that, a leaked callback URL is replayable."""
    state = _start(client_fixture)
    url = f"/oauth/seek/callback?code=c&state={state}"

    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        first = client_fixture.get(url)
        assert first.status_code == 302

        with patch("seek.oauth.client.exchange_code") as exchange:
            second = client_fixture.get(url)
    assert second.status_code == 200
    exchange.assert_not_called()


def test_a_declined_authorization_renders_the_login_page(client_fixture, oauth_on):
    _start(client_fixture)
    with patch("seek.oauth.client.exchange_code") as exchange:
        response = client_fixture.get("/oauth/seek/callback?error=access_denied")
    assert response.status_code == 200
    exchange.assert_not_called()


def test_a_failing_exchange_renders_the_login_page(client_fixture, oauth_on):
    state = _start(client_fixture)
    with patch("seek.oauth.client.exchange_code",
               side_effect=client.TransientError("SEEK is down")):
        response = client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")
    assert response.status_code == 200
    assert not SeekOAuthToken.objects.exists()


# -- the happy path ----------------------------------------------------------


def test_a_successful_callback_signs_the_user_in_and_stores_the_token(
    client_fixture, oauth_on, seek_identity
):
    state = _start(client_fixture)
    with patch("seek.oauth.client.exchange_code", return_value=_tokens("at-1", "rt-1")), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        response = client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    assert response.status_code == 302
    user = User.objects.get(username="researcher")
    assert client_fixture.session["_auth_user_id"] == str(user.pk)

    row = SeekOAuthToken.objects.get(user=user)
    assert (row.access_token, row.refresh_token, row.seek_person_id) == ("at-1", "rt-1", 42)


def test_the_session_carries_username_but_never_a_password(
    client_fixture, oauth_on, seek_identity
):
    """The session contract. `username` is set because a dozen non-test call
    sites read it; `password` is absent because there is not one -- and
    getSeekLogin has a guard so that absence fails cleanly rather than as a
    TypeError."""
    state = _start(client_fixture)
    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    session = client_fixture.session
    assert session["username"] == "researcher"
    assert session["server"] == settings.SEEK_URL
    # `storage_type`, with the underscore, is the key dmac/views.py:267 reads.
    assert session["storage_type"] == "SEEK"
    assert "password" not in session


def test_the_callback_will_not_redirect_off_site(client_fixture, oauth_on, seek_identity):
    """Re-validated on the way out, not just on the way in -- the session value
    is trusted no further than the query parameter was."""
    client_fixture.get("/oauth/seek/login")
    session = client_fixture.session
    session[views.NEXT_SESSION_KEY] = "https://evil.test/steal"
    session.save()
    state = session[views.STATE_SESSION_KEY]

    with patch("seek.oauth.client.exchange_code", return_value=_tokens()), \
         patch("seek.oauth.client.fetch_current_person", return_value=(42, {})):
        response = client_fixture.get(f"/oauth/seek/callback?code=c&state={state}")

    assert response["Location"] == "/"


def test_the_login_page_offers_seek_while_the_flag_is_on(client_fixture, oauth_on):
    body = client_fixture.get("/login").content.decode()
    assert "Log in with SEEK" in body
    # Coexistence: the password form must still be there until SP2-4 land.
    assert 'name="password"' in body
