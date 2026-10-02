"""Acting as the user downstream under a turn pass (MAP-APPROACH-1 section 4): every consumer gets the login the
turn holds; never the pass itself, never a session's login, never the shared config's (piece 1)."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import CCTurn, QueryTask
from nextseek_api.helpers import SeekAPIClient, basic_auth_header, get_token_auth, resolve_seek_auth
from nextseek_api.tests.turn_pass_support import PASSWORD, make_turn, make_user, pass_header, pass_request

pytestmark = pytest.mark.django_db

A = "/nextseek_api/assistant"


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"


class _Seek:
    """requests.Session.request stand-in: records the headers it was given and answers like SEEK.

    Set on the class, an instance is not a descriptor, so SeekAPIClient's ``self.session.request(method=...,
    url=..., headers=..., ...)`` calls it with keywords only and no session argument."""

    def __init__(self, body=b"{}"):
        self.body, self.headers = body, None

    def __call__(self, *args, headers=None, **kwargs):
        self.headers = headers
        return SimpleNamespace(content=self.body, status_code=200,
                               headers={"Content-Type": "application/vnd.api+json"})


def _wipe_login(turn):
    CCTurn.objects.filter(pk=turn.pk).update(login_nonce=None, login_ciphertext=None)
    turn.refresh_from_db()


def test_resolve_seek_auth_gives_the_held_login_whatever_the_order():
    turn, _ = make_turn(login=("seek-user", PASSWORD))
    assert resolve_seek_auth(pass_request(turn)) == (("seek-user", PASSWORD), {})
    assert resolve_seek_auth(pass_request(turn), ["TOKEN"]) == (("seek-user", PASSWORD), {})


def test_a_pass_whose_login_is_gone_resolves_to_nothing_not_a_session():
    turn, _ = make_turn()
    _wipe_login(turn)
    request = pass_request(turn)
    request.session = {"username": "someone-else", "password": "their-password"}
    assert resolve_seek_auth(request) == (None, None)


def test_the_pass_is_never_read_as_a_token():
    request = SimpleNamespace(META={"HTTP_AUTHORIZATION": "NextseekTurn " + "A" * 43})
    assert get_token_auth(request) is None


def test_the_seek_proxy_sends_the_held_login_as_basic(monkeypatch):
    turn, _ = make_turn(login=("seek-user", PASSWORD))
    seek = _Seek()
    monkeypatch.setattr("requests.Session.request", seek)
    _body, code, _headers, _resp = SeekAPIClient().list_projects(pass_request(turn))
    assert code == 200
    assert seek.headers["Authorization"] == basic_auth_header(("seek-user", PASSWORD))["Authorization"]
    decoded = base64.b64decode(seek.headers["Authorization"].split(" ", 1)[1]).decode("utf-8")
    assert decoded == f"seek-user:{PASSWORD}"


def test_the_participation_check_asks_seek_with_the_held_login(monkeypatch):
    from nextseek_api.services.assistant import UserInParticipatingProject

    cache.clear()  # the check caches a positive answer per user id for 60 s
    turn, _ = make_turn(make_user("participant"), login=("participant", PASSWORD))
    person = {"data": {"relationships": {"projects": {"data": [{"id": "1"}]}}}}
    seek = _Seek(json.dumps(person).encode())
    monkeypatch.setattr("requests.Session.request", seek)
    assert UserInParticipatingProject().has_permission(pass_request(turn), None) is True
    assert seek.headers["Authorization"] == basic_auth_header(("participant", PASSWORD))["Authorization"]


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_an_op_config_carries_the_held_login(monkeypatch):
    """tool_nextseek_api_request (api-read, the graph_search fallback, submission metadata), the schema fetch and
    the SOP fetches all read these two attributes of the per-request config."""
    from nextseek_api.services.assistant import _granular_chat_config

    monkeypatch.setattr("nextseek_api.services.assistant.plain_scope", lambda user: None)
    turn, _ = make_turn(login=("op-user", PASSWORD))
    cfg = _granular_chat_config(pass_request(turn), SimpleNamespace(use_prod=False))
    assert (cfg.API_USER, cfg.API_PASS) == ("op-user", PASSWORD)


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_an_op_under_a_pass_never_falls_back_to_the_shared_login(monkeypatch):
    from nextseek_api.services.assistant import _granular_chat_config

    monkeypatch.setattr("nextseek_api.services.assistant.plain_scope", lambda user: None)
    turn, _ = make_turn()
    _wipe_login(turn)
    cfg = _granular_chat_config(pass_request(turn), SimpleNamespace(use_prod=False))
    assert (cfg.API_USER, cfg.API_PASS) == ("", "")


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_a_nested_turn_runs_as_the_held_login_and_names_its_turn(monkeypatch):
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    monkeypatch.setattr("nextseek_api.services.assistant.plain_scope", lambda user: None)
    started = []

    class _Thread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            started.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr("nextseek_api.services.assistant.threading.Thread", _Thread)
    turn, raw = make_turn(login=("nested-user", PASSWORD))
    resp = APIClient().post(f"{A}/query/async/",
                            {"query": "how many", "mode": "plan", "session_id": str(turn.chat_id)},
                            format="json", **pass_header(raw))
    assert resp.status_code == 202, resp.content
    (kwargs,) = started
    assert (kwargs["api_user"], kwargs["api_pass"]) == ("nested-user", PASSWORD)
    child = QueryTask.objects.get(task_id=resp.json()["task_id"])
    assert child.parent_cc_turn_id == turn.pk
    progress = APIClient().get(f"{A}/tasks/{child.task_id}/progress/", **pass_header(raw))
    assert progress.status_code == 200, progress.content


def test_a_non_admin_pass_search_is_scoped_by_the_held_login():
    turn, raw = make_turn(login=("searcher", PASSWORD))
    made = []

    class _SeekDB:
        def __init__(self, server, user, password):
            made.append((user, password))

        def getCurrentUser(self):
            return {"data": {"relationships": {"projects": {"data": [{"id": "7"}]}}}}

    with patch("nextseek_api.services.samples.resolve_sampletype_to_seek_id", return_value=None), \
         patch("nextseek_api.services.samples.SeekDB", _SeekDB), \
         patch("nextseek_api.services.samples.DBtable_sample") as table:
        table.return_value.searchAdvanced.return_value = json.dumps({"total": 0, "rows": []})
        resp = APIClient().post("/nextseek_api/samples/advanced_search/", {"filter_searchText": "lung"},
                                format="json", **pass_header(raw))
    assert resp.status_code == 200, resp.content
    assert made == [("searcher", PASSWORD)]
    assert table.return_value.searchAdvanced.call_args.kwargs["scoped_project_ids"] == ["7"]


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_a_wiped_pass_cannot_start_a_turn_as_the_service_login(monkeypatch):
    """A session_id gets the request past the allow table, so the refusal tested here is the login's."""
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    started = []
    monkeypatch.setattr("nextseek_api.services.assistant.threading.Thread",
                        lambda *a, **k: started.append(k) or SimpleNamespace(start=lambda: None))
    turn, raw = make_turn()
    _wipe_login(turn)
    before = QueryTask.objects.count()
    resp = APIClient().post(f"{A}/query/async/",
                            {"query": "how many", "mode": "plan", "session_id": str(turn.chat_id)},
                            format="json", **pass_header(raw))
    assert resp.status_code == 401, resp.content
    assert QueryTask.objects.count() == before
    assert started == []


def test_a_wiped_pass_gets_the_auth_failed_envelope_on_an_op_route():
    turn, raw = make_turn()
    _wipe_login(turn)
    resp = APIClient().post(f"{A}/api-read/", {}, format="json", **pass_header(raw))
    assert resp.status_code == 401, resp.content
    assert resp.json()["code"] == "AUTH_FAILED"


@pytest.mark.parametrize("login", [("", ""), ("user", ""), ("", PASSWORD)])
def test_a_pass_holding_an_empty_login_part_gets_the_auth_failed_envelope(login):
    """A turn issued with an empty name or password (the issuer maps a missing one to "") is dead on every route."""
    turn, raw = make_turn(login=login)
    resp = APIClient().post(f"{A}/api-read/", {}, format="json", **pass_header(raw))
    assert resp.status_code == 401, resp.content
    assert resp.json()["code"] == "AUTH_FAILED"


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_a_wiped_pass_has_no_login_even_with_a_session():
    from nextseek_api.services.assistant import _granular_chat_config, _request_login

    turn, _ = make_turn()
    _wipe_login(turn)
    request = pass_request(turn)
    request.session = {"username": "someone-else", "password": "their-password"}
    assert _request_login(request) == (None, None)
    with patch("nextseek_api.services.assistant.plain_scope", lambda user: None):
        cfg = _granular_chat_config(request, SimpleNamespace(use_prod=False))
    assert (cfg.API_USER, cfg.API_PASS) == ("", "")


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=SimpleNamespace(
    API_USER="prod-service", API_PASS="prod-pw"))
def test_a_pass_never_takes_the_prod_credentials(monkeypatch):
    """Gated by not-a-pass, not by the config being absent: even if the pass's config were the prod one."""
    from django.conf import settings
    from nextseek_api.services import assistant

    monkeypatch.setattr(assistant, "plain_scope", lambda user: None)
    monkeypatch.setattr(assistant, "_chat_config_for", lambda request, req: settings.NEXTSEEK_CHAT_CONFIG_PROD)
    turn, _ = make_turn(login=("op-user", PASSWORD))
    cfg = assistant._granular_chat_config(pass_request(turn), SimpleNamespace(use_prod=False))
    assert (cfg.API_USER, cfg.API_PASS) == ("op-user", PASSWORD)


def test_a_password_with_awkward_characters_survives_the_basic_header():
    pw = 'p:"\\\'é漢'
    turn, _ = make_turn(login=("seek-user", pw))
    (basic, _h) = resolve_seek_auth(pass_request(turn))
    decoded = base64.b64decode(basic_auth_header(basic)["Authorization"].split(" ", 1)[1]).decode("utf-8")
    assert decoded == f"seek-user:{pw}"


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=None)
def test_query_async_itself_refuses_a_pass_with_no_login(monkeypatch):
    """The view's own guard, reached with the pass layer out of the way."""
    from nextseek_api.services.assistant import AssistantViewSet

    started = []
    monkeypatch.setattr("nextseek_api.services.assistant.threading.Thread",
                        lambda *a, **k: started.append(k) or SimpleNamespace(start=lambda: None))
    turn, _ = make_turn()
    _wipe_login(turn)
    before = QueryTask.objects.count()
    request = pass_request(turn)
    request.data = {"query": "x", "mode": "plan"}
    resp = AssistantViewSet().query_async(request)
    assert resp.status_code == 401
    assert started == [] and QueryTask.objects.count() == before


@override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=SimpleNamespace(
    API_USER="prod-service", API_PASS="prod-pw"))
def test_query_async_never_swaps_a_pass_onto_the_prod_credentials(monkeypatch):
    """The view's own not-a-pass gate before the prod swap: even if the pass's config were the prod one."""
    from django.conf import settings
    from nextseek_api.services import assistant

    started = []

    class _Thread:
        def __init__(self, target=None, kwargs=None, daemon=None):
            started.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(assistant, "plain_scope", lambda user: None)
    monkeypatch.setattr(assistant, "_chat_config_for", lambda request, req: settings.NEXTSEEK_CHAT_CONFIG_PROD)
    monkeypatch.setattr(assistant.threading, "Thread", _Thread)
    turn, _ = make_turn(login=("op-user", PASSWORD))
    request = pass_request(turn)
    request.data = {"query": "x", "mode": "standard", "session_id": str(turn.chat_id)}
    resp = assistant.AssistantViewSet().query_async(request)
    assert resp.status_code == 202, resp.data
    (kwargs,) = started
    assert (kwargs["api_user"], kwargs["api_pass"]) == ("op-user", PASSWORD)
