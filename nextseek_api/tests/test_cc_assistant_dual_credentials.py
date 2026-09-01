"""``cc_assistant`` needs two credentials, in opposite directions (#16, SP3).

One file, one former helper, two different services:

* the **chat pipeline** calls NExtSEEK's own API and wants the caller's DRF
  token (or a Basic pair);
* ``resolve_user_project`` builds a ``SeekDB`` and calls ``getCurrentUser()``
  (``cc_assistant/cc_provision.py``), which goes to **SEEK**, and wants an OAuth
  token provider.

The single ``_resolve_credentials`` that used to serve both could express
neither for an OAuth caller: it ended at ``session["password"]`` and returned
``(username, None)``. Handing either consumer the other's credential produces a
401 from whichever service was not expecting it, which is why the split exists
and why these tests assert the two are never crossed.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from nextseek_api import local_tokens
from nextseek_api.cc_assistant import cc_provision
from nextseek_api.services.cc_assistant import CCAssistantViewSet

pytestmark = pytest.mark.django_db

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


def _request(user, session=None):
    return SimpleNamespace(
        META={}, method="GET",
        session=session if session is not None else {},
        user=user,
    )


def _db_user(username="researcher"):
    return User.objects.create_user(username=username, password="x")


# -- the two resolvers stay apart --------------------------------------------


def test_an_oauth_caller_gets_a_drf_token_for_chat_and_a_provider_for_seek(oauth_on):
    """The heart of the split. Neither credential is usable at the other's
    service, so both must be resolved and kept distinct."""
    user = _db_user()
    key = local_tokens.ensure_for(user)
    view = CCAssistantViewSet()
    request = _request(user, {"username": "researcher"})

    with patch("seek.oauth.service.get_valid_access_token", return_value="seek-at-1"):
        chat = view._chat_credentials(request)
        seek_user, seek_pass, provider = view._seek_credentials(request)

    # NExtSEEK side: a DRF token, no password.
    assert chat["api_token"] == key
    assert chat["api_pass"] is None

    # SEEK side: a token provider, and emphatically not the DRF token.
    assert seek_pass is None
    assert provider is not None and provider() == "seek-at-1"
    assert provider() != key

    # api_user survives on both: resolve_user_project names a personal
    # namespace after it when the user belongs to no SEEK project.
    assert seek_user == "researcher"


def test_a_leftover_session_password_resolves_neither_credential(oauth_on):
    """Inverted by the cutover (#16, sub-project 5).

    Both resolvers used to hand back the session's Basic pair. Neither has a
    password branch now, so a stale password resolves nothing on the NExtSEEK
    side and nothing on the SEEK side -- the split still holds, with one source
    each instead of two.
    """
    user = _db_user()
    view = CCAssistantViewSet()
    request = _request(user, {"username": "researcher", "password": "hunter2"})

    with patch("seek.oauth.service.get_valid_access_token", return_value=None):
        chat = view._chat_credentials(request)
        seek_user, seek_pass, provider = view._seek_credentials(request)

    assert chat["api_pass"] is None and chat["api_token"] is None
    assert seek_pass is None and provider is None
    assert (chat["api_user"], seek_user) == ("researcher", "researcher")


# -- resolve_user_project accepts the SEEK token -----------------------------


def test_resolve_user_project_passes_the_token_provider_to_seekdb():
    captured = {}

    def factory(server, username, password, token_provider=None):
        captured["args"] = (server, username, password)
        captured["token_provider"] = token_provider
        return SimpleNamespace(
            getCurrentUser=lambda: {
                "data": {"relationships": {"projects": {"data": [{"id": 3}]}}}
            },
            getProjectName=lambda pid: "Ada Lab",
        )

    provider = lambda: "seek-at-1"
    cc_provision.resolve_user_project(
        "researcher", None, seekdb_factory=factory, token_provider=provider
    )

    assert captured["args"] == (None, "researcher", None)
    assert captured["token_provider"] is provider


def test_a_three_argument_factory_still_works():
    """seekdb_factory is a test injection point and existing doubles take three
    positional arguments. Offering a keyword they do not accept would break
    them, so the token is only passed when there is one."""
    def old_style_factory(server, username, password):
        return SimpleNamespace(
            getCurrentUser=lambda: {
                "data": {"relationships": {"projects": {"data": []}}}
            }
        )

    identity = cc_provision.resolve_user_project(
        "researcher", "pw", seekdb_factory=old_style_factory
    )
    assert identity.id == "personal-researcher"


def test_a_project_less_oauth_user_still_gets_a_personal_namespace():
    """Why api_user is still resolved even when a token supplies the auth."""
    def factory(server, username, password, token_provider=None):
        return SimpleNamespace(
            getCurrentUser=lambda: {
                "data": {"relationships": {"projects": {"data": []}}}
            }
        )

    identity = cc_provision.resolve_user_project(
        "researcher", None, seekdb_factory=factory, token_provider=lambda: "at-1"
    )
    assert identity.id == "personal-researcher"
    assert identity.title == "researcher"


# -- the deprecated shim -----------------------------------------------------


def test_the_old_helper_still_answers_but_cannot_express_a_token(oauth_on):
    """Kept so nothing outside the class breaks on the rename. Its inability to
    carry a token is exactly why callers were moved off it."""
    user = _db_user()
    local_tokens.ensure_for(user)
    view = CCAssistantViewSet()

    api_user, api_pass = view._resolve_credentials(
        _request(user, {"username": "researcher"})
    )
    assert (api_user, api_pass) == ("researcher", None)
