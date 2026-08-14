"""Orchestration for V4-4 pair-preserving fit + decision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence, cast

from nextseek_api.eval.fit.v14.decision import (
    GenerationDecision,
    evaluate_generation,
    legacy_fallback,
    retained_support_ok,
)
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, config_fingerprint
from nextseek_api.eval.fit.v14.latency_model import (
    DescriptiveLatencyResult,
    LatencyFitResult,
    fit_latency_model,
)
from nextseek_api.eval.fit.v14.pair_rows import PairFitRow
from nextseek_api.eval.fit.v14.quality_model import (
    DescriptiveQualityResult,
    QualityFitResult,
    fit_quality_models,
)

if TYPE_CHECKING:
    from nextseek_api.eval.paired_run import PairedExperimentalBatch

__all__ = ["CombinedFitResult", "run_v14_generation"]


@dataclass(frozen=True)
class CombinedFitResult:
    quality: dict[str, QualityFitResult] | dict[str, DescriptiveQualityResult]
    latency: dict[str, LatencyFitResult | DescriptiveLatencyResult]
    decision: GenerationDecision
    diagnostics_ok: bool
    quality_mcmc: bool = True
    latency_mcmc: bool = True


def run_v14_generation(
    rows: Sequence[PairFitRow],
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
    quality_use_mcmc: bool | None = None,
    latency_use_mcmc: bool | None = None,
    paired_batch: "PairedExperimentalBatch | None" = None,
) -> CombinedFitResult:
    if paired_batch is not None:
        from nextseek_api.eval.fit.fit_boundary import (
            assert_paired_experimental_only,
            require_approved_paired_run,
        )

        assert_paired_experimental_only(paired_batch)
        require_approved_paired_run(paired_batch.paired_run_id)
    quality_mcmc = use_mcmc if quality_use_mcmc is None else quality_use_mcmc
    latency_mcmc = use_mcmc if latency_use_mcmc is None else latency_use_mcmc
    families = sorted({r.family for r in rows})
    quality = fit_quality_models(rows, cfg, seed=seed, use_mcmc=quality_mcmc)
    latency: dict[str, LatencyFitResult | DescriptiveLatencyResult] = {}
    for i, fam in enumerate(families):
        lat_mcmc = latency_mcmc and retained_support_ok(rows, fam, cfg)
        latency[fam] = fit_latency_model(rows, fam, cfg, seed=seed + 100 + i, use_mcmc=lat_mcmc)
    fp = config_fingerprint(cfg)
    if quality_mcmc:
        if not all(isinstance(item, QualityFitResult) for item in quality.values()):
            raise TypeError("MCMC quality fit returned a descriptive result")
        posterior_quality = cast(dict[str, QualityFitResult], quality)
        decision = evaluate_generation(
            rows,
            posterior_quality,
            latency,
            cfg,
            config_fingerprint=fp,
        )
    else:
        candidates = tuple(legacy_fallback(family) for family in families)
        decision = GenerationDecision(
            candidates=candidates,
            posterior_expected_fdr=None,
            activated_families=(),
            generation_status="profile_only",
            config_fingerprint=fp,
        )

    quality_diag_ok = False
    if quality_mcmc:
        quality_diag_ok = all(
            q.divergences == 0 and q.rhat_max <= cfg.rhat_max and q.ess_bulk_min >= cfg.ess_min and q.ess_tail_min >= cfg.ess_min
            for q in posterior_quality.values()
        )
    latency_diag_ok = True
    if latency_mcmc:
        latency_diag_ok = all(
            isinstance(latency_fit, LatencyFitResult)
            and latency_fit.divergences == 0
            and latency_fit.rhat_max <= cfg.rhat_max
            and latency_fit.ess_bulk_min >= cfg.ess_min
            and latency_fit.ess_tail_min >= cfg.ess_min
            for fam, latency_fit in latency.items()
            if retained_support_ok(rows, fam, cfg)
        )
    return CombinedFitResult(
        quality=quality,
        latency=latency,
        decision=decision,
        diagnostics_ok=quality_diag_ok and latency_diag_ok,
        quality_mcmc=quality_mcmc,
        latency_mcmc=latency_mcmc,
    )
