"""V4-8 run manifest approval and atomic spend reservation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from nextseek_api.assistant.models_db import ApprovedRunManifest, SpendReservation

__all__ = [
    "AuthorizationError",
    "ApprovedManifest",
    "ReservationResult",
    "approve_manifest",
    "manifest_hash",
    "reconcile_reservation",
    "release_reservation",
    "require_reservation",
    "reserve_budget",
]


class AuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedManifest:
    manifest_hash: str
    max_spend_usd: Decimal
    max_calls: int


@dataclass(frozen=True)
class ReservationResult:
    attempt_id: str
    reserved_usd: Decimal
    remaining_usd: Decimal
    remaining_calls: int


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def approve_manifest(
    manifest: dict,
    *,
    max_spend_usd: Decimal,
    max_calls: int,
    ttl_seconds: int = 3600,
) -> ApprovedRunManifest:
    now = timezone.now()
    fp = manifest_hash(manifest)
    record, _ = ApprovedRunManifest.objects.update_or_create(
        manifest_hash=fp,
        defaults={
            "manifest": manifest,
            "approved_at": now,
            "expires_at": now + timezone.timedelta(seconds=ttl_seconds),
            "max_spend_usd": max_spend_usd,
            "max_calls": max_calls,
            "consumed": False,
        },
    )
    return record


def _load_manifest(manifest_hash_value: str) -> ApprovedRunManifest:
    try:
        record = ApprovedRunManifest.objects.get(manifest_hash=manifest_hash_value)
    except ApprovedRunManifest.DoesNotExist as exc:
        raise AuthorizationError("manifest not approved") from exc
    if record.consumed:
        raise AuthorizationError("manifest already consumed")
    if record.expires_at <= timezone.now():
        raise AuthorizationError("manifest expired")
    return record


def _reserved_totals(record: ApprovedRunManifest) -> tuple[Decimal, int]:
    pending = record.reservations.filter(status=SpendReservation.STATUS_PENDING)
    reserved_usd = pending.aggregate(total=Sum("reserved_usd"))["total"] or Decimal("0")
    return reserved_usd, pending.count()


def reserve_budget(
    manifest_hash_value: str,
    *,
    attempt_id: str,
    idempotency_key: str,
    max_cost_usd: Decimal,
) -> ReservationResult:
    if max_cost_usd <= 0:
        raise AuthorizationError("non-positive reservation refused")

    with transaction.atomic():
        record = ApprovedRunManifest.objects.select_for_update().get(
            manifest_hash=manifest_hash_value
        )
        if record.consumed:
            raise AuthorizationError("manifest already consumed")
        if record.expires_at <= timezone.now():
            raise AuthorizationError("manifest expired")

        existing = SpendReservation.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            reserved_usd, pending_calls = _reserved_totals(record)
            return ReservationResult(
                attempt_id=existing.attempt_id,
                reserved_usd=existing.reserved_usd,
                remaining_usd=record.max_spend_usd - reserved_usd,
                remaining_calls=record.max_calls - pending_calls,
            )

        reserved_usd, pending_calls = _reserved_totals(record)
        reconciled = record.reservations.filter(status=SpendReservation.STATUS_RECONCILED).aggregate(
            total=Sum("actual_usd")
        )["total"] or Decimal("0")
        if reserved_usd + reconciled + max_cost_usd > record.max_spend_usd:
            raise AuthorizationError("spend cap exceeded")
        if pending_calls + record.reservations.filter(status=SpendReservation.STATUS_RECONCILED).count() >= record.max_calls:
            raise AuthorizationError("call cap exceeded")

        SpendReservation.objects.create(
            manifest=record,
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
            reserved_usd=max_cost_usd,
            status=SpendReservation.STATUS_PENDING,
        )
        reserved_usd, pending_calls = _reserved_totals(record)
        return ReservationResult(
            attempt_id=attempt_id,
            reserved_usd=max_cost_usd,
            remaining_usd=record.max_spend_usd - reserved_usd - reconciled,
            remaining_calls=record.max_calls - pending_calls,
        )


def require_reservation(manifest_hash_value: str, attempt_id: str) -> SpendReservation:
    try:
        reservation = SpendReservation.objects.select_related("manifest").get(
            attempt_id=attempt_id,
            manifest__manifest_hash=manifest_hash_value,
        )
    except SpendReservation.DoesNotExist as exc:
        raise AuthorizationError("reservation required before provider call") from exc
    if reservation.status != SpendReservation.STATUS_PENDING:
        raise AuthorizationError("reservation not pending")
    return reservation


def reconcile_reservation(attempt_id: str, *, actual_usd: Decimal) -> SpendReservation:
    with transaction.atomic():
        reservation = SpendReservation.objects.select_for_update().get(attempt_id=attempt_id)
        if reservation.status != SpendReservation.STATUS_PENDING:
            return reservation
        reservation.actual_usd = actual_usd
        reservation.status = SpendReservation.STATUS_RECONCILED
        reservation.reconciled_at = timezone.now()
        reservation.save(update_fields=["actual_usd", "status", "reconciled_at"])
    return reservation


def release_reservation(attempt_id: str) -> SpendReservation:
    with transaction.atomic():
        reservation = SpendReservation.objects.select_for_update().get(attempt_id=attempt_id)
        if reservation.status == SpendReservation.STATUS_PENDING:
            reservation.status = SpendReservation.STATUS_RELEASED
            reservation.reconciled_at = timezone.now()
            reservation.save(update_fields=["status", "reconciled_at"])
    return reservation


def mark_manifest_consumed(manifest_hash_value: str) -> None:
    ApprovedRunManifest.objects.filter(manifest_hash=manifest_hash_value).update(consumed=True)
