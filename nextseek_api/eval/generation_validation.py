"""Pre-activation validation for posterior generations (V4-5)."""
from __future__ import annotations

from dataclasses import dataclass

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration

__all__ = [
    "ValidationError",
    "ValidationResult",
    "validate_generation_for_activation",
]

ALLOWED_DECISION_STATUSES = frozenset(
    {
        "activated_all",
        "empty_candidate_set",
        "multiplicity_indecisive",
        "legacy_fallback",
    }
)


class ValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...] = ()


def validate_generation_for_activation(generation: PosteriorGeneration) -> ValidationResult:
    from nextseek_api.eval.generation_store import generation_content_hash, manifest_from_generation

    reasons: list[str] = []

    posteriors = list(
        FamilyPosterior.objects.filter(generation=generation).order_by("task_family", "route")
    )
    if not posteriors and generation.decision_status not in {
        "empty_candidate_set",
        "multiplicity_indecisive",
    }:
        reasons.append("schema: generation has no family posteriors")

    manifest = manifest_from_generation(generation, posteriors)
    recomputed = generation_content_hash(**manifest.to_hash_kwargs())
    if recomputed != generation.generation_hash:
        reasons.append("hash: content hash does not match stored generation_hash")

    payload = generation.payload or {}
    compat = payload.get("compatibility_keys") or {}
    required_compat = {"taxonomy_version", "corpus_hash"}
    missing_compat = required_compat - set(compat)
    if missing_compat:
        reasons.append(f"compatibility: missing keys {sorted(missing_compat)}")

    if generation.parent_id:
        parent_hash = generation.parent.generation_hash
        payload_parent = payload.get("parent_hash")
        if payload_parent and payload_parent != parent_hash:
            reasons.append("parent: payload parent_hash mismatch")

    if payload.get("stale") is True:
        reasons.append("staleness: generation marked stale")

    counts = payload.get("counts") or {}
    min_pairs = int(payload.get("min_retained_pairs") or counts.get("min_retained_pairs") or 5)
    retained = int(counts.get("retained_pairs") or 0)
    if retained < min_pairs and generation.decision_status == "activated_all":
        reasons.append("precision: retained_pairs below minimum for activated_all")

    for row in posteriors:
        if row.n_total < 1:
            reasons.append(f"precision: n_total below floor for {row.task_family}/{row.route}")

    if generation.decision_status not in ALLOWED_DECISION_STATUSES:
        reasons.append(f"decision_status: {generation.decision_status!r} not activatable")

    if payload.get("partial_publish") is True:
        reasons.append("partial publication refused")

    if payload.get("filename_only_validation") is True:
        reasons.append("filename-only validation refused")

    return ValidationResult(ok=not reasons, reasons=tuple(reasons))


def require_valid_for_activation(generation: PosteriorGeneration) -> None:
    result = validate_generation_for_activation(generation)
    if not result.ok:
        raise ValidationError("; ".join(result.reasons))
