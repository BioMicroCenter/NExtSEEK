"""Publish fit results into the immutable generation store (V4-5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nextseek_api.eval.generation_store import GenerationManifest

__all__ = [
    "FitGroup",
    "FitResult",
    "PublicationEvidence",
    "PublicationEvidenceRequired",
    "manifest_for_combined",
    "publish",
]


class PublicationEvidenceRequired(ValueError):
    """A statistical result is not evidence provenance and cannot invent it."""


@dataclass(frozen=True)
class PublicationEvidence:
    input_hash: str
    attempt_hash: str
    aggregate_hash: str
    compatibility_keys: dict[str, str]
    counts: dict[str, int]
    exclusions: dict[str, int]
    fit_diagnostics: dict[str, Any]
    source_provenance: dict[str, Any]
    family_retained_pairs: dict[str, int]

    def validate(
        self,
        *,
        for_publication: bool,
        allow_initial_release_override: bool = False,
    ) -> None:
        missing_hashes = [
            name
            for name in ("input_hash", "attempt_hash", "aggregate_hash")
            if not getattr(self, name)
        ]
        if missing_hashes:
            raise PublicationEvidenceRequired(f"missing evidence hashes: {missing_hashes}")
        if set(self.compatibility_keys) != {"taxonomy_version", "corpus_hash"}:
            raise PublicationEvidenceRequired(
                "compatibility_keys must contain exactly taxonomy_version and corpus_hash"
            )
        if int(self.counts.get("retained_pairs", -1)) < 0:
            raise PublicationEvidenceRequired("counts.retained_pairs is required")
        required_provenance = {
            "paired_run_id",
            "paired_run_content_hash",
            "evidence_kind",
            "route_source",
            "model_mode",
            "functional_success_source",
        }
        missing = required_provenance - set(self.source_provenance)
        if missing:
            raise PublicationEvidenceRequired(f"missing source provenance: {sorted(missing)}")
        if self.source_provenance.get("functional_success_source") == "human_grades":
            if self.source_provenance.get("judge_calls_used") != 0:
                raise PublicationEvidenceRequired(
                    "human-grade fit must record judge_calls_used=0"
                )
        authoritative = self.fit_diagnostics.get("authoritative") is True
        diagnostics_ok = self.fit_diagnostics.get("diagnostics_ok") is True
        legacy_stack = (
            self.source_provenance.get("stack_identity_status")
            == "legacy_git_sha_only"
        )
        if for_publication and not (authoritative and diagnostics_ok):
            is_honest_initial_release = (
                allow_initial_release_override
                and self.source_provenance.get("model_mode") == "initial_human_grade"
                and self.source_provenance.get("initial_release_override") is True
                and not authoritative
            )
            if not is_honest_initial_release:
                raise PublicationEvidenceRequired(
                    "publication requires authoritative diagnostics, or an explicit "
                    "initial_human_grade release override"
                )
        if for_publication and legacy_stack:
            is_explicit_initial_release = (
                allow_initial_release_override
                and self.source_provenance.get("model_mode") == "initial_human_grade"
                and self.source_provenance.get("initial_release_override") is True
            )
            if not is_explicit_initial_release:
                raise PublicationEvidenceRequired(
                    "legacy git-SHA-only stack identity requires an explicit "
                    "initial_human_grade release override"
                )


@dataclass
class FitGroup:
    name: str
    route: str
    posterior_mean: float
    band: str
    n_total: int
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


def manifest_for_combined(
    fit_result: object,
    evidence: PublicationEvidence,
    *,
    for_publication: bool = False,
    allow_initial_release_override: bool = False,
) -> GenerationManifest:
    from nextseek_api.eval.fit.v14.combined import CombinedFitResult
    from nextseek_api.eval.fit.v14.decision import decision_status_to_band

    if not isinstance(fit_result, CombinedFitResult):
        raise TypeError(f"unsupported fit result type: {type(fit_result)!r}")
    evidence.validate(
        for_publication=for_publication,
        allow_initial_release_override=allow_initial_release_override,
    )
    groups = [
        FitGroup(
            name=candidate.family,
            route=_route_from_status(candidate.status.value),
            posterior_mean=max(0.0, 1.0 - candidate.local_error_prob),
            band=decision_status_to_band(candidate.status.value),
            n_total=int(evidence.family_retained_pairs.get(candidate.family, 0)),
        )
        for candidate in fit_result.decision.candidates
    ]
    payload_extra = {
        "activated_families": list(fit_result.decision.activated_families),
        "posterior_expected_fdr": fit_result.decision.posterior_expected_fdr,
    }
    missing_families = [group.name for group in groups if group.n_total < 1]
    if missing_families:
        raise PublicationEvidenceRequired(
            f"missing retained-pair counts for fit families: {missing_families}"
        )
    manifest = GenerationManifest(
        input_hash=evidence.input_hash,
        attempt_hash=evidence.attempt_hash,
        aggregate_hash=evidence.aggregate_hash,
        config_fingerprint=fit_result.decision.config_fingerprint,
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
        compatibility_keys=dict(evidence.compatibility_keys),
        counts=dict(evidence.counts),
        exclusions=dict(evidence.exclusions),
        fit_diagnostics=dict(evidence.fit_diagnostics),
        decision_results=payload_extra,
        source_provenance=dict(evidence.source_provenance),
    )
    return manifest


def _manifest_from_fit_result(fit_result: FitResult) -> GenerationManifest:
    payload = dict(fit_result.payload or {})
    compat = fit_result.compatibility_keys or payload.pop("compatibility_keys", {})
    counts = fit_result.counts or payload.pop("counts", {})
    provenance = fit_result.source_provenance or payload.pop("source_provenance", {})
    missing = [
        name
        for name in ("input_hash", "attempt_hash", "aggregate_hash", "config_fingerprint")
        if not getattr(fit_result, name)
    ]
    if missing:
        raise PublicationEvidenceRequired(f"FitResult missing explicit fields: {missing}")
    if set(compat) != {"taxonomy_version", "corpus_hash"}:
        raise PublicationEvidenceRequired(
            "FitResult requires exact taxonomy_version/corpus_hash compatibility keys"
        )
    if "retained_pairs" not in counts:
        raise PublicationEvidenceRequired("FitResult requires counts.retained_pairs")
    if not provenance:
        raise PublicationEvidenceRequired("FitResult requires explicit source_provenance")
    return GenerationManifest(
        input_hash=fit_result.input_hash,
        attempt_hash=fit_result.attempt_hash,
        aggregate_hash=fit_result.aggregate_hash,
        config_fingerprint=fit_result.config_fingerprint,
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
        source_provenance=provenance,
    )


def publish(
    fit_result: FitResult | object,
    *,
    evidence: PublicationEvidence | None = None,
    allow_initial_release_override: bool = False,
) -> int:
    """Refuse public publication; the release path is ``publish_human_grade_fit``."""
    if isinstance(fit_result, FitResult):
        raise PublicationEvidenceRequired(
            "generic FitResult publication is disabled for this release"
        )
    raise PublicationEvidenceRequired(
        "direct CombinedFitResult publication is disabled; use publish_human_grade_fit"
    )
