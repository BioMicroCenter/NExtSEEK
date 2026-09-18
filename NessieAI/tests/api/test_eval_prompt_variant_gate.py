"""The prompt-variant switch, host side: the request field and the CC turn's gate.

`QueryRequest.prompt_variant` ("v2" or "v2_apoc") runs one NS turn on an alternative prompt set
(`chat_nextseek.prompt_variants`). Same gate and shape as `force_parser_mode`: honoured only for a superuser
(`is_superuser`, never `is_staff`) on a process that sets NEXTSEEK_EVAL_PARSER_FORCE=1, applied to a shallow
per-request copy of the ChatConfig, and dropped without a word otherwise. It is independent of
`force_parser_mode`: an unforced turn with a variant is the main use, and the two compose.

The variant tree here is built under tmp_path and patched in; nothing reads or writes the package's own
prompts/variants/, which the prompt writers own.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from chat_nextseek import prompt_variants as pv
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
        self.GRAPH_AGENT_SYSTEM_PROMPT = "DEFAULT GRAPH"
        self.shared = {"catalog": [1, 2, 3]}


@pytest.fixture
def variants(tmp_path, monkeypatch):
    root = tmp_path / "variants"
    for name, files, manifest in (
        ("v2", {"graph_agent.txt": "V2 GRAPH"}, {"project_parser_plan": True}),
        ("v2_apoc", {}, {"inherits": "v2", "allowed_procedures": ["apoc.path.subgraphNodes"]}),
    ):
        (root / name).mkdir(parents=True)
        for fname, text in files.items():
            (root / name / fname).write_text(text, encoding="utf-8")
        (root / name / "variant.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(pv, "VARIANTS_DIR", root)
    return root


def _req(variant=None, **over):
    return QueryRequest(query="How many tissue samples are there?", mode="standard",
                        prompt_variant=variant, **over)


# ------------------------------------------------------------------ the request field


def test_prompt_variant_defaults_to_none():
    assert QueryRequest(query="hi", mode="standard").prompt_variant is None


@pytest.mark.parametrize("val", ["v2", "v2_apoc", "v3"])
def test_prompt_variant_accepts_the_known_variants(val):
    assert QueryRequest(query="hi", mode="standard", prompt_variant=val).prompt_variant == val


@pytest.mark.parametrize("val", ["v4", "apoc", "rewrite", "V2", "", "default"])
def test_prompt_variant_rejects_anything_else(val):
    with pytest.raises(ValidationError):
        QueryRequest(query="hi", mode="standard", prompt_variant=val)


def test_the_literal_is_the_engines_variant_list():
    annotation = QueryRequest.model_fields["prompt_variant"].annotation
    literal = next(a for a in get_args(annotation) if a is not type(None))
    assert get_args(literal) == pv.VARIANT_NAMES


def test_prompt_variant_is_documented_as_admin_and_evaluation_only():
    desc = QueryRequest.model_fields["prompt_variant"].description
    assert "Admin-only" in desc and "superuser" in desc and ENV in desc


def test_the_turn_module_takes_the_names_from_the_engine():
    assert turn.PROMPT_VARIANT_NAMES == pv.VARIANT_NAMES


# ------------------------------------------------------------------ the gate


def test_a_superuser_with_the_flag_gets_a_variant_copy(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()

    out = turn._with_prompt_variant(config, SUPER, _req("v2"))

    assert out is not config
    assert out.PROMPT_VARIANT == "v2"
    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH"
    assert out.PROJECT_PARSER_PLAN is True
    assert config.GRAPH_AGENT_SYSTEM_PROMPT == "DEFAULT GRAPH"
    assert not hasattr(config, "PROMPT_VARIANT"), "the singleton was mutated"
    assert out.shared is config.shared


def test_the_inherited_variant_carries_its_parents_files_and_its_own_procedures(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    out = turn._with_prompt_variant(_Config(), SUPER, _req("v2_apoc"))
    assert out.PROMPT_VARIANT == "v2_apoc"
    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH"
    assert out.EXTRA_ALLOWED_PROCEDURES == frozenset({"apoc.path.subgraphNodes"})


def test_a_staff_user_who_is_not_a_superuser_is_ignored(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_prompt_variant(config, STAFF, _req("v2")) is config


def test_a_plain_user_is_ignored(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_prompt_variant(config, PLAIN, _req("v2")) is config


@pytest.mark.parametrize("flag", [None, "", "0", "true", " 1"])
def test_without_the_flag_the_value_is_ignored(monkeypatch, variants, flag):
    if flag is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, flag)
    config = _Config()
    assert turn._with_prompt_variant(config, SUPER, _req("v2")) is config


@pytest.mark.parametrize("req", [
    None,
    SimpleNamespace(query="q"),
    SimpleNamespace(query="q", prompt_variant=None),
    SimpleNamespace(query="q", prompt_variant="v9"),        # an unknown name, past the model
    SimpleNamespace(query="q", prompt_variant="rewrite"),
])
def test_a_missing_or_unknown_name_is_a_no_op(monkeypatch, variants, req):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._with_prompt_variant(config, SUPER, req) is config


def test_a_broken_variant_runs_the_defaults_and_says_so_in_the_log(monkeypatch, variants, caplog):
    monkeypatch.setenv(ENV, "1")
    (variants / "v2" / "stray.md").write_text("x", encoding="utf-8")
    config = _Config()

    with caplog.at_level("ERROR"):
        out = turn._with_prompt_variant(config, SUPER, _req("v2"))

    assert out is config
    assert any("prompt_variant 'v2'" in r.getMessage() and "stray.md" in r.getMessage()
               for r in caplog.records)


def test_the_variant_works_without_a_parser_force(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    out = turn._eval_config(_Config(), SUPER, _req("v2"))
    assert out.PROMPT_VARIANT == "v2"
    assert not hasattr(out, "FORCE_PARSER_MODE")


def test_the_variant_and_the_parser_force_compose(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    out = turn._eval_config(config, SUPER, _req("v2_apoc", force_parser_mode="graph"))
    assert out.PROMPT_VARIANT == "v2_apoc" and out.FORCE_PARSER_MODE == "graph"
    assert not hasattr(config, "PROMPT_VARIANT") and not hasattr(config, "FORCE_PARSER_MODE")


def test_the_eval_config_of_a_plain_request_is_the_singleton(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    assert turn._eval_config(config, SUPER, _req()) is config


def test_the_real_chat_config_copies_without_touching_the_singleton(monkeypatch, variants):
    from django.conf import settings

    singleton = getattr(settings, "NEXTSEEK_CHAT_CONFIG", None)
    if singleton is None:
        pytest.skip("this lane's settings build no NEXTSEEK_CHAT_CONFIG")
    monkeypatch.setenv(ENV, "1")
    before = singleton.GRAPH_AGENT_SYSTEM_PROMPT

    out = turn._with_prompt_variant(singleton, SUPER, _req("v2"))

    assert out is not singleton and type(out) is type(singleton)
    assert out.GRAPH_AGENT_SYSTEM_PROMPT == "V2 GRAPH"
    assert singleton.GRAPH_AGENT_SYSTEM_PROMPT == before
    assert getattr(singleton, "PROMPT_VARIANT", None) is None


# ------------------------------------------------------------------ start_task wiring


class _SyncThreading:
    class Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()


class _Adapter(dict):
    def save(self):
        pass


def _run_start_task(monkeypatch, *, user, req, chat_config, plan_mode=False):
    from django.conf import settings

    seen = {}

    def _fake(adapter, config, query, send_event, credentials=None):
        seen["config"] = config
        send_event("query_complete", {"reply": "ok"})

    decision = SimpleNamespace(route=cc_router.ROUTE_NS, model_class=None, model_id=None,
                               source="forced", reasoning="test")
    monkeypatch.setattr(turn, "threading", _SyncThreading)
    monkeypatch.setattr(turn, "_select_chat_config", lambda request, r: chat_config)
    monkeypatch.setattr(turn, "_decide_route", lambda *a, **k: decision)
    monkeypatch.setattr(turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(turn, "run_query", _fake)
    monkeypatch.setattr(turn, "run_query_plan", _fake)
    monkeypatch.setattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None, raising=False)

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


def test_start_task_hands_the_ns_engine_the_variant_copy_unforced(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()

    seen = _run_start_task(monkeypatch, user=SUPER, req=_req("v2", force_route="ns"), chat_config=config)

    assert seen["config"] is not config
    assert seen["config"].PROMPT_VARIANT == "v2"
    assert not hasattr(seen["config"], "FORCE_PARSER_MODE")
    assert not hasattr(config, "PROMPT_VARIANT")


def test_start_task_hands_the_planner_the_variant_copy(monkeypatch, variants):
    """multi_parser_agent.txt is read only on the plan path, so plan mode gets the variant too."""
    monkeypatch.setenv(ENV, "1")
    req = QueryRequest(query="plan this", mode="plan", prompt_variant="v2")

    seen = _run_start_task(monkeypatch, user=SUPER, req=req, chat_config=_Config())

    assert seen["config"].PROMPT_VARIANT == "v2"


def test_start_task_hands_a_non_superuser_the_singleton_itself(monkeypatch, variants):
    monkeypatch.setenv(ENV, "1")
    config = _Config()
    seen = _run_start_task(monkeypatch, user=STAFF, req=_req("v2"), chat_config=config)
    assert seen["config"] is config
