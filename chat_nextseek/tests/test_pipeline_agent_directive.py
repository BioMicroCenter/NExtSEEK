"""Directive parse step — covers all sub_modes + edge cases."""
from unittest.mock import MagicMock, patch

from chat_nextseek import pipeline_agent
from chat_nextseek.schemas.pipeline import DirectiveParseOutput, SamplesRef


def _config():
    c = MagicMock()
    c.BASE_DIR = "/tmp/cn"
    return c


def _patch_directive(retval):
    return patch(
        "chat_nextseek.pipeline_agent._pipeline_directive_parse",
        return_value=retval,
    )


def test_start_with_build_directive_stores_parsed_state():
    session = {}
    with _patch_directive(DirectiveParseOutput(
        sub_mode="build",
        pipeline_key="rnaseq",
        samples_ref=SamplesRef(kind="last_search"),
        group_by_phrase="exposure",
    )), patch(
        "chat_nextseek.pipeline_agent._run_build_flow",
        return_value={"action": "ask", "reply": "ok", "params": None},
    ):
        out = pipeline_agent.start(
            session, _config(),
            user_query="for these mice, group by exposure, run rnaseq",
            parser_plan={}, reporter_plan={},
        )
    assert out["action"] == "ask"
    state = session["pipeline_agent"]
    assert state["directive"]["pipeline_key"] == "rnaseq"
    assert state["directive"]["sub_mode"] == "build"


def test_start_with_reject_replies_and_clears():
    session = {}
    with _patch_directive(DirectiveParseOutput(
        sub_mode="reject",
        rejection_reason="Not a samplesheet directive.",
    )):
        out = pipeline_agent.start(
            session, _config(),
            user_query="hi",
            parser_plan={}, reporter_plan={},
        )
    assert out["action"] == "cancel"
    assert "not a samplesheet directive" in out["reply"].lower()
    assert session.get("pipeline_agent") in (None, {})


def test_start_with_multi_pipeline_attempt_rejects():
    session = {}
    with _patch_directive(DirectiveParseOutput(
        sub_mode="reject",
        rejection_reason="One pipeline per directive.",
        multi_pipeline_attempt=True,
    )):
        out = pipeline_agent.start(
            session, _config(),
            user_query="run rnaseq and sarek on these",
            parser_plan={}, reporter_plan={},
        )
    assert out["action"] == "cancel"
    assert "one pipeline" in out["reply"].lower()


def test_handle_turn_passthrough_when_inactive():
    session = {}
    out = pipeline_agent.handle_turn(session, _config(), "anything")
    assert out == {"action": "passthrough", "reply": "", "params": None}


def test_handle_turn_cancel_token_clears_and_replies():
    session = {"pipeline_agent": {
        "active": True, "phase": "awaiting_validation",
    }}
    out = pipeline_agent.handle_turn(session, _config(), "cancel")
    assert out["action"] == "cancel"
    assert session["pipeline_agent"] == {}
