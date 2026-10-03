"""How a granular op says no (approach 1, piece 2: ops answer with closed codes).

Every refusal is ``{"code", "reason", "message", "errors"}``: ``code`` from ``CODES``; ``reason`` from ``REASONS``,
only with AGENT_FAILED; ``message`` a fixed sentence per code and reason, never an exception's text; ``errors`` for
VALIDATION one ``{"field", "type"}`` per refused field (never its value), and for any other code one
``{"title", "detail"}`` item repeating the message, kept for the sidecar, which reads ``errors``, for the one release
that keeps it. The tools turn ``code`` into an exit number with ``EXIT``, mirrored in the plugin
(``bin/_op_errors.py``; NessieAI/tests/cc/test_op_road_parity.py pins the copies together).
"""
from __future__ import annotations

from rest_framework.response import Response

CODES = ("VALIDATION", "WRITE_BLOCKED", "AUTH_FAILED", "PASS_NOT_ALLOWED", "BUSY", "TIME_UP", "AGENT_FAILED")
REASONS = ("model_unavailable", "deadline", "bad_output", "internal")

HTTP_STATUS: dict[str, int] = {
    "VALIDATION": 422,
    "WRITE_BLOCKED": 403,
    "AUTH_FAILED": 401,
    "PASS_NOT_ALLOWED": 403,
    "BUSY": 429,
    "TIME_UP": 408,
    "AGENT_FAILED": 502,
}

EXIT: dict[str, int] = {
    "VALIDATION": 3,
    "AGENT_FAILED": 4,
    "WRITE_BLOCKED": 5,
    "TRANSPORT_ERROR": 7,
    "AUTH_FAILED": 8,
    "STAGING_ERROR": 9,
    "BUSY": 10,
    "TIME_UP": 11,
    "PASS_NOT_ALLOWED": 12,
}

#: The sentence the agent reads for each code (AGENT_FAILED reads REASON_MESSAGES). Brain change: reviewed with the
#: skill's error section (BRAIN-CHANGES.md, plan 03).
CODE_MESSAGES: dict[str, str] = {
    "VALIDATION": "The op did not accept its arguments; errors names each refused field and what was wrong with it.",
    "WRITE_BLOCKED": "The op was refused because it would change data, and this op may only read.",
    "AUTH_FAILED": "NExtSEEK did not accept this turn's pass, or could not act as the user with it.",
    "PASS_NOT_ALLOWED": "This turn's pass does not allow this request.",
    "BUSY": ("Two ops or NS queries of this turn are already running; wait for one of them to answer before starting "
             "another."),
    "TIME_UP": "Not enough time is left in this turn to run this op.",
}
REASON_MESSAGES: dict[str, str] = {
    "model_unavailable": "The op failed because the AI models it needs did not answer, the fallback model included.",
    "deadline": "The op ran out of time before it could finish.",
    "bad_output": "The op failed because an AI model answered in a form the op could not use.",
    "internal": "The op failed inside NExtSEEK.",
}


def op_error(code: str, *, reason: str | None = None, fields: list[dict] | None = None, status: int) -> Response:
    """The reply for a refused or failed op: ``error_body`` as a Response with ``status``."""
    return Response(error_body(code, reason=reason, fields=fields), status=status)


def error_body(code: str, *, reason: str | None = None, fields: list[dict] | None = None) -> dict:
    """The refusal body. ``reason`` only with AGENT_FAILED (anything unknown is ``internal``); ``fields`` only with
    VALIDATION, as ``{"field", "type"}`` items. Plan 02's turn-pass refusals build theirs here too."""
    if code not in CODES:
        raise ValueError(f"unknown op error code {code!r}")
    if code == "AGENT_FAILED":
        reason = reason if reason in REASONS else "internal"
        message = REASON_MESSAGES[reason]
    elif reason is not None:
        raise ValueError("only AGENT_FAILED carries a reason")
    else:
        message = CODE_MESSAGES[code]
    if code == "VALIDATION":
        errors = [{"field": str(item.get("field") or "request"), "type": str(item.get("type") or "invalid")}
                  for item in (fields or [])] or [{"field": "request", "type": "invalid"}]
    else:
        errors = [{"title": code, "detail": message}]
    return {"code": code, "reason": reason, "message": message, "errors": errors}


def validation_fields(exc) -> list[dict]:
    """``{"field", "type"}`` for each error of a pydantic ``ValidationError``: the field's path and the error type,
    never the input or the message (a validator's message may quote the value)."""
    out = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc") or ()) or "request"
        out.append({"field": field, "type": str(err.get("type") or "invalid")})
    return out


def failure_reason(exc: BaseException) -> str:
    """The closed reason for an op that failed with ``exc``."""
    from pydantic import ValidationError as PydanticValidationError

    from chat_nextseek.llm_clients import LLMError, LLMFatalError
    from chat_nextseek.schemas.schema_helper import StructuredOutputError
    from NessieAI.ns.granular import OpDeadlineError

    if isinstance(exc, OpDeadlineError):
        return "deadline"
    if isinstance(exc, LLMFatalError):
        if getattr(exc, "reason", None) == "deadline":
            return "deadline"
        return "model_unavailable" if exc.unavailable else "internal"
    if isinstance(exc, (StructuredOutputError, PydanticValidationError)):
        return "bad_output"
    if isinstance(exc, LLMError):
        return "model_unavailable"
    return "internal"
