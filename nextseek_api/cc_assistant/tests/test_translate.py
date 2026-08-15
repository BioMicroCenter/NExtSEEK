"""Unit tests for the Claude stream-json -> {event,data} translator.

Pure logic; no Django/docker/dmac needed. Run standalone:
    uv run --with pytest python -m pytest nextseek_api/cc_assistant/tests/test_translate.py
"""
from __future__ import annotations

from nextseek_api.cc_assistant.translate import CCStreamTranslator


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
                                          "cc_session_id": "s1", "total_cost_usd": None,
                                          "num_turns": None, "duration_ms": None})]


def test_terminal_frames_omit_session_id_so_callback_fills_nextseek_id():
    """The claude in-container session UUID must NOT occupy ``session_id``.

    ``make_db_event_callback`` fills ``session_id`` with the NExtSEEK ChatSession
    id via ``setdefault`` — which is a no-op if the key is already present. So the
    translator must leave ``session_id`` absent on terminal frames and surface the
    claude session under ``cc_session_id`` (kept for later ``--resume`` use). This
    is the fix for the multi-turn 404 (the frontend promoted the new chat's active
    session from the leaked container UUID).
    """
    # success path
    t = CCStreamTranslator()
    t.handle({"type": "system", "subtype": "init", "session_id": "cc-uuid"})
    (event, data), = t.handle({"type": "result", "subtype": "success",
                               "result": "ok", "is_error": False})
    assert event == "query_complete"
    assert "session_id" not in data            # setdefault must win downstream
    assert data["cc_session_id"] == "cc-uuid"  # preserved for resume

    # error path
    t2 = CCStreamTranslator()
    t2.handle({"type": "system", "subtype": "init", "session_id": "cc-uuid"})
    (e_event, e_data), = t2.handle({"type": "result", "subtype": "error_during_execution",
                                    "is_error": True, "result": "boom"})
    assert e_event == "query_error"
    assert "session_id" not in e_data
    assert e_data["cc_session_id"] == "cc-uuid"

    # finalize safety-net path
    t3 = CCStreamTranslator()
    t3.handle({"type": "system", "subtype": "init", "session_id": "cc-uuid"})
    (_f_event, f_data), = t3.finalize()
    assert "session_id" not in f_data
    assert f_data["cc_session_id"] == "cc-uuid"


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


# --- #4 event-stepper trace: tool detail, narration/thinking, error status ----

def test_tool_use_carries_detail_for_bash_and_read():
    t = CCStreamTranslator()
    (bash,) = t.handle({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "nextseek-run-ls --run-dir /x"}, "id": "a"}]}})
    assert bash == ("search_started", {"source": "Bash", "detail": "nextseek-run-ls --run-dir /x"})
    (read,) = t.handle({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/app/SKILL.md"}, "id": "b"}]}})
    assert read == ("search_started", {"source": "Read", "detail": "/app/SKILL.md"})


def test_narration_text_alongside_tool_becomes_thinking_not_reply():
    t = CCStreamTranslator()
    frames = t.handle({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Let me list the run directory."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}, "id": "a"}]}})
    assert ("search_started", {"source": "thinking", "detail": "Let me list the run directory."}) in frames
    assert ("search_complete", {"source": "thinking"}) in frames
    assert ("search_started", {"source": "Bash", "detail": "ls"}) in frames
    assert "Let me list" not in t.accumulated_reply  # narration is not the answer


def test_thinking_block_becomes_thinking_step():
    t = CCStreamTranslator()
    frames = t.handle({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "The user wants X."}]}})
    assert frames == [("search_started", {"source": "thinking", "detail": "The user wants X."}),
                      ("search_complete", {"source": "thinking"})]


def test_tool_result_error_marks_not_ok():
    t = CCStreamTranslator()
    t.handle({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "boom"}, "id": "a"}]}})
    done = t.handle({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "err", "is_error": True}]}})
    assert done == [("search_complete", {"source": "Bash", "ok": False})]


def test_handle_ignores_non_dict_and_unknown_type():
    from nextseek_api.cc_assistant.translate import _format_tool_detail
    t = CCStreamTranslator()
    assert t.handle("nope") == []
    assert t.handle({"type": "other"}) == []
    assert t.handle({"type": "system", "subtype": "other"}) == []
    frames = t.handle({"type": "assistant", "message": {"content": ["skip", {"type": "text"}]}})
    assert frames == []
    assert _format_tool_detail("bash", "not-a-dict") == ""
    assert _format_tool_detail("bash", {"command": "ls"}) == "ls"
    assert _format_tool_detail("mystery", {"foo": "bar"}) == "bar"
    assert _format_tool_detail("mystery", {"foo": ""}) == ""
    t.handle({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "narrating"},
        {"type": "tool_use", "name": "Bash", "id": "z", "input": {"command": "ls"}},
    ]}})
