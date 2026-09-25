"""The translator records which model answered a Container-CC turn, and what fell back.

Claude Code 2.1.282 reports a ``--fallback-model`` switch as a ``system/model_fallback``
stdout frame, retries as ``system/api_retry``, and other notices as
``system/informational``; the result frame carries ``modelUsage`` keyed by model id.
The frames below are the shapes a local run against a fake Bedrock endpoint printed on
2026-09-25 (uuids and session ids shortened).

Turn-record contract: ``query_complete`` carries ``models_used`` and ``model_fallback``
(``[]`` when nothing fell back, else items ``{"agent": "container_cc", "from", "to",
"reason"}``). A turn that ends because the model was unavailable (an ``API Error: 5xx``,
``Repeated 529``, ``Request rejected (429)`` or ``Request timed out``) ends in a
``query_error`` with the operator-approved text, ``reason: "model_unavailable"``, Claude
Code's own text as ``detail``, and ``model_fallback``.
"""
from __future__ import annotations

import pytest

from NessieAI.cc.translate import CCStreamTranslator

MAIN = "us.anthropic.claude-opus-4-8"
FALLBACK = "us.anthropic.claude-opus-4-7"

TRIED_TEXT = ("The AI model was unavailable during this turn (we also tried a second model), "
              "so I could not finish. Please ask again in a few minutes.")
NOT_TRIED_TEXT = ("The AI model was unavailable during this turn, so I could not finish. "
                  "Please ask again in a few minutes.")

INIT = {"type": "system", "subtype": "init", "session_id": "s-1", "model": MAIN}
FALLBACK_503 = {"type": "system", "subtype": "model_fallback", "uuid": "u-1",
                "trigger": "server_error", "original_model": MAIN, "fallback_model": FALLBACK,
                "content": "Switched to Opus 4.7 due to high demand for Opus 4.8",
                "session_id": "s-1"}
FALLBACK_529 = dict(FALLBACK_503, trigger="overloaded")
RETRY_503 = {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 3,
             "retry_delay_ms": 591, "error_status": 503, "error": "server_error",
             "session_id": "s-1", "uuid": "u-2"}
INFORMATIONAL = {"type": "system", "subtype": "informational", "isMeta": False,
                 "content": "A notice about auto mode.", "uuid": "u-3"}
PERMISSION_DENIED = {"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
                     "decision_reason": "Classifier unavailable", "session_id": "s-1"}


def _answer(model: str) -> dict:
    return {"type": "assistant", "message": {"model": model, "content": [
        {"type": "text", "text": f"FAKE_OK from {model}"}]}, "session_id": "s-1"}


def _ok_result(model_usage: dict | None) -> dict:
    frame = {"type": "result", "subtype": "success", "is_error": False,
             "result": "the answer", "session_id": "s-1", "total_cost_usd": 0.000185,
             "num_turns": 1, "duration_ms": 12}
    if model_usage is not None:
        frame["modelUsage"] = model_usage
    return frame


def _synthetic_error(text: str) -> dict:
    return {"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant",
            "content": [{"type": "text", "text": text}]}, "session_id": "s-1",
            "error": "server_error", "is_api_error_message": True}


def _error_result(text: str, status: int | None) -> dict:
    frame = {"type": "result", "subtype": "success", "is_error": True, "result": text,
             "session_id": "s-1", "total_cost_usd": 0, "modelUsage": {},
             "terminal_reason": "api_error", "num_turns": 1}
    if status is not None:
        frame["api_error_status"] = status
    return frame


def _run(frames, model_id=MAIN):
    t = CCStreamTranslator(model_id=model_id)
    out = []
    for frame in frames:
        out.extend(t.handle(frame))
    return t, out


def _terminal(out):
    terminals = [(e, d) for e, d in out if e in ("query_complete", "query_error")]
    assert len(terminals) == 1
    return terminals[0]


# --- the new system frames -------------------------------------------------------

def test_a_fallback_frame_is_recorded_per_the_contract_and_emits_nothing():
    t, out = _run([INIT, FALLBACK_503])
    assert [e for e, _ in out] == ["agent_started"]
    assert t.model_fallback == [
        {"agent": "container_cc", "from": MAIN, "to": FALLBACK, "reason": "server_error"}]


def test_retry_informational_and_denial_frames_touch_neither_events_nor_reply():
    t, out = _run([INIT, RETRY_503, RETRY_503, INFORMATIONAL, PERMISSION_DENIED])
    assert [e for e, _ in out] == ["agent_started"]
    assert t.api_retries == 2
    assert t.accumulated_reply == ""
    assert t.model_fallback == []


# --- a completed turn ------------------------------------------------------------

def test_a_turn_that_fell_back_and_finished_says_so():
    _, out = _run([INIT, FALLBACK_503, _answer(FALLBACK), INFORMATIONAL,
                   _ok_result({FALLBACK: {"inputTokens": 12, "costUSD": 0.000185}})])
    event, data = _terminal(out)
    assert event == "query_complete"
    assert data["models_used"] == [FALLBACK]
    assert data["model_fallback"] == [
        {"agent": "container_cc", "from": MAIN, "to": FALLBACK, "reason": "server_error"}]
    assert data["reply"] == "the answer"


def test_a_529_fallback_carries_its_own_trigger():
    _, out = _run([INIT, RETRY_503, FALLBACK_529, _answer(FALLBACK), _ok_result({FALLBACK: {}})])
    _, data = _terminal(out)
    assert data["model_fallback"][0]["reason"] == "overloaded"


def test_a_plain_turn_uses_the_model_usage_keys_and_records_no_fallback():
    _, out = _run([INIT, _answer(MAIN), _ok_result({MAIN: {}, "us.anthropic.x": {}})])
    _, data = _terminal(out)
    assert data["models_used"] == [MAIN, "us.anthropic.x"]
    assert data["model_fallback"] == []


@pytest.mark.parametrize("usage", [None, {}])
def test_without_model_usage_the_turn_names_its_model_flag(usage):
    _, out = _run([INIT, _answer(MAIN), _ok_result(usage)])
    _, data = _terminal(out)
    assert data["models_used"] == [MAIN]


def test_without_model_usage_a_fallback_names_the_model_that_took_over():
    _, out = _run([INIT, FALLBACK_503, _answer(FALLBACK), _ok_result(None)])
    _, data = _terminal(out)
    assert data["models_used"] == [FALLBACK]


def test_without_a_model_flag_the_init_frames_model_is_used():
    _, out = _run([INIT, _answer(MAIN), _ok_result(None)], model_id=None)
    _, data = _terminal(out)
    assert data["models_used"] == [MAIN]


def test_the_finalize_safety_net_carries_the_record_too():
    t, _ = _run([INIT, FALLBACK_503, _answer(FALLBACK)])
    ((event, data),) = t.finalize()
    assert event == "query_complete"
    assert data["model_fallback"][0]["to"] == FALLBACK
    assert data["models_used"] == [FALLBACK]


def test_an_api_error_message_is_never_taken_for_the_answer():
    t, _ = _run([INIT, _synthetic_error("API Error: 503 Service Unavailable.")])
    assert t.accumulated_reply == ""
    assert t.partial_reply() == ""


# --- a turn the model's unavailability ended ----------------------------------------

API_503 = ("API Error: 503 Service Unavailable. This is a server-side issue, usually "
           "temporary. If it persists, check your Amazon Bedrock service status.")
API_529 = ("API Error: Repeated 529 Overloaded errors. The API is at capacity. Try again "
           "in a moment.")
API_429 = "API Error: Request rejected (429) · Too many requests, please wait before trying again."
TIMED_OUT = "Request timed out"


@pytest.mark.parametrize("text, status", [(API_503, 503), (API_503, None), (API_529, 529),
                                          (API_529, None), ("API Error: 500 {}", 500)])
def test_a_server_error_after_a_fallback_gets_the_tried_text(text, status):
    _, out = _run([INIT, FALLBACK_503, RETRY_503, _synthetic_error(text),
                   _error_result(text, status)])
    event, data = _terminal(out)
    assert event == "query_error"
    assert data["error"] == TRIED_TEXT
    assert data["reason"] == "model_unavailable"
    assert data["detail"] == text
    assert data["agent"] == "container_cc"
    assert data["cc_session_id"] == "s-1"
    assert data["model_fallback"] == [
        {"agent": "container_cc", "from": MAIN, "to": FALLBACK, "reason": "server_error"}]


@pytest.mark.parametrize("text, status", [(API_429, 429), (API_429, None),
                                          (TIMED_OUT, None)])
def test_a_rate_limit_or_timeout_gets_the_text_without_the_second_model(text, status):
    _, out = _run([INIT, RETRY_503, _synthetic_error(text), _error_result(text, status)])
    event, data = _terminal(out)
    assert event == "query_error"
    assert data["error"] == NOT_TRIED_TEXT
    assert data["reason"] == "model_unavailable"
    assert data["detail"] == text
    assert data["model_fallback"] == []


def test_a_server_error_with_no_fallback_does_not_claim_a_second_model():
    """With no --fallback-model (it did not resolve), a 503 is never retried elsewhere."""
    _, out = _run([INIT, _synthetic_error(API_503), _error_result(API_503, 503)])
    _, data = _terminal(out)
    assert data["error"] == NOT_TRIED_TEXT
    assert data["model_fallback"] == []


def test_a_timeout_after_a_fallback_says_a_second_model_was_tried():
    """The fallback switched on a 503, then the second model timed out: both were tried."""
    _, out = _run([INIT, FALLBACK_503, _synthetic_error(TIMED_OUT),
                   _error_result(TIMED_OUT, None)])
    _, data = _terminal(out)
    assert data["error"] == TRIED_TEXT


@pytest.mark.parametrize("frame", [
    {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"},
    {"type": "result", "subtype": "error_max_turns", "is_error": True},
    {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 403,
     "result": 'AWS authentication failed · API Error: 403 {"error":"path not permitted"}'},
    {"type": "result", "subtype": "success", "is_error": True,
     "result": "API Error: Claude Code is unable to respond to this request, which appears "
               "to violate our Usage Policy."},
])
def test_any_other_error_keeps_todays_text(frame):
    _, out = _run([INIT, frame])
    event, data = _terminal(out)
    assert event == "query_error"
    expected = frame.get("result") or frame.get("subtype")
    assert data["error"] == expected
    assert "reason" not in data and "detail" not in data
    assert data["agent"] == "container_cc"


# --- was the turn waiting on a model retry? (read by the engine's watchdog) -----------

def test_a_turn_whose_last_frame_is_a_retry_is_waiting_on_the_model():
    t, _ = _run([INIT, _answer(MAIN), RETRY_503])
    assert t.retrying_model == RETRY_503


@pytest.mark.parametrize("after", [_answer(MAIN), INFORMATIONAL, FALLBACK_503,
                                   {"type": "user", "message": {"content": []}}])
def test_any_frame_after_the_retry_means_the_turn_moved_on(after):
    t, _ = _run([INIT, RETRY_503, after])
    assert t.retrying_model is None


def test_a_turn_with_no_retry_is_not_waiting_on_the_model():
    t, _ = _run([INIT, _answer(MAIN)])
    assert t.retrying_model is None
    assert CCStreamTranslator().retrying_model is None
