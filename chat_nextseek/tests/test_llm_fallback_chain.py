"""
Regression lock for B1: the 503 provider-fallback chain must be reachable for Bedrock.

`_FALLBACK_CHAINS` is keyed on the *catalog* provider vocabulary ("gcp", "anth", "oai" —
the `provider` field in `agent_model_catalog.json`). The lookup in `call_llm_structured`
used the *client* vocabulary instead (`BaseLLMClient.provider`: "openai", "gcp",
"anthropic", "bedrock"). Only "gcp" coincides, so a 503 from `BedrockClient` looked up
`("default", "bedrock")`, got `[]`, and raised `LLMFatalError` immediately.

That is dead precisely where it hurts: the shipped `default` profile routes `parser`,
`report_writer`, `report_coder` and `multi_parser` to `us.anthropic.claude-opus-4-7`
with `provider: "anth"` → `BedrockClient`. In the 2026-08-03 seed-6 run one Bedrock
outage produced ten of the eighteen reds, all reported as
`All provider fallbacks exhausted — agent 'parser': ServiceUnavailableException`.

No live calls — every client is a stub, and the agent routes come from the shipped
`agent_model_catalog.json` so the tests move if the real routing moves.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from pydantic import BaseModel

from chat_nextseek import llm_clients
from chat_nextseek.config import ChatConfig
from chat_nextseek.llm_clients import (
    AnthropicClient,
    BaseLLMClient,
    BedrockClient,
    GeminiClient,
    LLMFatalError,
    LLMServiceUnavailableError,
    OpenAIClient,
)
from chat_nextseek.schemas.schema_helper import (
    _CLIENT_TO_CATALOG_PROVIDER,
    _catalog_provider,
    _get_fallback_agent_configs,
    call_llm_structured,
)


# --------------------------------------------------------------------------- fakes


class _StubResponse:
    def __init__(self, text: str):
        self.content = text
        self.usage = {}
        self.metadata = {}


class _StubClient:
    """Stands in for an `llm_clients` client: a provider tag and a `chat()`."""

    def __init__(self, provider: str, *, raises: Exception | None = None,
                 replies: list[str] | None = None):
        self.provider = provider
        self._raises = raises
        self._replies = list(replies or [])
        self.calls: list[dict] = []

    def chat(self, *, model, temperature=0, messages=None, response_format=None,
             thinking_budget=None):
        self.calls.append({"model": model, "thinking_budget": thinking_budget})
        if self._raises is not None:
            raise self._raises
        return _StubResponse(self._replies.pop(0) if self._replies else "{}")


def _outage() -> LLMServiceUnavailableError:
    """The exact shape of the failure that ate the seed-6 run."""
    return LLMServiceUnavailableError(
        "ServiceUnavailableException (reached max retries: 4)"
    )


# ------------------------------------------------------- shipped catalog (real data)


def _flatten_profile(profile: dict) -> dict[str, dict]:
    """Expand a grouped model-keyed profile into the agent-keyed form config normalizes to."""
    flattened: dict[str, dict] = {}
    for model_name, spec in profile.get("models", {}).items():
        for entry in (spec if isinstance(spec, list) else [spec]):
            for agent in entry.get("agents", []):
                flattened[agent] = {
                    "provider": entry.get("provider"),
                    "model": model_name,
                    "thinking_level": entry.get("thinking_level"),
                }
    return flattened


def _shipped_catalog() -> dict[str, dict]:
    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "agent_model_catalog.json").read_text()
    )
    return {name: _flatten_profile(p) for name, p in raw.items() if not name.startswith("_")}


def _stub_config(clients: dict, *, catalog_key: str = "default"):
    return types.SimpleNamespace(
        LOG_DIR=None,
        LLM_CLIENT=None,
        LLM_MODEL="unused-primary-model",
        LLM_CLIENTS=clients,
        AGENT_MODEL_CATALOG=_shipped_catalog(),
        _CATALOG_KEY=catalog_key,
        _THINKING_BUDGET_MAP=ChatConfig._THINKING_BUDGET_MAP,
    )


class _Plan(BaseModel):
    mode: str


# ------------------------------------------------------------- the headline (B1 lock)


def test_bedrock_503_falls_back_instead_of_killing_the_turn():
    """A 503 from BedrockClient under the shipped `default` profile must fail over.

    Before the fix this raised
    `LLMFatalError: All provider fallbacks exhausted — agent 'parser'`
    because ("default", "bedrock") is not a key of _FALLBACK_CHAINS.
    """
    bedrock = _StubClient("bedrock", raises=_outage())
    gcp = _StubClient("gcp", replies=['{"mode": "new_search"}'])
    config = _stub_config({"anth": bedrock, "gcp": gcp})

    plan = call_llm_structured(
        config,
        "Find me mice treated with NDMA",
        _Plan,
        system="You are a router.",
        client=bedrock,
        model_name="us.anthropic.claude-opus-4-7",
        agent_label="parser",
        retries=2,
    )

    assert plan.mode == "new_search"
    assert gcp.calls, "the GCP fallback client was never called — the chain is unreachable"


def test_gcp_503_still_falls_back():
    """Regression guard: "gcp" is the one client value that matched a chain key by
    coincidence, and normalising the lookup must not disturb it."""
    gcp = _StubClient("gcp", raises=_outage())
    bedrock = _StubClient("bedrock", replies=['{"mode": "new_search"}'])
    config = _stub_config({"gcp": gcp, "anth": bedrock})

    plan = call_llm_structured(
        config,
        "Find me mice treated with NDMA",
        _Plan,
        system="You are a router.",
        client=gcp,
        model_name="gemini-3.5-flash",
        agent_label="parser",
        retries=2,
    )

    assert plan.mode == "new_search"
    assert bedrock.calls, "the ('default', 'gcp') chain stopped firing"


def test_a_provider_with_no_chain_still_raises_fatal():
    """Normalising must not invent fallbacks: an unmapped provider keeps the old
    fail-fast behaviour rather than silently routing somewhere unintended."""
    mystery = _StubClient("some-new-provider", raises=_outage())
    gcp = _StubClient("gcp", replies=['{"mode": "new_search"}'])
    config = _stub_config({"gcp": gcp, "anth": _StubClient("bedrock")})

    with pytest.raises(LLMFatalError):
        call_llm_structured(
            config,
            "Find me mice treated with NDMA",
            _Plan,
            client=mystery,
            model_name="whatever",
            agent_label="parser",
            retries=2,
        )

    assert gcp.calls == []


# ------------------------------------------------------------- the resolved chain


def test_first_fallback_for_a_failed_bedrock_parser_is_a_gcp_client():
    """("default", "anth") -> ["gcp:current", "anth:lite", "gcp:lite"], so a parser
    knocked out on Bedrock must land on Gemini first — not on a second Bedrock model
    that the same outage would also take down."""
    bedrock = _StubClient("bedrock")
    gcp = _StubClient("gcp")
    config = _stub_config({"anth": bedrock, "gcp": gcp})

    chain = _get_fallback_agent_configs(config, "parser", _catalog_provider(bedrock))

    assert chain, "no fallbacks resolved for a failed Bedrock parser"
    first_client, first_model, first_budget = chain[0]
    assert first_client is gcp
    assert first_client.provider == "gcp"
    # From the shipped catalog's gcp:current profile, not a literal:
    assert first_model == _shipped_catalog()["gcp:current"]["parser"]["model"]
    assert first_budget == ChatConfig._THINKING_BUDGET_MAP["high"]


def test_gcp_lookup_is_byte_for_byte_what_it_was_before():
    """The regression guard at the lookup level: for "gcp" the normalised value and the
    raw client value are the same string, so the resolved chain is identical."""
    gcp = _StubClient("gcp")
    bedrock = _StubClient("bedrock")
    config = _stub_config({"gcp": gcp, "anth": bedrock})

    assert _catalog_provider(gcp) == "gcp"
    normalised = _get_fallback_agent_configs(config, "parser", _catalog_provider(gcp))
    raw = _get_fallback_agent_configs(config, "parser", "gcp")

    assert normalised == raw
    assert normalised, "the ('default', 'gcp') chain resolved to nothing"
    assert normalised[0][0] is bedrock, "gcp's first fallback should be the anth:current profile"


# ------------------------------------------------------------------ _catalog_provider


@pytest.mark.parametrize(
    "client_cls, expected",
    [
        (BedrockClient, "anth"),
        (AnthropicClient, "anth"),
        (GeminiClient, "gcp"),
        (OpenAIClient, "oai"),
    ],
)
def test_catalog_provider_maps_every_shipped_client_class(client_cls, expected):
    """Built with __new__ so no credentials, SDK import or network is touched."""
    assert _catalog_provider(client_cls.__new__(client_cls)) == expected


def test_catalog_provider_passes_an_unknown_provider_through_unchanged():
    """An unmapped provider must survive the translation intact, so the failure stays
    diagnosable. Swallowing it to "" or None would make every unknown client collide
    on one meaningless lookup key."""
    assert _catalog_provider(_StubClient("some-new-provider")) == "some-new-provider"


def test_catalog_provider_is_empty_string_when_there_is_no_provider_at_all():
    assert _catalog_provider(object()) == ""
    assert _catalog_provider(_StubClient("")) == ""


# ------------------------------------------------------------------------ anti-drift


def _concrete_client_classes() -> list[type]:
    """Every concrete BaseLLMClient subclass defined in llm_clients, by introspection.

    Deliberately not a hardcoded list: the point of the guard is that a NEW client class
    cannot silently reintroduce the unreachable-chain bug. BaseLLMClient itself is
    excluded — its `provider = "unknown"` is a placeholder, not a real provider.
    """
    return [
        obj
        for obj in vars(llm_clients).values()
        if isinstance(obj, type)
        and issubclass(obj, BaseLLMClient)
        and obj is not BaseLLMClient
        and obj.__module__ == llm_clients.__name__
    ]


def test_every_client_class_provider_has_a_catalog_mapping():
    discovered = _concrete_client_classes()
    assert len(discovered) >= 4, (
        f"introspection found only {[c.__name__ for c in discovered]} — the guard is "
        "not actually looking at the client classes"
    )

    unmapped = {
        cls.__name__: cls.provider
        for cls in discovered
        if cls.provider not in _CLIENT_TO_CATALOG_PROVIDER
    }
    assert not unmapped, (
        f"client class(es) with no _CLIENT_TO_CATALOG_PROVIDER entry: {unmapped}. "
        "Without one, a 503 from that client looks up a chain key that does not exist "
        "and the whole turn dies instead of failing over. Add the mapping in "
        "schemas/schema_helper.py."
    )


def test_every_fallback_chain_key_uses_the_catalog_vocabulary():
    """The reverse drift: a chain keyed on a client-vocabulary provider (e.g. "bedrock")
    would be unreachable, because the lookup is always normalised first."""
    catalog_vocabulary = set(_CLIENT_TO_CATALOG_PROVIDER.values())
    from chat_nextseek.schemas.schema_helper import _FALLBACK_CHAINS

    stray = {
        key for key in _FALLBACK_CHAINS if key[1] not in catalog_vocabulary
    }
    assert not stray, f"unreachable _FALLBACK_CHAINS keys (not catalog providers): {stray}"
