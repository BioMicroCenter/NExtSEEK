"""A model that failed is remembered for the rest of the turn (F3, operator rulings D3 and D4, 2026-09-28).

With Gemini stalled, every Gemini agent of a turn (entity, graph, chatter) used to wait out its own first try on the
stalled model before moving: 300 s each. The turn's ``CallScope`` (``call_scope.py``) now remembers the model that
failed, and every later call whose primary it is starts on its fallback without asking the primary. The rules
pinned here:

* one strike: the first timeout, 5xx, 429, dropped connection or refused model marks the model (D4);
* an empty body, a 400 and bad output mark nothing; nor does the parsers' own timeout (D3), though their 503 does;
* the key is the model, not the agent and not the provider: a Gemini 3.5 Flash mark does not skip Gemini 3.1 Pro;
* a call whose primary AND fallback both failed earlier fails at once, without calling either, and says so;
* a call whose primary fails live while its fallback failed earlier ends without calling the fallback;
* the skip shows in the ledger (``fallback_remembered``) and the turn record (``remembered: true``), and no
  new top-level event key is added (the CC plugin's event models forbid unknown keys);
* a new turn starts clean, a turn inside a turn shares its scope, and a call outside any scope behaves as before;
* marks and reads are thread safe.
"""
from __future__ import annotations

import json
import threading

import pytest
from pydantic import BaseModel

from chat_nextseek import call_scope, turn_spend
from chat_nextseek.llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMFatalError,
    LLMModelUnusableError,
    LLMRateLimitError,
    LLMResponse,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text

FLASH = "gemini-3.5-flash"
PRO = "gemini-3.1-pro-preview"
SONNET = "us.anthropic.claude-sonnet-4-6"
OPUS = "us.anthropic.claude-opus-4-7"


class _Plan(BaseModel):
    mode: str = "unsupported"


class _Client:
    """A provider stand-in: outcomes are replayed per model, and every call is recorded."""

    def __init__(self, provider, outcomes: dict[str, list]):
        self.provider = provider
        self.outcomes = {m: list(v) for m, v in outcomes.items()}
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        queue = self.outcomes.get(model) or ['{"mode": "ok"}']
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={"stop_reason": "end_turn"})

    def chat_with_tools(self, *, model, **kw):
        self.calls.append(model)
        queue = self.outcomes.get(model) or ["ok"]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": outcome}], "usage": {}, "metadata": {}}


class _Config:
    """The shipped shape: Gemini agents fall back to Sonnet 4.6, Opus agents to Gemini 3.1 Pro, the tool loops
    to Sonnet 4.6 through the catalog's _fallback block."""

    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "high": 16000}
    LLM_MODEL = FLASH

    def __init__(self, gcp, anth, log_dir=None):
        self.LOG_DIR = log_dir
        self.LLM_CLIENT = gcp
        self.LLM_CLIENTS = {"gcp": gcp, "anth": anth}
        gemini_agents = ("entity", "graph", "chatter", "api")
        self.AGENT_MODEL_CATALOG = {
            "anth:current": {a: {"provider": "anth", "model": SONNET, "thinking_level": None} for a in gemini_agents},
            "gcp:current": {"parser": {"provider": "gcp", "model": PRO, "thinking_level": "high"},
                            "report_writer": {"provider": "gcp", "model": PRO, "thinking_level": "high"}},
            "_fallback": {"followup": {"provider": "anth", "model": SONNET, "thinking_level": None}},
        }


def _gemini_call(config, agent="entity"):
    return call_llm_structured(config, "q", _Plan, system="s", client=config.LLM_CLIENTS["gcp"], model_name=FLASH,
                               agent_label=agent)


def _opus_call(config, agent="parser"):
    return call_llm_structured(config, "q", _Plan, system="s", client=config.LLM_CLIENTS["anth"], model_name=OPUS,
                               agent_label=agent)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    from chat_nextseek.schemas import schema_helper
    monkeypatch.setattr(schema_helper.time, "sleep", lambda s: None)


# --------------------------------------------------------------------------
# One strike marks, and later calls skip the marked primary.
# --------------------------------------------------------------------------

OUTAGES = [
    ("timeout", LLMTimeoutError("stalled")),
    ("unavailable", LLMServiceUnavailableError("503 UNAVAILABLE")),
    ("rate_limited", LLMRateLimitError("429 RESOURCE_EXHAUSTED")),
    ("connection", LLMAPIConnectionError("reset")),
    ("model_unusable", LLMModelUnusableError("NOT_FOUND")),
]


@pytest.mark.parametrize("reason, failure", OUTAGES, ids=[r for r, _ in OUTAGES])
def test_one_failure_marks_the_model_and_later_agents_start_on_their_fallback(reason, failure):
    gcp = _Client("gcp", {FLASH: [failure, '{"mode": "never"}']})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}']})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        assert _gemini_call(config, "entity").mode == "sonnet"
        assert _gemini_call(config, "graph").mode == "sonnet"
        assert _gemini_call(config, "chatter").mode == "sonnet"
        assert scope.failed(("gcp", FLASH))["reason"] == reason
    assert gcp.calls == [FLASH], "the stalled model is asked once per turn, not once per agent"
    assert anth.calls == [SONNET, SONNET, SONNET]


@pytest.mark.parametrize("failure", [
    "",                                                   # an empty body: one response
    LLMError("ValidationException: 400 malformed request"),
], ids=["empty", "400"])
def test_an_empty_body_or_a_400_marks_nothing(failure):
    gcp = _Client("gcp", {FLASH: [failure, '{"mode": "flash"}']})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}']})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        try:
            _gemini_call(config, "entity")
        except LLMFatalError:
            pass
        assert scope.failed(("gcp", FLASH)) is None
        assert _gemini_call(config, "graph").mode == "flash"


def test_bad_output_marks_nothing():
    gcp = _Client("gcp", {FLASH: ["not json", "not json", "not json", '{"mode": "flash"}']})
    anth = _Client("bedrock", {})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        with pytest.raises(Exception):
            _gemini_call(config, "entity")
        assert scope.failed(("gcp", FLASH)) is None


def test_the_parsers_own_timeout_does_not_mark_opus_but_its_503_does():
    """D3: a thinking Opus may miss the parser's 35 s without being down (run 2)."""
    gcp = _Client("gcp", {PRO: ['{"mode": "pro"}']})
    anth = _Client("bedrock", {OPUS: [LLMTimeoutError("35 s"), '{"mode": "opus"}']})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        assert _opus_call(config, "parser").mode == "pro"
        assert scope.failed(("anth", OPUS)) is None
        assert _opus_call(config, "report_writer").mode == "opus", "report_writer still asks Opus"

    anth = _Client("bedrock", {OPUS: [LLMServiceUnavailableError("503"), '{"mode": "opus"}']})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        assert _opus_call(config, "parser").mode == "pro"
        assert scope.failed(("anth", OPUS))["reason"] == "unavailable"
        assert _opus_call(config, "report_writer").mode == "pro"
    assert anth.calls == [OPUS]


def test_the_key_is_the_model_not_the_provider():
    """A Gemini 3.5 Flash stall does not skip Gemini 3.1 Pro, the parser's fallback."""
    gcp = _Client("gcp", {FLASH: [LLMTimeoutError("stalled")], PRO: ['{"mode": "pro"}']})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}'], OPUS: [LLMServiceUnavailableError("503")]})
    config = _Config(gcp, anth)
    with call_scope.scope():
        _gemini_call(config, "entity")
        assert _opus_call(config, "parser").mode == "pro"
    assert PRO in gcp.calls


# --------------------------------------------------------------------------
# Both failed earlier, or the fallback failed earlier.
# --------------------------------------------------------------------------

def test_both_models_failed_earlier_fails_at_once_without_a_call(tmp_path):
    gcp = _Client("gcp", {FLASH: [LLMTimeoutError("stalled")]})
    anth = _Client("bedrock", {SONNET: [LLMTimeoutError("stalled too")]})
    config = _Config(gcp, anth, log_dir=str(tmp_path))
    with call_scope.scope():
        with pytest.raises(LLMFatalError):
            _gemini_call(config, "entity")
        gcp.calls.clear()
        anth.calls.clear()
        with pytest.raises(LLMFatalError) as excinfo:
            _gemini_call(config, "graph")
    assert gcp.calls == [] and anth.calls == [], "no model is called"
    fatal = excinfo.value
    assert fatal.unavailable is True
    assert fatal.model_fallback == [
        {"agent": "graph", "from": FLASH, "to": SONNET, "reason": "timeout", "remembered": True},
    ]
    entries = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    (not_called,) = [e for e in entries if e["outcome"] == "not_called"]
    assert not_called["agent"] == "graph" and not_called["model"] == SONNET
    assert not_called["fallback_from"] == FLASH and not_called["fallback_remembered"] is True
    assert "failed earlier in this turn" in not_called["error"]


def test_a_live_failure_whose_fallback_failed_earlier_does_not_call_the_fallback():
    gcp = _Client("gcp", {FLASH: [LLMServiceUnavailableError("503")]})
    anth = _Client("bedrock", {SONNET: [LLMServiceUnavailableError("503")], OPUS: ['{"mode": "opus"}']})
    config = _Config(gcp, anth)
    with call_scope.scope() as scope:
        scope.mark_failed(("anth", SONNET), reason="unavailable", agent="followup")
        with pytest.raises(LLMFatalError) as excinfo:
            _gemini_call(config, "entity")
    assert gcp.calls == [FLASH]
    assert anth.calls == []
    assert excinfo.value.unavailable is True
    assert excinfo.value.model_fallback[0]["remembered"] is True


# --------------------------------------------------------------------------
# How the skip shows.
# --------------------------------------------------------------------------

def test_the_skip_is_named_in_the_ledger_and_the_turn_record(tmp_path):
    gcp = _Client("gcp", {FLASH: [LLMTimeoutError("stalled")]})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}']})
    config = _Config(gcp, anth, log_dir=str(tmp_path))
    with turn_spend.collecting() as spend, call_scope.scope():
        _gemini_call(config, "entity")
        _gemini_call(config, "graph")
        record = spend.summary()
    entries = [json.loads(line) for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()]
    graph = [e for e in entries if e["agent"] == "graph"]
    assert [(e["model"], e["outcome"]) for e in graph] == [(SONNET, "ok")], "the primary was never called"
    assert graph[0]["fallback_from"] == FLASH and graph[0]["fallback_reason"] == "timeout"
    assert graph[0]["fallback_remembered"] is True
    entity_move = [e for e in entries if e["agent"] == "entity" and e.get("fallback_from")]
    assert "fallback_remembered" not in entity_move[0], "a live move is not a remembered one"
    assert record["model_fallback"] == [
        {"agent": "entity", "from": FLASH, "to": SONNET, "reason": "timeout"},
        {"agent": "graph", "from": FLASH, "to": SONNET, "reason": "timeout", "remembered": True},
    ]


def test_the_turn_record_keys_are_unchanged():
    """The CC plugin's event models forbid unknown top-level keys; the skip lives inside model_fallback items."""
    with turn_spend.collecting() as spend:
        assert set(spend.summary()) == {"total_cost_usd", "cost_partial", "models_used", "model_fallback", "cost"}


# --------------------------------------------------------------------------
# Scope boundaries.
# --------------------------------------------------------------------------

def test_a_call_outside_any_scope_asks_its_primary_every_time():
    gcp = _Client("gcp", {FLASH: [LLMTimeoutError("stalled"), LLMTimeoutError("stalled")]})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}']})
    config = _Config(gcp, anth)
    _gemini_call(config, "entity")
    _gemini_call(config, "graph")
    assert gcp.calls == [FLASH, FLASH]


def test_a_new_turn_starts_clean_and_a_turn_inside_a_turn_shares_the_scope():
    @turn_spend.collects_turn
    def inner():
        return call_scope.current()

    @turn_spend.collects_turn
    def outer():
        return call_scope.current(), inner()

    first_outer, first_inner = outer()
    second_outer, _ = outer()
    assert first_outer is first_inner, "a turn inside a turn shares its scope"
    assert first_outer is not second_outer, "each turn opens its own"
    assert call_scope.current() is None, "nothing is left behind"


def test_every_ns_entry_point_opens_a_scope():
    import inspect

    from chat_nextseek import orchestrator as orch

    for fn in (orch.run_query, orch.run_query_plan, orch.run_pipeline_launch):
        assert getattr(fn, "__wrapped__", None) is not None, f"{fn.__name__} is not collects_turn-wrapped"
    assert "call_scope.scope()" in inspect.getsource(turn_spend.collects_turn)


def test_marks_and_reads_are_thread_safe():
    scope = call_scope.CallScope()
    barrier = threading.Barrier(16)
    errors: list[BaseException] = []

    def worker(i):
        try:
            barrier.wait()
            for j in range(200):
                scope.mark_failed(("gcp", f"m{(i + j) % 7}"), reason="timeout", agent=f"a{i}")
                scope.failed(("gcp", f"m{j % 7}"))
                scope.failed_models()
        except BaseException as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert {m["model"] for m in scope.failed_models()} == {f"m{k}" for k in range(7)}


def test_the_first_mark_of_a_model_stands():
    scope = call_scope.CallScope()
    scope.mark_failed(("gcp", FLASH), reason="timeout", agent="entity")
    scope.mark_failed(("gcp", FLASH), reason="unavailable", agent="graph")
    assert scope.failed(("gcp", FLASH))["reason"] == "timeout"


# --------------------------------------------------------------------------
# The tool loops remember too.
# --------------------------------------------------------------------------

def test_a_tool_loop_step_after_a_move_starts_on_sonnet():
    """The pipeline agent used to pay a stalled Opus's first try on each of up to 12 steps."""
    from chat_nextseek.tool_loop import call_tools

    anth = _Client("bedrock", {OPUS: [LLMTimeoutError("stalled")], SONNET: ["ok"]})
    config = _Config(_Client("gcp", {}), anth)
    config.AGENT_MODEL_CATALOG["_fallback"]["pipeline_agent"] = {"provider": "anth", "model": SONNET,
                                                                "thinking_level": None}
    with call_scope.scope():
        for _ in range(3):
            call_tools(config, messages=[], tools=[], system="s", model_name=OPUS, client=anth,
                       agent_label="pipeline_agent")
    assert anth.calls == [OPUS, SONNET, SONNET, SONNET]


def test_the_text_path_remembers_too():
    gcp = _Client("gcp", {FLASH: [LLMServiceUnavailableError("503")]})
    anth = _Client("bedrock", {SONNET: ['{"mode": "sonnet"}', "Here are your samples."]})
    config = _Config(gcp, anth)
    with call_scope.scope():
        _gemini_call(config, "entity")
        reply = call_llm_text(config, messages=[{"role": "user", "content": "hi"}], client=gcp, model_name=FLASH,
                              agent_label="chatter")
    assert reply == "Here are your samples."
    assert gcp.calls == [FLASH]
