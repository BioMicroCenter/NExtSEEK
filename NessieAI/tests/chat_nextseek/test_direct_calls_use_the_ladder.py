"""The four model calls that went straight to the SDK now go through the recovery ladder.

A read-only inventory at 926be1e3 found four calls that bypassed ``_call_with_recovery``,
so a timeout, a 503 or an empty body there never moved to another model, and one of
them had no wall clock at all (operator ruling 2026-09-25, fix 5):

* the entity agent's raw fallback (``entity_client.chat`` on a 180 s thread wrapper),
* the memory coder's reply (``chatter_client.chat``),
* the legacy memory agent (``memory_client.chat``, Sonnet 4.6, no wall clock),
* the context engineer's LLM fallback (``ce_client.chat``).

Each is now ``call_llm_text`` under its catalog key, so the primary model and client are
the ones ``get_agent_model(<key>)`` returns, as before, and each has a wall clock: the
entity keeps 180 s, the two memory calls take the chatter's 300 s (they do the chatter's
work, on the chatter's and the memory agent's models), and the context engineer's short
extraction gets 60 s and a 60 s retry.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from chat_nextseek.agents import entity as entity_mod
from chat_nextseek.agents import memory as memory_mod
from chat_nextseek.agents.planner import agent as planner_mod
from chat_nextseek.llm_clients import LLMAPIConnectionError, LLMResponse, LLMServiceUnavailableError
from chat_nextseek.schemas import ContextEngineerOutput, MemoryCoderOutput, PlanStep

PRIMARY_CLIENT = object()
PRIMARY_MODEL = "primary-model"


class _TextCapture:
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    def __call__(self, config, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _model_lookup(expected):
    def get_agent_model(label):
        assert label == expected
        return PRIMARY_CLIENT, PRIMARY_MODEL, None
    return get_agent_model


def _assert_on_the_ladder(call, *, key, timeout, retry):
    assert call["agent_label"] == key, "the chain is looked up by the catalog key whose model it uses today"
    assert call["client"] is PRIMARY_CLIENT and call["model_name"] == PRIMARY_MODEL, "the primary is unchanged"
    assert call["timeout_seconds"] == timeout
    assert call["timeout_retry_seconds"] == retry


def test_no_direct_sdk_call_is_left_in_these_agents():
    for module, names in ((entity_mod, ["entity_client.chat("]),
                          (memory_mod, ["chatter_client.chat(", "memory_client.chat("]),
                          (planner_mod, ["ce_client.chat("])):
        src = inspect.getsource(module)
        for name in names:
            assert name not in src, f"{module.__name__} still calls {name}"


# ---------------------------------------------------------------------------- entity

def _entity_config():
    return SimpleNamespace(ENTITY_SYSTEM_PROMPT="s", MIN_SAMPLETYPES=[], MIN_ASSAYS=[], MIN_PROJECTS=[],
                           LABS=None, get_agent_model=_model_lookup("entity"))


def _structured_fails(**kwargs):
    raise ValueError("the structured output did not validate")


def test_the_entity_raw_fallback_goes_through_the_ladder_with_its_180_s(monkeypatch):
    monkeypatch.setattr(entity_mod, "call_llm_structured", _structured_fails)
    capture = _TextCapture('{"sampletypes": [], "assays": [], "keywords": ["NDMA"]}')
    monkeypatch.setattr(entity_mod, "call_llm_text", capture)

    out = entity_mod.entity_agent(_entity_config(), "mice treated with NDMA", [], [], [])

    (call,) = capture.calls
    _assert_on_the_ladder(call, key="entity", timeout=180, retry=180)
    assert "NDMA" in out.keywords


class _Client:
    def __init__(self, provider, outcomes):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(content=outcome, raw=None, usage=None, model=model, provider=self.provider,
                           metadata={})


def test_a_503_on_the_entity_raw_fallback_moves_to_the_next_provider(monkeypatch):
    monkeypatch.setattr(entity_mod, "call_llm_structured", _structured_fails)
    flash = _Client("gcp", [LLMServiceUnavailableError("503 UNAVAILABLE")])
    sonnet = _Client("bedrock", ['{"sampletypes": [], "assays": [], "keywords": ["NDMA"]}'])
    config = _entity_config()
    config.get_agent_model = lambda label: (flash, "gemini-3.5-flash", None)
    config.LOG_DIR = None
    config._CATALOG_KEY = "default"
    config._THINKING_BUDGET_MAP = {None: None}
    config.LLM_CLIENTS = {"gcp": flash, "anth": sonnet}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {"entity": {"provider": "anth", "model": "us.anthropic.claude-sonnet-4-6",
                                    "thinking_level": None}},
    }

    out = entity_mod.entity_agent(config, "mice treated with NDMA", [], [], [])

    assert sonnet.calls == ["us.anthropic.claude-sonnet-4-6"]
    assert "NDMA" in out.keywords


# ---------------------------------------------------------------------------- memory

def test_the_memory_coder_reply_goes_through_the_ladder_as_the_chatter(monkeypatch, tmp_path):
    capture = _TextCapture("Three of them.")
    monkeypatch.setattr(memory_mod, "call_llm_text", capture)
    config = SimpleNamespace(CHATTER_SYSTEM_PROMPT="s", LOG_DIR=str(tmp_path),
                             get_agent_model=_model_lookup("chatter"))

    answer = memory_mod._format_memory_coder_answer(
        config, original_query="mice", user_query="how many?", source_label="graph",
        coder_output=MemoryCoderOutput(extraction_code="result = 3"), computed_result={"n": 3},
        row_count=3, log_dir=str(tmp_path),
    )

    (call,) = capture.calls
    _assert_on_the_ladder(call, key="chatter", timeout=300, retry=180)
    assert call["log_label"] == "memory_coder_chatter", "the ledger still names the memory coder's reply"
    assert answer == "Three of them."


def _legacy_config(tmp_path):
    return SimpleNamespace(MEMORY_SYSTEM_PROMPT="s", LOG_DIR=str(tmp_path), get_agent_model=_model_lookup("memory"))


def test_the_legacy_memory_agent_goes_through_the_ladder_with_a_wall_clock(monkeypatch, tmp_path):
    capture = _TextCapture("It held three records.")
    monkeypatch.setattr(memory_mod, "call_llm_text", capture)

    answer = memory_mod._legacy_memory_agent_answer(
        _legacy_config(tmp_path), "how many?", {"user_query": "mice", "graph_result": {"count": 3}},
        log_dir=str(tmp_path),
    )

    (call,) = capture.calls
    _assert_on_the_ladder(call, key="memory", timeout=300, retry=180)
    assert answer == "It held three records."


def test_the_legacy_memory_agent_still_degrades_on_a_connection_error(monkeypatch, tmp_path):
    """With nowhere to move, the ladder hands a connection error back unchanged, and this
    agent's own degraded reply still answers it."""
    monkeypatch.setattr(memory_mod, "call_llm_text", _TextCapture(LLMAPIConnectionError("reset")))

    answer = memory_mod._legacy_memory_agent_answer(
        _legacy_config(tmp_path), "how many?", {"user_query": "mice", "graph_result": {"count": 3}},
        log_dir=str(tmp_path),
    )

    assert "connection issue" in answer and "3 records" in answer


# ---------------------------------------------------------------------------- context engineer

# Code whose result looks like a ContextEngineerOutput, which the step rejects and hands to
# the LLM fallback. (Code that raises reaches the fallback too, but the fallback then fails
# on its own before any model call: ``ce_keys`` is bound only once the code has run. That
# is older than this change and reported, not fixed, here.)
CE_SHAPED = ('result = {"extraction_code": "", "enriched_context": {}, "method": "code", '
             '"notes": ""}')


def test_the_context_engineer_fallback_goes_through_the_ladder(monkeypatch):
    monkeypatch.setattr(planner_mod, "call_llm_structured", lambda **kw: ContextEngineerOutput(
        extraction_code=CE_SHAPED, enriched_context={}, method="code"))
    capture = _TextCapture('{"uids": ["TIS-1"]}')
    monkeypatch.setattr(planner_mod, "call_llm_text", capture)
    config = SimpleNamespace(CONTEXT_ENGINEER_SYSTEM_PROMPT="s", get_agent_model=_model_lookup("context_engineer"))
    step = PlanStep(step_id=1, tool="graph_query", context_prompt="find tissue", extraction_hint="uids")

    out = planner_mod.context_engineer_step(config, step, {"output": {"data": [{"uid": "TIS-1"}]}})

    (call,) = capture.calls
    _assert_on_the_ladder(call, key="context_engineer", timeout=60, retry=60)
    assert out.enriched_context == {"uids": ["TIS-1"]}
    assert out.method == "llm"


@pytest.mark.parametrize("module", [entity_mod, memory_mod, planner_mod], ids=lambda m: m.__name__)
def test_each_module_imports_the_ladder(module):
    assert callable(getattr(module, "call_llm_text", None))
