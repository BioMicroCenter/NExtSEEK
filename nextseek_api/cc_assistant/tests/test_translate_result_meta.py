"""_handle_result surfaces num_turns/duration_ms on query_complete. Hermetic."""
from nextseek_api.cc_assistant.translate import CCStreamTranslator


def _translator():
    # construct minimally; _handle_result only reads the payload + self.session_id
    t = CCStreamTranslator.__new__(CCStreamTranslator)
    t.session_id = "sess-1"
    t._terminated = False
    return t


def test_result_surfaces_num_turns_and_duration():
    t = _translator()
    frames = t._handle_result({
        "subtype": "success", "result": "done",
        "total_cost_usd": 0.07, "num_turns": 5, "duration_ms": 1234,
        "session_id": "sess-1",
    })
    (evt, data), = frames
    assert evt == "query_complete"
    assert data["num_turns"] == 5
    assert data["duration_ms"] == 1234
    assert data["total_cost_usd"] == 0.07


def test_missing_meta_is_none_not_crash():
    t = _translator()
    frames = t._handle_result({"subtype": "success", "result": "ok", "session_id": "s"})
    (evt, data), = frames
    assert evt == "query_complete"
    assert data["num_turns"] is None and data["duration_ms"] is None
