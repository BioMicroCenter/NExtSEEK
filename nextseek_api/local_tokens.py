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
this choice. It is bounded here instead: minted at OAuth login, deleted at
logout, and deleted whenever the user's SEEK credentials are cleared. So it
lives exactly as long as the SEEK session that justified it, rather than
indefinitely.
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


def revoke_for(user):
    """Delete ``user``'s NExtSEEK API token. Returns whether one was removed.

    Called at logout and whenever SEEK credentials are cleared. Never raises:
    logging out must succeed regardless, and a token that outlives its session
    is a problem to log, not one to fail a request over.
    """
    if user is None or not getattr(user, "pk", None):
        return False
    try:
        from rest_framework.authtoken.models import Token

        deleted, _ = Token.objects.filter(user=user).delete()
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
