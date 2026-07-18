"""Turn-id derivation + chat_log entry schema (shared by NS + CC writers).

Regression guard for the poison-pill bug: a CC-written chat_log entry carries a
str (UUID) turn_id; the old `log[-1]["turn_id"] + 1` read then crashed with
`TypeError: can only concatenate str (not "int") to str`, poisoning every
subsequent NS turn in the session. The shared `next_turn_id` helper derives the
next id as max-over-int-coercible + 1, which is inherently type-tolerant.
"""
from __future__ import annotations

import pytest

from chat_nextseek.chat_memory import (
    ChatLogEntryError,
    append_turn,
    next_turn_id,
    validate_chat_log_entry,
    MAX_TURNS,
)


# --- next_turn_id -----------------------------------------------------------

def test_next_turn_id_empty_log_is_one():
    assert next_turn_id([]) == 1
    assert next_turn_id(None) == 1


def test_next_turn_id_pure_int_log():
    assert next_turn_id([{"turn_id": 1}, {"turn_id": 2}, {"turn_id": 3}]) == 4


def test_next_turn_id_skips_str_uuid_turn_id():
    """The crashing case: a CC entry with a str UUID turn_id must not raise;
    the next id comes from the max int-coercible id + 1."""
    log = [
        {"turn_id": 1, "mode": "new_search"},
        {"turn_id": 2, "mode": "new_search"},
        {"turn_id": "0a1b2c3d-4e5f-6789-abcd-ef0123456789", "mode": "cc"},
    ]
    assert next_turn_id(log) == 3


def test_next_turn_id_all_str_turn_ids_defaults_to_one():
    log = [{"turn_id": "abc"}, {"turn_id": "def"}]
    assert next_turn_id(log) == 1


def test_next_turn_id_coerces_numeric_strings():
    assert next_turn_id([{"turn_id": "7"}]) == 8


def test_next_turn_id_ignores_bool_turn_id():
    # bool is an int subclass; must not be treated as a turn number.
    assert next_turn_id([{"turn_id": True}]) == 1


def test_next_turn_id_uses_max_not_last():
    # After FIFO eviction the tail may not hold the largest id.
    log = [{"turn_id": 40}, {"turn_id": 41}, {"turn_id": 5}]
    assert next_turn_id(log) == 42


# --- append_turn read-site (the poison-pill repro) --------------------------

def test_ns_append_after_cc_entry_succeeds_with_next_int_id():
    """NS turn following a CC-written str-turn_id entry must append cleanly."""
    session = {
        "chat_log": [
            {"turn_id": 3, "mode": "new_search"},
            {"turn_id": "0a1b2c3d-4e5f-6789-abcd-ef0123456789", "mode": "cc"},
        ]
    }
    append_turn(session, user_query="follow up", mode="new_search")
    log = session["chat_log"]
    assert len(log) == 3
    assert log[-1]["turn_id"] == 4  # max int-coercible (3) + 1


def test_append_turn_after_fifo_eviction_no_collision():
    """After eviction ids continue from the max present, never len(log)+1."""
    session = {"chat_log": [{"turn_id": i} for i in range(1, MAX_TURNS + 1)]}
    append_turn(session, user_query="q51", mode="new_search")
    log = session["chat_log"]
    assert len(log) == MAX_TURNS  # capped
    assert log[-1]["turn_id"] == MAX_TURNS + 1  # 51, not len(log)+1 == 51 by luck
    # Prove the derivation is max-based, not len-based: evict again with a gap.
    append_turn(session, user_query="q52", mode="new_search")
    assert session["chat_log"][-1]["turn_id"] == MAX_TURNS + 2


# --- validate_chat_log_entry (shared schema) --------------------------------

def test_validate_accepts_ns_writer_output():
    session = {"chat_log": []}
    append_turn(session, user_query="q", mode="new_search")
    validate_chat_log_entry(session["chat_log"][-1])  # must not raise


def test_validate_rejects_str_turn_id():
    entry = {
        "turn_id": "0a1b2c3d-4e5f-6789-abcd-ef0123456789",
        "ts": "t", "mode": "cc", "user_query": "q", "assistant_reply": "a",
    }
    with pytest.raises(ChatLogEntryError):
        validate_chat_log_entry(entry)


def test_validate_rejects_missing_required_field():
    entry = {"turn_id": 1, "ts": "t", "mode": "cc", "user_query": "q"}
    with pytest.raises(ChatLogEntryError):
        validate_chat_log_entry(entry)


def test_validate_rejects_bool_turn_id():
    entry = {
        "turn_id": True, "ts": "t", "mode": "cc",
        "user_query": "q", "assistant_reply": "a",
    }
    with pytest.raises(ChatLogEntryError):
        validate_chat_log_entry(entry)
