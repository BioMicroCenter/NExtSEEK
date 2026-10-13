"""The entity agent asks a stalled model once, not three times (F3, operator ruling 2026-09-28).

After its structured call timed out, the entity called the SAME Gemini again (``retries=0``, ``timeout_retries=0``,
300 s, never moving). The ladder had already moved the first call to the fallback, so that was a third try, on the
model that had just stalled: with both models stalled the entity took 300 + 180 + 300 = 780 s and then dropped its
entities. That extra call is gone. The ladder's one move is the entity's only second chance:

* the primary stalls, the fallback answers: the fallback's entities, the primary asked once;
* both stall: the turn ends with the models-unavailable text (the ladder's ``LLMFatalError``, test D2);
* no chain (a profile that is not deployed): the ladder's own same-provider retry, then the entities are dropped,
  with no third call.

Driven through the real ladder with stand-in clients; no model is called.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_nextseek.agents import entity as entity_mod
from chat_nextseek.llm_clients import LLMFatalError, LLMResponse, LLMServiceUnavailableError, LLMTimeoutError

FLASH = "gemini-3.5-flash"
SONNET = "us.anthropic.claude-sonnet-4-6"
ANSWER = '{"sampletypes": [], "assays": [], "keywords": ["NDMA"]}'


class _Client:
    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def reset_connections(self):
        return True

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={"stop_reason": "end_turn"})


def _config(flash, sonnet=None):
    clients = {"gcp": flash}
    catalog = {}
    if sonnet is not None:
        clients["anth"] = sonnet
        catalog = {"anth:current": {"entity": {"provider": "anth", "model": SONNET, "thinking_level": None}}}
    return SimpleNamespace(
        ENTITY_SYSTEM_PROMPT="s", MIN_SAMPLETYPES=[], MIN_ASSAYS=[], MIN_PROJECTS=[], LABS=None, LOG_DIR=None,
        _CATALOG_KEY="default", _THINKING_BUDGET_MAP={None: None}, LLM_MODEL=FLASH, LLM_CLIENT=flash,
        LLM_CLIENTS=clients, AGENT_MODEL_CATALOG=catalog,
        get_agent_model=lambda label: (flash, FLASH, None),
    )


def test_a_stalled_primary_is_asked_once_and_the_fallback_answers():
    flash = _Client("gcp", [LLMTimeoutError("30 s"), ANSWER])
    sonnet = _Client("bedrock", [ANSWER])
    out = entity_mod.entity_agent(_config(flash, sonnet), "mice treated with NDMA", [], [], [])
    assert "NDMA" in out.keywords
    assert flash.calls == [FLASH]
    assert sonnet.calls == [SONNET]


def test_both_stalled_ends_the_turn_after_one_call_each():
    """It used to be 780 s and a turn with no entities; now 30 + 90 s and the plain text."""
    flash = _Client("gcp", [LLMTimeoutError("30 s"), ANSWER, ANSWER])
    sonnet = _Client("bedrock", [LLMTimeoutError("90 s"), ANSWER])
    with pytest.raises(LLMFatalError) as excinfo:
        entity_mod.entity_agent(_config(flash, sonnet), "mice treated with NDMA", [], [], [])
    assert excinfo.value.unavailable is True
    assert flash.calls == [FLASH], "no third try on the stalled model"
    assert sonnet.calls == [SONNET]


def test_with_no_chain_the_entities_are_dropped_without_a_third_call():
    flash = _Client("gcp", [LLMTimeoutError("t1"), LLMTimeoutError("t2"), ANSWER])
    out = entity_mod.entity_agent(_config(flash), "mice treated with NDMA", [], [], [])
    assert out.keywords == [] and out.sampletypes == []
    assert flash.calls == [FLASH, FLASH], "the ladder's one same-provider retry, and nothing after it"


def test_the_entity_no_longer_has_a_retry_of_its_own():
    import inspect

    src = inspect.getsource(entity_mod.entity_agent)
    assert "retry_after_timeout" not in src
    assert "timeout_retries=0" not in src


def test_both_models_down_on_the_raw_path_ends_the_turn():
    """CI-COVERAGE gap 3a, settled by D2: bad output sends the entity down its raw-text path, and when both models
    fail there the turn ends with the models-unavailable text (it used to return no entities)."""
    flash = _Client("gcp", ["this is not json", "still not json", "nor this", LLMServiceUnavailableError("503")])
    sonnet = _Client("bedrock", [LLMServiceUnavailableError("503 again")])
    with pytest.raises(LLMFatalError) as excinfo:
        entity_mod.entity_agent(_config(flash, sonnet), "mice treated with NDMA", [], [], [])
    assert excinfo.value.unavailable is True
    assert [m["to"] for m in excinfo.value.model_fallback] == [SONNET]
