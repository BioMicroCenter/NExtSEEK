"""The evaluation switch, host side: the request field and the CC turn's gate (spec 4.6, E2).

`QueryRequest.force_parser_mode` is admin-only and evaluation-only. `start_task`
honours it only when the caller is a superuser (never `is_staff`, which the SEEK
login sets on everyone) AND the server process sets NEXTSEEK_EVAL_PARSER_FORCE=1.
It then hands the NS engine a shallow copy of the chosen ChatConfig carrying
FORCE_PARSER_MODE; the shared singleton is never mutated, and the PROD identity
check still compares the singleton.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from nextseek_api.assistant.models_api import QueryRequest
from NessieAI.cc import turn
from NessieAI.router import router as cc_router

ENV = "NEXTSEEK_EVAL_PARSER_FORCE"

SUPER = SimpleNamespace(username="root", is_superuser=True, is_staff=True)
STAFF = SimpleNamespace(username="staff", is_superuser=False, is_staff=True)
PLAIN = SimpleNamespace(username="plain", is_superuser=False, is_staff=False)


class _Config:
    """A config object with an instance __dict__, like ChatConfig."""

    def __init__(self, name="default", api_user=None, api_pass=None):
        self.name = name
        self.API_USER = api_user
        self.API_PASS = api_pass
        self.shared = {"catalog": [1, 2, 3]}


def _req(mode=None, **over):
    return QueryRequest(query="How many tissue samples are there?", mode="standard",
                        force_parser_mode=mode, **over)


# ------------------------------------------------------------------ the request field


def test_force_parser_mode_defaults_to_none():
    assert QueryRequest(query="hi", mode="standard").force_parser_mode is None


@pytest.mark.parametrize("val", ["graph", "api"])
def test_force_parser_mode_accepts_the_two_arms(val):
    assert QueryRequest(query="hi", mode="standard", force_parser_mode=val).force_parser_mode == val


@pytest.mark.parametrize("val", ["graph_legacy", "cypher", "GRAPH", "", "auto"])
def test_force_parser_mode_rejects_anything_else(val):
    with pytest.raises(ValidationError):
        QueryRequest(query="hi", mode="standard", force_parser_mode=val)


def test_force_parser_mode_is_documented_as_admin_and_evaluation_only():
    desc = QueryRequest.model_fields["force_parser_mode"].description
    assert "Admin-only" in desc
    assert "superuser" in desc
    assert ENV in desc


def test_the_request_model_still_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        QueryRequest(query="hi", mode="standard", force_parser="graph")


# ------------------------------------------------------------------ the gate


def test_the_env_constant():
    assert turn.EVAL_PARSER_FORCE_ENV == ENV


@pytest.mark.parametrize("mode", ["graph", "api"])
def test_a_superuser_with_the_flag_gets_a_forced_copy(monkeypatch, mode):
    monkeypatch.setenv(ENV, "1")
    config = _Config()

    forced = turn._with_parser_force(config, SUPER, _req(mode))

    assert forced is not config
    assert forced.FORCE_PARSER_MODE == mode
    assert not hasattr(config, "FORCE_PARSER_MODE"), "the singleton was mutated"
    # a shallow copy: every other attribute is the very same object
    assert forced.shared is config.shared
    assert forced.name == config.name


def test_a_staff_user_who_is_not_a_superuser_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_parser_force(config, STAFF, _req("graph")) is config


def test_a_plain_user_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_parser_force(config, PLAIN, _req("api")) is config


def test_a_user_without_the_attribute_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_parser_force(config, object(), _req("graph")) is config


@pytest.mark.parametrize("flag", [None, "", "0", "true", "yes", " 1"])
def test_without_the_flag_the_value_is_ignored(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, flag)
    config = _Config()
    assert turn._with_parser_force(config, SUPER, _req("graph")) is config


def test_no_value_is_a_no_op(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_parser_force(config, SUPER, _req(None)) is config


@pytest.mark.parametrize("req", [
    SimpleNamespace(query="q"),                                 # the attribute is missing
    SimpleNamespace(query="q", force_parser_mode="cypher"),     # an invalid value
    SimpleNamespace(query="q", force_parser_mode="graph_legacy"),
    None,
])
def test_a_missing_or_invalid_value_is_a_no_op(monkeypatch, req):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_parser_force(config, SUPER, req) is config


def test_the_real_chat_config_copies_without_touching_the_singleton(monkeypatch):
    from django.conf import settings

    singleton = getattr(settings, "NEXTSEEK_CHAT_CONFIG", None)
    if singleton is None:
        pytest.skip("this lane's settings build no NEXTSEEK_CHAT_CONFIG")
    monkeypatch.setenv(ENV, "1")

    forced = turn._with_parser_force(singleton, SUPER, _req("api"))

    assert forced is not singleton
    assert type(forced) is type(singleton)
    assert forced.FORCE_PARSER_MODE == "api"
    assert getattr(singleton, "FORCE_PARSER_MODE", None) is None


# ------------------------------------------------------------------ start_task wiring


class _SyncThreading:
    """Stands in for the `threading` module inside NessieAI.cc.turn: runs the turn inline."""

    class Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()


class _Adapter(dict):
    def __init__(self):
        super().__init__()
        self.saved = 0

    def save(self):
        self.saved += 1


def _run_start_task(monkeypatch, *, user, req, chat_config, prod_config=None):
    """Drive one NS turn through start_task with every seam faked; return run_query's args."""
    from django.conf import settings

    seen = {}

    def _fake_run_query(adapter, config, query, send_event, credentials=None):
        seen["config"] = config
        seen["credentials"] = credentials
        send_event("query_complete", {"reply": "ok"})

    decision = SimpleNamespace(route=cc_router.ROUTE_NS, model_class=None, model_id=None,
                               source="forced", reasoning="test")
    monkeypatch.setattr(turn, "threading", _SyncThreading)
    monkeypatch.setattr(turn, "_select_chat_config", lambda request, r: chat_config)
    monkeypatch.setattr(turn, "_decide_route", lambda *a, **k: decision)
    monkeypatch.setattr(turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(turn, "run_query", _fake_run_query)
    monkeypatch.setattr(turn, "run_query_plan",
                        lambda *a, **k: pytest.fail("standard mode must not run the planner"))
    monkeypatch.setattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", prod_config, raising=False)

    events = []
    turn.start_task(
        SimpleNamespace(user=user), req, force_cc=False,
        chat_session=SimpleNamespace(extra_state={}, session_id="s-1", results_history=[]),
        query_task=SimpleNamespace(task_id="t-1"),
        send_event=lambda ev, data: events.append(ev),
        adapter=_Adapter(), api_user="caller", api_pass="caller-pw",
        resolved_session_id="s-1",
    )
    assert "query_complete" in events
    return seen


def test_start_task_hands_the_ns_engine_the_forced_copy(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()

    seen = _run_start_task(monkeypatch, user=SUPER, req=_req("graph", force_route="ns"),
                           chat_config=config)

    assert seen["config"] is not config
    assert seen["config"].FORCE_PARSER_MODE == "graph"
    assert not hasattr(config, "FORCE_PARSER_MODE")
    assert seen["credentials"] == {"api_user": "caller", "api_pass": "caller-pw"}


def test_start_task_hands_a_non_superuser_the_singleton_itself(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    config = _Config()

    seen = _run_start_task(monkeypatch, user=STAFF, req=_req("graph"), chat_config=config)

    assert seen["config"] is config


def test_start_task_without_the_flag_hands_the_singleton_itself(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    config = _Config()

    seen = _run_start_task(monkeypatch, user=SUPER, req=_req("api"), chat_config=config)

    assert seen["config"] is config


def test_the_prod_identity_check_still_sees_the_singleton(monkeypatch):
    """The PROD swap compares the chosen config by identity before the copy is made."""
    monkeypatch.setenv(ENV, "1")
    prod = _Config("prod", api_user="prod-service", api_pass="prod-pw")

    seen = _run_start_task(monkeypatch, user=SUPER, req=_req("api", use_prod=True),
                           chat_config=prod, prod_config=prod)

    assert seen["credentials"] == {"api_user": "prod-service", "api_pass": "prod-pw"}
    assert seen["config"] is not prod
    assert seen["config"].FORCE_PARSER_MODE == "api"
    assert seen["config"].API_USER == "prod-service"
    assert not hasattr(prod, "FORCE_PARSER_MODE")


def test_copy_semantics_are_shallow():
    """The gate relies on copy.copy: the copy shares every attribute value."""
    config = _Config()
    clone = copy.copy(config)
    assert clone is not config and clone.shared is config.shared
