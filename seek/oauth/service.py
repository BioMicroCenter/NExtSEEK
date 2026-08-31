"""``get_valid_access_token(user)`` -- the single entry point for SEEK tokens.

Every consumer, in every sub-project, asks this function and nothing else. It
takes a user rather than a request on purpose: a Celery task or a cron job has
no session to borrow, and "run as the triggering user" only works if a token
can be resolved from the user alone.

Concurrency is the whole difficulty, and it is not hypothetical. Doorkeeper may
rotate refresh tokens -- each refresh mints a new one and revokes the old. Two
callers for the same user (two browser tabs; a request and a worker) that both
observe an expired token will both try to refresh, and the loser presents a
token that has just been revoked. The user is logged out for no reason they
could have caused or avoided.

The fix is a row lock plus a re-check *inside* it. The re-check is the part
that matters and the part that looks redundant: the waiter blocked on the lock
read the row's staleness *before* blocking, so it must look again afterwards to
see the winner's freshly written token. Drop the second check and the lock
serialises the two refreshes without preventing either -- which is the exact
bug the lock was added to prevent, now harder to see.

The lock is held across one HTTP call to SEEK. That is deliberate: it is a
per-user row, so only that user's own concurrent requests serialise. It does
make ``SEEK_OAUTH_HTTP_TIMEOUT`` load-bearing, since that is what bounds how
long the lock can be held.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.oauth import client

log = logging.getLogger(__name__)

# Refresh this far before nominal expiry, so a token cannot expire in flight
# between our check and SEEK's.
REFRESH_SKEW_SECONDS = 60


def get_valid_access_token(user):
    """Return a live SEEK access token for ``user``, or None.

    None means "this user must authenticate again" and is a normal outcome, not
    an error: no token row, an unreadable row, a dead refresh token. Callers
    should treat it exactly as they treat an unauthenticated request.

    None is *also* what a SEEK outage produces. That is deliberate -- see
    ``_refresh_locked``: a transient failure must never destroy stored
    credentials, so it degrades to "not right now" rather than "log in again".
    """
    if user is None or not getattr(user, "pk", None):
        return None

    db = _db_alias()
    with transaction.atomic(using=db):
        try:
            row = (
                SeekOAuthToken.objects.using(db)
                .select_for_update()
                .get(user=user)
            )
        except SeekOAuthToken.DoesNotExist:
            return None

        if _is_live(row):
            return row.access_token

        return _refresh_locked(row, db)


def _db_alias():
    return getattr(SeekOAuthToken, "_DATABASE", "default")


def _is_live(row):
    """Whether the stored access token is usable without refreshing.

    ``row.access_token`` can be None even though the column is NOT NULL: an
    undecryptable value reads as None (see seek/oauth/crypto.py), which lands
    here as "not live" and falls through to a refresh -- the right answer, since
    the refresh token may still decrypt under a rotated key.
    """
    if not row.access_token or not row.access_token_expires_at:
        return False
    deadline = timezone.now() + timedelta(seconds=REFRESH_SKEW_SECONDS)
    return row.access_token_expires_at > deadline


def _refresh_locked(row, db):
    """Refresh in place. Must only be called while holding the row lock."""
    if not row.refresh_token:
        log.info(
            "seek_oauth: user_id=%s has an expired access token and no usable "
            "refresh token; re-authentication required.",
            row.user_id,
        )
        return None

    try:
        tokens = client.refresh(row.refresh_token)
    except client.InvalidGrant:
        # The refresh token is genuinely dead. Clearing it is what stops every
        # later request paying for a network round trip to learn the same
        # thing. Only client.InvalidGrant reaches here -- a mistyped client
        # secret raises SeekOAuthError instead and is caught below, precisely
        # so a configuration error cannot empty the table one user at a time.
        log.info(
            "seek_oauth: SEEK rejected the refresh token for user_id=%s; "
            "clearing it and requiring re-authentication.",
            row.user_id,
        )
        row.access_token = ""
        row.refresh_token = None
        row.save(using=db, update_fields=["access_token", "refresh_token", "updated_at"])
        # The NExtSEEK API token was issued on the strength of this SEEK session
        # (#16, sub-project 3). With the SEEK credential dead, it must not keep
        # letting services act as this user -- it is the one thing bounding a
        # DRF token's otherwise unlimited life.
        from nextseek_api.local_tokens import revoke_for

        revoke_for(row.user)
        return None
    except client.SeekOAuthError as exc:
        # Transient, or our own misconfiguration. Either way the stored
        # credentials are probably still good, so leave them completely alone:
        # deleting them here would log out every user in the instance during a
        # SEEK restart.
        log.warning(
            "seek_oauth: could not refresh the token for user_id=%s (%s); "
            "leaving the stored credentials intact.",
            row.user_id,
            exc,
        )
        return None

    row.access_token = tokens.access_token
    # Rotation-agnostic: persist a new refresh token when SEEK issues one, keep
    # the existing one when it does not. Correct under either behaviour, which
    # is why this does not need to know which one SEEK does.
    if tokens.refresh_token:
        row.refresh_token = tokens.refresh_token
    row.access_token_expires_at = tokens.expires_at
    if tokens.scope:
        row.scope = tokens.scope
    row.save(
        using=db,
        update_fields=[
            "access_token",
            "refresh_token",
            "access_token_expires_at",
            "scope",
            "updated_at",
        ],
    )
    return tokens.access_token


def token_provider_for_request(request):
    """A callable yielding a fresh access token for this request's user, or None.

    None means "this request carries no OAuth credential" and covers every
    ordinary case: the flag is off, the request is anonymous, the user has no
    usable token. Callers treat it exactly as they treat a missing password.

    A token is resolved once here so the caller can tell whether the credential
    exists at all, but the value is discarded: what is returned resolves again
    on each use, so a long-lived object cannot serve a token that has since
    expired.

    Never raises. This sits on the ordinary request path, so a SEEK outage
    during a refresh must look like "no credential" rather than a 500.
    """
    from django.conf import settings

    if not getattr(settings, "SEEK_OAUTH_ENABLED", False):
        return None
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        if not get_valid_access_token(user):
            return None
        return lambda: get_valid_access_token(user)
    except Exception:
        log.warning(
            "seek_oauth: could not resolve an access token for user_id=%s; "
            "treating the request as having no SEEK credential.",
            getattr(user, "pk", None),
            exc_info=True,
        )
        return None


def store_tokens(user, tokens, seek_person_id=None):
    """Create or replace ``user``'s stored SEEK credentials.

    Used by the OAuth callback after a successful code exchange. Separate from
    the refresh path because it must overwrite unconditionally: a fresh
    authorization supersedes whatever was there, including a row whose tokens
    no longer decrypt.
    """
    db = _db_alias()
    defaults = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "access_token_expires_at": tokens.expires_at,
        "scope": tokens.scope,
    }
    if seek_person_id is not None:
        defaults["seek_person_id"] = seek_person_id

    row, created = SeekOAuthToken.objects.using(db).update_or_create(
        user=user, defaults=defaults
    )
    log.info(
        "seek_oauth: stored SEEK credentials for user_id=%s (%s)",
        user.pk,
        "new" if created else "replaced",
    )
    return row
