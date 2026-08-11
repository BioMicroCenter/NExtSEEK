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

__all__ = ["AuthorizationError", "guarded_provider_call"]


def guarded_provider_call(
    manifest_hash_value: str,
    *,
    attempt_id: str,
    idempotency_key: str,
    max_cost_usd: Decimal,
    fn: Callable[[], T],
    actual_cost_fn: Callable[[T], Decimal] | None = None,
) -> T:
    reserve_budget(
        manifest_hash_value,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        max_cost_usd=max_cost_usd,
    )
    require_reservation(manifest_hash_value, attempt_id)
    try:
        result = fn()
    except Exception:
        release_reservation(attempt_id)
        raise
    actual = actual_cost_fn(result) if actual_cost_fn else max_cost_usd
    reconcile_reservation(attempt_id, actual_usd=actual)
    return result
