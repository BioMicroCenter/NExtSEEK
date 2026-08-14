"""V14 operational decision contract + complete-set FDR."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np

from nextseek_api.eval.conservation import FitAdmission, SupportGateConfig, check_support_gate
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.latency_model import (
    DescriptiveLatencyResult,
    LatencyFitResult,
    latency_win_probability,
)
from nextseek_api.eval.fit.v14.pair_rows import JointQualityState, PairFitRow
from nextseek_api.eval.fit.v14.quality_model import QualityFitResult

__all__ = [
    "CandidateDecision",
    "DecisionStatus",
    "GenerationDecision",
    "apply_complete_set_fdr",
    "decide_family",
    "decision_status_to_band",
    "discordant_pair_count",
    "evaluate_generation",
    "legacy_fallback",
    "quality_discordance_ok",
    "retained_support_ok",
    "unrelated_spend_gate_path",
]


class DecisionStatus(str, Enum):
    quality_ns = "quality_ns"
    quality_cc = "quality_cc"
    quality_equivalent_ns = "quality_equivalent_ns"
    latency_ns = "latency_ns"
    latency_cc = "latency_cc"
    indecisive = "indecisive"
    legacy_fallback = "legacy_fallback"
    multiplicity_indecisive = "multiplicity_indecisive"
    too_uncertain = "too_uncertain"
    unrelated_canned = "unrelated_canned"


@dataclass(frozen=True)
class CandidateDecision:
    family: str
    status: DecisionStatus
    local_error_prob: float
    activated: bool = False


@dataclass(frozen=True)
class GenerationDecision:
    candidates: tuple[CandidateDecision, ...]
    posterior_expected_fdr: float | None
    activated_families: tuple[str, ...]
    generation_status: str
    config_fingerprint: str


def legacy_fallback(family: str) -> CandidateDecision:
    return CandidateDecision(family=family, status=DecisionStatus.legacy_fallback, local_error_prob=0.0)


def unrelated_spend_gate_path(family: str) -> CandidateDecision:
    return CandidateDecision(family=family, status=DecisionStatus.unrelated_canned, local_error_prob=1.0)


def decision_status_to_band(status: str) -> str:
    if status in {
        DecisionStatus.legacy_fallback.value,
        DecisionStatus.indecisive.value,
        DecisionStatus.multiplicity_indecisive.value,
        DecisionStatus.too_uncertain.value,
    }:
        return "TooUncertain"
    if status.startswith("quality_") or status.startswith("latency_"):
        return "Reliable"
    if status == DecisionStatus.unrelated_canned.value:
        return "Brittle"
    return "Watch"


def discordant_pair_count(rows: Sequence[PairFitRow], family: str) -> int:
    return sum(
        1
        for r in rows
        if r.family == family
        and r.joint_state
        in {JointQualityState.nextseek_only_succeeds, JointQualityState.container_cc_only_succeeds}
    )


def _family_rows(rows: Sequence[PairFitRow], family: str) -> list[PairFitRow]:
    return [r for r in rows if r.family == family]


def retained_support_ok(rows: Sequence[PairFitRow], family: str, cfg: V14FitConfig) -> bool:
    return len(_family_rows(rows, family)) >= cfg.min_retained_pairs


def quality_discordance_ok(rows: Sequence[PairFitRow], family: str, cfg: V14FitConfig) -> bool:
    fam = _family_rows(rows, family)
    discordant = discordant_pair_count(rows, family)
    admission = FitAdmission(
        retained_pairs=[(r.pair_id, r.query_id, r.family) for r in fam],
        excluded_pair_ids=[],
        pending_pair_ids=[],
    )
    gate = check_support_gate(
        admission,
        SupportGateConfig(min_retained_pairs=cfg.min_retained_pairs, min_discordant_pairs=cfg.min_discordant_pairs),
        discordant_pairs=discordant,
    )
    return bool(gate["passes"])


def decide_family(
    rows: Sequence[PairFitRow],
    family: str,
    quality: QualityFitResult,
    latency: LatencyFitResult | DescriptiveLatencyResult,
    cfg: V14FitConfig,
) -> CandidateDecision:
    if family == "unrelated":
        return unrelated_spend_gate_path(family)
    if not retained_support_ok(rows, family, cfg):
        return legacy_fallback(family)

    adv = quality.posterior_samples_advantage
    thr = cfg.quality_advantage_threshold
    p_thr = cfg.posterior_probability_threshold
    quality_ok = quality_discordance_ok(rows, family, cfg)

    if quality_ok:
        p_ns = float(np.mean(adv >= thr))
        p_cc = float(np.mean(adv <= -thr))
        if p_ns >= p_thr:
            err = float(np.mean(adv < thr))
            return CandidateDecision(family=family, status=DecisionStatus.quality_ns, local_error_prob=err)
        if p_cc >= p_thr:
            err = float(np.mean(adv > -thr))
            return CandidateDecision(family=family, status=DecisionStatus.quality_cc, local_error_prob=err)

    p_eq = float(np.mean(np.abs(adv) <= cfg.rope_half_width))
    if p_eq >= p_thr:
        equivalence_error = 1.0 - p_eq
        if isinstance(latency, DescriptiveLatencyResult):
            return CandidateDecision(
                family=family,
                status=DecisionStatus.quality_equivalent_ns,
                local_error_prob=equivalence_error,
            )
        ns_lat_p = latency_win_probability(
            latency.posterior_log_d, ratio_threshold=cfg.latency_ratio_threshold, ns_wins=True
        )
        cc_lat_p = latency_win_probability(
            latency.posterior_log_d, ratio_threshold=cfg.latency_ratio_threshold, ns_wins=False
        )
        if ns_lat_p >= p_thr:
            err = 1.0 - float(
                np.mean(
                    (np.abs(adv) <= cfg.rope_half_width)
                    & (latency.posterior_log_d <= np.log(cfg.latency_ratio_threshold))
                )
            )
            return CandidateDecision(family=family, status=DecisionStatus.latency_ns, local_error_prob=err)
        if cc_lat_p >= p_thr:
            err = 1.0 - float(
                np.mean(
                    (np.abs(adv) <= cfg.rope_half_width)
                    & (latency.posterior_log_d >= -np.log(cfg.latency_ratio_threshold))
                )
            )
            return CandidateDecision(family=family, status=DecisionStatus.latency_cc, local_error_prob=err)
        return CandidateDecision(
            family=family,
            status=DecisionStatus.quality_equivalent_ns,
            local_error_prob=equivalence_error,
        )
    return CandidateDecision(family=family, status=DecisionStatus.indecisive, local_error_prob=1.0)


def apply_complete_set_fdr(candidates: Sequence[CandidateDecision], cfg: V14FitConfig) -> tuple[tuple[CandidateDecision, ...], float | None, str]:
    winners = [c for c in candidates if c.status in {
        DecisionStatus.quality_ns, DecisionStatus.quality_cc,
        DecisionStatus.quality_equivalent_ns,
        DecisionStatus.latency_ns, DecisionStatus.latency_cc,
    }]
    if not winners:
        return tuple(candidates), None, "empty_candidate_set"

    winner_ids = {id(candidate) for candidate in winners}
    ranked = sorted(
        winners,
        key=lambda candidate: (
            candidate.local_error_prob,
            candidate.family,
            candidate.status.value,
        ),
    )
    selected_count = 0
    selected_fdr: float | None = None
    for index in range(1, len(ranked) + 1):
        prefix_fdr = float(
            np.mean([candidate.local_error_prob for candidate in ranked[:index]])
        )
        if prefix_fdr <= cfg.fdr_threshold:
            selected_count = index
            selected_fdr = prefix_fdr
        else:
            break

    selected_ids = {id(candidate) for candidate in ranked[:selected_count]}
    if not selected_ids:
        rejected = tuple(
            CandidateDecision(
                candidate.family,
                DecisionStatus.multiplicity_indecisive,
                candidate.local_error_prob,
            )
            if id(candidate) in winner_ids
            else candidate
            for candidate in candidates
        )
        proposed_fdr = float(
            np.mean([candidate.local_error_prob for candidate in winners])
        )
        return rejected, proposed_fdr, "multiplicity_indecisive"

    activated = []
    for c in candidates:
        if id(c) in selected_ids:
            activated.append(CandidateDecision(c.family, c.status, c.local_error_prob, activated=True))
        elif id(c) in winner_ids:
            activated.append(
                CandidateDecision(
                    c.family,
                    DecisionStatus.multiplicity_indecisive,
                    c.local_error_prob,
                )
            )
        else:
            activated.append(c)
    generation_status = (
        "activated_all" if selected_count == len(winners) else "activated_subset"
    )
    return tuple(activated), selected_fdr, generation_status


def evaluate_generation(
    rows: Sequence[PairFitRow],
    quality_by_family: dict[str, QualityFitResult],
    latency_by_family: dict[
        str, LatencyFitResult | DescriptiveLatencyResult
    ],
    cfg: V14FitConfig,
    *,
    config_fingerprint: str,
) -> GenerationDecision:
    families = sorted({r.family for r in rows})
    candidates = [decide_family(rows, f, quality_by_family[f], latency_by_family[f], cfg) for f in families]
    final, fdr, status = apply_complete_set_fdr(candidates, cfg)
    activated = tuple(c.family for c in final if c.activated)
    return GenerationDecision(
        candidates=final,
        posterior_expected_fdr=fdr,
        activated_families=activated,
        generation_status=status,
        config_fingerprint=config_fingerprint,
    )
