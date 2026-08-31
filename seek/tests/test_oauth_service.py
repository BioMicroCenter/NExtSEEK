"""``get_valid_access_token`` -- the refresh state machine.

The single behaviour worth stating up front, because every other test here is
in service of it: **a SEEK outage must not destroy stored credentials.**

Both a dead refresh token and an unreachable SEEK produce ``None``, so from a
caller's side they look identical. What must differ is the row afterwards. A
dead token is cleared, so later requests stop paying for a round trip to learn
the same thing. A transient failure leaves the row exactly as it was, so the
user is logged back in by the next successful call rather than by a fresh
authorization. Collapse those two and a SEEK restart logs out every user in the
instance -- and, because the tokens are gone, does so irreversibly.

``test_a_transient_failure_leaves_the_row_intact`` and
``test_a_configuration_error_leaves_the_row_intact`` are the ones that pin it.

A note on the locking tests: ``select_for_update`` has no effect on SQLite,
which is what the test settings use, so nothing here can prove the database
actually serialises two callers. What they *can* prove is the logic the lock
exists to enable -- that the second caller re-reads the row and returns the
first one's token instead of refreshing again. That is the half that lives in
this file; the other half needs the manual MySQL check in the sub-project 1
spec.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connections
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.oauth import client, service

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    """Every test here writes a token row, and EncryptedTextField encrypts on
    every save. Set explicitly rather than inherited so the file passes under
    either settings module."""
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


def _alias():
    return getattr(SeekOAuthToken, "_DATABASE", "default")


def _user(username="researcher"):
    return User.objects.create_user(username=username, password="irrelevant")


def _plant_unreadable_access_token(row):
    """Write a value that cannot be decrypted.

    Raw SQL, deliberately: ``QuerySet.update`` goes through the field's
    ``get_prep_value``, so it would helpfully *encrypt* the garbage and produce
    a row that decrypts perfectly to the string "not-a-fernet-token" -- a test
    that passes while proving nothing.
    """
    with connections[_alias()].cursor() as cur:
        cur.execute(
            "UPDATE seek_oauth_token SET access_token = %s WHERE id = %s",
            ["not-a-fernet-token", row.pk],
        )


def _row(user, *, access="at-live", refresh="rt-1", expires_in=3600, scope="read"):
    return SeekOAuthToken.objects.create(
        user=user,
        seek_person_id=42,
        access_token=access,
        refresh_token=refresh,
        access_token_expires_at=timezone.now() + timedelta(seconds=expires_in),
        scope=scope,
    )


def _tokens(access="at-new", refresh="rt-new", expires_in=3600, scope="read"):
    return client.TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
        scope=scope,
    )


# -- no refresh needed -------------------------------------------------------


def test_a_live_token_is_returned_without_touching_the_network():
    user = _user()
    _row(user, access="at-live", expires_in=3600)
    with patch("seek.oauth.client.refresh") as refresh:
        assert service.get_valid_access_token(user) == "at-live"
    refresh.assert_not_called()


def test_no_row_means_re_authentication():
    with patch("seek.oauth.client.refresh") as refresh:
        assert service.get_valid_access_token(_user()) is None
    refresh.assert_not_called()


def test_an_anonymous_or_unsaved_user_is_not_an_error():
    assert service.get_valid_access_token(None) is None
    assert service.get_valid_access_token(User()) is None


# -- refresh triggers --------------------------------------------------------


@pytest.mark.parametrize(
    "expires_in",
    [pytest.param(-60, id="already-expired"),
     pytest.param(30, id="inside-the-skew-window")],
)
def test_an_expired_or_nearly_expired_token_is_refreshed(expires_in):
    """The skew matters: a token with 30s left would otherwise be handed out and
    expire mid-flight, somewhere inside SEEK's request handling."""
    user = _user()
    _row(user, access="at-old", expires_in=expires_in)
    with patch("seek.oauth.client.refresh", return_value=_tokens("at-fresh")) as refresh:
        assert service.get_valid_access_token(user) == "at-fresh"
    refresh.assert_called_once_with("rt-1")

    row = SeekOAuthToken.objects.get(user=user)
    assert row.access_token == "at-fresh"
    assert row.access_token_expires_at > timezone.now()


def test_a_token_just_outside_the_skew_window_is_left_alone():
    user = _user()
    _row(user, access="at-live", expires_in=service.REFRESH_SKEW_SECONDS + 120)
    with patch("seek.oauth.client.refresh") as refresh:
        assert service.get_valid_access_token(user) == "at-live"
    refresh.assert_not_called()


def test_an_undecryptable_access_token_falls_through_to_refresh():
    """A row written under a retired key reads as None. The refresh token may
    still decrypt under a rotated key, so this is recoverable without a fresh
    authorization."""
    user = _user()
    _plant_unreadable_access_token(_row(user))
    with patch("seek.oauth.client.refresh", return_value=_tokens("at-fresh")):
        assert service.get_valid_access_token(user) == "at-fresh"


def test_an_expired_token_with_no_refresh_token_means_re_authentication():
    user = _user()
    _row(user, refresh=None, expires_in=-60)
    with patch("seek.oauth.client.refresh") as refresh:
        assert service.get_valid_access_token(user) is None
    refresh.assert_not_called()


# -- rotation-agnostic writes ------------------------------------------------


def test_a_rotated_refresh_token_is_persisted():
    user = _user()
    _row(user, refresh="rt-old", expires_in=-60)
    with patch("seek.oauth.client.refresh", return_value=_tokens(refresh="rt-rotated")):
        service.get_valid_access_token(user)
    assert SeekOAuthToken.objects.get(user=user).refresh_token == "rt-rotated"


def test_an_absent_refresh_token_keeps_the_existing_one():
    """Doorkeeper omits it when it does not rotate. Overwriting with None here
    would strand the user at the next expiry -- a bug that only shows up one
    token-lifetime after deployment."""
    user = _user()
    _row(user, refresh="rt-keep", expires_in=-60)
    with patch("seek.oauth.client.refresh", return_value=_tokens(refresh=None)):
        service.get_valid_access_token(user)
    assert SeekOAuthToken.objects.get(user=user).refresh_token == "rt-keep"


# -- failure handling: the asymmetry -----------------------------------------


def test_a_dead_refresh_token_is_cleared():
    user = _user()
    _row(user, expires_in=-60)
    with patch("seek.oauth.client.refresh", side_effect=client.InvalidGrant("revoked")):
        assert service.get_valid_access_token(user) is None

    row = SeekOAuthToken.objects.get(user=user)
    assert not row.access_token
    assert row.refresh_token is None
    # The row itself survives, so seek_person_id stays available for the
    # re-authentication that follows.
    assert row.seek_person_id == 42


def test_a_transient_failure_leaves_the_row_intact():
    """The one that matters. If this clears credentials, a SEEK restart logs
    out every user in the instance and no token survives to log them back in."""
    user = _user()
    _row(user, access="at-old", refresh="rt-keep", expires_in=-60)
    with patch("seek.oauth.client.refresh", side_effect=client.TransientError("down")):
        assert service.get_valid_access_token(user) is None

    row = SeekOAuthToken.objects.get(user=user)
    assert row.access_token == "at-old"
    assert row.refresh_token == "rt-keep"


def test_a_configuration_error_leaves_the_row_intact():
    """A mistyped client secret raises SeekOAuthError, not InvalidGrant. If it
    cleared credentials, one wrong character would empty the table one user at
    a time as each came back to refresh."""
    user = _user()
    _row(user, access="at-old", refresh="rt-keep", expires_in=-60)
    with patch("seek.oauth.client.refresh", side_effect=client.SeekOAuthError("invalid_client")):
        assert service.get_valid_access_token(user) is None

    row = SeekOAuthToken.objects.get(user=user)
    assert row.access_token == "at-old"
    assert row.refresh_token == "rt-keep"


# -- the re-check the lock exists for ----------------------------------------


def test_a_second_caller_reuses_the_first_refresh_rather_than_repeating_it():
    """The point of re-checking expiry *inside* the lock.

    A waiter evaluated the row as stale before it blocked; after acquiring the
    lock it must look again and find the winner's fresh token. Without the
    re-check the lock serialises two refreshes instead of preventing one -- and
    against a Doorkeeper that rotates, the second presents a token the first
    just revoked.
    """
    user = _user()
    _row(user, access="at-old", expires_in=-60)

    with patch("seek.oauth.client.refresh", return_value=_tokens("at-fresh")) as refresh:
        first = service.get_valid_access_token(user)
        second = service.get_valid_access_token(user)

    assert first == second == "at-fresh"
    refresh.assert_called_once()


# -- storing after a successful authorization --------------------------------


def test_store_tokens_creates_a_row():
    user = _user()
    service.store_tokens(user, _tokens("at-1", "rt-1"), seek_person_id=7)
    row = SeekOAuthToken.objects.get(user=user)
    assert (row.access_token, row.refresh_token, row.seek_person_id) == ("at-1", "rt-1", 7)


def test_store_tokens_replaces_an_unusable_row():
    """A fresh authorization supersedes whatever was there, including a row
    whose tokens no longer decrypt -- otherwise logging in again would not fix
    a key rotation."""
    user = _user()
    _plant_unreadable_access_token(_row(user))

    service.store_tokens(user, _tokens("at-2", "rt-2"), seek_person_id=7)

    assert SeekOAuthToken.objects.filter(user=user).count() == 1
    assert SeekOAuthToken.objects.get(user=user).access_token == "at-2"


def test_a_stored_token_is_not_readable_as_plaintext_in_the_column():
    user = _user()
    service.store_tokens(user, _tokens("super-secret-token", "rt"), seek_person_id=7)

    # Through the ORM the value round-trips, so that proves nothing on its own;
    # the column itself has to be read raw, because every ORM read path applies
    # from_db_value and would hand back the plaintext.
    assert SeekOAuthToken.objects.get(user=user).access_token == "super-secret-token"

    with connections[_alias()].cursor() as cur:
        cur.execute(
            "SELECT access_token FROM seek_oauth_token WHERE user_id = %s", [user.pk]
        )
        stored = cur.fetchone()[0]

    assert "super-secret-token" not in stored
