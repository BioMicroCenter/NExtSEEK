"""Publish fit results into the immutable generation store (V4-5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nextseek_api.eval.generation_store import create_generation

__all__ = ["FitGroup", "FitResult", "publish"]


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
    config_fingerprint: str = ""
    decision_status: str = "activated_all"
    payload: dict | None = None


def _band_from_status(status: str) -> str:
    if status in {
        "legacy_fallback",
        "indecisive",
        "multiplicity_indecisive",
        "too_uncertain",
    }:
        return "TooUncertain"
    if status.startswith("quality_") or status.startswith("latency_"):
        return "Reliable"
    if status == "unrelated_canned":
        return "Brittle"
    return "Watch"


def _route_from_status(status: str, default: str = "container_cc") -> str:
    if status.endswith("_ns"):
        return "nextseek_query"
    if status.endswith("_cc"):
        return "container_cc"
    return default


def _groups_from_combined(fit_result) -> tuple[list[FitGroup], str, str, str, dict | None]:
    from nextseek_api.eval.fit.v14.combined import CombinedFitResult

    if not isinstance(fit_result, CombinedFitResult):
        raise TypeError(f"unsupported fit result type: {type(fit_result)!r}")
    groups = [
        FitGroup(
            name=candidate.family,
            route=_route_from_status(candidate.status.value),
            posterior_mean=max(0.0, 1.0 - candidate.local_error_prob),
            band=_band_from_status(candidate.status.value),
            n_total=1,
        )
        for candidate in fit_result.decision.candidates
    ]
    payload = {
        "activated_families": list(fit_result.decision.activated_families),
        "posterior_expected_fdr": fit_result.decision.posterior_expected_fdr,
    }
    fp = fit_result.decision.config_fingerprint
    return groups, fp, fp, fit_result.decision.generation_status, payload


def publish(fit_result: FitResult | object) -> int:
    """Create or return an immutable generation; returns group count."""
    if isinstance(fit_result, FitResult):
        groups = fit_result.groups
        input_hash = fit_result.input_hash or "local"
        config_fingerprint = fit_result.config_fingerprint or "local"
        decision_status = fit_result.decision_status
        payload = fit_result.payload
    else:
        groups, input_hash, config_fingerprint, decision_status, payload = _groups_from_combined(
            fit_result
        )

    group_payloads = [
        {
            "name": g.name,
            "route": g.route,
            "posterior_mean": g.posterior_mean,
            "band": g.band,
            "n_total": g.n_total,
            "fitted_at": g.fitted_at,
        }
        for g in groups
    ]
    create_generation(
        input_hash=input_hash,
        config_fingerprint=config_fingerprint,
        decision_status=decision_status,
        groups=group_payloads,
        payload=payload,
    )
    return len(groups)
