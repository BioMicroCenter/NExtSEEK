"""Every NS turn says what its model calls cost, which models answered, and what fell back.

``chat_nextseek.turn_spend`` keeps one collector per NS turn in a ContextVar. The three
orchestrator entry points (``run_query``, ``run_query_plan``, ``run_pipeline_launch``)
start it; every ledger write of ``_call_with_recovery`` and ``tool_loop.call_tools``
feeds it; ``_emit_query_complete`` puts the turn record on ``query_complete``:
``total_cost_usd``, ``cost_partial``, ``models_used``, ``model_fallback`` and a
breakdown in ``debug["cost"]``.

The rules pinned here:

* spend is summed from USAGE, never from ledger records: an empty body writes two
  records (``empty_completion`` with usage, then ``service_unavailable`` without) and is
  one billed call;
* an attempt the wall clock abandoned may still be billed with no usage ever seen: it
  is an unobserved call and the turn is partial;
* a 5xx, a 429 and a 400 are not billed and change nothing;
* a model with no price is named and makes the turn partial; a turn whose every call
  was unpriced or unobserved has no total (None), never 0;
* a turn with no model call costs 0.

No live calls: every client is a fake.
"""
from __future__ import annotations

import threading
import time

import pytest
from pydantic import BaseModel

from chat_nextseek import model_prices, orchestrator, tool_loop, turn_spend
from chat_nextseek.llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMFatalError,
    LLMResponse,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text

OPUS = "us.anthropic.claude-opus-4-7"
SONNET = "us.anthropic.claude-sonnet-4-6"
FLASH = "gemini-3.5-flash"
LITE = "anthropic.claude-sonnet-4-5-20250929-v1:0"  # anth:lite, deliberately unpriced


def _price(model, usage):
    return model_prices.call_cost(model, usage).cost_usd


def _entry(model=FLASH, *, agent="graph_agent", outcome="ok", attempt=1, provider="gcp", **extra):
    return {"agent": agent, "provider": provider, "model": model, "attempt": attempt, "outcome": outcome, **extra}


def _resp(usage, model=FLASH, provider="gcp"):
    return LLMResponse(content="x", raw=None, usage=usage, model=model, provider=provider, metadata={})


GEMINI_USAGE = {"prompt_tokens": 4000, "completion_tokens": 100, "total_tokens": 4100,
                "thoughts_tokens": 300, "cached_tokens": 1000}
BEDROCK_USAGE = {"prompt_tokens": 2000, "completion_tokens": 400, "total_tokens": 2400}


# ---------------------------------------------------------------------------- the collector

def test_no_collector_means_nothing_is_recorded_and_no_record_is_made():
    assert turn_spend.current() is None
    turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))  # a no-op, never an error
    assert turn_spend.turn_record() is None


def test_a_turn_with_no_model_call_costs_zero():
    with turn_spend.collecting():
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] == 0.0
    assert record["cost_partial"] is False
    assert record["models_used"] == [] and record["model_fallback"] == []


def test_calls_are_priced_from_their_usage_and_summed():
    with turn_spend.collecting():
        turn_spend.record_call(_entry(FLASH, agent="entity"), resp=_resp(GEMINI_USAGE))
        turn_spend.record_call(_entry(OPUS, agent="parser", provider="bedrock"),
                               resp=_resp(BEDROCK_USAGE, OPUS, "bedrock"))
        record = turn_spend.turn_record()
    expected = _price(FLASH, GEMINI_USAGE) + _price(OPUS, BEDROCK_USAGE)
    assert record["total_cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert record["cost_partial"] is False
    assert record["models_used"] == [FLASH, OPUS]
    cost = record["cost"]
    assert cost["price_table_version"] == model_prices.load_price_table().version
    assert cost["by_agent"]["entity"]["cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE))
    assert cost["by_model"][OPUS]["calls"] == 1
    assert cost["by_model"][FLASH]["output"] == 400, "thinking is billed as output"
    assert [c["agent"] for c in cost["calls"]] == ["entity", "parser"]
    assert cost["unpriced_models"] == [] and cost["unobserved_calls"] == []


def test_an_empty_body_is_one_billed_call_not_two():
    """Two ledger records, one call: the empty completion carried the usage, the 503
    record that follows it carries none."""
    with turn_spend.collecting():
        turn_spend.record_call(_entry(OPUS, outcome="empty_completion", provider="bedrock"),
                               resp=_resp(BEDROCK_USAGE, OPUS, "bedrock"))
        turn_spend.record_call(_entry(OPUS, outcome="service_unavailable", provider="bedrock"),
                               err=LLMServiceUnavailableError("empty completion"))
        record = turn_spend.turn_record()
    assert len(record["cost"]["calls"]) == 1
    assert record["total_cost_usd"] == pytest.approx(_price(OPUS, BEDROCK_USAGE), abs=1e-6)
    assert record["cost_partial"] is False
    assert record["models_used"] == [], "an empty body answered nothing"


@pytest.mark.parametrize("err", [LLMServiceUnavailableError("503"), LLMError("400 bad request")])
def test_a_refused_call_costs_nothing_and_is_not_partial(err):
    with turn_spend.collecting():
        turn_spend.record_call(_entry(outcome="error"), err=err)
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] == 0.0 and record["cost_partial"] is False


@pytest.mark.parametrize("err, outcome", [
    (LLMTimeoutError("LLM call timed out after 35 seconds"), "timeout"),
    (LLMAPIConnectionError("connection reset"), "error"),
])
def test_an_abandoned_attempt_is_unobserved_and_makes_the_turn_partial(err, outcome):
    with turn_spend.collecting():
        turn_spend.record_call(_entry(OPUS, agent="parser", outcome=outcome, provider="bedrock"), err=err)
        turn_spend.record_call(_entry(FLASH, agent="parser", attempt=2), resp=_resp(GEMINI_USAGE))
        record = turn_spend.turn_record()
    assert record["cost_partial"] is True
    assert record["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    (unobserved,) = record["cost"]["unobserved_calls"]
    assert unobserved["model"] == OPUS and unobserved["agent"] == "parser"


def test_an_unpriced_model_is_named_and_the_turn_is_partial():
    with turn_spend.collecting():
        turn_spend.record_call(_entry(LITE, provider="bedrock"), resp=_resp(BEDROCK_USAGE, LITE, "bedrock"))
        turn_spend.record_call(_entry(FLASH), resp=_resp(GEMINI_USAGE))
        record = turn_spend.turn_record()
    assert record["cost_partial"] is True
    assert record["cost"]["unpriced_models"] == [LITE]
    assert record["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)


def test_a_turn_with_no_priced_call_has_no_total_rather_than_zero():
    with turn_spend.collecting():
        turn_spend.record_call(_entry(LITE, provider="bedrock"), resp=_resp(BEDROCK_USAGE, LITE, "bedrock"))
        turn_spend.record_call(_entry(OPUS, outcome="timeout", provider="bedrock"), err=LLMTimeoutError("t"))
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] is None and record["cost_partial"] is True


def test_a_response_without_usage_is_unobserved_not_free():
    with turn_spend.collecting():
        turn_spend.record_call(_entry(), resp=_resp(None))
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] is None and record["cost_partial"] is True
    assert record["models_used"] == [FLASH]


def test_the_ledgers_fallback_fields_become_the_turns_model_fallback():
    with turn_spend.collecting():
        turn_spend.record_call(_entry(OPUS, agent="parser", outcome="timeout", provider="bedrock"),
                               err=LLMTimeoutError("t"))
        turn_spend.record_call(
            _entry("gemini-3.1-pro-preview", agent="parser", attempt=2,
                   fallback_from=OPUS, fallback_reason="timeout"),
            resp=_resp(GEMINI_USAGE, "gemini-3.1-pro-preview"))
        record = turn_spend.turn_record()
    assert record["model_fallback"] == [
        {"agent": "parser", "from": OPUS, "to": "gemini-3.1-pro-preview", "reason": "timeout"}]


def test_a_broken_price_table_makes_the_turn_partial_and_never_fails_it(monkeypatch):
    def broken(*_a, **_kw):
        raise ValueError("bad table")

    monkeypatch.setattr(turn_spend.model_prices, "call_cost", broken)
    with turn_spend.collecting():
        turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] is None and record["cost_partial"] is True


def test_collectors_do_not_leak_between_threads_or_turns():
    seen = {}

    def turn(name, model):
        with turn_spend.collecting():
            turn_spend.record_call(_entry(model), resp=_resp(GEMINI_USAGE, model))
            time.sleep(0.05)
            seen[name] = turn_spend.turn_record()["models_used"]

    threads = [threading.Thread(target=turn, args=("a", FLASH)),
               threading.Thread(target=turn, args=("b", "gemini-3.1-pro-preview"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"a": [FLASH], "b": ["gemini-3.1-pro-preview"]}
    assert turn_spend.current() is None


def test_a_turn_inside_a_turn_sums_into_the_outer_one():
    @turn_spend.collects_turn
    def inner():
        turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))

    @turn_spend.collects_turn
    def outer():
        inner()
        return turn_spend.turn_record()

    record = outer()
    assert record["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert turn_spend.current() is None


# ---------------------------------------------------------------------------- the ladder feeds it

class _Client:
    def __init__(self, provider, outcomes, usage):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.usage = usage

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=dict(self.usage), model=model,
                           provider=self.provider, metadata={"stop_reason": "end_turn"})


class _Config:
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    LOG_DIR = None

    def __init__(self, primary, fallback, agent):
        self.LLM_CLIENT = primary
        self.LLM_MODEL = OPUS
        self.LLM_CLIENTS = {"anth": primary, "gcp": fallback}
        self.AGENT_MODEL_CATALOG = {
            "gcp:current": {agent: {"provider": "gcp", "model": FLASH, "thinking_level": None}},
        }


class _Plan(BaseModel):
    mode: str


def test_a_structured_call_that_moved_on_an_empty_body_is_costed_on_both_models():
    primary = _Client("bedrock", ["", '{"mode": "x"}'], BEDROCK_USAGE)
    fallback = _Client("gcp", ['{"mode": "graph_query"}'], GEMINI_USAGE)
    with turn_spend.collecting():
        plan = call_llm_structured(_Config(primary, fallback, "parser"), "q", _Plan, system="s",
                                   client=primary, model_name=OPUS, agent_label="parser")
        record = turn_spend.turn_record()
    assert plan.mode == "graph_query"
    assert record["total_cost_usd"] == pytest.approx(
        _price(OPUS, BEDROCK_USAGE) + _price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert record["models_used"] == [FLASH]
    assert record["model_fallback"] == [{"agent": "parser", "from": OPUS, "to": FLASH, "reason": "empty"}]
    assert record["cost_partial"] is False


def test_a_text_call_that_timed_out_and_moved_is_partial():
    primary = _Client("bedrock", [LLMTimeoutError("LLM call timed out after 300 seconds")], BEDROCK_USAGE)
    fallback = _Client("gcp", ["the reply"], GEMINI_USAGE)
    with turn_spend.collecting():
        text = call_llm_text(_Config(primary, fallback, "chatter"), messages=[{"role": "user", "content": "hi"}],
                             client=primary, model_name=OPUS, agent_label="chatter")
        record = turn_spend.turn_record()
    assert text == "the reply"
    assert record["cost_partial"] is True
    assert record["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert record["cost"]["unobserved_calls"][0]["model"] == OPUS


def test_a_fatal_turn_still_counts_what_it_spent():
    primary = _Client("bedrock", [LLMServiceUnavailableError("503")], BEDROCK_USAGE)
    fallback = _Client("gcp", [""], GEMINI_USAGE)
    with turn_spend.collecting():
        with pytest.raises(LLMFatalError):
            call_llm_text(_Config(primary, fallback, "chatter"), messages=[{"role": "user", "content": "hi"}],
                          client=primary, model_name=OPUS, agent_label="chatter")
        record = turn_spend.turn_record()
    assert record["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert record["model_fallback"][0]["reason"] == "unavailable"


class _ToolClient:
    provider = "bedrock"

    def __init__(self, usage):
        self.usage = usage

    def reset_connections(self):
        return True

    def chat_with_tools(self, **kw):
        usage = dict(self.usage)
        if kw.get("cache_prompt"):
            usage["cache_ttl"] = "1h"
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t", "name": "f", "input": {}}],
                "usage": usage, "metadata": {"stop_reason": "tool_use"}}


def test_a_tool_loop_call_is_costed_with_its_one_hour_cache_writes():
    usage = {"prompt_tokens": 300, "completion_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 9000}
    config = type("C", (), {"AGENT_MODEL_CATALOG": {}, "_CATALOG_KEY": "default", "LOG_DIR": None,
                            "LLM_CLIENTS": {}, "_THINKING_BUDGET_MAP": {}})()
    with turn_spend.collecting():
        tool_loop.call_tools(config, messages=[{"role": "user", "content": "hi"}], tools=[], system="s",
                             model_name=OPUS, client=_ToolClient(usage), agent_label="followup")
        record = turn_spend.turn_record()
    expected = (300 * 5.50 + 9000 * 11.00 + 50 * 27.50) / 1_000_000
    assert record["total_cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert record["models_used"] == [OPUS]
    assert record["cost"]["calls"][0]["billed"]["cache_write_1h"] == 9000


# ---------------------------------------------------------------------------- the turn record

def test_query_complete_carries_the_turn_record_and_the_breakdown():
    events = []
    debug = {"parser_plan": {"mode": "graph_query"}}
    with turn_spend.collecting():
        turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))
        payload = orchestrator._emit_query_complete(
            lambda name, data: events.append((name, data)), "Found 3.", debug, 7)
    assert events == [("query_complete", payload)]
    assert payload["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert payload["cost_partial"] is False
    assert payload["models_used"] == [FLASH]
    assert payload["model_fallback"] == []
    assert payload["debug"]["parser_plan"] == {"mode": "graph_query"}
    assert payload["debug"]["cost"]["calls"][0]["model"] == FLASH
    assert "cost" not in debug, "the caller's debug dict (often session['last_debug']) is left alone"


def test_query_complete_outside_a_turn_is_unchanged():
    payload = orchestrator._emit_query_complete(None, "r", {}, None)
    assert payload == {"reply": "r", "debug": {}, "bundle_id": None}


@pytest.mark.parametrize("entry_point", ["run_query", "run_query_plan", "run_pipeline_launch"])
def test_every_ns_entry_point_collects_its_turn(monkeypatch, entry_point):
    """Each entry point starts the collector before anything else runs and ends it after
    the turn: a model call made inside reaches that turn's query_complete."""
    def gate(session, config, credentials, send_event, **_kw):
        turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))
        return config, orchestrator._emit_query_complete(send_event, "refused", {}, None)

    monkeypatch.setattr(orchestrator, "_identity_gate", gate)
    payload = getattr(orchestrator, entry_point)({}, object(), "q")
    assert payload["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert payload["models_used"] == [FLASH]
    assert turn_spend.current() is None


def test_a_turn_that_escapes_its_entry_point_takes_its_record_with_it():
    """run_pipeline_launch does not guard the pipeline agent, so a fatal from a tool loop
    escapes it and the pipeline body reports it (NessieAI/ns/turn.py _report_fatal).
    The collector is gone by then, so the record rides on the exception."""
    @turn_spend.collects_turn
    def launch():
        turn_spend.record_call(_entry(OPUS, agent="pipeline_agent", provider="bedrock"),
                               resp=_resp(BEDROCK_USAGE, OPUS, "bedrock"))
        raise LLMFatalError("both models failed", agent="pipeline_agent", unavailable=True)

    with pytest.raises(LLMFatalError) as caught:
        launch()
    fields = turn_spend.cost_fields(caught.value)
    assert fields["total_cost_usd"] == pytest.approx(_price(OPUS, BEDROCK_USAGE), abs=1e-6)
    assert fields["cost_partial"] is False
    assert set(fields) == {"total_cost_usd", "cost_partial", "models_used", "model_fallback"}
    assert turn_spend.cost_fields(RuntimeError("no record")) == {}


def test_inside_a_turn_the_cost_fields_are_the_running_turns():
    """run_query's own crash handler reads the turn it is still inside."""
    assert turn_spend.cost_fields() == {}
    with turn_spend.collecting():
        turn_spend.record_call(_entry(), resp=_resp(GEMINI_USAGE))
        fields = turn_spend.cost_fields()
    assert fields["total_cost_usd"] == pytest.approx(_price(FLASH, GEMINI_USAGE), abs=1e-6)
    assert fields["models_used"] == [FLASH]
