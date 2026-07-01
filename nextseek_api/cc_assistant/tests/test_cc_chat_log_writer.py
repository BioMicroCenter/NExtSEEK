"""Hermetic: pure chat_log serialize + FIFO-cap helpers. No Django, no DB."""
from pathlib import Path

from nextseek_api.cc_assistant.cc_turn_complete import append_capped


def test_append_capped_keeps_newest_in_order():
    log: list = []
    for i in range(60):
        log = append_capped(log, {"i": i}, cap=50)
    assert len(log) == 50
    assert [e["i"] for e in log] == list(range(10, 60))


def test_append_capped_under_cap_keeps_all_in_order():
    log: list = []
    for i in range(5):
        log = append_capped(log, {"i": i}, cap=50)
    assert [e["i"] for e in log] == [0, 1, 2, 3, 4]


def test_apply_turn_writes_chat_log_and_cc_traces_mirror():
    """Locked SPEC-3 E5 / §6.5: the per-turn trace is written to BOTH chat_log[]
    (reload source of truth) AND the es["cc_traces"] mirror in ONE RMW transform."""
    from nextseek_api.cc_assistant.cc_turn_complete import (
        TurnCompletePayload, apply_turn_to_extra_state)
    trace = {"cc_session_id": "s", "ts": "t", "steps": []}
    payload = TurnCompletePayload(
        chat_session=None, user_query="q", assistant_reply="a", ts="t",
        artifacts=None, cc_traces=[trace], turn_id="T1",
        cc_session_id="s", raw_jsonl=b"")
    es = apply_turn_to_extra_state({}, payload, cap=50)
    assert es["chat_log"][-1]["cc_traces"] == [trace]
    assert es["cc_traces"] == [trace]


def test_serialize_cc_chat_log_entry_keys():
    from nextseek_api.cc_assistant.cc_turn_complete import (
        TurnCompletePayload, serialize_cc_chat_log_entry)
    payload = TurnCompletePayload(
        chat_session=None, user_query="q", assistant_reply="a", ts="t",
        artifacts=[{"key": "k"}], cc_traces=[{"steps": []}], turn_id="T1",
        cc_session_id="s", raw_jsonl=b"")
    entry = serialize_cc_chat_log_entry(payload)
    assert entry["mode"] == "cc"
    assert entry["assistant_reply"] == "a"
    assert entry["user_query"] == "q"
    assert entry["ts"] == "t"
    assert entry["artifacts"] == [{"key": "k"}]
    assert entry["cc_traces"] == [{"steps": []}]
    assert entry["turn_id"] == "T1"


def test_chat_log_entries_use_assistant_reply_key():
    """MUTATION-SENSITIVE: CC chat_log entries must use assistant_reply, not reply."""
    src = (Path(__file__).resolve().parents[1] / "cc_turn_complete.py").read_text()
    assert '"assistant_reply": payload.assistant_reply' in src
    assert '"reply":' not in src.split("serialize_cc_chat_log_entry")[1].split("def apply_turn")[0]
