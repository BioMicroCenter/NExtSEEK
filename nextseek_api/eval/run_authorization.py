"""V4-8 run manifest approval and atomic spend reservation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from nextseek_api.assistant.models_db import ApprovedRunManifest, SpendReservation
from nextseek_api.eval.run_manifest import RunManifest, manifest_body_hash, validate_manifest_dict

__all__ = [
    "AuthorizationError",
    "ApprovedManifest",
    "ReservationResult",
    "approve_manifest",
    "approve_run_manifest",
    "manifest_hash",
    "reconcile_reservation",
    "release_reservation",
    "expire_stale_reservations",
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


def manifest_hash(manifest: dict[str, Any]) -> str:
    return manifest_body_hash(validate_manifest_dict(manifest))


def _validated_existing_manifest(
    manifest_hash_value: str,
    body: dict[str, Any],
) -> ApprovedRunManifest:
    existing = ApprovedRunManifest.objects.get(manifest_hash=manifest_hash_value)
    if existing.manifest != body:
        raise AuthorizationError("manifest hash collision with different body")
    if existing.consumed:
        raise AuthorizationError("manifest already consumed")
    return existing


def approve_run_manifest(manifest: RunManifest | dict[str, Any]) -> ApprovedRunManifest:
    if isinstance(manifest, RunManifest):
        body = manifest.model_dump(mode="json")
    else:
        body = validate_manifest_dict(manifest)
    fp = manifest_body_hash(body)
    if ApprovedRunManifest.objects.filter(manifest_hash=fp).exists():
        return _validated_existing_manifest(fp, body)
    expires_at = manifest.approval_expires_at if isinstance(manifest, RunManifest) else body["approval_expires_at"]
    if isinstance(expires_at, str):
        from datetime import datetime

        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    try:
        with transaction.atomic():
            return ApprovedRunManifest.objects.create(
                manifest_hash=fp,
                manifest=body,
                approved_at=timezone.now(),
                expires_at=expires_at,
                max_spend_usd=Decimal(str(body["hard_cap_usd"])),
                max_calls=int(body["max_calls"]),
                consumed=False,
            )
    except IntegrityError:
        # Another process can win between the exists() check and INSERT.  The
        # unique hash is the serialization point; replay only an identical,
        # still-unconsumed manifest after the losing savepoint rolls back.
        return _validated_existing_manifest(fp, body)


def approve_manifest(
    manifest: dict[str, Any],
    *,
    max_spend_usd: Decimal | None = None,
    max_calls: int | None = None,
    ttl_seconds: int | None = None,
) -> ApprovedRunManifest:
    body = validate_manifest_dict(manifest)
    body_cap = Decimal(str(body["hard_cap_usd"]))
    body_calls = int(body["max_calls"])
    if max_spend_usd is not None and Decimal(str(max_spend_usd)) != body_cap:
        raise AuthorizationError("max_spend_usd override diverges from manifest body")
    if max_calls is not None and int(max_calls) != body_calls:
        raise AuthorizationError("max_calls override diverges from manifest body")
    if ttl_seconds is not None:
        raise AuthorizationError("ttl_seconds override diverges from manifest body")

    expires_at = body["approval_expires_at"]
    if isinstance(expires_at, str):
        from datetime import datetime

        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))

    fp = manifest_body_hash(body)
    if ApprovedRunManifest.objects.filter(manifest_hash=fp).exists():
        return _validated_existing_manifest(fp, body)
    now = timezone.now()
    try:
        with transaction.atomic():
            return ApprovedRunManifest.objects.create(
                manifest_hash=fp,
                manifest=body,
                approved_at=now,
                expires_at=expires_at,
                max_spend_usd=body_cap,
                max_calls=body_calls,
                consumed=False,
            )
    except IntegrityError:
        return _validated_existing_manifest(fp, body)


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


def _reconciled_total(record: ApprovedRunManifest) -> Decimal:
    return record.reservations.filter(status=SpendReservation.STATUS_RECONCILED).aggregate(
        total=Sum("actual_usd")
    )["total"] or Decimal("0")


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
            reconciled = _reconciled_total(record)
            return ReservationResult(
                attempt_id=existing.attempt_id,
                reserved_usd=existing.reserved_usd,
                remaining_usd=record.max_spend_usd - reserved_usd - reconciled,
                remaining_calls=record.max_calls - pending_calls,
            )

        reserved_usd, pending_calls = _reserved_totals(record)
        reconciled = _reconciled_total(record)
        if reserved_usd + reconciled + max_cost_usd > record.max_spend_usd:
            raise AuthorizationError("spend cap exceeded")
        reconciled_count = record.reservations.filter(
            status=SpendReservation.STATUS_RECONCILED
        ).count()
        if pending_calls + reconciled_count >= record.max_calls:
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


def expire_stale_reservations(*, older_than_seconds: int = 3600) -> int:
    cutoff = timezone.now() - timezone.timedelta(seconds=older_than_seconds)
    updated = SpendReservation.objects.filter(
        status=SpendReservation.STATUS_PENDING,
        created_at__lt=cutoff,
    ).update(status=SpendReservation.STATUS_EXPIRED, reconciled_at=timezone.now())
    return updated


def mark_manifest_consumed(manifest_hash_value: str) -> None:
    ApprovedRunManifest.objects.filter(manifest_hash=manifest_hash_value).update(consumed=True)
