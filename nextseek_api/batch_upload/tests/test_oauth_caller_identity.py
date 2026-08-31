"""Batch upload resolves an OAuth caller's identity (#16, sub-project 4).

Sub-project 2 flagged this as a functional break to fix in SP4: an OAuth caller
resolves no Basic pair, so Phase 1a produces no ``person_id`` and the
Phase 1b-fallback is skipped, leaving ``lababbv = "NA"`` -- which feeds UID
generation and would stamp wrong lab abbreviations onto real samples.

Re-reading it after SP2 landed, the break is already closed, and by SP2 rather
than by anything here: **Phase 1b** goes through ``SeekDB.getSeekLogin``, which
SP2 taught to resolve a token provider, so an OAuth session gets both the person
id and the lababbv from it. The fallback path is skipped because it is not
needed, not because it silently failed.

That is worth a test rather than a paragraph. The chain it depends on --
getSeekLogin resolving a provider, handing it to SeekAPI, and getUserInfo going
out over Bearer -- spans three sub-projects, and nothing else asserts it end to
end. If any link regresses, the symptom is not an error: it is a successful
upload with "NA" in the UIDs.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.seekdb import SeekDB
from seek.tests.seek_mirror_rows import build_seek_identity

pytestmark = pytest.mark.django_db(databases=["default", "seek"])

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


@pytest.fixture
def oauth_caller(oauth_on):
    """A user signed in through SEEK: session username, no password."""
    from datetime import timedelta

    person_id = build_seek_identity(person_id=42, login="researcher")
    user = User.objects.create_user(username="researcher", password="x")
    user.set_unusable_password()
    user.save()
    SeekOAuthToken.objects.create(
        user=user, seek_person_id=person_id, access_token="at-1", refresh_token="rt-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return user


def _request(user):
    return SimpleNamespace(
        META={}, method="GET",
        session={"server": "http://seek:3000", "username": "researcher"},
        user=user,
    )


def test_an_oauth_caller_resolves_a_person_id_and_lababbv(oauth_caller):
    """The end-to-end chain SP4 was scheduled to build and SP2 already closed.

    getUserInfo is stubbed at the SEEK boundary: what is under test is that a
    password-less session reaches it *authenticated*, which is the link that was
    missing.
    """
    person_info = {
        "person_id": 42,
        "lababbv": "BMC",
        "projectid": "3",
        "projectname": "Ada Lab",
        "institutionname": "BMC",
    }

    with patch("seek.oauth.service.get_valid_access_token", return_value="at-1"), \
         patch.object(SeekDB, "getUserInfo", return_value=(person_info, True, "")):
        result = SeekDB(None, None, None).getSeekLogin(_request(oauth_caller), True)

    assert result["status"] is True
    assert result["lababbv"] == "BMC"
    assert result["password"] is None


def test_the_seek_call_goes_out_over_bearer(oauth_caller):
    """The link that spans SP1-SP3: the provider resolved in getSeekLogin has to
    reach SeekAPI, or the request leaves unauthenticated and SEEK answers 401 --
    which the caller would log and swallow into lababbv="NA"."""
    with patch("seek.oauth.service.get_valid_access_token", return_value="at-1"):
        seekdb = SeekDB(None, None, None)
        seekdb.getSeekLogin(_request(oauth_caller), False)
        api = seekdb._SeekDB__seekapi

    assert api._credentialConfig() == 'header = "Authorization: Bearer at-1"\n'
    # And no password anywhere in the command it would run.
    assert "--config -" in api.apiPost()


def test_a_caller_whose_token_died_does_not_resolve_an_identity(oauth_caller):
    """Fails closed. A revoked token must not produce a half-identity that
    reaches UID generation as "NA"."""
    with patch("seek.oauth.service.get_valid_access_token", return_value=None):
        result = SeekDB(None, None, None).getSeekLogin(_request(oauth_caller), True)

    assert result["status"] is False
    assert result.get("lababbv") is None
