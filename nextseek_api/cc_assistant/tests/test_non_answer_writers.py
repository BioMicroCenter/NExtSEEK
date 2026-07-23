"""F §12.3: non-answer entries, terminal tracker (AR-1-safe: no closure shadow),
first-error-wins, exactly-once predicate for all site classes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "chat_nextseek" / "src"))

import cc_turn_complete as ctc
from chat_nextseek.chat_memory import validate_chat_log_entry


def test_non_answer_entry_shape_unrelated():
    e = ctc.serialize_non_answer_entry(
        user_query="who won the game?", router_choice="unrelated",
        status="completed", error=None, ts="2026-07-22T00:00:00+00:00", turn_id=3)
    validate_chat_log_entry(e)
    assert e["router_choice"] == "unrelated" and e["status"] == "completed"
    assert "assistant_reply" not in e
    assert "error" not in e


def test_non_answer_entry_shape_error():
    e = ctc.serialize_non_answer_entry(
        user_query="q", router_choice="container_cc", status="error",
        error="Container-CC turn exceeded the 180s limit and was stopped.",
        ts="t", turn_id=1)
    validate_chat_log_entry(e)
    assert e["status"] == "error" and e["error"].startswith("Container-CC")


def test_non_answer_entry_error_never_empty_string():
    e = ctc.serialize_non_answer_entry(
        user_query="q", router_choice=None, status="error", error="",
        ts="t", turn_id=1)
    assert "error" not in e


def test_apply_non_answer_appends_with_sequential_turn_id():
    es = {"chat_log": [{"turn_id": 7, "ts": "t", "mode": "search",
                        "user_query": "q", "assistant_reply": "a"}]}
    out = ctc.apply_non_answer_to_extra_state(
        es, user_query="q2", router_choice="unrelated", status="completed",
        error=None, ts="t2")
    assert out["chat_log"][-1]["turn_id"] == 8
    assert es["chat_log"][-1]["turn_id"] == 7


def test_cc_success_entry_self_describes():
    p = ctc.TurnCompletePayload(
        chat_session=None, user_query="q", assistant_reply="a", ts="t",
        artifacts=None, cc_traces=[], turn_id="uuid", cc_session_id=None,
        raw_jsonl=b"")
    e = ctc.serialize_cc_chat_log_entry(p, turn_id=1)
    assert e["router_choice"] == "container_cc" and e["status"] == "completed"


def test_wrap_send_event_passthrough_and_tracking():
    seen = []
    tracker = ctc.new_terminal_tracker()
    wrapped = ctc.wrap_send_event(lambda et, d: seen.append((et, d)), tracker)
    wrapped("route_decided", {"route": "container_cc"})
    wrapped("query_complete", {"reply": "hi"})
    assert seen == [("route_decided", {"route": "container_cc"}),
                    ("query_complete", {"reply": "hi"})]
    assert tracker["completed"] is True and tracker["error"] is None


def test_wrap_send_event_first_error_wins():
    tracker = ctc.new_terminal_tracker()
    wrapped = ctc.wrap_send_event(lambda et, d: None, tracker)
    wrapped("query_error", {"error": "graph agent exploded on clause X"})
    wrapped("query_error", {"error": "Internal pipeline error"})
    assert tracker["error"] == "graph agent exploded on clause X"


def test_wrap_send_event_blank_error_coerced():
    tracker = ctc.new_terminal_tracker()
    ctc.wrap_send_event(lambda et, d: None, tracker)("query_error", {"error": ""})
    assert tracker["error"] == "unknown error"


def test_should_append_non_answer_site_classes():
    T = ctc.new_terminal_tracker
    t = T(); t["error"] = "boom"
    assert ctc.should_append_non_answer(t, unrelated=False) is True
    t = T(); t["error"] = "fatal"; t["completed"] = True
    assert ctc.should_append_non_answer(t, unrelated=False) is False
    t = T(); t["completed"] = True
    assert ctc.should_append_non_answer(t, unrelated=False) is False
    t = T(); t["error"] = "x"
    assert ctc.should_append_non_answer(t, unrelated=True) is False
    assert ctc.should_append_non_answer(T(), unrelated=False) is False
