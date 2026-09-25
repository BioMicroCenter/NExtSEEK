"""What the user is told when an AI model could not do its part of a turn.

The wording is operator-approved (2026-09-25, fix 5) and applied verbatim: change it only
with the operator. Two places use it.

* The parser's own failure is an unsupported plan, answered by ``unsupported_reply`` in
  ``orchestrator.py``: one text when the planning model did not answer in time
  (``failure = transport_timeout``), another for anything else it sent back unusable.
* A model failure anywhere that ends the turn is an ``LLMFatalError``. When it ended
  because the models did not answer (``unavailable``), the orchestrator's fatal handlers
  reply with ``model_unavailable_reply`` and emit ``query_error`` with
  ``reason = MODEL_UNAVAILABLE_REASON``; the raw provider message goes to the event's
  ``detail``, the debug payload's ``fatal_error`` and the chat log's ``error``, never to
  the reply. A fatal error that is not unavailability (a bare 400) keeps its old reply.
"""
from __future__ import annotations

#: ``query_error`` data ``reason`` for a turn the models could not answer. The Container-CC
#: route uses the same value, so a run can list both engines' outages the same way.
MODEL_UNAVAILABLE_REASON = "model_unavailable"

PLANNER_TIMEOUT_REPLY = (
    "The AI model that plans the search did not respond in time, so I have not run your question. "
    "This is a temporary problem on our side, not a problem with your question. "
    "Please ask again in a minute."
)

PLANNER_UNUSABLE_REPLY = (
    "The AI model that plans the search sent back something I could not use, so I have not run your "
    "question. Please ask again in a minute. If it happens again, try rewording it."
)

#: A second model was tried (the call moved to the next provider) and failed too.
MODELS_UNAVAILABLE_TRIED_TWO_REPLY = (
    "The AI models we use were unavailable (we tried a second one as well), so I could not finish your "
    "question. Please ask again in a few minutes."
)

#: No second model was tried: the agent has no fallback chain.
MODELS_UNAVAILABLE_REPLY = (
    "The AI models we use were unavailable, so I could not finish your question. "
    "Please ask again in a few minutes."
)


def model_unavailable_reply(fatal: BaseException) -> str | None:
    """The reply for a fatal model failure that was unavailability, or None when it was not."""
    if not getattr(fatal, "unavailable", False):
        return None
    if getattr(fatal, "model_fallback", None):
        return MODELS_UNAVAILABLE_TRIED_TWO_REPLY
    return MODELS_UNAVAILABLE_REPLY


def fatal_query_error(fatal: BaseException, *, agent: str | None) -> tuple[str | None, dict]:
    """The ``query_error`` data for a fatal model failure, and the reply that replaces the raw message.

    Unavailability: ``(plain text, {"error": plain text, "reason": "model_unavailable",
    "detail": raw message, "agent", "fatal": True, "model_fallback": [...]})``. Anything
    else: ``(None, {"error": raw message, "agent", "fatal": True})``, the event as it was.
    """
    message = str(fatal)
    reply = model_unavailable_reply(fatal)
    if reply is None:
        return None, {"error": message, "agent": agent, "fatal": True}
    return reply, {
        "error": reply,
        "reason": MODEL_UNAVAILABLE_REASON,
        "detail": message,
        "agent": agent,
        "fatal": True,
        "model_fallback": list(getattr(fatal, "model_fallback", None) or []),
    }
