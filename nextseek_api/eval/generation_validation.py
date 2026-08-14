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


def _canonical_groups(groups) -> list[dict]:
    return sorted(
        [
            {
                "name": str(group.get("name")),
                "route": str(group.get("route")),
                "posterior_mean": float(group.get("posterior_mean")),
                "band": str(group.get("band")),
                "n_total": int(group.get("n_total")),
            }
            for group in groups
        ],
        key=lambda group: (group["name"], group["route"]),
    )


def validate_generation_for_activation(
    generation: PosteriorGeneration,
    *,
    require_current_compatibility: bool = True,
) -> ValidationResult:
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
    canonical = payload.get("_canonical_hash_inputs")
    if not isinstance(canonical, dict):
        reasons.append("hash: canonical hash inputs missing")
    else:
        stored_generation_fields = {
            "input_hash": generation.input_hash,
            "config_fingerprint": generation.config_fingerprint,
            "decision_status": generation.decision_status,
            "parent_hash": (
                generation.parent.generation_hash if generation.parent_id else None
            ),
        }
        for key, stored_value in stored_generation_fields.items():
            if stored_value != canonical.get(key):
                reasons.append(
                    f"hash: stored {key} differs from canonical generation"
                )

        actual_groups = _canonical_groups(
            {
                "name": row.task_family,
                "route": row.route,
                "posterior_mean": row.posterior_mean,
                "band": row.band,
                "n_total": row.n_total,
            }
            for row in posteriors
        )
        if actual_groups != _canonical_groups(canonical.get("groups") or []):
            reasons.append("hash: family posterior rows differ from canonical generation")
        for key in (
            "attempt_hash",
            "aggregate_hash",
            "compatibility_keys",
            "counts",
            "exclusions",
            "fit_diagnostics",
            "decision_results",
            "source_provenance",
        ):
            if (payload.get(key) or {}) != (canonical.get(key) or {}):
                reasons.append(f"hash: payload {key} differs from canonical generation")

    compat = payload.get("compatibility_keys") or {}
    required_compat = {"taxonomy_version", "corpus_hash"}
    missing_compat = required_compat - set(compat)
    if missing_compat:
        reasons.append(f"compatibility: missing keys {sorted(missing_compat)}")
    elif require_current_compatibility:
        try:
            from nextseek_api.cc_assistant.family_labels import corpus_snapshot

            current = corpus_snapshot()
            if str(compat.get("taxonomy_version")) != current.taxonomy_version:
                reasons.append("compatibility: taxonomy_version does not match current corpus")
            if str(compat.get("corpus_hash")) != current.corpus_sha256:
                reasons.append("compatibility: corpus_hash does not match current corpus")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"compatibility: current corpus unavailable ({type(exc).__name__})")

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
