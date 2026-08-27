"""Hermetic: CC chat_log entry gets a sequential int turn_id + cc_run_id.

The CC writer used to serialize `turn_id=str(run_id)` (a Celery UUID) straight
into the chat_log entry. That poisoned the NS read site (`str + int` TypeError).
Now the chat_log ENTRY carries a sequential int turn_id (shared derivation) and
the run UUID moves to a distinct `cc_run_id` field. The wire/transcript/artifact
uses of `str(run_id)` are unchanged (pinned by test_cc_load_bearing_run_id.py).
"""
from chat_nextseek.chat_memory import validate_chat_log_entry

from nextseek_api.cc_assistant.cc_turn_complete import (
    TurnCompletePayload,
    apply_turn_to_extra_state,
    serialize_cc_chat_log_entry,
)

_UUID = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


def _payload(**kw):
    base = dict(
        chat_session=None, user_query="q", assistant_reply="a", ts="t",
        artifacts=None, cc_traces=[], turn_id=_UUID, cc_session_id="s",
        raw_jsonl=b"")
    base.update(kw)
    return TurnCompletePayload(**base)


def test_cc_entry_has_int_turn_id_and_cc_run_id():
    entry = serialize_cc_chat_log_entry(_payload(), turn_id=4)
    assert entry["turn_id"] == 4
    assert isinstance(entry["turn_id"], int)
    assert entry["cc_run_id"] == _UUID


def test_cc_entry_validates_against_shared_schema():
    entry = serialize_cc_chat_log_entry(_payload(), turn_id=1)
    validate_chat_log_entry(entry)  # must not raise


def test_apply_turn_derives_sequential_int_after_ns_entries():
    """A CC turn following two NS turns (int ids 1,2) gets turn_id 3, not the UUID."""
    es = apply_turn_to_extra_state(
        {"chat_log": [{"turn_id": 1, "mode": "new_search"},
                      {"turn_id": 2, "mode": "new_search"}]},
        _payload())
    entry = es["chat_log"][-1]
    assert entry["turn_id"] == 3
    assert entry["cc_run_id"] == _UUID
    validate_chat_log_entry(entry)


def test_apply_turn_first_cc_turn_is_one():
    es = apply_turn_to_extra_state({}, _payload())
    assert es["chat_log"][-1]["turn_id"] == 1
    assert es["chat_log"][-1]["cc_run_id"] == _UUID


def test_two_cc_turns_get_distinct_sequential_ids():
    es = apply_turn_to_extra_state({}, _payload(turn_id="uuid-1"))
    es = apply_turn_to_extra_state(es, _payload(turn_id="uuid-2"))
    ids = [e["turn_id"] for e in es["chat_log"]]
    assert ids == [1, 2]
    assert [e["cc_run_id"] for e in es["chat_log"]] == ["uuid-1", "uuid-2"]
