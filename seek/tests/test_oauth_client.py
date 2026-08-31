"""The SEEK OAuth network layer, against a stubbed SEEK.

Three properties are worth more than the rest here, because each corresponds to
a specific way this goes wrong in production rather than in review.

**Only ``invalid_grant`` is InvalidGrant.** The caller's response to that
exception is to erase the user's stored credentials. Doorkeeper also returns
``invalid_client`` for a mistyped client secret -- a NExtSEEK configuration
error that has nothing to do with any user's token. If that mapped to
InvalidGrant, a single wrong character in the deployment's secret would empty
the token table one user at a time as each of them came back and tried to
refresh, and the symptom would present as "everyone was logged out" rather than
as "the secret is wrong".

**A missing ``refresh_token`` is not an error.** Doorkeeper omits it when it
does not rotate. Treating absence as failure would break the deployment where
everything is actually fine.

**A missing ``expires_in`` means "soon", not "never".** The two readings fail
in opposite directions; only one of them is recoverable.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from seek.oauth import client

OAUTH_SETTINGS = dict(
    SEEK_OAUTH_CLIENT_ID="client-abc",
    SEEK_OAUTH_CLIENT_SECRET="secret-xyz",
    SEEK_OAUTH_REDIRECT_URI="https://nextseek.test/oauth/seek/callback",
    SEEK_OAUTH_SCOPE="read write",
    SEEK_OAUTH_AUTHORIZE_URL="https://seek.public.test/oauth/authorize",
    SEEK_OAUTH_TOKEN_URL="http://seek:3000/oauth/token",
    SEEK_URL="http://seek:3000",
    SEEK_OAUTH_HTTP_TIMEOUT=10,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text_body=None):
        self.status_code = status_code
        self._payload = payload
        self._text_body = text_body

    def json(self):
        if self._text_body is not None:
            raise ValueError("not json")
        return self._payload


def _token_post(**kwargs):
    return patch("seek.oauth.client.requests.post", **kwargs)


def _people_get(**kwargs):
    return patch("seek.oauth.client.requests.get", **kwargs)


# -- authorize URL -----------------------------------------------------------


@override_settings(**OAUTH_SETTINGS)
def test_authorize_url_targets_the_public_host_and_carries_the_state():
    url = client.build_authorize_url("st4te")
    assert url.startswith("https://seek.public.test/oauth/authorize?")
    assert "state=st4te" in url
    assert "response_type=code" in url
    assert "client_id=client-abc" in url
    assert "scope=read+write" in url
    # The secret is for the server-to-server exchange only; it must never reach
    # a URL the browser follows.
    assert "secret-xyz" not in url


@override_settings(**{**OAUTH_SETTINGS, "SEEK_OAUTH_SCOPE": ""})
def test_authorize_url_omits_scope_entirely_when_unconfigured():
    """Doorkeeper falls back to the application's default scopes when the
    parameter is absent, but rejects `scope=` outright."""
    assert "scope=" not in client.build_authorize_url("st4te")


@override_settings(**{**OAUTH_SETTINGS,
                      "SEEK_OAUTH_AUTHORIZE_URL": "https://seek.test/oauth/authorize?foo=1"})
def test_authorize_url_appends_to_an_existing_query_string():
    url = client.build_authorize_url("st4te")
    assert "?foo=1&" in url
    assert url.count("?") == 1


# -- token exchange, happy paths ---------------------------------------------


@override_settings(**OAUTH_SETTINGS)
def test_exchange_code_returns_parsed_tokens():
    payload = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_in": 7200,
        "scope": "read write",
    }
    with _token_post(return_value=FakeResponse(200, payload)) as post:
        tokens = client.exchange_code("the-code")

    assert tokens.access_token == "at-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.scope == "read write"
    expected = timezone.now() + timedelta(seconds=7200)
    assert abs((tokens.expires_at - expected).total_seconds()) < 30

    sent = post.call_args.kwargs["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["client_secret"] == "secret-xyz"


@override_settings(**OAUTH_SETTINGS)
def test_refresh_sends_the_refresh_grant():
    with _token_post(return_value=FakeResponse(200, {"access_token": "at-2", "expires_in": 60})) as post:
        client.refresh("rt-old")
    sent = post.call_args.kwargs["data"]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "rt-old"


@override_settings(**OAUTH_SETTINGS)
def test_a_response_without_a_refresh_token_is_not_an_error():
    """Doorkeeper omits it when it does not rotate. The caller keeps the one it
    already has; see service._refresh_locked."""
    with _token_post(return_value=FakeResponse(200, {"access_token": "at-3", "expires_in": 60})):
        tokens = client.refresh("rt-old")
    assert tokens.access_token == "at-3"
    assert tokens.refresh_token is None


@override_settings(**OAUTH_SETTINGS)
@pytest.mark.parametrize(
    "expires_in",
    [pytest.param(None, id="absent"), pytest.param("", id="empty"),
     pytest.param("banana", id="non-numeric"), pytest.param(0, id="zero"),
     pytest.param(-5, id="negative")],
)
def test_an_unusable_expires_in_becomes_a_short_life_not_an_unlimited_one(expires_in):
    body = {"access_token": "at-4"}
    if expires_in is not None:
        body["expires_in"] = expires_in
    with _token_post(return_value=FakeResponse(200, body)):
        tokens = client.refresh("rt")
    expected = timezone.now() + timedelta(seconds=client.DEFAULT_EXPIRES_IN)
    assert abs((tokens.expires_at - expected).total_seconds()) < 30


# -- error classification ----------------------------------------------------


@override_settings(**OAUTH_SETTINGS)
def test_invalid_grant_is_the_one_error_that_means_the_token_is_dead():
    body = {"error": "invalid_grant", "error_description": "revoked"}
    with _token_post(return_value=FakeResponse(400, body)):
        with pytest.raises(client.InvalidGrant):
            client.refresh("rt-dead")


@override_settings(**OAUTH_SETTINGS)
@pytest.mark.parametrize(
    "error",
    ["invalid_client", "invalid_scope", "unsupported_grant_type", "invalid_request", ""],
)
def test_configuration_errors_are_not_invalid_grant(error):
    """The load-bearing one. These describe NExtSEEK's own configuration, and
    the caller erases stored credentials on InvalidGrant -- so mapping any of
    them there would empty the token table one user at a time."""
    with _token_post(return_value=FakeResponse(401, {"error": error})):
        with pytest.raises(client.SeekOAuthError) as caught:
            client.refresh("rt-fine")
    assert not isinstance(caught.value, client.InvalidGrant)


@override_settings(**OAUTH_SETTINGS)
def test_a_server_error_is_transient():
    with _token_post(return_value=FakeResponse(503, None)):
        with pytest.raises(client.TransientError):
            client.refresh("rt")


@override_settings(**OAUTH_SETTINGS)
def test_an_unreachable_seek_is_transient():
    with _token_post(side_effect=requests.ConnectionError("no route")):
        with pytest.raises(client.TransientError):
            client.refresh("rt")


@override_settings(**OAUTH_SETTINGS)
def test_a_timeout_is_transient():
    with _token_post(side_effect=requests.Timeout("too slow")):
        with pytest.raises(client.TransientError):
            client.refresh("rt")


@override_settings(**OAUTH_SETTINGS)
def test_a_non_json_body_is_an_error_not_a_crash():
    with _token_post(return_value=FakeResponse(200, text_body="<html>a proxy page</html>")):
        with pytest.raises(client.SeekOAuthError):
            client.refresh("rt")


@override_settings(**OAUTH_SETTINGS)
def test_a_success_without_an_access_token_is_an_error():
    with _token_post(return_value=FakeResponse(200, {"token_type": "Bearer"})):
        with pytest.raises(client.SeekOAuthError):
            client.refresh("rt")


# -- /people/current ---------------------------------------------------------


@override_settings(**OAUTH_SETTINGS)
def test_current_person_returns_the_id_and_attributes():
    body = {"data": {"id": "42", "type": "people",
                     "attributes": {"first_name": "Ada", "last_name": "Lovelace"}}}
    with _people_get(return_value=FakeResponse(200, body)) as get:
        person_id, attributes = client.fetch_current_person("at-1")

    assert person_id == 42
    assert attributes["first_name"] == "Ada"
    # Server-to-server: the internal host, never the public one.
    assert get.call_args.args[0] == "http://seek:3000/people/current"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer at-1"


@override_settings(**OAUTH_SETTINGS)
def test_current_person_rejects_a_body_with_no_usable_id():
    for body in ({"data": {}}, {"data": {"id": None}}, {"data": {"id": "not-a-number"}}, {}):
        with _people_get(return_value=FakeResponse(200, body)):
            with pytest.raises(client.SeekOAuthError):
                client.fetch_current_person("at-1")


@override_settings(**OAUTH_SETTINGS)
def test_a_rejected_access_token_on_people_current_is_invalid_grant():
    with _people_get(return_value=FakeResponse(401, {})):
        with pytest.raises(client.InvalidGrant):
            client.fetch_current_person("at-stale")
