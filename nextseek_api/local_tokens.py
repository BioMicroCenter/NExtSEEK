"""Per-user DRF tokens: how a service calls NExtSEEK's own API *as* a user.

**This is not a SEEK credential.** Keeping that straight is the whole reason
this module is named the way it is and lives here rather than under
``seek/oauth/``. There are two authentication systems in play and they point in
opposite directions:

* ``SeekOAuthToken`` (``seek/oauth/``) is what NExtSEEK presents *to SEEK*.
* the DRF token here is what a NExtSEEK-side service presents *to NExtSEEK*.

The chat stack needs the second. ``ChatConfig.API_USER``/``API_PASS`` build a
URL from ``NEXTSEEK_BASE_URL`` and send HTTP Basic to NExtSEEK's own DRF layer
(``chat_nextseek/.../helpers/tools/nextseek_api.py:131-132``); it never calls
SEEK. Handing it a SEEK OAuth token would present a ``Bearer`` credential to
``TokenAuthentication``/``SessionAuthentication``/``BasicAuthentication``, which
matches none of them, and every request would 401.

Why a DRF token rather than forwarding the caller's session cookie: an async
task or a cron job has no cookie, and running work after the request ends is the
whole model of sub-project 4. One mechanism covers Layer C, those workers, and
the ``docker/cc-runtime/`` clients that will lose Basic auth at cutover.

**Lifetime.** A DRF token does not expire on its own, which is the real cost of
this choice. It is bounded here instead, and there are two tiers.

A token the user never asked for -- minted at OAuth login so the chat stack can
act for them -- is deleted at logout and whenever their SEEK credentials are
cleared, so it lives exactly as long as the SEEK session that justified it.

A token they *deliberately issued* for a script is marked self-service and
survives logout: it would be useless otherwise, evaporating the moment they
closed a browser tab. It is still destroyed when SEEK rejects their refresh
token, because their SEEK access is then gone and nearly every NExtSEEK endpoint
is SEEK-backed. See ``revoke_for``.
"""

import logging

log = logging.getLogger(__name__)


def ensure_for(user):
    """Return ``user``'s NExtSEEK API token, creating it if absent.

    Idempotent: safe to call on every login. Returns None rather than raising if
    the token cannot be issued -- a failure here must not cost the user their
    login, it only means the chat stack cannot act on their behalf yet.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    try:
        from rest_framework.authtoken.models import Token

        token, created = Token.objects.get_or_create(user=user)
        if created:
            log.info("local_token: issued a NExtSEEK API token for user_id=%s", user.pk)
        return token.key
    except Exception:
        log.warning(
            "local_token: could not issue a NExtSEEK API token for user_id=%s; "
            "services acting on their behalf will fall back to whatever "
            "credential they already had.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return None


def get_for(user):
    """Return ``user``'s existing token, or None. Never creates one.

    The request path uses this rather than ``ensure_for`` on purpose. Minting is
    confined to the OAuth callback so the token's life is exactly the SEEK
    session's: if it has been revoked -- at logout, or because SEEK rejected the
    refresh -- a later request must not quietly bring it back. A user whose
    token is missing signs in again, which is the intended repair.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    try:
        from rest_framework.authtoken.models import Token

        token = Token.objects.filter(user=user).first()
        return token.key if token is not None else None
    except Exception:
        log.warning(
            "local_token: could not read the NExtSEEK API token for user_id=%s.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return None


def is_self_service(user):
    """Whether this user's token was deliberately issued for unattended use.

    Such a token survives logout (#16, SP5). DRF's Token is a OneToOne, so a
    user cannot hold a separate script token alongside their session one -- the
    single token they have is marked exempt instead.
    """
    if user is None or not getattr(user, "pk", None):
        return False
    try:
        from nextseek_api.assistant.models_db import SelfServiceApiToken

        return SelfServiceApiToken.objects.filter(user=user).exists()
    except Exception:
        # Fail closed: an unreadable marker means we do NOT skip revocation.
        # Revoking a script token by mistake costs the user one re-issue;
        # leaving a token alive by mistake is a credential we meant to destroy.
        log.warning(
            "local_token: could not read the self-service marker for user_id=%s; "
            "treating the token as session-bound.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return False


def mark_self_service(user):
    """Record that this user's token is for unattended use, exempting it from
    logout revocation. Idempotent."""
    if user is None or not getattr(user, "pk", None):
        return False
    try:
        from nextseek_api.assistant.models_db import SelfServiceApiToken

        _, created = SelfServiceApiToken.objects.get_or_create(user=user)
        if created:
            log.info(
                "local_token: user_id=%s issued a self-service API token; it will "
                "survive logout.",
                user.pk,
            )
        return True
    except Exception:
        log.warning(
            "local_token: could not mark the API token self-service for user_id=%s.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return False


def revoke_for(user, *, force=False):
    """Delete ``user``'s NExtSEEK API token. Returns whether one was removed.

    ``force=False`` (the default, used at logout) respects the self-service
    exemption: a token the user deliberately issued for a script must not
    evaporate because they closed a browser tab.

    ``force=True`` ignores it, and is what runs when SEEK rejects the user's
    refresh token. The exemption is from *logout*, not from losing SEEK access:
    at that point almost every NExtSEEK endpoint is SEEK-backed and cannot
    answer for them anyway, so a surviving token would authenticate to an API
    that has nothing to give it. A script in regular use keeps its own SEEK
    session alive -- its requests drive get_valid_access_token, and hence the
    refresh -- so this does not expire a token that is actually being used.

    Never raises: logging out must succeed regardless, and a token that outlives
    its session is a problem to log, not one to fail a request over.
    """
    if user is None or not getattr(user, "pk", None):
        return False
    if not force and is_self_service(user):
        log.debug(
            "local_token: keeping the self-service API token for user_id=%s across logout.",
            user.pk,
        )
        return False
    try:
        from nextseek_api.assistant.models_db import SelfServiceApiToken
        from rest_framework.authtoken.models import Token

        deleted, _ = Token.objects.filter(user=user).delete()
        # The marker goes with the token it described; a stale one would exempt
        # the *next* token the user is issued, which they never asked for.
        SelfServiceApiToken.objects.filter(user=user).delete()
        if deleted:
            log.info("local_token: revoked the NExtSEEK API token for user_id=%s", user.pk)
        return bool(deleted)
    except Exception:
        log.warning(
            "local_token: could not revoke the NExtSEEK API token for user_id=%s; "
            "it may outlive the session that justified it.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return False
