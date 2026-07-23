"""F §12.2: assistant_reply Optional (absent/None on non-answer turns, never ""
when present); answered-entry filtering; writer outputs satisfy the validator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from chat_nextseek import chat_memory as cm


def _entry(**kw):
    base = {"turn_id": 1, "ts": "2026-07-22T00:00:00+00:00", "mode": "search",
            "user_query": "q", "assistant_reply": "a"}
    base.update(kw)
    return base


def test_validate_accepts_absent_assistant_reply():
    e = _entry(); del e["assistant_reply"]
    cm.validate_chat_log_entry(e)  # must not raise


def test_validate_accepts_none_assistant_reply():
    cm.validate_chat_log_entry(_entry(assistant_reply=None))


def test_validate_rejects_empty_string_reply():
    with pytest.raises(cm.ChatLogEntryError):
        cm.validate_chat_log_entry(_entry(assistant_reply=""))


def test_validate_rejects_non_str_reply():
    with pytest.raises(cm.ChatLogEntryError):
        cm.validate_chat_log_entry(_entry(assistant_reply=42))


def test_validate_still_requires_core_fields():
    e = _entry(); del e["user_query"]
    with pytest.raises(cm.ChatLogEntryError):
        cm.validate_chat_log_entry(e)


def test_validate_accepts_new_optional_fields():
    cm.validate_chat_log_entry(_entry(
        assistant_reply=None, router_choice="unrelated", status="completed", error=None))
    cm.validate_chat_log_entry(_entry(
        assistant_reply=None, router_choice="container_cc", status="error", error="boom"))


def test_append_turn_records_router_choice_and_status():
    session = {}
    cm.append_turn(session, user_query="q", mode="search", assistant_reply="a")
    e = session[cm.CHAT_LOG_KEY][0]
    assert e["router_choice"] == "nextseek_query"
    assert e["status"] == "completed"


def test_append_turn_error_status_passthrough():
    session = {}
    cm.append_turn(session, user_query="q", mode="error_parser",
                   assistant_reply="fatal reply", status="error", error="LLM fatal")
    e = session[cm.CHAT_LOG_KEY][0]
    assert e["status"] == "error" and e["error"] == "LLM fatal"


def test_append_turn_actual_output_passes_validator():
    """G-12b: pipe the REAL writer's stored entries through the validator."""
    session = {}
    cm.append_turn(session, user_query="q", mode="search", assistant_reply="real answer")
    cm.validate_chat_log_entry(session[cm.CHAT_LOG_KEY][0])
    session2 = {}
    cm.append_turn(session2, user_query="q", mode="error_parser",
                   assistant_reply="fatal reply", status="error", error="msg")
    cm.validate_chat_log_entry(session2[cm.CHAT_LOG_KEY][0])


def test_append_turn_empty_reply_is_legacy_known_shape():
    """append_turn coerces None reply to "" (legacy). The validator rejects ""
    — read-side compensated (router_context maps ""→None)."""
    session = {}
    cm.append_turn(session, user_query="q", mode="search", assistant_reply=None)
    assert session[cm.CHAT_LOG_KEY][0]["assistant_reply"] == ""


def test_recent_turns_filters_unanswered():
    session = {cm.CHAT_LOG_KEY: [
        _entry(turn_id=1),
        _entry(turn_id=2, assistant_reply=None, router_choice="unrelated"),
        _entry(turn_id=3),
    ]}
    got = cm.recent_turns(session, n=5)
    assert [t["turn_id"] for t in got] == [1, 3]


def test_recent_turns_treats_empty_string_as_unanswered():
    session = {cm.CHAT_LOG_KEY: [_entry(turn_id=1, assistant_reply="")]}
    assert cm.recent_turns(session, n=5) == []
