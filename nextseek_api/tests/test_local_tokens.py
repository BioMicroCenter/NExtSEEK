"""Per-user NExtSEEK API tokens and their lifetime (#16, sub-project 3).

The point of the module under test is a distinction that is easy to lose: this
is the credential a service presents *to NExtSEEK*, not the one NExtSEEK
presents *to SEEK*. The chat stack calls NExtSEEK's own API, so a SEEK OAuth
token in that position would 401 against every configured DRF authenticator.

The tests that matter here are the lifetime ones. A DRF token does not expire on
its own -- that is the acknowledged cost of choosing it -- so what bounds it is
being deleted at logout and whenever the SEEK credentials that justified it are
cleared. If those two ever stop happening, every OAuth login quietly leaves
behind a permanent bearer credential, and nothing else in the system would
notice.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from nextseek_api import local_tokens

pytestmark = pytest.mark.django_db


def _user(username="researcher"):
    return User.objects.create_user(username=username, password="x")


# -- issuing -----------------------------------------------------------------


def test_a_token_is_issued_for_a_user():
    user = _user()
    key = local_tokens.ensure_for(user)
    assert key
    assert Token.objects.get(user=user).key == key


def test_issuing_is_idempotent():
    """Called on every OAuth login, so it must not churn the key -- a new key on
    each sign-in would invalidate whatever a running task was holding."""
    user = _user()
    first = local_tokens.ensure_for(user)
    second = local_tokens.ensure_for(user)
    assert first == second
    assert Token.objects.filter(user=user).count() == 1


def test_users_get_distinct_tokens():
    """Attribution is the reason for per-user tokens rather than one service
    account: the existing per-user gates keep working unchanged."""
    assert local_tokens.ensure_for(_user("alice")) != local_tokens.ensure_for(_user("bob"))


def test_an_anonymous_or_unsaved_user_yields_nothing():
    assert local_tokens.ensure_for(None) is None
    assert local_tokens.ensure_for(User()) is None


def test_issuing_failure_does_not_propagate(monkeypatch):
    """A token that cannot be issued costs the chat stack, not the login. The
    user must still get their session."""
    def _boom(*args, **kwargs):
        raise RuntimeError("db is unhappy")

    monkeypatch.setattr(Token.objects, "get_or_create", _boom)
    assert local_tokens.ensure_for(_user()) is None


# -- revoking: what bounds the lifetime --------------------------------------


def test_revoking_removes_the_token():
    user = _user()
    local_tokens.ensure_for(user)
    assert local_tokens.revoke_for(user) is True
    assert not Token.objects.filter(user=user).exists()


def test_revoking_is_safe_when_there_is_nothing_to_revoke():
    assert local_tokens.revoke_for(_user()) is False
    assert local_tokens.revoke_for(None) is False


def test_revoking_only_touches_that_user():
    alice, bob = _user("alice"), _user("bob")
    local_tokens.ensure_for(alice)
    bob_key = local_tokens.ensure_for(bob)

    local_tokens.revoke_for(alice)

    assert not Token.objects.filter(user=alice).exists()
    assert Token.objects.get(user=bob).key == bob_key


def test_revoking_failure_does_not_propagate(monkeypatch):
    """Logging out is a local act and must succeed regardless. A token that
    outlives its session is a thing to log, not to fail a request over."""
    user = _user()
    local_tokens.ensure_for(user)

    def _boom(*args, **kwargs):
        raise RuntimeError("db is unhappy")

    monkeypatch.setattr(Token.objects, "filter", _boom)
    assert local_tokens.revoke_for(user) is False


# -- the token can be reissued after revocation ------------------------------


def test_a_revoked_token_is_reissued_on_the_next_login():
    """Logout then log back in. The key changes, which is correct -- the old one
    was surrendered."""
    user = _user()
    first = local_tokens.ensure_for(user)
    local_tokens.revoke_for(user)
    second = local_tokens.ensure_for(user)

    assert second and second != first
