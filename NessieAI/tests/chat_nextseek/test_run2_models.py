"""Run 2's models, as the shipped config resolves them (operator rulings 6 and 9, 2026-09-25).

* NS Gemini agents: Gemini 3.8 Flash, in the default profile and in gcp:current (the default profile's first
  fallback after a Bedrock failure). The BAML router clients are not this file's business and keep their ids.
* NS Opus agents: Claude Opus 5.5 in the default profile, effort medium for the parsers, high for the report
  writer and coder, low for the planner and the two tool loops. The other profiles keep their Opus.
* Sonnet 4.6 stays: the memory agent, and the tool loops' ``_fallback``.
* Container-CC: Opus 5.5, falling back to Opus 4.8.

Every agent is resolved through ``ChatConfig.agent_config`` exactly as a turn does, so a catalog edit that reads
right but resolves wrong fails here.
"""
from __future__ import annotations

import json

import pytest

from NessieAI import paths
from chat_nextseek.config import ChatConfig
from chat_nextseek.llm_clients import model_traits

OPUS55 = "us.anthropic.claude-opus-5-5"
FLASH38 = "gemini-3.8-flash"
SONNET = "global.anthropic.claude-sonnet-5-5"
SONNET_46 = "us.anthropic.claude-sonnet-4-6"  # the Container-CC map still names it


def _config(profile: str = "default") -> ChatConfig:
    raw = json.loads((paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json").read_text())
    config = ChatConfig.__new__(ChatConfig)
    config.AGENT_MODEL_CATALOG = ChatConfig._normalize_agent_model_catalog(config, raw)
    config._CATALOG_KEY = profile
    config.LLM_MODEL = "unused"
    return config


# agent -> (model, thinking budget, the effort the client sends)
DEFAULT_PROFILE = {
    "parser": (OPUS55, 8000, "medium"),
    "multi_parser": (OPUS55, 8000, "medium"),
    "report_writer": (OPUS55, 16000, "high"),
    "report_coder": (OPUS55, 16000, "high"),
    "planner": (OPUS55, 4000, "low"),
    "pipeline_agent": (OPUS55, 4000, "low"),
    "followup": (OPUS55, 4000, "low"),
    "memory": (SONNET, None, None),
    "system": (SONNET, None, None),  # its docs and catalog tool loop needs Bedrock (operator ruling 2026-10-02)
    "entity": (FLASH38, 8000, None),  # medium thinking, sent as a level (operator ruling 2026-09-30)
    **{agent: (FLASH38, None, None) for agent in (
        "api", "reporter", "chatter", "graph", "context_engineer", "memory_coder",
        "evaluator", "seqera_agent")},
}


@pytest.mark.parametrize("agent", sorted(DEFAULT_PROFILE))
def test_every_default_profile_agent_resolves_to_its_run_2_model_and_level(agent):
    model, budget, effort = DEFAULT_PROFILE[agent]
    cfg = _config().agent_config(agent)
    assert (cfg["model"], cfg["thinking_budget"]) == (model, budget)
    assert cfg["provider"] == ("gcp" if model == FLASH38 else "anth")
    if effort is not None:
        from chat_nextseek.llm_clients import _always_thinking_fields

        fields, _ = _always_thinking_fields(budget, 4096)
        assert fields["output_config"] == {"effort": effort}


def test_no_default_profile_agent_is_left_on_a_run_1_model():
    raw = json.loads((paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json").read_text())
    assert set(raw["default"]["models"]) == {FLASH38, SONNET, OPUS55}


def test_the_opus_agents_run_on_a_model_that_always_thinks_and_takes_no_forced_tool():
    traits = model_traits(OPUS55)
    assert traits.always_thinks and traits.adaptive_only and not traits.forced_tool_ok


def test_gcp_current_moves_its_flash_agents_and_keeps_its_pro_and_opus():
    config = _config("gcp:current")
    assert config.agent_config("entity")["model"] == FLASH38
    assert config.agent_config("entity")["thinking_budget"] == 8000
    assert config.agent_config("api")["thinking_budget"] is None
    assert config.agent_config("memory")["model"] == FLASH38  # the memory agent's fallback
    assert config.agent_config("parser")["model"] == "gemini-3.1-pro-preview"
    assert config.agent_config("followup")["model"] == "us.anthropic.claude-opus-4-7"


def test_the_tool_loops_move_to_sonnet_5_5():
    raw = json.loads((paths.CHAT_NEXTSEEK_DIR / "agent_model_catalog.json").read_text())
    for agent in ("followup", "pipeline_agent"):
        assert raw["_fallback"][agent] == {"provider": "anth", "model": SONNET, "thinking_level": None}


def test_the_gemini_default_model_is_3_8_flash():
    """ChatConfig's LLM_MODEL for mixed and gcp:current when GCP_LLM_MODEL is not set: config.py's literals."""
    source = (paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "config.py").read_text()
    assert '"gcp:current": "gemini-3.8-flash"' in source
    assert '_gcp_defaults.get(_mode, "gemini-3.8-flash")' in source
    assert '"gcp:lite": "gemini-3.5-flash"' in source  # the only 3.5 Flash left (off 2.5, 2026-10-06)
    assert source.count("gemini-3.5-flash") == 1


def test_container_cc_runs_opus_5_5_and_falls_back_to_opus_4_8():
    model_map = json.loads((paths.DMAC_BUILD_CONTEXT / "router_model_class_map.json").read_text())
    assert (model_map["opus"], model_map["opus_fallback"], model_map["sonnet"]) == (
        OPUS55, "us.anthropic.claude-opus-4-8", SONNET_46)


def test_the_entity_call_carries_medium_thinking_from_the_catalog_to_the_gemini_request():
    """Catalog level (medium) -> budget 8000 -> the client's thinking_config, for the entity and no other Flash agent."""
    import types

    from chat_nextseek.llm_clients import GeminiClient

    calls = []

    def generate_content(*, model, contents, config):
        calls.append((model, config))
        return types.SimpleNamespace(
            text="{}", candidates=[types.SimpleNamespace(finish_reason="STOP")],
            usage_metadata=types.SimpleNamespace(prompt_token_count=1, candidates_token_count=1, total_token_count=2))

    client = GeminiClient.__new__(GeminiClient)
    client.client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content))
    for profile in ("default", "gcp:current"):
        config = _config(profile)
        for agent, expected in (("entity", {"thinking_level": "medium"}), ("api", None)):
            cfg = config.agent_config(agent)
            calls.clear()
            client.chat(model=cfg["model"], messages=[{"role": "user", "content": "q"}],
                        thinking_budget=cfg["thinking_budget"])
            sent = calls[0][1]
            if expected is None:
                assert "thinking_config" not in sent, (profile, agent)
            else:
                assert sent["thinking_config"] == expected, (profile, agent)
