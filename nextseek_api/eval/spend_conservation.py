"""Spend conservation helpers for V4-8 reservation accounting."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nextseek_api.assistant.models_db import ApprovedRunManifest, SpendReservation

__all__ = ["ConservationSnapshot", "compute_conservation"]


@dataclass(frozen=True)
class ConservationSnapshot:
    approved_max_usd: Decimal
    available_usd: Decimal
    reserved_usd: Decimal
    reconciled_actual_usd: Decimal
    released_expired_usd: Decimal
    pending_calls: int
    reconciled_calls: int
    released_calls: int

    def assert_balanced(self) -> None:
        lhs = (
            self.available_usd
            + self.reserved_usd
            + self.reconciled_actual_usd
            + self.released_expired_usd
        )
        if lhs != self.approved_max_usd:
            raise ValueError(
                f"conservation mismatch: {lhs} != approved_max {self.approved_max_usd}"
            )
        total_calls = self.pending_calls + self.reconciled_calls + self.released_calls
        if total_calls < 0:
            raise ValueError("negative call counts")


def compute_conservation(record: ApprovedRunManifest) -> ConservationSnapshot:
    pending = record.reservations.filter(status=SpendReservation.STATUS_PENDING)
    reconciled = record.reservations.filter(status=SpendReservation.STATUS_RECONCILED)
    released = record.reservations.filter(
        status__in=(SpendReservation.STATUS_RELEASED, SpendReservation.STATUS_EXPIRED)
    )
    reserved_usd = sum((r.reserved_usd for r in pending), Decimal("0"))
    reconciled_actual = sum((r.actual_usd or Decimal("0") for r in reconciled), Decimal("0"))
    released_usd = sum((r.reserved_usd for r in released), Decimal("0"))
    available = record.max_spend_usd - reserved_usd - reconciled_actual - released_usd
    return ConservationSnapshot(
        approved_max_usd=record.max_spend_usd,
        available_usd=available,
        reserved_usd=reserved_usd,
        reconciled_actual_usd=reconciled_actual,
        released_expired_usd=released_usd,
        pending_calls=pending.count(),
        reconciled_calls=reconciled.count(),
        released_calls=released.count(),
    )
