"""The attributes auth bridge must accept an OAuth session (#16, sub-project 3).

``_selected_credential`` required ``session["password"]`` to be a non-empty
``str`` and otherwise raised ``AuthenticationFailed("Selected session SEEK
bridge is unavailable.")``. A session established through "Log in with SEEK" has
no password, so every OAuth caller was rejected by the whole attributes
subsystem. This was a Layer A leftover: sub-project 2 fixed the credential
*resolution* paths and missed this second, independent bridge.

The security shape is what to check when reading this. The new branch does not
call SEEK -- the caller's SEEK identity was proven at the OAuth callback and
recorded on the token row. But a recorded id is only as trustworthy as the row,
so it is still checked against SEEK's own ``users``/``people`` tables via
``_assert_local_seek_binding``, exactly as the Basic and DRF-token branches
check theirs. ``test_a_tampered_person_id_is_rejected`` is that guard.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from nextseek_api.attributes import auth as attributes_auth
from seek.models.nextseek import SeekOAuthToken

pytestmark = pytest.mark.django_db(databases=["default", "seek"])

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


def _oauth_user(username="researcher", person_id=42):
    from datetime import timedelta

    user = User.objects.create_user(username=username, password="x")
    user.set_unusable_password()
    user.save()
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=person_id, access_token="at-1", refresh_token="rt-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return user


def _oauth_request(user, username="researcher"):
    """A session with a username and, by construction, no password."""
    return SimpleNamespace(
        META={}, COOKIES={"sessionid": "abc"}, method="GET",
        session={"username": username}, user=user,
    )


# -- selection ---------------------------------------------------------------


def test_an_oauth_session_selects_the_oauth_scheme(settings):
    """Before this, the same request raised "Selected session SEEK bridge is
    unavailable" and the whole attributes API was closed to OAuth callers."""
    settings.SESSION_COOKIE_NAME = "sessionid"
    user = _oauth_user()

    selected = attributes_auth._selected_credential(
        _oauth_request(user), attributes_auth.SeekSessionAuthentication(), None
    )

    assert selected.scheme == "oauth"
    assert selected.password is None


def test_a_leftover_session_password_still_selects_oauth(settings):
    """Inverted by the cutover (#16, sub-project 5).

    The "session" scheme proved a SEEK password against SEEK. Nothing writes one
    now, but a stale value in an old session must not select a scheme whose
    proof path no longer has credentials behind it.
    """
    settings.SESSION_COOKIE_NAME = "sessionid"
    user = _oauth_user()
    request = _oauth_request(user)
    request.session["password"] = "hunter2"

    selected = attributes_auth._selected_credential(
        request, attributes_auth.SeekSessionAuthentication(), None
    )

    assert selected.scheme == "oauth"


def test_a_session_with_neither_credential_is_still_refused(settings):
    """A user with no token row has nothing to bind to and must not pass."""
    settings.SESSION_COOKIE_NAME = "sessionid"
    user = User.objects.create_user(username="stranger", password="x")

    with pytest.raises(AuthenticationFailed):
        attributes_auth._selected_credential(
            _oauth_request(user, "stranger"), attributes_auth.SeekSessionAuthentication(), None
        )


def test_an_oauth_session_is_not_treated_as_a_competing_credential(settings):
    """The session cookie is what carried the OAuth session, so the source and
    the selected scheme differ by name. Without the equivalence, every OAuth
    caller would be rejected as "conflicting"."""
    settings.SESSION_COOKIE_NAME = "sessionid"
    attributes_auth._reject_competing_sources(
        SimpleNamespace(META={}, COOKIES={"sessionid": "abc"}), "oauth"
    )  # must not raise


# -- proving -----------------------------------------------------------------


def test_the_recorded_person_id_is_used_without_calling_seek():
    """The callback already proved this against /people/current. Repeating it on
    every request would be a SEEK round trip per API call."""
    user = _oauth_user(person_id=42)
    selected = attributes_auth.SelectedSeekCredential("oauth", username="researcher")

    with patch.object(attributes_auth, "_assert_local_seek_binding") as binding, \
         patch.object(attributes_auth, "SeekAPIClient") as seek_client:
        identity = attributes_auth._prove_seek_person(selected, user)

    assert identity.person_id == 42
    assert identity.scheme == "oauth"
    seek_client.assert_not_called()
    binding.assert_called_once_with(user, 42)


def test_a_tampered_person_id_is_rejected():
    """The guard that makes skipping the SEEK call safe. A recorded id is only
    as trustworthy as the row it sits in, so it is still checked against SEEK's
    own users/people tables."""
    user = _oauth_user(person_id=999)
    selected = attributes_auth.SelectedSeekCredential("oauth", username="researcher")

    with patch.object(attributes_auth, "_assert_local_seek_binding",
                      side_effect=AuthenticationFailed("mismatch")):
        with pytest.raises(AuthenticationFailed):
            attributes_auth._prove_seek_person(selected, user)


def test_a_missing_person_id_is_rejected():
    from datetime import timedelta

    user = User.objects.create_user(username="researcher", password="x")
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=None, access_token="at-1", refresh_token="rt-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    selected = attributes_auth.SelectedSeekCredential("oauth", username="researcher")

    with pytest.raises(AuthenticationFailed):
        attributes_auth._prove_seek_person(selected, user)
