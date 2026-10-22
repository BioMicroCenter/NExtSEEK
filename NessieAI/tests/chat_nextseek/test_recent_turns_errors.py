"""A crashed turn is visible to the next turn: its question plus a fixed marker, never exception text."""
from chat_nextseek.chat_memory import format_for_prompt, history_block, recent_turns


def _log(*entries):
    return {"chat_log": list(entries)}


ANSWERED = {"turn_id": 1, "mode": "x", "user_query": "q1", "assistant_reply": "a1"}
ERRORED = {"turn_id": 2, "mode": "x", "user_query": "q2", "status": "error", "error": "ValueError",
           "intent_summary": "Unhandled error in the pipeline."}


def test_errored_turn_is_kept_with_a_marker():
    turns = recent_turns(_log(ANSWERED, ERRORED))
    assert [t["turn_id"] for t in turns] == [1, 2]
    assert "ValueError" in turns[1]["assistant_reply_preview"]
    assert "no result was produced" in turns[1]["assistant_reply_preview"]
    assert "assistant_reply" not in turns[1]


def test_history_block_shows_the_failed_question():
    block = history_block(_log(ANSWERED, ERRORED))
    assert "q2" in block and "failed with an internal error (ValueError)" in block


def test_error_text_beyond_the_type_name_is_not_shown():
    e = dict(ERRORED, error="Container-CC route is not available: secret detail here")
    block = format_for_prompt(recent_turns(_log(e)))
    assert "secret detail" not in block and "failed with an internal error" in block


def test_reply_less_ok_turn_still_dropped():
    assert recent_turns(_log({"turn_id": 3, "mode": "x", "user_query": "q"})) == []


def test_input_log_is_not_mutated():
    s = _log(ERRORED)
    recent_turns(s)
    assert "assistant_reply_preview" not in s["chat_log"][0]


def test_real_crash_writer_entry_is_recognised():
    """Runs the orchestrator's own crash writer, not a hand-built entry."""
    from chat_nextseek import orchestrator
    session, events = {}, []
    orchestrator._report_unhandled(session, "how many mice", lambda n, p: events.append(n),
                                   KeyError("secret"), agent="x", mode_label="nextseek_query")
    turns = recent_turns(session)
    assert [t["user_query"] for t in turns] == ["how many mice"]
    assert "(KeyError)" in turns[0]["assistant_reply_preview"] and "secret" not in str(turns)
