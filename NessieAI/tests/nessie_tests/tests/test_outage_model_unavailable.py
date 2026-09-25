"""A turn that ended because the AI models were unavailable is an outage, as before.

Since 2026-09-25 (fix 5) an NS turn whose models did not answer no longer replies with
the raw ``All provider fallbacks exhausted ...`` message: the reply is the operator's
plain text and the ``query_error`` event says ``reason: "model_unavailable"``, with the
raw message in ``detail``. The Container-CC route uses the same reason. The detector
must keep classifying those turns the way it classified the old ones, and keep
recognising the old text in stored runs.
"""
from __future__ import annotations

import pytest

from NessieAI.tests.nessie_tests import evaluate, outage

OLD_REPLY = ("**The request could not be completed.**\n\nAll provider fallbacks exhausted � agent "
             "'parser': ServiceUnavailableException")
NS_TRIED_TWO = ("The AI models we use were unavailable (we tried a second one as well), so I could not finish "
                "your question. Please ask again in a few minutes.")
NS_NO_SECOND = ("The AI models we use were unavailable, so I could not finish your question. Please ask again "
                "in a few minutes.")


def test_the_old_reply_is_still_an_outage():
    assert outage.is_provider_outage(OLD_REPLY) is True


@pytest.mark.parametrize("reply", [NS_TRIED_TWO, NS_NO_SECOND], ids=["tried-two", "no-second"])
def test_the_new_ns_reply_is_an_outage(reply):
    assert outage.is_provider_outage(reply) is True
    assert evaluate.classify_turn_status(False, reply) == "error"
    assert evaluate.classify_turn_status(True, reply) == "error"


@pytest.mark.parametrize("item", [
    {"error": "The AI models we use were unavailable.", "reason": "model_unavailable", "detail": "503"},
    {"error": "Claude could not reach its model.", "reason": "model_unavailable", "detail": "API Error: 529"},
    {"event": "query_error", "data": {"reason": "model_unavailable", "error": "x"}},
], ids=["ns-event-data", "cc-event-data", "progress-event"])
def test_a_query_error_with_reason_model_unavailable_is_an_outage(item):
    assert outage.is_provider_outage(item) is True


def test_a_dict_still_carrying_the_old_text_is_an_outage():
    assert outage.is_provider_outage({"error": "All provider fallbacks exhausted: agent 'x': 503"}) is True


@pytest.mark.parametrize("item", [
    {"error": "Container-CC turn exceeded the 600s limit and was stopped.", "reason": "exec_timeout"},
    {"reason": "something_else"},
    {"event": "query_error", "data": {"error": "Unrecoverable LLM error"}},
    {},
    None,
    42,
    "found 139 samples",
    # The planner's own failure replies are not an outage: they are an unsupported plan.
    ("The AI model that plans the search did not respond in time, so I have not run your question. This is a "
     "temporary problem on our side, not a problem with your question. Please ask again in a minute."),
], ids=["cc-timeout", "other-reason", "fatal-400", "empty", "none", "int", "answer", "planner-timeout"])
def test_everything_else_is_not_an_outage(item):
    assert outage.is_provider_outage(item) is False


def test_the_reason_value_matches_the_product():
    """The harness is dependency-free and keeps its own copy of the reason; it must be the
    product's. Skipped where the product package is not importable (the host lane)."""
    failure_replies = pytest.importorskip("chat_nextseek.failure_replies")

    assert outage.MODEL_UNAVAILABLE_REASON == failure_replies.MODEL_UNAVAILABLE_REASON


def test_the_ns_text_marker_is_the_products_reply():
    failure_replies = pytest.importorskip("chat_nextseek.failure_replies")

    for reply in (failure_replies.MODELS_UNAVAILABLE_TRIED_TWO_REPLY, failure_replies.MODELS_UNAVAILABLE_REPLY):
        assert outage.is_provider_outage(reply) is True


def test_the_outage_reason_still_names_the_old_marker_and_the_new_reason():
    assert outage.PROVIDER_OUTAGE_MARKER in outage.OUTAGE_REASON
    assert outage.MODEL_UNAVAILABLE_REASON in outage.OUTAGE_REASON
    assert outage.OUTAGE_REASON.startswith("provider outage")
