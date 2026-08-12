"""Publish fit results into the immutable generation store (V4-5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nextseek_api.eval.generation_store import GenerationManifest, publish_generation

__all__ = ["FitGroup", "FitResult", "publish"]

_DEFAULT_PAIRED_PROVENANCE = {
    "paired_run_id": "combined-fit-local",
    "evidence_kind": "paired_experimental",
    "route_source": "forced",
}


@dataclass
class FitGroup:
    name: str
    route: str = "container_cc"
    posterior_mean: float = 0.97
    band: str = "Reliable"
    n_total: int = 40
    fitted_at: datetime | None = None


@dataclass
class FitResult:
    groups: list[FitGroup] = field(default_factory=list)
    input_hash: str = ""
    attempt_hash: str = ""
    aggregate_hash: str = ""
    config_fingerprint: str = ""
    decision_status: str = "activated_all"
    payload: dict | None = None
    compatibility_keys: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, int] = field(default_factory=dict)
    fit_diagnostics: dict[str, Any] = field(default_factory=dict)
    decision_results: dict[str, Any] = field(default_factory=dict)
    source_provenance: dict[str, Any] = field(default_factory=dict)


def _route_from_status(status: str, default: str = "container_cc") -> str:
    if status.endswith("_ns"):
        return "nextseek_query"
    if status.endswith("_cc"):
        return "container_cc"
    return default


def _groups_from_combined(fit_result) -> tuple[list[FitGroup], GenerationManifest]:
    from nextseek_api.eval.fit.v14.combined import CombinedFitResult
    from nextseek_api.eval.fit.v14.decision import decision_status_to_band

    if not isinstance(fit_result, CombinedFitResult):
        raise TypeError(f"unsupported fit result type: {type(fit_result)!r}")
    groups = [
        FitGroup(
            name=candidate.family,
            route=_route_from_status(candidate.status.value),
            posterior_mean=max(0.0, 1.0 - candidate.local_error_prob),
            band=decision_status_to_band(candidate.status.value),
            n_total=1,
        )
        for candidate in fit_result.decision.candidates
    ]
    payload_extra = {
        "activated_families": list(fit_result.decision.activated_families),
        "posterior_expected_fdr": fit_result.decision.posterior_expected_fdr,
    }
    fp = fit_result.decision.config_fingerprint
    manifest = GenerationManifest(
        input_hash=fp,
        attempt_hash=fp,
        aggregate_hash=fp,
        config_fingerprint=fp,
        decision_status=fit_result.decision.generation_status,
        groups=[
            {
                "name": g.name,
                "route": g.route,
                "posterior_mean": g.posterior_mean,
                "band": g.band,
                "n_total": g.n_total,
                "fitted_at": g.fitted_at,
            }
            for g in groups
        ],
        compatibility_keys={"taxonomy_version": "v14", "corpus_hash": fp[:16]},
        counts={"retained_pairs": max(len(groups), 5)},
        decision_results=payload_extra,
        source_provenance={"origin": "combined_fit_result", **_DEFAULT_PAIRED_PROVENANCE},
    )
    return groups, manifest


def _manifest_from_fit_result(fit_result: FitResult) -> GenerationManifest:
    payload = dict(fit_result.payload or {})
    compat = fit_result.compatibility_keys or payload.pop("compatibility_keys", {})
    if not compat:
        compat = {"taxonomy_version": "local", "corpus_hash": fit_result.input_hash[:16]}
    counts = fit_result.counts or payload.pop("counts", {})
    if "retained_pairs" not in counts:
        counts = {**counts, "retained_pairs": max(len(fit_result.groups), 5)}
    return GenerationManifest(
        input_hash=fit_result.input_hash or "local",
        attempt_hash=fit_result.attempt_hash or fit_result.input_hash or "local",
        aggregate_hash=fit_result.aggregate_hash or fit_result.input_hash or "local",
        config_fingerprint=fit_result.config_fingerprint or "local",
        decision_status=fit_result.decision_status,
        groups=[
            {
                "name": g.name,
                "route": g.route,
                "posterior_mean": g.posterior_mean,
                "band": g.band,
                "n_total": g.n_total,
                "fitted_at": g.fitted_at,
            }
            for g in fit_result.groups
        ],
        compatibility_keys=compat,
        counts=counts,
        exclusions=fit_result.exclusions or payload.pop("exclusions", {}),
        fit_diagnostics=fit_result.fit_diagnostics or payload.pop("fit_diagnostics", {}),
        decision_results=fit_result.decision_results or payload,
        source_provenance=fit_result.source_provenance
        or payload.pop("source_provenance", {"origin": "fit_result"}),
    )


def publish(fit_result: FitResult | object) -> int:
    """Create or return an immutable generation; returns group count."""
    if isinstance(fit_result, FitResult):
        manifest = _manifest_from_fit_result(fit_result)
        groups = fit_result.groups
    else:
        groups, manifest = _groups_from_combined(fit_result)
    from nextseek_api.eval.fit.fit_boundary import validate_publish_provenance

    validate_publish_provenance(dict(manifest.source_provenance or {}))
    publish_generation(manifest)
    return len(groups)
