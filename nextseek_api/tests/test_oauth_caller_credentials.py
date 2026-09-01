"""Resolving a *caller's* SEEK credential for project scoping (#16, sub-project 2).

The design assumed Layer A meant adding ``"OAUTH"`` to sixteen restricted
``resolve_seek_auth`` orders. Reading them showed otherwise, and the shape of
what was actually wrong is worth stating.

Ten of the sixteen are pure auth gates already written as ``if not basic_tuple
and not request.user.is_authenticated``. An OAuth session *is* an authenticated
Django session, so those admitted OAuth callers before this sub-project touched
anything; adding a source to their order would have been noise.

Four more resolve ``api_user``/``api_pass`` to hand to the chat stack. Those
belong to sub-project 3, not here.

What was genuinely broken were the sites that need to *call* SEEK as the caller
to resolve their project scope. They built ``SeekDB(None, basic_tuple[0],
basic_tuple[1])``, which an OAuth caller cannot satisfy -- and the callers treat
an unresolvable scope as "sees nothing" (deliberately, so a lookup failure
cannot widen access). So an OAuth user got a **200 with no data**, not a 401.
A permissions bug in its least visible form.

``test_an_oauth_caller_resolves_their_projects`` is the regression guard.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nextseek_api import helpers

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture
def oauth_on(settings):
    settings.SEEK_OAUTH_ENABLED = True
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A
    return settings


@pytest.fixture
def oauth_off(settings):
    settings.SEEK_OAUTH_ENABLED = False
    return settings


def _request(authed=True):
    return SimpleNamespace(
        META={}, session={}, method="GET",
        user=SimpleNamespace(is_authenticated=authed, pk=7),
    )


def _stub_token(value):
    return patch("seek.oauth.service.get_valid_access_token", return_value=value)


# -- seekdb_for_caller -------------------------------------------------------


def test_a_caller_with_no_token_gets_no_seekdb(oauth_off):
    """Inverted by the cutover (#16, sub-project 5). This asserted that a Basic
    caller got a password-backed SeekDB; there is no password branch now, so a
    request with no OAuth token resolves nothing at all."""
    with patch("nextseek_api.helpers.SeekDB") as SeekDB:
        assert helpers.seekdb_for_caller(_request()) is None
    SeekDB.assert_not_called()


def test_an_oauth_caller_gets_a_token_backed_seekdb(oauth_on):
    with _stub_token("at-1"), \
         patch("nextseek_api.helpers.SeekDB") as SeekDB:
        helpers.seekdb_for_caller(_request())

    args, kwargs = SeekDB.call_args
    assert args == (None, None, None)
    assert kwargs["token_provider"]() == "at-1"


def test_a_caller_with_no_seek_credential_gets_nothing(oauth_on):
    """Still reachable -- a DRF-token client resolves neither a Basic pair nor
    an OAuth token. Callers must fail closed on this, not proceed unscoped."""
    with _stub_token(None):
        assert helpers.seekdb_for_caller(_request()) is None


def test_a_stored_token_is_now_the_only_source(oauth_on):
    """Was "a Basic pair is preferred over a stored token". The preference is
    moot: the Basic source is gone, so the token is all there is."""
    with _stub_token("at-1"), patch("nextseek_api.helpers.SeekDB") as SeekDB:
        helpers.seekdb_for_caller(_request())
    assert SeekDB.call_args.kwargs["token_provider"]() == "at-1"


# -- caller_is_authenticated -------------------------------------------------


def test_an_oauth_session_counts_as_authenticated(oauth_on):
    """The two gates that lacked an is_authenticated clause rejected OAuth
    callers outright; this is the check they now share with the other ten."""
    with _stub_token(None):
        assert helpers.caller_is_authenticated(_request(authed=True)) is True


def test_an_anonymous_request_is_not_authenticated(oauth_off):
    assert helpers.caller_is_authenticated(_request(authed=False)) is False


# -- the project-scope regression --------------------------------------------


def test_an_oauth_caller_resolves_their_projects(oauth_on):
    """The bug this commit exists for. Before it, an OAuth caller produced no
    credential here, _caller_seek_project_ids returned [], and the endpoint
    answered 200 with an empty result set instead of failing."""
    from nextseek_api.views import _caller_seek_project_ids

    seekdb = SimpleNamespace(getCurrentUser=lambda: {
        "data": {"relationships": {"projects": {"data": [{"id": 3}, {"id": 5}]}}}
    })
    with _stub_token("at-1"), \
         patch("nextseek_api.views.seekdb_for_caller", return_value=seekdb):
        assert _caller_seek_project_ids(_request()) == ["3", "5"]


def test_no_credential_still_scopes_to_nothing(oauth_on):
    """Fails closed. An unresolvable scope must never widen to "everything"."""
    from nextseek_api.views import _caller_seek_project_ids

    with patch("nextseek_api.views.seekdb_for_caller", return_value=None):
        assert _caller_seek_project_ids(_request()) == []


def test_a_seek_failure_still_scopes_to_nothing(oauth_on):
    from nextseek_api.views import _caller_seek_project_ids

    def _boom():
        raise RuntimeError("SEEK is down")

    seekdb = SimpleNamespace(getCurrentUser=_boom)
    with patch("nextseek_api.views.seekdb_for_caller", return_value=seekdb):
        assert _caller_seek_project_ids(_request()) == []
