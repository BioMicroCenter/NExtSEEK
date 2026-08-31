"""The "Log in with SEEK" flow.

Two views, both 404 when ``SEEK_OAUTH_ENABLED`` is off. The URLs are registered
unconditionally rather than built into the urlconf behind the flag, so both
states can be exercised in one test process -- and "the OAuth surface is not
reachable in production" becomes an assertion rather than an assumption.

The callback is the security-sensitive half:

* ``state`` is single-use. It is popped from the session before it is compared,
  so a replayed callback finds nothing to match even if the value is correct.
* ``state`` is compared with ``hmac.compare_digest``.
* The ``next`` destination is validated against the current host before the
  redirect. Note that ``login_seek`` does not do this -- it splits the raw query
  string on ``?next=`` and redirects to whatever follows
  (``dmac/views.py:153-158``) -- so this deliberately does not copy it.
* SEEK returning ``?error=access_denied`` (the user clicked Deny) is a normal
  outcome and renders the login page with a message, not a traceback.

The session contract is the other half. The callback writes the same non-secret
keys ``login_seek`` does -- ``server``, ``storage``, ``storagetype``,
``username`` -- because a dozen non-test call sites read
``session['username']`` for display and attribution. It does not write
``password``, because there no longer is one. Until sub-project 2 lands, that
means SEEK API calls made from an OAuth session will fail; ``getSeekLogin`` has
been given a guard so they fail as "not authenticated" rather than as a
TypeError.
"""

import hmac
import logging
import secrets

import requests
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from seek.oauth import client, provisioning, service

log = logging.getLogger(__name__)

STATE_SESSION_KEY = "seek_oauth_state"
NEXT_SESSION_KEY = "seek_oauth_next"
BACKEND_PATH = "seek.oauth.backends.SeekOAuthBackend"

# Matches the non-"stay signed in" branch of login_seek (dmac/views.py:132).
# The OAuth flow has no equivalent checkbox, so it takes the shorter of the two.
SESSION_AGE_SECONDS = 43200


def _require_enabled():
    if not getattr(settings, "SEEK_OAUTH_ENABLED", False):
        raise Http404("SEEK OAuth is not enabled on this instance.")


def _login_error(request, message):
    return render(request, "login.html", {"error": message})


def _safe_next(request, candidate):
    """A redirect target, or "/" if it is not safely local."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return "/"


def seek_login(request):
    """Start the authorization-code flow: mint state, redirect to SEEK."""
    _require_enabled()

    state = secrets.token_urlsafe(32)
    request.session[STATE_SESSION_KEY] = state
    request.session[NEXT_SESSION_KEY] = _safe_next(request, request.GET.get("next"))

    return HttpResponseRedirect(client.build_authorize_url(state))


def seek_callback(request):
    """Handle SEEK's redirect back: verify state, exchange, log in."""
    _require_enabled()

    expected_state = request.session.pop(STATE_SESSION_KEY, None)
    destination = request.session.pop(NEXT_SESSION_KEY, None) or "/"

    error = request.GET.get("error")
    if error:
        # The user declined at SEEK, or SEEK refused the request. Normal.
        log.info("seek_oauth: authorization was not granted (%s)", error)
        return _login_error(
            request, "SEEK did not grant access. You have not been signed in."
        )

    presented_state = request.GET.get("state") or ""
    if not expected_state or not hmac.compare_digest(expected_state, presented_state):
        log.warning(
            "seek_oauth: callback rejected -- state missing or mismatched. No "
            "code was exchanged."
        )
        return _login_error(
            request, "This sign-in link has expired or is invalid. Please try again."
        )

    code = request.GET.get("code")
    if not code:
        return _login_error(request, "SEEK did not return an authorization code.")

    try:
        tokens = client.exchange_code(code)
        person_id, attributes = client.fetch_current_person(tokens.access_token)
    except client.SeekOAuthError as exc:
        log.warning("seek_oauth: could not complete the exchange with SEEK (%s)", exc)
        return _login_error(
            request, "Could not complete sign-in with SEEK. Please try again."
        )

    try:
        user, outcome = provisioning.resolve_or_provision(person_id, attributes)
    except provisioning.ProvisioningError as exc:
        log.error("seek_oauth: provisioning failed (%s)", exc)
        return _login_error(
            request,
            "Your SEEK account could not be matched to a NExtSEEK account. "
            "Please ask an administrator for help.",
        )

    # login() cycles the session key, so the session contract is written after
    # it rather than before.
    auth_login(request, user, backend=BACKEND_PATH)

    service.store_tokens(user, tokens, seek_person_id=person_id)

    # A NExtSEEK API token, so services can call NExtSEEK's own API as this user
    # (#16, sub-project 3). Not a SEEK credential -- see nextseek_api/local_tokens.py.
    # Issued here so its lifetime is bounded by the SEEK session: logout and any
    # clearing of the SEEK credentials revoke it.
    from nextseek_api.local_tokens import ensure_for

    ensure_for(user)

    # Exactly the keys login_seek writes (dmac/views.py:124-127), minus the
    # password. Note the key is `storage_type` with an underscore -- that is
    # what dmac/views.py:267 reads back.
    request.session["server"] = settings.SEEK_URL
    request.session["storage"] = settings.SEEK_URL
    request.session["storage_type"] = "SEEK"
    request.session["username"] = user.username
    request.session.set_expiry(SESSION_AGE_SECONDS)

    log.info(
        "seek_oauth: signed in django_user_id=%s (%s) via seek_person_id=%s",
        user.pk,
        outcome,
        person_id,
    )
    return HttpResponseRedirect(_safe_next(request, destination))


def revoke_on_logout(user):
    """Best-effort token revocation at SEEK, if configured.

    Off by default: whether SEEK 1.15.1 exposes ``/oauth/revoke`` is
    unconfirmed, so the default has to be the behaviour that works either way.

    Every failure is swallowed. Logging out is a local act, and it must succeed
    whether or not SEEK is reachable -- a user who cannot log out because a
    remote service is down is a worse outcome than a token that stays valid
    until it expires.
    """
    if not getattr(settings, "SEEK_OAUTH_REVOKE_ON_LOGOUT", False):
        return
    if user is None or not getattr(user, "pk", None):
        return

    from seek.models.nextseek import SeekOAuthToken

    try:
        row = SeekOAuthToken.objects.filter(user=user).first()
        if row is None or not row.access_token:
            return
        requests.post(
            (settings.SEEK_URL or "").rstrip("/") + "/oauth/revoke",
            data={
                "token": row.access_token,
                "client_id": settings.SEEK_OAUTH_CLIENT_ID,
                "client_secret": settings.SEEK_OAUTH_CLIENT_SECRET,
            },
            timeout=settings.SEEK_OAUTH_HTTP_TIMEOUT,
        )
    except Exception:
        log.warning(
            "seek_oauth: token revocation at logout failed; continuing with "
            "local logout.",
            exc_info=True,
        )
