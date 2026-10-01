"""An unsupported turn tells the user what is possible, never the parser's routing prose.

Local run 2026-09-22, bucket6.export_this_session: "Is there a way to export this session
to send to the development team for feedback?" was answered "I can't turn that request
into a valid NExtSEEK operation yet. Reason from parser: No downstream path supports
exporting or sharing a chat session. This is a product/UX feature request, not a data
query: no REST endpoint, graph query, reporter deliverable, or system_question ...".
The notes stay in the debug payload's parser_plan; the reply carries none of them.
"""
from __future__ import annotations

import pytest

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


# Operator-approved wording (2026-09-25, fix 5), pinned literally.
PLANNER_TIMEOUT = ("The AI model that plans the search did not respond in time, so I have not run your question. "
                   "This is a temporary problem on our side, not a problem with your question. Please ask again "
                   "in a minute.")
PLANNER_UNUSABLE = ("The AI model that plans the search sent back something I could not use, so I have not run "
                    "your question. Please ask again in a minute. If it happens again, try rewording it.")


def test_a_planning_timeout_says_the_model_did_not_answer_in_time():
    plan = ParserPlan(mode="unsupported", notes="The query planner could not reach the language model in time.",
                      metadata={"failure": "transport_timeout", "error": "LLMTimeoutError('60 s')"})
    assert unsupported_reply(plan) == PLANNER_TIMEOUT


@pytest.mark.parametrize("failure", ["parse_error", "parse", "anything else"])
def test_any_other_planning_fault_says_the_model_sent_back_something_unusable(failure):
    plan = ParserPlan(mode="unsupported", notes="Parser could not produce valid structured output.",
                      metadata={"failure": failure})
    reply = unsupported_reply(plan)
    assert reply == PLANNER_UNUSABLE
    assert "Parser" not in reply and "structured output" not in reply


def test_the_graph_refusal_reply_carries_no_machinery():
    """Prod retest 2026-09-23 Q3/Q4 printed the guard's reason to the user."""
    from chat_nextseek.orchestrator import GRAPH_REFUSAL_REPLY
    for leak in ("Graph agent", "Cypher", "catalog", "Reason", "node.", "guard"):
        assert leak not in GRAPH_REFUSAL_REPLY
