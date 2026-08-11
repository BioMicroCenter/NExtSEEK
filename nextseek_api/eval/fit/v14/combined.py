"""Orchestration for V4-4 pair-preserving fit + decision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from nextseek_api.eval.fit.v14.decision import GenerationDecision, evaluate_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, config_fingerprint
from nextseek_api.eval.fit.v14.latency_model import LatencyFitResult, fit_latency_model
from nextseek_api.eval.fit.v14.pair_rows import PairFitRow
from nextseek_api.eval.fit.v14.quality_model import QualityFitResult, fit_quality_model

__all__ = ["CombinedFitResult", "run_v14_generation"]


@dataclass(frozen=True)
class CombinedFitResult:
    quality: dict[str, QualityFitResult]
    latency: dict[str, LatencyFitResult]
    decision: GenerationDecision
    diagnostics_ok: bool


def run_v14_generation(
    rows: Sequence[PairFitRow],
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
) -> CombinedFitResult:
    families = sorted({r.family for r in rows})
    quality: dict[str, QualityFitResult] = {}
    latency: dict[str, LatencyFitResult] = {}
    for i, fam in enumerate(families):
        quality[fam] = fit_quality_model(rows, fam, cfg, seed=seed + i, use_mcmc=use_mcmc)
        latency[fam] = fit_latency_model(rows, fam, cfg, seed=seed + 100 + i, use_mcmc=use_mcmc)
    fp = config_fingerprint(cfg)
    decision = evaluate_generation(rows, quality, latency, cfg, config_fingerprint=fp)
    diag_ok = all(
        q.divergences == 0 and q.rhat_max <= cfg.rhat_max and q.ess_bulk_min >= cfg.ess_min and q.ess_tail_min >= cfg.ess_min
        for q in quality.values()
    ) and all(
        l.divergences == 0 and l.rhat_max <= cfg.rhat_max and l.ess_bulk_min >= cfg.ess_min and l.ess_tail_min >= cfg.ess_min
        for l in latency.values()
    )
    return CombinedFitResult(quality=quality, latency=latency, decision=decision, diagnostics_ok=diag_ok)
