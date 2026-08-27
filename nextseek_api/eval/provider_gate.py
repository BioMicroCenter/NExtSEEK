"""Pre-transport provider-call gate using V4-8 reservation controls."""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, TypeVar

from nextseek_api.eval.run_authorization import (
    AuthorizationError,
    reconcile_reservation,
    release_reservation,
    require_reservation,
    reserve_budget,
)

T = TypeVar("T")

# Test-only crash injection (default off — mirror V4-5 abort hooks).
CRASH_BEFORE_RESERVE = False
CRASH_AFTER_RESERVE = False
CRASH_AFTER_PROVIDER = False
CRASH_BEFORE_RECONCILE = False

__all__ = [
    "AuthorizationError",
    "CRASH_AFTER_PROVIDER",
    "CRASH_AFTER_RESERVE",
    "CRASH_BEFORE_RECONCILE",
    "CRASH_BEFORE_RESERVE",
    "guarded_provider_call",
]


def guarded_provider_call(
    manifest_hash_value: str,
    *,
    attempt_id: str,
    idempotency_key: str,
    max_cost_usd: Decimal,
    fn: Callable[[], T],
    actual_cost_fn: Callable[[T], Decimal] | None = None,
) -> T:
    if CRASH_BEFORE_RESERVE:
        raise RuntimeError("crash before reserve")

    reserve_budget(
        manifest_hash_value,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        max_cost_usd=max_cost_usd,
    )
    if CRASH_AFTER_RESERVE:
        raise RuntimeError("crash after reserve")

    require_reservation(manifest_hash_value, attempt_id)
    try:
        result = fn()
    except Exception:
        release_reservation(attempt_id)
        raise

    if CRASH_AFTER_PROVIDER:
        raise RuntimeError("crash after provider")

    actual = actual_cost_fn(result) if actual_cost_fn else max_cost_usd
    if CRASH_BEFORE_RECONCILE:
        raise RuntimeError("crash before reconcile")
    reconcile_reservation(attempt_id, actual_usd=actual)
    return result
