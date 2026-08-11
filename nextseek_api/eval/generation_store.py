"""Immutable posterior generation store and CAS activation (V4-5)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone as dj_timezone

from nextseek_api.assistant.models_db import (
    ActiveGenerationPointer,
    FamilyPosterior,
    PosteriorGeneration,
)

__all__ = [
    "ActivationError",
    "GenerationSnapshot",
    "activate_generation",
    "create_generation",
    "get_active_snapshot",
    "generation_content_hash",
]


class ActivationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationSnapshot:
    generation_id: int
    generation_hash: str
    decision_status: str
    posteriors: tuple[FamilyPosterior, ...]


def generation_content_hash(
    *,
    input_hash: str,
    config_fingerprint: str,
    decision_status: str,
    groups: list[dict],
    parent_hash: str | None = None,
) -> str:
    payload = {
        "input_hash": input_hash,
        "config_fingerprint": config_fingerprint,
        "decision_status": decision_status,
        "groups": groups,
        "parent_hash": parent_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def create_generation(
    *,
    input_hash: str,
    config_fingerprint: str,
    decision_status: str,
    groups: list[dict],
    payload: dict | None = None,
    parent: PosteriorGeneration | None = None,
) -> PosteriorGeneration:
    parent_hash = parent.generation_hash if parent else None
    gen_hash = generation_content_hash(
        input_hash=input_hash,
        config_fingerprint=config_fingerprint,
        decision_status=decision_status,
        groups=groups,
        parent_hash=parent_hash,
    )
    if PosteriorGeneration.objects.filter(generation_hash=gen_hash).exists():
        return PosteriorGeneration.objects.get(generation_hash=gen_hash)
    with transaction.atomic():
        generation = PosteriorGeneration.objects.create(
            generation_hash=gen_hash,
            input_hash=input_hash,
            config_fingerprint=config_fingerprint,
            decision_status=decision_status,
            payload=payload or {},
            parent=parent,
        )
        fitted_at = dj_timezone.now()
        for group in groups:
            FamilyPosterior.objects.create(
                generation=generation,
                task_family=group["name"],
                route=group["route"],
                posterior_mean=float(group["posterior_mean"]),
                band=str(group["band"]),
                n_total=int(group["n_total"]),
                fitted_at=group.get("fitted_at") or fitted_at,
            )
    return generation


def get_active_snapshot() -> GenerationSnapshot | None:
    pointer = ActiveGenerationPointer.objects.select_related("active").first()
    if pointer is None or pointer.active_id is None:
        return None
    generation = pointer.active
    posteriors = tuple(
        FamilyPosterior.objects.filter(generation=generation).order_by("task_family", "route")
    )
    return GenerationSnapshot(
        generation_id=generation.id,
        generation_hash=generation.generation_hash,
        decision_status=generation.decision_status,
        posteriors=posteriors,
    )


def activate_generation(
    generation: PosteriorGeneration,
    *,
    expected_hash: str,
    activated_by: str = "system",
) -> ActiveGenerationPointer:
    if generation.generation_hash != expected_hash:
        raise ActivationError("generation hash mismatch — activation refused")
    with transaction.atomic():
        pointer, _ = ActiveGenerationPointer.objects.select_for_update().get_or_create(
            pk=1,
            defaults={"expected_hash": expected_hash},
        )
        if pointer.expected_hash and pointer.expected_hash != expected_hash:
            raise ActivationError("stale CAS — expected hash does not match")
        pointer.previous = pointer.active
        pointer.active = generation
        pointer.expected_hash = expected_hash
        pointer.activated_by = activated_by
        pointer.activated_at = dj_timezone.now()
        pointer.save(
            update_fields=[
                "previous",
                "active",
                "expected_hash",
                "activated_by",
                "activated_at",
            ]
        )
    return pointer
