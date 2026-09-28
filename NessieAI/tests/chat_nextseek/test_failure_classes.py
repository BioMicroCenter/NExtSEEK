"""One failure-class table for both NS surfaces: the recovery ladder and the tool loop (F5, 2026-09-28).

``schema_helper.FAILURE_CLASSES`` says which failures move a call to its fallback model and the reason the move
records. The ladder (``_call_with_recovery``) and the tool loop (``tool_loop.call_tools``) both move through the same
``_Failover`` object, so they move on the same failures, to the same model, with the same budget. The per-surface
behaviour that stays different is pinned where it lives (test_provider_fallback_trigger.py): with no chain, a ladder
timeout keeps the same-provider retry and a tool-loop failure is unavailable at once; a bare 400 is a fatal in the
ladder and propagates unchanged from a tool loop.
"""
from __future__ import annotations

import inspect

import pytest

from chat_nextseek import tool_loop
from chat_nextseek.llm_clients import (
    LLMAPIConnectionError,
    LLMError,
    LLMModelUnusableError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMStructuredUnsupportedError,
    LLMTimeoutError,
)
from chat_nextseek.schemas import schema_helper
from chat_nextseek.schemas.schema_helper import FAILURE_CLASSES, FALLBACK_REASONS, _EmptyCompletion, failure_reason


@pytest.mark.parametrize("err, reason", [
    (LLMModelUnusableError("AccessDeniedException"), "model_unusable"),
    (_EmptyCompletion("empty"), "empty"),
    (LLMServiceUnavailableError("503"), "unavailable"),
    (LLMRateLimitError("429"), "rate_limited"),
    (LLMTimeoutError("t"), "timeout"),
    (LLMAPIConnectionError("reset"), "connection"),
], ids=lambda v: v if isinstance(v, str) else type(v).__name__)
def test_each_moving_failure_has_one_reason(err, reason):
    assert failure_reason(err) == reason


@pytest.mark.parametrize("err", [
    LLMError("ValidationException: 400"),
    LLMStructuredUnsupportedError("toolChoice"),
    ValueError("a bug in a client"),
    RuntimeError("raw ClientError"),
], ids=lambda e: type(e).__name__)
def test_nothing_else_moves(err):
    assert failure_reason(err) is None


def test_every_reason_is_a_ledger_reason_and_every_ledger_reason_has_a_class():
    assert sorted(reason for _, reason in FAILURE_CLASSES) == sorted(FALLBACK_REASONS)


def test_a_subclass_is_matched_before_its_base():
    classes = [cls for cls, _ in FAILURE_CLASSES]
    for i, cls in enumerate(classes):
        for later in classes[i + 1:]:
            assert not issubclass(later, cls), f"{later.__name__} would never match after {cls.__name__}"


def test_both_surfaces_move_through_the_same_failover():
    assert "_Failover(" in inspect.getsource(schema_helper._call_with_recovery)
    assert "_Failover(" in inspect.getsource(tool_loop.call_tools)
    for src in (inspect.getsource(schema_helper._call_with_recovery), inspect.getsource(tool_loop.call_tools)):
        assert "_get_fallback_agent_configs" not in src, "the chain is resolved in one place, _Failover"
