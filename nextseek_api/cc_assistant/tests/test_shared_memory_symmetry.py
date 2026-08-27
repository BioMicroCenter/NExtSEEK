from chat_nextseek.chat_memory import REPLY_PREVIEW_CHARS, format_for_prompt

from nextseek_api.cc_assistant.ns_turn_context import build_contexts
from nextseek_api.cc_assistant.cc_turn_context import build_cc_contexts
from nextseek_api.cc_assistant.ns_digest import render_within_chat_digest
from nextseek_api.cc_assistant.cc_turn_complete import serialize_cc_chat_log_entry, TurnCompletePayload


# Mixed conversation: turn 1 NS (has a bundle), turn 2 CC (answered).
CHAT_LOG = [
    {"turn_id": 1, "mode": "nextseek_query", "user_query": "find NHP sequencing",
     "status": "completed", "bundle_id": 10},
    {"turn_id": 2, "mode": "cc", "user_query": "count sex of those",
     "assistant_reply": "3 male, 2 female", "status": "completed"},
]
RESULTS_HISTORY = [{"id": 10, "user_query": "find NHP sequencing", "endpoint": "/advanced_search",
                    "method": "POST", "api_result_full": {"ok": True, "data": {"total": 139,
                    "rows": [{"uid": "D.SEQ-1"}]}}, "terminal_reply": "139 records"}]


def test_cc_agent_digest_contains_both_ns_and_cc_prior_turns():
    md = render_within_chat_digest(
        build_contexts(CHAT_LOG, RESULTS_HISTORY, session_id="s"),
        build_cc_contexts(CHAT_LOG))
    assert "turn 1 (bundle 10): find NHP sequencing" in md   # prior NS turn visible
    assert "nextseek-recall --turn 1" in md                   # with its recall affordance
    assert "turn 2 (CC): count sex of those" in md            # prior CC turn NOW visible (the fix)
    assert "3 male, 2 female" in md


def test_cc_turn_writeback_carries_query_and_reply():
    payload = TurnCompletePayload(
        chat_session=None, user_query="count sex of those", assistant_reply="3 male, 2 female",
        ts="t", artifacts=None, cc_traces=[], turn_id="run-uuid", cc_session_id="s", raw_jsonl=b"")
    entry = serialize_cc_chat_log_entry(payload, turn_id=2)
    assert entry["user_query"] == "count sex of those"
    assert entry["assistant_reply"] == "3 male, 2 female"
    assert entry["mode"] == "cc" and entry["status"] == "completed"


# --------------------------------------------------------------------------- #
# The other half of the symmetry: a CC reply must be visible to the next NS turn.
#
# The CC writer emitted `assistant_reply`, but chat_memory.format_for_prompt renders
# `assistant_reply_preview`, which only the NS writer set (chat_memory.py:235). So
# turn 796's parser saw exactly:
#
#     - turn 1 (cc)
#       user: Find samples from a 4 week study.
#
# and nothing else — CC's correct answer was discarded.
# --------------------------------------------------------------------------- #


def _cc_entry(reply: str, *, turn_id: int = 1) -> dict:
    payload = TurnCompletePayload(
        chat_session=None, user_query="Find samples from a 4 week study.",
        assistant_reply=reply, ts="t", artifacts=None, cc_traces=[],
        turn_id="run-uuid", cc_session_id="s", raw_jsonl=b"")
    return serialize_cc_chat_log_entry(payload, turn_id=turn_id)


def test_a_cc_reply_is_visible_to_the_next_nextseek_turn():
    entry = _cc_entry("I found 6 samples: D.SEQ-220823SHA-1 through -6.")

    rendered = format_for_prompt([entry])

    assert "user: Find samples from a 4 week study." in rendered
    assert "assistant: I found 6 samples" in rendered, (
        "the CC reply never reached the NS prompt — format_for_prompt reads "
        "assistant_reply_preview, not assistant_reply"
    )


def test_the_cc_preview_uses_the_same_helpers_as_the_ns_writer():
    entry = _cc_entry("x" * (REPLY_PREVIEW_CHARS + 200))

    preview = entry["assistant_reply_preview"]
    assert len(preview) <= REPLY_PREVIEW_CHARS + 1, "preview must be truncated like the NS side"
    assert entry["assistant_reply"] == "x" * (REPLY_PREVIEW_CHARS + 200), "full reply is preserved"


def test_the_cc_preview_strips_the_debug_block():
    """`**Debug info**` is _strip_debug_block's real marker, same as the NS writer."""
    entry = _cc_entry("The answer is 408.\n\n**Debug info**\nrouter_choice: container_cc")

    assert "408" in entry["assistant_reply_preview"]
    assert "router_choice" not in entry["assistant_reply_preview"]
    assert "router_choice" in entry["assistant_reply"], "the full reply is not truncated"


def test_an_empty_cc_reply_does_not_crash_the_preview():
    payload = TurnCompletePayload(
        chat_session=None, user_query="q", assistant_reply=None, ts="t", artifacts=None,
        cc_traces=[], turn_id="run-uuid", cc_session_id="s", raw_jsonl=b"")

    assert serialize_cc_chat_log_entry(payload, turn_id=1)["assistant_reply_preview"] == ""
