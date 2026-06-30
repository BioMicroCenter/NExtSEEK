"""Hermetic tests for the 1c memory config knobs. No Django, no Docker."""
import importlib

from nextseek_api.cc_assistant.cc_config import CCMemoryConfig


def test_defaults():
    cfg = CCMemoryConfig.from_env(source={})
    assert cfg.window_size == 10
    assert cfg.max_items == 8
    assert cfg.truncate_chars == 500
    assert cfg.sweep_idle_seconds == 900
    assert cfg.summary_model == "gemini-flash"


def test_env_overrides():
    cfg = CCMemoryConfig.from_env(source={
        "DMAC_CC_MEMORY_WINDOW": "5",
        "DMAC_CC_MEMORY_MAX_ITEMS": "3",
        "DMAC_CC_MEMORY_TRUNCATE_CHARS": "200",
        "DMAC_CC_MEMORY_SWEEP_IDLE_SECONDS": "60",
        "DMAC_CC_MEMORY_SUMMARY_MODEL": "gemini-x",
    })
    assert (cfg.window_size, cfg.max_items, cfg.truncate_chars,
            cfg.sweep_idle_seconds, cfg.summary_model) == (5, 3, 200, 60, "gemini-x")


def test_bad_int_env_falls_back_to_default():
    cfg = CCMemoryConfig.from_env(source={"DMAC_CC_MEMORY_WINDOW": "not-an-int"})
    assert cfg.window_size == 10


def test_container_path_constants():
    eng = importlib.import_module("nextseek_api.cc_assistant.cc_engine")
    assert eng._CONTAINER_USER_MEMORY == "/home/user/.claude/CLAUDE.md"
    assert eng._CONTAINER_MEMORY_TRANSCRIPTS == "/home/user/.cc-memory/transcripts"
