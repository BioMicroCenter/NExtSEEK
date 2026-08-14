"""V4-8 spend conservation helpers — independent bucket sums + attempt accounting."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum

from nextseek_api.assistant.models_db import ApprovedRunManifest, PaidRunState, SpendReservation

__all__ = ["ConservationSnapshot", "compute_conservation"]


@dataclass(frozen=True)
class ConservationSnapshot:
    approved_max_usd: Decimal
    available_usd: Decimal
    reserved_usd: Decimal
    reconciled_actual_usd: Decimal
    released_expired_usd: Decimal
    pending_calls: int
    succeeded_calls: int
    failed_calls: int

    @property
    def reconciled_calls(self) -> int:
        return self.succeeded_calls

    @property
    def released_calls(self) -> int:
        return self.failed_calls

    def assert_balanced(self) -> None:
        bucket_sum = (
            self.available_usd
            + self.reserved_usd
            + self.reconciled_actual_usd
            + self.released_expired_usd
        )
        if bucket_sum != self.approved_max_usd:
            raise ValueError(
                f"conservation mismatch: bucket_sum {bucket_sum} != approved_max {self.approved_max_usd}"
            )

        call_total = self.succeeded_calls + self.failed_calls + self.pending_calls
        if call_total < 0:
            raise ValueError("negative call counts")


def _sum_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def compute_conservation(
    record: ApprovedRunManifest,
    *,
    run_id: str | None = None,
) -> ConservationSnapshot:
    reservations = record.reservations.all()

    reserved_usd = _sum_decimal(
        reservations.filter(status=SpendReservation.STATUS_PENDING).aggregate(
            total=Sum("reserved_usd")
        )["total"]
    )
    reconciled_actual_usd = _sum_decimal(
        reservations.filter(status=SpendReservation.STATUS_RECONCILED).aggregate(
            total=Sum("actual_usd")
        )["total"]
    )
    released_expired_usd = _sum_decimal(
        reservations.filter(
            status__in=(
                SpendReservation.STATUS_RELEASED,
                SpendReservation.STATUS_EXPIRED,
            )
        ).aggregate(total=Sum("reserved_usd"))["total"]
    )

    available_usd = (
        record.max_spend_usd - reserved_usd - reconciled_actual_usd - released_expired_usd
    )

    pending_calls = reservations.filter(status=SpendReservation.STATUS_PENDING).count()
    succeeded_calls = reservations.filter(status=SpendReservation.STATUS_RECONCILED).count()
    failed_calls = reservations.filter(
        status__in=(SpendReservation.STATUS_RELEASED, SpendReservation.STATUS_EXPIRED)
    ).count()

    distinct_attempts = reservations.values("attempt_id").distinct().count()
    if pending_calls + succeeded_calls + failed_calls != distinct_attempts:
        raise ValueError(
            "reservation attempt IDs do not partition into pending/succeeded/failed buckets"
        )

    if run_id is not None:
        attempt_qs = PaidRunState.objects.filter(run_id=run_id, manifest=record).exclude(
            arm_id="__run_lock__", attempt_id="__lock__"
        )
        by_status = {
            row["status"]: row["n"]
            for row in attempt_qs.values("status").annotate(n=Count("id"))
        }
        attempt_pending = by_status.get(PaidRunState.STATUS_PENDING, 0)
        attempt_succeeded = by_status.get(PaidRunState.STATUS_SUCCEEDED, 0) + by_status.get(
            PaidRunState.STATUS_CACHED, 0
        )
        attempt_failed = by_status.get(PaidRunState.STATUS_FAILED, 0)
        attempt_total = attempt_qs.count()
        if attempt_pending + attempt_succeeded + attempt_failed != attempt_total:
            raise ValueError(
                "PaidRunState attempt IDs do not partition into pending/succeeded/failed"
            )

    return ConservationSnapshot(
        approved_max_usd=record.max_spend_usd,
        available_usd=available_usd,
        reserved_usd=reserved_usd,
        reconciled_actual_usd=reconciled_actual_usd,
        released_expired_usd=released_expired_usd,
        pending_calls=pending_calls,
        succeeded_calls=succeeded_calls,
        failed_calls=failed_calls,
    )
