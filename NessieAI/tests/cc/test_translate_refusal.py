"""A model usage-policy refusal ends a Container-CC turn in a plain-text ``model_refused``.

Prod text (2026-10-14, task 1374): "API Error: <model> can't help with this. Start a new
session to continue. Learn more: https://www.anthropic.com/legal/aup Details: `[bio]` ...".
The user gets the approved text, the raw text stays in ``detail``, and the stored Claude
Code session id is cleared so the next turn does not resume the refused session.
"""
from __future__ import annotations

from NessieAI.cc import cc_session
from NessieAI.cc.translate import CCStreamTranslator

RAW = ("API Error: global.anthropic.claude-sonnet-5-5 can't help with this. Start a new "
       "session to continue.\n\nLearn more: https://www.anthropic.com/legal/aup\n\n"
       "Details: `[bio]`\nRequest ID req_1")
TEXT = ("The AI model declined to answer this question under its provider's usage rules, "
        "so I could not finish. This can happen with questions that name a pathogen. "
        "Please rephrase, for example by naming the study or sample type instead.")


def _frame(result: str) -> dict:
    return {"type": "result", "subtype": "success", "is_error": True, "result": result,
            "session_id": "s-1"}


def test_refusal_gives_plain_text_with_raw_in_detail():
    (event, data), = CCStreamTranslator().handle(_frame(RAW))
    assert event == "query_error"
    assert data["error"] == TEXT
    assert data["reason"] == "model_refused"
    assert data["detail"] == RAW


def test_refusal_clears_the_stored_session_id():
    (event, data), = CCStreamTranslator().handle(_frame(RAW))
    captured = []
    wrapped = cc_session.make_session_sniffer(lambda e, d: None, captured.append)
    wrapped("tool", {"cc_session_id": "s-1"})
    wrapped(event, data)
    assert captured == ["s-1", ""]
    assert cc_session.resume_id_from_state({"cc_session_id": captured[-1]}) is None


def test_other_error_still_goes_the_generic_way():
    (event, data), = CCStreamTranslator().handle(_frame("Some other failure"))
    assert event == "query_error"
    assert data["error"] == "Some other failure"
    assert "reason" not in data
    assert not data.get("clear_cc_session")
