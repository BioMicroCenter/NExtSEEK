"""Durable paid-run arm/attempt resume helpers (V4-8)."""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from nextseek_api.assistant.models_db import ApprovedRunManifest, PaidRunState

__all__ = [
    "ResumeError",
    "acquire_overlap_lock",
    "build_cache_key",
    "ensure_attempt_pending",
    "get_attempt_state",
    "mark_attempt_cached",
    "mark_attempt_failed",
    "mark_attempt_succeeded",
]


class ResumeError(RuntimeError):
    pass


def build_cache_key(*, input_fingerprint: str, manifest_hash: str, model_version: str) -> str:
    return f"{input_fingerprint}:{manifest_hash}:{model_version}"


def acquire_overlap_lock(*, run_id: str, manifest_hash: str) -> PaidRunState:
    manifest = ApprovedRunManifest.objects.get(manifest_hash=manifest_hash)
    lock_key = f"{run_id}:{manifest_hash}"
    try:
        with transaction.atomic():
            return PaidRunState.objects.create(
                run_id=run_id,
                manifest=manifest,
                overlap_lock=lock_key,
                arm_id="__run_lock__",
                attempt_id="__lock__",
                status=PaidRunState.STATUS_PENDING,
            )
    except IntegrityError as exc:
        raise ResumeError("overlap lock already held") from exc


def get_attempt_state(*, run_id: str, arm_id: str, attempt_id: str) -> PaidRunState | None:
    return PaidRunState.objects.filter(
        run_id=run_id, arm_id=arm_id, attempt_id=attempt_id
    ).first()


def ensure_attempt_pending(
    *,
    run_id: str,
    manifest_hash: str,
    arm_id: str,
    attempt_id: str,
    cache_key: str,
) -> PaidRunState:
    manifest = ApprovedRunManifest.objects.get(manifest_hash=manifest_hash)
    state, created = PaidRunState.objects.get_or_create(
        run_id=run_id,
        arm_id=arm_id,
        attempt_id=attempt_id,
        defaults={
            "manifest": manifest,
            "overlap_lock": f"{run_id}:{arm_id}:{attempt_id}",
            "status": PaidRunState.STATUS_PENDING,
            "cache_key": cache_key,
        },
    )
    if not created and state.status in {
        PaidRunState.STATUS_SUCCEEDED,
        PaidRunState.STATUS_CACHED,
    }:
        raise ResumeError("attempt already completed")
    return state


def mark_attempt_succeeded(state: PaidRunState) -> PaidRunState:
    state.status = PaidRunState.STATUS_SUCCEEDED
    state.updated_at = timezone.now()
    state.save(update_fields=["status", "updated_at"])
    return state


def mark_attempt_cached(state: PaidRunState) -> PaidRunState:
    state.status = PaidRunState.STATUS_CACHED
    state.updated_at = timezone.now()
    state.save(update_fields=["status", "updated_at"])
    return state


def mark_attempt_failed(state: PaidRunState, *, reason: str = "") -> PaidRunState:
    state.status = PaidRunState.STATUS_FAILED
    state.failure_reason = reason
    state.updated_at = timezone.now()
    state.save(update_fields=["status", "failure_reason", "updated_at"])
    return state
