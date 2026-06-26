"""Unit tests for the Claude stream-json -> {event,data} translator.

Pure logic; no Django/docker/dmac needed. Run standalone:
    uv run --with pytest python -m pytest nextseek_api/cc_assistant/tests/test_translate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file in isolation (no Django settings / app loading).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translate import CCStreamTranslator  # noqa: E402


def _events(translator, payloads):
    out = []
    for p in payloads:
        out.extend(translator.handle(p))
    return out


def test_system_init_emits_agent_started():
    t = CCStreamTranslator()
    frames = t.handle({"type": "system", "subtype": "init",
                       "session_id": "abc", "model": "claude-x"})
    assert frames == [("agent_started", {"agent": "container_cc", "model": "claude-x"})]
    assert t.session_id == "abc"


def test_assistant_text_is_accumulated_not_emitted():
    t = CCStreamTranslator()
    frames = t.handle({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": "Hello"}]}})
    assert frames == []
    assert t.accumulated_reply == "Hello"


def test_tool_use_emits_search_started_and_result_closes_it():
    t = CCStreamTranslator()
    started = t.handle({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}, "id": "tu_1"},
    ]}})
    assert started == [("search_started", {"source": "Bash"})]
    done = t.handle({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
    ]}})
    assert done == [("search_complete", {"source": "Bash"})]


def test_result_success_emits_query_complete_with_result_text():
    t = CCStreamTranslator()
    t.handle({"type": "system", "subtype": "init", "session_id": "s1"})
    t.handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}})
    frames = t.handle({"type": "result", "subtype": "success",
                       "result": "Final answer.", "session_id": "s1", "is_error": False})
    assert frames == [("query_complete", {"reply": "Final answer.", "bundle_id": None,
                                          "session_id": "s1", "total_cost_usd": None})]


def test_result_surfaces_total_cost_usd():
    t = CCStreamTranslator()
    frames = t.handle({"type": "result", "subtype": "success", "result": "ok",
                       "is_error": False, "total_cost_usd": 0.1234})
    assert frames[0][1]["total_cost_usd"] == 0.1234


def test_result_success_without_result_field_falls_back_to_accumulated():
    t = CCStreamTranslator()
    t.handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "Body text"}]}})
    frames = t.handle({"type": "result", "subtype": "success", "is_error": False})
    assert frames[0][0] == "query_complete"
    assert frames[0][1]["reply"] == "Body text"


def test_result_error_emits_query_error():
    t = CCStreamTranslator()
    frames = t.handle({"type": "result", "subtype": "error_during_execution",
                       "is_error": True, "result": "boom"})
    assert frames[0][0] == "query_error"
    assert frames[0][1]["error"] == "boom"
    assert frames[0][1]["agent"] == "container_cc"


def test_finalize_emits_terminal_when_no_result_arrived():
    t = CCStreamTranslator()
    t.handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "Only text"}]}})
    frames = t.finalize()
    assert frames[0][0] == "query_complete"
    assert frames[0][1]["reply"] == "Only text"
    # idempotent: a second finalize emits nothing
    assert t.finalize() == []


def test_finalize_noop_after_result():
    t = CCStreamTranslator()
    t.handle({"type": "result", "subtype": "success", "result": "done"})
    assert t.finalize() == []
