"""Provider resilience: the failures that made production turns return nothing.

Three production incidents are pinned here.

* turns 463/464 (wesselr, 2026-09-04) — the graph query had already returned, the
  chatter drew a Gemini 503, nothing retried, and the user saw "Internal pipeline
  error" instead of the finished result or the real cause.
* turn 406 (bonniethiel, 2026-08-28) — the parser model returned a 1-token completion
  three times; an empty completion was treated as a schema error, so the repair loop
  asked the same model again instead of failing over.
* The generic-error clobber that hid both: ``run_query`` emits the real message and
  re-raises, and the pipeline body then emitted a second, generic ``query_error``.
"""
from __future__ import annotations

import pytest

from chat_nextseek.llm_clients import (
    BedrockClient,
    GeminiClient,
    LLMFatalError,
    LLMResponse,
    LLMServiceUnavailableError,
)
from chat_nextseek.schemas.schema_helper import call_llm_structured, call_llm_text
from pydantic import BaseModel


class _Plan(BaseModel):
    mode: str = "unsupported"


class _FakeClient:
    """Minimal BaseLLMClient stand-in that replays a scripted list of outcomes."""

    def __init__(self, provider: str, outcomes: list):
        self.provider = provider
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None, thinking_budget=None):
        self.calls.append({"model": model, "messages": messages})
        outcome = self.outcomes.pop(0) if self.outcomes else ""
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(
            content=outcome, raw=None, usage=None, model=model,
            provider=self.provider, metadata={"stop_reason": "end_turn"},
        )


class _FakeConfig:
    """Only the attributes the recovery ladder touches."""

    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None, "low": 4000, "medium": 8000, "high": 16000}
    LOG_DIR = "/tmp"

    def __init__(self, primary, fallback=None, agent="parser"):
        self.LLM_CLIENT = primary
        self.LLM_MODEL = "primary-model"
        self.LLM_CLIENTS = {"anth": primary}
        self.AGENT_MODEL_CATALOG = {}
        if fallback is not None:
            self.LLM_CLIENTS["gcp"] = fallback
            self.AGENT_MODEL_CATALOG = {
                "gcp:current": {agent: {"provider": "gcp", "model": "fallback-model", "thinking_level": None}},
            }


# --------------------------------------------------------------------------
# Gemini and Bedrock clients now retry the same class of failure.
# --------------------------------------------------------------------------

def _stub_google_genai(monkeypatch) -> dict:
    """Stand in for google-genai; returns the dict the stub client's kwargs land in.

    `import google.genai as genai` binds `genai` as an attribute of the `google` package
    and falls back to `sys.modules` only when the attribute is missing. Once the real SDK
    was imported anywhere earlier in the session the attribute is set, so a `sys.modules`
    stub alone was bypassed and the real client was built: the test passed alone and
    failed in the full lane. Both the entries and the attribute are patched, and
    monkeypatch puts all of them back.
    """
    import sys
    import types

    captured: dict = {}

    def _client(**kwargs):
        captured.update(kwargs)
        return object()

    class HttpRetryOptions:
        def __init__(self, **kw):
            self.kw = kw

    class HttpOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    types_mod = types.ModuleType("google.genai.types")
    types_mod.HttpOptions = HttpOptions
    types_mod.HttpRetryOptions = HttpRetryOptions
    stub = types.ModuleType("google.genai")
    stub.Client = _client
    stub.types = types_mod

    google_pkg = sys.modules.get("google")
    if google_pkg is None:
        google_pkg = types.ModuleType("google")
        google_pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", stub)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    monkeypatch.setattr(google_pkg, "genai", stub, raising=False)
    return captured


def test_gemini_client_is_constructed_with_retries_enabled(monkeypatch):
    """`google-genai` defaults retry_options to None, which its own retry_args()
    documents as the "never retry" strategy. Every Gemini 503 therefore reached the
    caller on the first try, which is production turn 463."""
    captured = _stub_google_genai(monkeypatch)

    GeminiClient(api_key="k")

    assert "http_options" in captured, "Gemini client built without http_options: retries are off"
    assert getattr(captured["http_options"], "retry_options", None) is not None


def test_the_gemini_retry_check_holds_after_the_real_sdk_was_imported(monkeypatch):
    """The order the full lane runs in: something earlier imported the real google-genai,
    so `google.genai` is also an attribute of the `google` package."""
    pytest.importorskip("google.genai")

    test_gemini_client_is_constructed_with_retries_enabled(monkeypatch)


def test_bedrock_client_pins_standard_retry_mode():
    """botocore rewrites max_attempts (a RETRY count) into total_max_attempts, so the
    assertion is on the resolved value: 5 total attempts, the same as the legacy
    default the client relied on implicitly before."""
    client = BedrockClient(region="us-east-1")
    retries = client.client.meta.config.retries
    assert retries["mode"] == "standard"
    assert retries["total_max_attempts"] == 5


# --------------------------------------------------------------------------
# An empty completion is a provider fault, not a schema error.
# --------------------------------------------------------------------------

def test_empty_completion_fails_over_to_the_next_provider():
    """Turn 406: opus-4-7 returned 1 token three times. Before, the repair loop asked
    the SAME model twice more and raised StructuredOutputError. Now the first empty
    completion trips the provider chain."""
    primary = _FakeClient("bedrock", ["", "", ""])
    fallback = _FakeClient("gcp", ['{"mode": "graph_query"}'])
    config = _FakeConfig(primary, fallback)

    plan = call_llm_structured(
        config, "q", _Plan, system="s", agent_label="parser", client=primary,
        model_name="primary-model",
    )

    assert plan.mode == "graph_query"
    assert len(primary.calls) == 1, "the empty completion should not be retried on the same model"
    assert len(fallback.calls) == 1


def test_empty_completion_with_no_chain_is_fatal_not_a_parse_error():
    primary = _FakeClient("bedrock", ["", "", ""])
    config = _FakeConfig(primary)

    with pytest.raises(LLMFatalError) as excinfo:
        call_llm_structured(
            config, "q", _Plan, system="s", agent_label="parser", client=primary,
            model_name="primary-model",
        )
    assert "empty completion" in str(excinfo.value)


def test_whitespace_only_completion_counts_as_empty():
    primary = _FakeClient("bedrock", ["   \n  "])
    config = _FakeConfig(primary)
    with pytest.raises(LLMFatalError):
        call_llm_structured(
            config, "q", _Plan, system="s", agent_label="parser", client=primary,
            model_name="primary-model",
        )


def test_malformed_json_still_uses_the_repair_loop():
    """The repair loop must survive: a non-empty but invalid completion is a schema
    problem and the same model gets another go."""
    primary = _FakeClient("bedrock", ["not json", '{"mode": "graph_query"}'])
    config = _FakeConfig(primary)

    plan = call_llm_structured(
        config, "q", _Plan, system="s", agent_label="parser", client=primary,
        model_name="primary-model",
    )
    assert plan.mode == "graph_query"
    assert len(primary.calls) == 2
    repair_turn = primary.calls[1]["messages"][-1]
    assert "did not validate" in repair_turn["content"]


# --------------------------------------------------------------------------
# Free-text calls get the same ladder.
# --------------------------------------------------------------------------

def test_call_llm_text_falls_back_on_503():
    """The chatter's failure: a 503 on the reply-writing step used to end the turn."""
    primary = _FakeClient("gcp", [LLMServiceUnavailableError("503 UNAVAILABLE")])
    fallback = _FakeClient("bedrock", ["Here are your 129 samples."])
    config = _FakeConfig(primary, fallback, agent="chatter")
    config.LLM_CLIENTS = {"gcp": primary, "anth": fallback}
    config.AGENT_MODEL_CATALOG = {
        "anth:current": {"chatter": {"provider": "anth", "model": "fallback-model", "thinking_level": None}},
    }

    text = call_llm_text(
        config, messages=[{"role": "user", "content": "hi"}],
        client=primary, model_name="primary-model", agent_label="chatter",
    )
    assert text == "Here are your 129 samples."


def test_call_llm_text_raises_fatal_when_every_provider_refuses():
    primary = _FakeClient("gcp", [LLMServiceUnavailableError("503 UNAVAILABLE")])
    config = _FakeConfig(primary, agent="chatter")
    with pytest.raises(LLMFatalError):
        call_llm_text(
            config, messages=[{"role": "user", "content": "hi"}],
            client=primary, model_name="primary-model", agent_label="chatter",
        )
