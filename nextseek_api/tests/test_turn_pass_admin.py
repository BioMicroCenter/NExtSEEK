"""An admin's own Container-CC turn under a pass (operator ruling 2026-09-28, Review Focus 3): the admin keeps
their own data reach (unscoped search and graph) and loses read-any of other users' records and the prod config."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.permissions import is_turn_pass, may_read_any
from nextseek_api.tests.turn_pass_support import make_turn, make_user, pass_header, pass_request

pytestmark = pytest.mark.django_db

A = "/nextseek_api/assistant"


class _Config:
    API_USER = "service"
    API_PASS = "service-pw"


class _Prod:
    API_USER = "prod-service"
    API_PASS = "prod-pw"


@pytest.fixture
def admin():
    return make_user("the-admin", is_superuser=True)


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    monkeypatch.setattr("nextseek_api.services.assistant.UserInParticipatingProject.has_permission",
                        lambda self, request, view: True)
    with override_settings(NEXTSEEK_CHAT_CONFIG=_Config(), NEXTSEEK_CHAT_CONFIG_PROD=_Prod()):
        yield


def test_read_any_is_off_for_a_pass_and_unchanged_otherwise(admin):
    turn, _ = make_turn(admin)
    assert is_turn_pass(pass_request(turn)) is True
    assert may_read_any(pass_request(turn)) is False
    assert is_turn_pass(SimpleNamespace(user=admin, auth=None)) is False
    assert may_read_any(SimpleNamespace(user=admin, auth=None)) is True
    assert may_read_any(SimpleNamespace(user=make_user("plain"), auth=None)) is False


def test_an_admins_pass_cannot_read_a_foreign_task_even_one_the_table_lets_through(admin):
    """Belt and braces: a task row owned by someone else that names this turn passes the allow table's child
    check; the view's owner filter still answers 404, because read-any is off under a pass."""
    turn, raw = make_turn(admin)
    other = make_user("someone")
    foreign = QueryTask.objects.create(session=ChatSession.objects.create(user=other), user=other, query="x",
                                       status="running", parent_cc_turn=turn)
    resp = APIClient().get(f"{A}/tasks/{foreign.task_id}/progress/", **pass_header(raw))
    assert resp.status_code == 404, resp.content


def test_an_admins_pass_cannot_open_another_users_chat(admin):
    turn, raw = make_turn(admin)
    foreign_chat = ChatSession.objects.create(user=make_user("someone"))
    resp = APIClient().get(f"{A}/sessions/{foreign_chat.session_id}/", **pass_header(raw))
    assert resp.status_code == 403 and resp.json()["code"] == "PASS_NOT_ALLOWED"


@pytest.mark.parametrize("tail", ["", "/bundles/1", "/bundles/1/artifacts/key1"])
def test_the_views_own_read_any_lock_refuses_an_admins_pass_on_a_foreign_chat(admin, tail):
    """The pass is bound to the foreign chat so the allow table's path_session check lets it through; the view's
    own owner check (read-any is off under a pass) is what answers 403."""
    other = make_user("someone")
    foreign = ChatSession.objects.create(
        user=other, results_history=[{"id": 1, "artifacts": {"key1": {"path": "x"}}}],
    )
    _, raw = make_turn(admin, chat=foreign)
    resp = APIClient().get(f"{A}/sessions/{foreign.session_id}{tail}/", **pass_header(raw))
    assert resp.status_code == 403, resp.content
    body = resp.json()
    assert body.get("code") != "PASS_NOT_ALLOWED" and "own this session" in json.dumps(body), body


def test_an_admins_pass_never_gets_the_prod_config(admin):
    from nextseek_api.services.assistant import _chat_config_for

    turn, _ = make_turn(admin)
    asks_prod = SimpleNamespace(use_prod=True)
    assert _chat_config_for(pass_request(turn), asks_prod) is settings.NEXTSEEK_CHAT_CONFIG
    assert _chat_config_for(SimpleNamespace(user=admin, auth=None), asks_prod) is settings.NEXTSEEK_CHAT_CONFIG_PROD


def test_an_admins_pass_keeps_the_admins_unscoped_search(admin):
    turn, raw = make_turn(admin)
    with patch("nextseek_api.services.samples.resolve_sampletype_to_seek_id", return_value=None), \
         patch("nextseek_api.services.samples.SeekDB") as seekdb, \
         patch("nextseek_api.services.samples.DBtable_sample") as table:
        table.return_value.searchAdvanced.return_value = json.dumps({"total": 0, "rows": []})
        resp = APIClient().post("/nextseek_api/samples/advanced_search/", {"filter_searchText": "lung"},
                                format="json", **pass_header(raw))
    assert resp.status_code == 200, resp.content
    assert table.return_value.searchAdvanced.call_args.kwargs["scoped_project_ids"] is None
    seekdb.assert_not_called()


def test_an_admins_pass_keeps_the_admins_graph_scope(admin):
    from chat_nextseek.graph_scope import scope_of

    from nextseek_api.services.assistant import _granular_chat_config

    turn, _ = make_turn(admin)
    cfg = _granular_chat_config(pass_request(turn), SimpleNamespace(use_prod=False))
    assert scope_of(cfg).is_admin is True
    asks_prod = _granular_chat_config(pass_request(turn), SimpleNamespace(use_prod=True))
    # never the prod config, even when the body asks for it; the login is the turn's own (Task 6)
    assert asks_prod.API_USER == admin.username
