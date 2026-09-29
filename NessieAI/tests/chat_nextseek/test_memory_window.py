"""
The parser's two memories of the conversation look back equally far.

The parser is handed the chat log (``chat_memory.history_block``, the last answered
turns) and the recent-results summary (``build_recent_results_summary``, the last result
bundles). The first showed 5 turns and the second 8 bundles, so a result the summary
listed could come from a turn the chat log no longer showed, and the parser prompt's
advice to look a bundle up by the ``bundle_id`` in CHAT_HISTORY could never reach past
the summary. One constant, ``chat_memory.MEMORY_WINDOW``, now sets both defaults.
"""
from __future__ import annotations

from chat_nextseek import chat_memory
from chat_nextseek.helpers.tools.nextseek_api import build_recent_results_summary


def _session(n):
    return {
        "chat_log": [
            {"turn_id": i, "ts": "2026-01-01T00:00:00+00:00", "mode": "new_search",
             "user_query": f"question {i}", "assistant_reply": f"answer {i}", "bundle_id": i}
            for i in range(1, n + 1)
        ],
        "results_history": [
            {"id": i, "mode": "new_search", "user_query": f"question {i}", "endpoint": "/e/",
             "api_result_slim": {"data": {"total": i}}}
            for i in range(1, n + 1)
        ],
    }


def test_the_chat_log_and_the_results_summary_show_the_same_number_of_recent_turns():
    session = _session(12)

    turns = [line for line in chat_memory.history_block(session).splitlines() if line.startswith("- turn ")]
    bundles = [line for line in build_recent_results_summary(session).splitlines() if line.startswith("- id=")]

    assert len(turns) == len(bundles) == chat_memory.MEMORY_WINDOW


def test_both_windows_end_at_the_same_turn():
    session = _session(12)

    turns = [line for line in chat_memory.history_block(session).splitlines() if line.startswith("- turn ")]
    bundles = [line for line in build_recent_results_summary(session).splitlines() if line.startswith("- id=")]

    oldest_turn = int(turns[0].split()[2])
    oldest_bundle = int(bundles[-1].split("id=")[1].split(",")[0])
    assert oldest_turn == oldest_bundle == 12 - chat_memory.MEMORY_WINDOW + 1
