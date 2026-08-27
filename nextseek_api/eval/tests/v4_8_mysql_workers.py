"""Multiprocess Lane M worker entrypoints (pickle-safe, spawn context)."""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any


def _ensure_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.test_settings_realstack")
    import django
    from django.conf import settings
    from django.db import connections

    if not settings.configured:
        django.setup()
    connections.close_all()


def _ensure_manifest(payload: dict[str, Any]) -> str:
    from nextseek_api.eval.run_authorization import approve_run_manifest
    from nextseek_api.eval.run_manifest import RunManifest

    body = payload.get("manifest_body")
    if body is not None:
        approved = approve_run_manifest(RunManifest.model_validate(body))
        return approved.manifest_hash
    return payload["manifest_hash"]


def mp_reserve_worker(payload: dict[str, Any]) -> str:
    """Concurrent reserve_budget from a separate process."""
    _ensure_django()
    from nextseek_api.eval.run_authorization import AuthorizationError, reserve_budget

    manifest_hash = _ensure_manifest(payload)
    barrier = payload["barrier"]
    barrier.wait(timeout=10)
    try:
        reserve_budget(
            manifest_hash,
            attempt_id=payload["attempt_id"],
            idempotency_key=payload["idempotency_key"],
            max_cost_usd=Decimal(payload["max_cost_usd"]),
        )
        return "ok"
    except AuthorizationError:
        return "refused"


def mp_idempotency_replay_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Reserve under barrier and report idempotency-key row count from worker DB."""
    _ensure_django()
    from nextseek_api.assistant.models_db import SpendReservation
    from nextseek_api.eval.run_authorization import AuthorizationError, reserve_budget

    manifest_hash = _ensure_manifest(payload)
    barrier = payload["barrier"]
    barrier.wait(timeout=10)
    try:
        result = reserve_budget(
            manifest_hash,
            attempt_id=payload["attempt_id"],
            idempotency_key=payload["idempotency_key"],
            max_cost_usd=Decimal(payload["max_cost_usd"]),
        )
        status = "ok"
        attempt_id = result.attempt_id
    except AuthorizationError:
        status = "refused"
        attempt_id = payload["attempt_id"]
    key_count = SpendReservation.objects.filter(
        idempotency_key=payload["idempotency_key"]
    ).count()
    return {"status": status, "attempt_id": attempt_id, "key_count": key_count}


def mp_crash_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Run guarded_provider_call with a crash flag in an isolated process."""
    _ensure_django()
    import nextseek_api.eval.provider_gate as provider_gate
    from nextseek_api.assistant.models_db import SpendReservation
    from nextseek_api.eval.provider_gate import guarded_provider_call

    manifest_hash = _ensure_manifest(payload)
    flag_name = payload["flag_name"]
    setattr(provider_gate, flag_name, True)
    attempt_id = payload["attempt_id"]
    outcome: dict[str, Any] = {"flag": flag_name, "attempt_id": attempt_id}
    try:
        guarded_provider_call(
            manifest_hash,
            attempt_id=attempt_id,
            idempotency_key=payload["idempotency_key"],
            max_cost_usd=Decimal(payload["max_cost_usd"]),
            fn=lambda: "ok",
        )
        outcome["raised"] = None
    except RuntimeError as exc:
        outcome["raised"] = str(exc)

    try:
        reservation = SpendReservation.objects.get(attempt_id=attempt_id)
        outcome["status"] = reservation.status
        outcome["exists"] = True
    except SpendReservation.DoesNotExist:
        outcome["status"] = "missing"
        outcome["exists"] = False
    return outcome


def mp_broker_redelivery_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Simulate broker redelivery: reserve then replay same idempotency key."""
    _ensure_django()
    from nextseek_api.assistant.models_db import SpendReservation
    from nextseek_api.eval.run_authorization import reserve_budget

    manifest_hash = _ensure_manifest(payload)
    idempotency_key = payload["idempotency_key"]
    attempt_id = payload["attempt_id"]
    phase = payload["phase"]

    if phase == "first_delivery":
        reserve_budget(
            manifest_hash,
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
            max_cost_usd=Decimal(payload["max_cost_usd"]),
        )
        return {
            "phase": phase,
            "count": SpendReservation.objects.filter(idempotency_key=idempotency_key).count(),
        }

    reserve_budget(
        manifest_hash,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        max_cost_usd=Decimal(payload["max_cost_usd"]),
    )
    rows = list(
        SpendReservation.objects.filter(idempotency_key=idempotency_key).values(
            "attempt_id", "status", "reserved_usd"
        )
    )
    return {"phase": phase, "count": len(rows), "rows": rows}
