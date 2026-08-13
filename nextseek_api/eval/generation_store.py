"""Immutable posterior generation store and CAS activation (V4-5)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from django.db import connection, transaction
from django.utils import timezone as dj_timezone

from nextseek_api.assistant.models_db import (
    ActiveGenerationPointer,
    FamilyPosterior,
    GenerationActivationAudit,
    PosteriorGeneration,
    TurnLedger,
)

__all__ = [
    "ActivationAbort",
    "ActivationError",
    "EMPTY_ACTIVE_HASH",
    "GenerationManifest",
    "GenerationSnapshot",
    "PublishAbort",
    "PublishError",
    "PermissionError",
    "activate_generation",
    "create_generation",
    "get_active_snapshot",
    "get_current_active_hash",
    "get_pinned_snapshot_for_turn",
    "generation_content_hash",
    "manifest_from_generation",
    "pin_generation_for_turn",
    "require_activate_permission",
    "require_publish_permission",
    "rollback_generation",
    "set_test_abort_activate_after_pointer_mutate",
    "set_test_abort_publish_after_generation",
]

EMPTY_ACTIVE_HASH = ""


_test_hooks = threading.local()


class PublishAbort(RuntimeError):
    """Test-only abort mid-publish; rolls back the enclosing transaction."""


class ActivationAbort(RuntimeError):
    """Test-only abort mid-activation; rolls back the enclosing transaction."""


def set_test_abort_publish_after_generation(enabled: bool) -> None:
    _test_hooks.abort_publish_after_generation = enabled


def set_test_abort_activate_after_pointer_mutate(enabled: bool) -> None:
    _test_hooks.abort_activate_after_pointer_mutate = enabled


def _should_abort_publish_after_generation() -> bool:
    return os.environ.get("PLAN018_TEST_ABORT_PUBLISH_AFTER_GENERATION") == "1" or bool(
        getattr(_test_hooks, "abort_publish_after_generation", False)
    )


def _should_abort_activate_after_pointer_mutate() -> bool:
    return os.environ.get("PLAN018_TEST_ABORT_ACTIVATE_AFTER_POINTER") == "1" or bool(
        getattr(_test_hooks, "abort_activate_after_pointer_mutate", False)
    )


class ActivationError(RuntimeError):
    pass


class PublishError(RuntimeError):
    pass


class PermissionError(RuntimeError):
    pass


class _AuthenticatedHumanPublishCapability:
    __slots__ = ()


_AUTHENTICATED_HUMAN_PUBLISH_CAPABILITY = _AuthenticatedHumanPublishCapability()


@dataclass(frozen=True)
class GenerationManifest:
    input_hash: str
    attempt_hash: str
    aggregate_hash: str
    config_fingerprint: str
    decision_status: str
    groups: list[dict]
    compatibility_keys: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, int] = field(default_factory=dict)
    fit_diagnostics: dict[str, Any] = field(default_factory=dict)
    decision_results: dict[str, Any] = field(default_factory=dict)
    source_provenance: dict[str, Any] = field(default_factory=dict)
    parent_hash: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "attempt_hash": self.attempt_hash,
            "aggregate_hash": self.aggregate_hash,
            "compatibility_keys": self.compatibility_keys,
            "counts": self.counts,
            "exclusions": self.exclusions,
            "fit_diagnostics": self.fit_diagnostics,
            "decision_results": self.decision_results,
            "source_provenance": self.source_provenance,
        }

    def to_hash_kwargs(self) -> dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "attempt_hash": self.attempt_hash,
            "aggregate_hash": self.aggregate_hash,
            "config_fingerprint": self.config_fingerprint,
            "decision_status": self.decision_status,
            "groups": self.groups,
            "compatibility_keys": self.compatibility_keys,
            "counts": self.counts,
            "exclusions": self.exclusions,
            "fit_diagnostics": self.fit_diagnostics,
            "decision_results": self.decision_results,
            "source_provenance": self.source_provenance,
            "parent_hash": self.parent_hash,
        }


@dataclass(frozen=True)
class GenerationSnapshot:
    generation_id: int
    generation_hash: str
    decision_status: str
    posteriors: tuple[FamilyPosterior, ...]
    taxonomy_version: str = ""
    corpus_hash: str = ""
    content_valid: bool = False


def generation_content_hash(**kwargs: Any) -> str:
    groups = kwargs.get("groups")
    if groups is not None:
        kwargs = {
            **kwargs,
            "groups": [
                {k: v for k, v in group.items() if k != "fitted_at"}
                for group in groups
            ],
        }
    canonical = json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def manifest_from_generation(
    generation: PosteriorGeneration,
    posteriors: list[FamilyPosterior] | None = None,
) -> GenerationManifest:
    posteriors = posteriors or list(
        FamilyPosterior.objects.filter(generation=generation).order_by("task_family", "route")
    )
    payload = generation.payload or {}
    canonical = payload.get("_canonical_hash_inputs")
    if isinstance(canonical, dict):
        return GenerationManifest(
            input_hash=canonical["input_hash"],
            attempt_hash=canonical["attempt_hash"],
            aggregate_hash=canonical["aggregate_hash"],
            config_fingerprint=canonical["config_fingerprint"],
            decision_status=canonical["decision_status"],
            groups=canonical["groups"],
            compatibility_keys=canonical.get("compatibility_keys") or {},
            counts=canonical.get("counts") or {},
            exclusions=canonical.get("exclusions") or {},
            fit_diagnostics=canonical.get("fit_diagnostics") or {},
            decision_results=canonical.get("decision_results") or {},
            source_provenance=canonical.get("source_provenance") or {},
            parent_hash=canonical.get("parent_hash"),
        )
    groups = [
        {
            "name": p.task_family,
            "route": p.route,
            "posterior_mean": p.posterior_mean,
            "band": p.band,
            "n_total": p.n_total,
            "fitted_at": p.fitted_at.isoformat() if p.fitted_at else None,
        }
        for p in posteriors
    ]
    parent_hash = generation.parent.generation_hash if generation.parent_id else None
    return GenerationManifest(
        input_hash=generation.input_hash,
        attempt_hash=str(payload.get("attempt_hash") or generation.input_hash),
        aggregate_hash=str(payload.get("aggregate_hash") or generation.input_hash),
        config_fingerprint=generation.config_fingerprint,
        decision_status=generation.decision_status,
        groups=groups,
        compatibility_keys=dict(payload.get("compatibility_keys") or {}),
        counts={k: int(v) for k, v in (payload.get("counts") or {}).items()},
        exclusions={k: int(v) for k, v in (payload.get("exclusions") or {}).items()},
        fit_diagnostics=dict(payload.get("fit_diagnostics") or {}),
        decision_results=dict(payload.get("decision_results") or {}),
        source_provenance=dict(payload.get("source_provenance") or {}),
        parent_hash=parent_hash,
    )


def require_publish_permission(actor: str = "local") -> None:
    if actor.startswith("live:"):
        raise PermissionError("live publish requires maintainer approval")


def require_activate_permission(actor: str = "local") -> None:
    if actor.startswith("live:"):
        raise PermissionError("live activation requires maintainer approval")


def create_generation(
    manifest: GenerationManifest,
    *,
    parent: PosteriorGeneration | None = None,
    actor: str = "local",
) -> PosteriorGeneration:
    require_publish_permission(actor)
    parent_hash = parent.generation_hash if parent else None
    if parent_hash != manifest.parent_hash:
        manifest = replace(manifest, parent_hash=parent_hash)
    gen_hash = generation_content_hash(**manifest.to_hash_kwargs())
    existing = PosteriorGeneration.objects.filter(generation_hash=gen_hash).first()
    if existing is not None:
        return existing
    existing_input = PosteriorGeneration.objects.filter(input_hash=manifest.input_hash).first()
    if existing_input is not None and existing_input.generation_hash != gen_hash:
        raise PublishError("overwrite refused — input_hash already bound to a different generation")
    payload = manifest.to_payload()
    payload["partial_publish"] = False
    payload["_canonical_hash_inputs"] = manifest.to_hash_kwargs()
    with transaction.atomic():
        generation = PosteriorGeneration.objects.create(
            generation_hash=gen_hash,
            input_hash=manifest.input_hash,
            config_fingerprint=manifest.config_fingerprint,
            decision_status=manifest.decision_status,
            payload=payload,
            parent=parent,
        )
        if _should_abort_publish_after_generation():
            raise PublishAbort(
                "test abort after generation row before family posteriors"
            )
        fitted_at = dj_timezone.now()
        for group in manifest.groups:
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


def _publish_authenticated_generation(
    manifest: GenerationManifest,
    *,
    capability: _AuthenticatedHumanPublishCapability,
    actor: str = "local",
) -> PosteriorGeneration:
    if capability is not _AUTHENTICATED_HUMAN_PUBLISH_CAPABILITY:
        raise PublishError("invalid authenticated human-fit publication capability")
    from nextseek_api.eval.fit.fit_boundary import validate_publish_provenance

    validate_publish_provenance(dict(manifest.source_provenance or {}))
    return create_generation(manifest, actor=actor)


def publish_generation(manifest: GenerationManifest, *, actor: str = "local") -> PosteriorGeneration:
    del manifest, actor
    raise PublishError(
        "direct generation publication is disabled; use the authenticated human-fit publisher"
    )


def get_current_active_hash() -> str:
    pointer = ActiveGenerationPointer.objects.select_related("active").first()
    if pointer is None or pointer.active_id is None:
        return EMPTY_ACTIVE_HASH
    return pointer.active.generation_hash


def get_active_snapshot() -> GenerationSnapshot | None:
    pointer = ActiveGenerationPointer.objects.select_related("active").first()
    if pointer is None or pointer.active_id is None:
        return None
    return _snapshot_for_generation(pointer.active)


def _snapshot_for_generation(generation: PosteriorGeneration) -> GenerationSnapshot:
    posteriors = tuple(
        FamilyPosterior.objects.filter(generation=generation).order_by("task_family", "route")
    )
    manifest = manifest_from_generation(generation, list(posteriors))
    compatibility = manifest.compatibility_keys
    from nextseek_api.eval.generation_validation import validate_generation_for_activation

    validation = validate_generation_for_activation(generation, require_current_compatibility=False)
    return GenerationSnapshot(
        generation_id=generation.id,
        generation_hash=generation.generation_hash,
        decision_status=generation.decision_status,
        posteriors=posteriors,
        taxonomy_version=str(compatibility.get("taxonomy_version") or ""),
        corpus_hash=str(compatibility.get("corpus_hash") or ""),
        content_valid=validation.ok,
    )


def _record_audit(
    *,
    action: str,
    previous_hash: str,
    active_hash: str,
    activated_by: str,
) -> None:
    GenerationActivationAudit.objects.create(
        action=action,
        previous_hash=previous_hash,
        active_hash=active_hash,
        activated_by=activated_by,
        isolation_level=getattr(connection, "isolation_level", "") or "",
    )


def activate_generation(
    generation: PosteriorGeneration,
    *,
    expected_hash: str,
    activated_by: str = "local",
) -> ActiveGenerationPointer:
    from nextseek_api.eval.generation_validation import require_valid_for_activation
    from nextseek_api.eval.fit.fit_boundary import validate_publish_provenance

    require_activate_permission(activated_by)
    with transaction.atomic():
        # Lock the singleton first, matching rollback's lock order. Then lock
        # the target and current generation rows in primary-key order so
        # activation cannot deadlock with another pointer mutation.
        pointer, _ = ActiveGenerationPointer.objects.select_for_update().get_or_create(
            pk=1,
            defaults={"expected_hash": EMPTY_ACTIVE_HASH},
        )
        generation_ids = {generation.pk}
        if pointer.active_id is not None:
            generation_ids.add(pointer.active_id)
        locked_generations = {
            candidate.pk: candidate
            for candidate in PosteriorGeneration.objects.select_for_update()
            .filter(pk__in=generation_ids)
            .order_by("pk")
        }
        locked_generation = locked_generations[generation.pk]
        locked_current = locked_generations.get(pointer.active_id)

        # Lock the current child rows before validating. Validation deliberately
        # re-queries them, but these locks keep its DB-backed identity snapshot
        # stable until the active pointer and audit row are committed.
        list(
            FamilyPosterior.objects.select_for_update()
            .filter(generation=locked_generation)
            .order_by("task_family", "route")
        )

        payload = locked_generation.payload or {}
        canonical = payload.get("_canonical_hash_inputs") or {}
        provenance = dict(
            canonical.get("source_provenance")
            or payload.get("source_provenance")
            or {}
        )
        validate_publish_provenance(provenance)
        require_valid_for_activation(locked_generation)

        locked_hash = (
            locked_current.generation_hash
            if locked_current is not None
            else EMPTY_ACTIVE_HASH
        )
        if expected_hash != locked_hash:
            raise ActivationError("stale CAS — expected hash does not match active generation")
        previous_hash = locked_hash
        pointer.previous = pointer.active
        pointer.active = locked_generation
        pointer.expected_hash = locked_generation.generation_hash
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
        if _should_abort_activate_after_pointer_mutate():
            raise ActivationAbort("test abort after pointer mutate before audit")
        _record_audit(
            action="activate",
            previous_hash=previous_hash,
            active_hash=locked_generation.generation_hash,
            activated_by=activated_by,
        )
    return pointer


def rollback_generation(*, expected_hash: str, activated_by: str = "local") -> ActiveGenerationPointer:
    require_activate_permission(activated_by)
    with transaction.atomic():
        pointer = ActiveGenerationPointer.objects.select_for_update().get(pk=1)
        current_hash = pointer.active.generation_hash if pointer.active_id else EMPTY_ACTIVE_HASH
        if expected_hash != current_hash:
            raise ActivationError("stale CAS — rollback refused")
        if pointer.previous_id is None:
            raise ActivationError("rollback refused — no previous generation")
        previous_hash = current_hash
        pointer.active = pointer.previous
        pointer.previous = None
        pointer.expected_hash = pointer.active.generation_hash
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
        _record_audit(
            action="rollback",
            previous_hash=previous_hash,
            active_hash=pointer.active.generation_hash,
            activated_by=activated_by,
        )
    return pointer


def pin_generation_for_turn(turn: TurnLedger) -> GenerationSnapshot:
    snapshot = get_active_snapshot()
    if snapshot is None:
        raise ActivationError("cannot pin generation — no active generation")
    turn.pinned_generation_id = snapshot.generation_id
    turn.pinned_generation_hash = snapshot.generation_hash
    turn.save(update_fields=["pinned_generation_id", "pinned_generation_hash"])
    return snapshot


def get_pinned_snapshot_for_turn(turn: TurnLedger) -> GenerationSnapshot | None:
    if not turn.pinned_generation_id or not turn.pinned_generation_hash:
        return None
    generation = PosteriorGeneration.objects.filter(id=turn.pinned_generation_id).first()
    if generation is None or generation.generation_hash != turn.pinned_generation_hash:
        return None
    return _snapshot_for_generation(generation)
