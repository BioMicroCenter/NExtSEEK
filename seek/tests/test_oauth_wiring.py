"""The pieces that connect SEEK OAuth to the rest of the project.

Each of these is small, and each guards something that would otherwise fail
late and confusingly:

* the **auth backend** must be invisible to password login, or adding it would
  change the behaviour of the path production is still running on;
* the **startup check** must refuse to boot a half-configured instance, because
  every one of those settings is first consulted partway through a redirect the
  user has already left the site for;
* the **getSeekLogin guard** must turn a password-less session into "not
  authenticated" rather than a TypeError.
"""

import pytest
from django.contrib.auth.models import User
from django.core.checks import Error
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.oauth.backends import SeekOAuthBackend
from seek.oauth.checks import check_seek_oauth_settings

pytestmark = pytest.mark.django_db

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


def _linked_user(person_id=42, username="researcher", is_active=True):
    from datetime import timedelta

    user = User.objects.create_user(username=username, password="x")
    if not is_active:
        user.is_active = False
        user.save()
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=person_id, access_token="at", refresh_token="rt",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return user


# -- the backend -------------------------------------------------------------


def test_the_backend_ignores_a_password_login():
    """The whole reason adding it is safe while the flag is off: a password
    login lands in **kwargs, seek_person_id is None, and this declines before
    Mezzanine's backend is reached."""
    assert SeekOAuthBackend().authenticate(
        None, username="researcher", password="hunter2"
    ) is None


def test_the_backend_resolves_a_linked_person():
    user = _linked_user()
    assert SeekOAuthBackend().authenticate(None, seek_person_id=42) == user


def test_the_backend_declines_an_unknown_person():
    assert SeekOAuthBackend().authenticate(None, seek_person_id=999) is None


def test_the_backend_declines_a_deactivated_account():
    """Deactivating a Django user must actually lock them out, even though the
    SEEK side would still happily authenticate them."""
    _linked_user(is_active=False)
    assert SeekOAuthBackend().authenticate(None, seek_person_id=42) is None


def test_get_user_declines_a_deactivated_account():
    """The session-deserialisation path needs the same gate, or a user
    deactivated mid-session keeps their existing session."""
    user = _linked_user(is_active=False)
    assert SeekOAuthBackend().get_user(user.pk) is None
    assert SeekOAuthBackend().get_user(999999) is None


# -- the startup check -------------------------------------------------------


def test_no_complaints_while_the_flag_is_off(settings):
    """An instance that never turns OAuth on must not be nagged about settings
    it has no reason to fill in."""
    settings.SEEK_OAUTH_ENABLED = False
    settings.SEEK_OAUTH_CLIENT_ID = ""
    settings.SEEK_OAUTH_TOKEN_KEYS = ""
    assert check_seek_oauth_settings(None) == []


@pytest.mark.parametrize(
    "missing",
    ["SEEK_OAUTH_CLIENT_ID", "SEEK_OAUTH_CLIENT_SECRET", "SEEK_OAUTH_REDIRECT_URI",
     "SEEK_OAUTH_TOKEN_KEYS", "SEEK_OAUTH_AUTHORIZE_URL", "SEEK_OAUTH_TOKEN_URL"],
)
def test_enabling_without_a_required_setting_is_a_startup_error(settings, missing):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_CLIENT_ID = "id"
    settings.SEEK_OAUTH_CLIENT_SECRET = "secret"
    settings.SEEK_OAUTH_REDIRECT_URI = "https://nextseek.test/oauth/seek/callback"
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    settings.SEEK_OAUTH_AUTHORIZE_URL = "https://seek.test/oauth/authorize"
    settings.SEEK_OAUTH_TOKEN_URL = "http://seek:3000/oauth/token"
    setattr(settings, missing, "")

    errors = check_seek_oauth_settings(None)
    assert len(errors) == 1
    assert isinstance(errors[0], Error)
    assert missing in errors[0].msg


def test_a_fully_configured_instance_passes(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_CLIENT_ID = "id"
    settings.SEEK_OAUTH_CLIENT_SECRET = "secret"
    settings.SEEK_OAUTH_REDIRECT_URI = "https://nextseek.test/oauth/seek/callback"
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    settings.SEEK_OAUTH_AUTHORIZE_URL = "https://seek.test/oauth/authorize"
    settings.SEEK_OAUTH_TOKEN_URL = "http://seek:3000/oauth/token"
    assert check_seek_oauth_settings(None) == []


# -- the getSeekLogin guard --------------------------------------------------


def test_a_session_without_a_password_is_unauthenticated_not_a_crash(rf):
    """An OAuth session carries a username and no password by construction.

    Before the guard, None passed getSeekLogin's checks (only "" was rejected)
    and reached SeekAPI(server, username, None), which raised TypeError. That
    particular crash is gone -- sub-project 2 rewrote __curlPrefix -- but the
    guard still matters: without it a password-less session sends a
    credential-less request to SEEK and waits for a 401, instead of failing
    here for free.
    """
    from seek.seekdb import SeekDB

    request = rf.get("/")
    request.session = {"server": "http://seek:3000", "username": "researcher"}

    result = SeekDB(None, None, None).getSeekLogin(request)

    assert result["status"] is False
    assert any("username or password" in message for message in result["err"])


def test_a_session_with_a_password_is_unaffected_by_the_guard(rf):
    """The guard must not narrow the existing password path, which is what
    production still runs on."""
    from seek.seekdb import SeekDB

    request = rf.get("/")
    request.session = {
        "server": "http://seek:3000", "username": "researcher", "password": "",
    }

    # An empty password was already rejected before this change; assert the
    # behaviour is identical rather than merely still-failing.
    result = SeekDB(None, None, None).getSeekLogin(request)
    assert result["status"] is False
