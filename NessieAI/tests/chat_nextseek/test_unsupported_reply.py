"""An unsupported turn tells the user what is possible, never the parser's routing prose.

Local run 2026-09-22, bucket6.export_this_session: "Is there a way to export this session
to send to the development team for feedback?" was answered "I can't turn that request
into a valid NExtSEEK operation yet. Reason from parser: No downstream path supports
exporting or sharing a chat session. This is a product/UX feature request, not a data
query: no REST endpoint, graph query, reporter deliverable, or system_question ...".
The notes stay in the debug payload's parser_plan; the reply carries none of them.
"""
from __future__ import annotations

from chat_nextseek.orchestrator import UNSUPPORTED_REPLY, unsupported_reply
from chat_nextseek.schemas import ParserPlan

NOTES = ("No downstream path supports exporting or sharing a chat session. This is a product/UX "
         "feature request, not a data query: no REST endpoint, graph query, reporter deliverable, "
         "or system_question capability description covers session export.")


def test_an_unsupported_plan_gets_the_plain_reply_and_no_notes():
    reply = unsupported_reply(ParserPlan(mode="unsupported", intent_summary="export the chat", notes=NOTES))
    assert reply == UNSUPPORTED_REPLY
    for leak in ("Reason from", "parser", "downstream", "system_question", "endpoint", "REST"):
        assert leak not in reply


def test_a_planning_fault_is_reported_as_ours_without_the_notes():
    plan = ParserPlan(mode="unsupported", notes="Parser could not produce valid structured output.",
                      metadata={"failure": "parse"})
    reply = unsupported_reply(plan)
    assert "went wrong on our side" in reply
    assert "Parser" not in reply and "structured output" not in reply
