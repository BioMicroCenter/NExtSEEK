"""Network layer for SEEK's Doorkeeper OAuth2 endpoints.

Deliberately free of database access and of any notion of a Django user, so the
refresh state machine in ``seek/oauth/service.py`` can be tested against a
stubbed SEEK without a live instance.

Three facts about the SEEK side are unconfirmed against a real 1.15.1 instance
and this module is written not to depend on any of them:

* **Which scopes exist.** ``SEEK_OAUTH_SCOPE`` is forwarded verbatim; nothing
  here inspects or validates it.
* **The exact token-response shape.** Only ``access_token`` is required.
  ``refresh_token`` is optional, and a missing ``expires_in`` is treated as a
  short life rather than an unlimited one.
* **Whether refresh tokens rotate.** Callers persist a returned
  ``refresh_token`` when present and keep the existing one when absent, which
  is correct under either behaviour.

The browser reaches SEEK at ``SEEK_OAUTH_AUTHORIZE_URL`` (derived from the
public host); the token exchange is server-to-server via
``SEEK_OAUTH_TOKEN_URL`` (the internal host). Mixing those up is the classic
way to get a callback that works for the server and 404s for the user, or vice
versa.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

JSONAPI_ACCEPT = "application/vnd.api+json"

# Used when SEEK omits expires_in. An omitted lifetime is ambiguous in OAuth2,
# and the two readings fail in opposite directions: assume "never expires" and
# a token that did expire is used until every call 401s, with nothing
# triggering a refresh. Assume a short life and the worst case is a redundant
# refresh. The second is the recoverable one.
DEFAULT_EXPIRES_IN = 3600


class SeekOAuthError(Exception):
    """SEEK refused or could not answer an OAuth request."""


class InvalidGrant(SeekOAuthError):
    """The presented code or refresh token is dead: revoked, expired, or used.

    Raised ONLY for Doorkeeper's ``invalid_grant``. Callers may clear stored
    credentials in response, so no other error maps here -- see the note in
    ``_raise_for_oauth_error``.
    """


class TransientError(SeekOAuthError):
    """SEEK was unreachable or failed. The stored credentials are still good."""


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: Optional[str]
    expires_at: object  # datetime; aware, because settings.USE_TZ is True
    scope: str


def build_authorize_url(state, redirect_uri=None):
    """The URL to send the browser to. Public SEEK host, by construction."""
    params = {
        "client_id": settings.SEEK_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri or settings.SEEK_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    # Only send scope when one is configured: Doorkeeper applies the
    # application's default scopes when the parameter is absent, but rejects an
    # empty-string scope outright.
    if settings.SEEK_OAUTH_SCOPE:
        params["scope"] = settings.SEEK_OAUTH_SCOPE
    separator = "&" if "?" in settings.SEEK_OAUTH_AUTHORIZE_URL else "?"
    return f"{settings.SEEK_OAUTH_AUTHORIZE_URL}{separator}{urlencode(params)}"


def exchange_code(code, redirect_uri=None):
    """Trade an authorization code for tokens (server-to-server)."""
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or settings.SEEK_OAUTH_REDIRECT_URI,
        }
    )


def refresh(refresh_token):
    """Trade a refresh token for a new access token."""
    return _post_token({"grant_type": "refresh_token", "refresh_token": refresh_token})


def fetch_current_person(access_token):
    """Return SEEK's ``/people/current`` for the token holder.

    Yields ``(person_id, attributes)``. Only ``person_id`` is depended on: it is
    the durable identity link, and which attributes SEEK exposes here varies by
    version. The caller resolves the login name and contact details from the
    mirrored ``users``/``people`` tables, which are authoritative and always
    present, falling back to these attributes.
    """
    url = _internal_base() + "/people/current"
    try:
        resp = requests.get(
            url,
            headers={
                "Accept": JSONAPI_ACCEPT,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=settings.SEEK_OAUTH_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TransientError(f"SEEK /people/current was unreachable: {exc}") from exc

    if resp.status_code >= 500:
        raise TransientError(f"SEEK /people/current returned {resp.status_code}")
    if resp.status_code in (401, 403):
        raise InvalidGrant(f"SEEK rejected the access token ({resp.status_code})")
    if resp.status_code >= 400:
        raise SeekOAuthError(f"SEEK /people/current returned {resp.status_code}")

    try:
        data = (resp.json() or {}).get("data") or {}
    except ValueError as exc:
        raise SeekOAuthError("SEEK /people/current returned a non-JSON body") from exc

    raw_id = data.get("id")
    try:
        person_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise SeekOAuthError(
            "SEEK /people/current returned no usable person id"
        ) from exc

    return person_id, (data.get("attributes") or {})


# -- internals ---------------------------------------------------------------


def _internal_base():
    """The internal SEEK API root, for server-to-server calls."""
    return (getattr(settings, "SEEK_URL", "") or "").rstrip("/")


def _post_token(payload):
    data = dict(payload)
    data["client_id"] = settings.SEEK_OAUTH_CLIENT_ID
    data["client_secret"] = settings.SEEK_OAUTH_CLIENT_SECRET

    try:
        resp = requests.post(
            settings.SEEK_OAUTH_TOKEN_URL,
            data=data,
            headers={"Accept": "application/json"},
            timeout=settings.SEEK_OAUTH_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TransientError(f"SEEK token endpoint was unreachable: {exc}") from exc

    if resp.status_code >= 500:
        raise TransientError(f"SEEK token endpoint returned {resp.status_code}")

    try:
        body = resp.json() or {}
    except ValueError as exc:
        raise SeekOAuthError("SEEK token endpoint returned a non-JSON body") from exc

    if resp.status_code >= 400:
        _raise_for_oauth_error(body, resp.status_code)

    return _parse_token_response(body)


def _raise_for_oauth_error(body, status_code):
    """Map an OAuth2 error body onto an exception class.

    ``invalid_grant`` is the ONLY error that becomes InvalidGrant, because the
    caller's response to it is to clear the user's stored credentials. Every
    other error -- ``invalid_client`` above all -- describes something wrong
    with NExtSEEK's own configuration, not with this user's token. Mapping a
    mistyped client secret onto InvalidGrant would delete every stored token in
    the instance, one user at a time, as each of them tried to refresh.
    """
    error = (body.get("error") or "").strip()
    description = (body.get("error_description") or "").strip()
    detail = f"{error or status_code}" + (f": {description}" if description else "")

    if error == "invalid_grant":
        raise InvalidGrant(f"SEEK rejected the grant ({detail})")

    log.error(
        "seek_oauth: token endpoint returned status=%s error=%s. This is a "
        "NExtSEEK configuration problem, not a per-user one; stored tokens are "
        "left untouched.",
        status_code,
        error or "(none)",
    )
    raise SeekOAuthError(f"SEEK token endpoint refused the request ({detail})")


def _parse_token_response(body):
    access_token = body.get("access_token")
    if not access_token:
        raise SeekOAuthError("SEEK token response carried no access_token")

    raw_expires = body.get("expires_in")
    try:
        expires_in = int(raw_expires)
    except (TypeError, ValueError):
        log.warning(
            "seek_oauth: token response omitted a usable expires_in; assuming "
            "%ds. If this is every response, the SEEK application is probably "
            "configured with non-expiring tokens.",
            DEFAULT_EXPIRES_IN,
        )
        expires_in = DEFAULT_EXPIRES_IN
    if expires_in <= 0:
        expires_in = DEFAULT_EXPIRES_IN

    return TokenResponse(
        access_token=access_token,
        # Absent is meaningful and NOT an error: Doorkeeper omits this when it
        # does not rotate refresh tokens. The caller keeps the one it has.
        refresh_token=body.get("refresh_token") or None,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
        scope=body.get("scope") or "",
    )
