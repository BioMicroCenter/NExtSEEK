"""Deterministic V14 decision boundary tests (no MCMC)."""
from __future__ import annotations

import math

import numpy as np

from nextseek_api.eval.fit.v14.decision import (
    CandidateDecision,
    DecisionStatus,
    apply_complete_set_fdr,
    decide_family,
    discordant_pair_count,
    quality_discordance_ok,
    retained_support_ok,
    unrelated_spend_gate_path,
)
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.latency_model import (
    DescriptiveLatencyResult,
    LatencyFitResult,
)
from nextseek_api.eval.fit.v14.pair_rows import JointQualityState, LatencyObservationKind, PairFitRow
from nextseek_api.eval.fit.v14.quality_model import QualityFitResult


def _rows(n: int = 8, state: JointQualityState = JointQualityState.nextseek_only_succeeds) -> list[PairFitRow]:
    out = []
    for i in range(n):
        out.append(
            PairFitRow(
                pair_id=f"p{i}",
                query_id=f"q{i}",
                family="fam_a",
                joint_state=state,
                latency_kind=LatencyObservationKind.observed,
                log_latency_ns=math.log(1.0),
                log_latency_cc=math.log(2.0),
            )
        )
    return out


def _both_succeed_rows(n: int = 8) -> list[PairFitRow]:
    return _rows(n=n, state=JointQualityState.both_succeed)


def _quality(adv: float) -> QualityFitResult:
    samples = np.full(500, adv)
    p = np.array([0.1, 0.8, 0.05, 0.05])
    return QualityFitResult("fam_a", p, adv, samples, 0, 1.0, 1000.0, 1000.0)


def _latency(log_d: float = -0.5) -> LatencyFitResult:
    return LatencyFitResult("fam_a", np.full(500, log_d), 0.99, 0, 1.0, 1000.0, 1000.0)


def test_quality_ns_winner():
    cfg = V14FitConfig()
    d = decide_family(_rows(), "fam_a", _quality(0.25), _latency(), cfg)
    assert d.status == DecisionStatus.quality_ns


def test_quality_cc_winner():
    cfg = V14FitConfig()
    d = decide_family(_rows(state=JointQualityState.container_cc_only_succeeds), "fam_a", _quality(-0.25), _latency(), cfg)
    assert d.status == DecisionStatus.quality_cc


def test_latency_both_succeed_zero_discordance_ruling_b():
    """Ruling B: latency win allowed with all both_succeed (0 discordant) after ROPE."""
    cfg = V14FitConfig(min_discordant_pairs=2)
    rows = _both_succeed_rows()
    assert discordant_pair_count(rows, "fam_a") == 0
    assert retained_support_ok(rows, "fam_a", cfg)
    assert not quality_discordance_ok(rows, "fam_a", cfg)
    d = decide_family(rows, "fam_a", _quality(0.02), _latency(-0.7), cfg)
    assert d.status == DecisionStatus.latency_ns


def test_quality_blocked_without_discordance_ruling_b():
    cfg = V14FitConfig(min_discordant_pairs=2)
    rows = _both_succeed_rows()
    d = decide_family(rows, "fam_a", _quality(0.25), _latency(), cfg)
    assert d.status != DecisionStatus.quality_ns
    assert d.status != DecisionStatus.quality_cc


def test_latency_only_after_equivalence():
    cfg = V14FitConfig(min_discordant_pairs=2)
    rows = []
    for i in range(4):
        rows.append(
            PairFitRow(
                pair_id=f"p{i}",
                query_id=f"q{i}",
                family="fam_a",
                joint_state=JointQualityState.nextseek_only_succeeds,
                latency_kind=LatencyObservationKind.observed,
                log_latency_ns=math.log(1.0),
                log_latency_cc=math.log(2.0),
            )
        )
    for i in range(4, 8):
        rows.append(
            PairFitRow(
                pair_id=f"p{i}",
                query_id=f"q{i}",
                family="fam_a",
                joint_state=JointQualityState.container_cc_only_succeeds,
                latency_kind=LatencyObservationKind.observed,
                log_latency_ns=math.log(2.0),
                log_latency_cc=math.log(1.0),
            )
        )
    d = decide_family(rows, "fam_a", _quality(0.02), _latency(-0.7), cfg)
    assert d.status == DecisionStatus.latency_ns


def test_missing_latency_posterior_defaults_ns_only_after_quality_equivalence():
    cfg = V14FitConfig(min_discordant_pairs=2)
    unavailable = DescriptiveLatencyResult("fam_a", observation_count=8)

    equivalent = decide_family(
        _both_succeed_rows(), "fam_a", _quality(0.02), unavailable, cfg
    )
    quality_winner = decide_family(
        _rows(), "fam_a", _quality(0.25), unavailable, cfg
    )

    assert equivalent.status == DecisionStatus.quality_equivalent_ns
    assert equivalent.local_error_prob == 0.0
    assert quality_winner.status == DecisionStatus.quality_ns


def test_inconclusive_latency_defaults_ns_after_quality_equivalence():
    cfg = V14FitConfig(min_discordant_pairs=2)

    decision = decide_family(
        _both_succeed_rows(), "fam_a", _quality(0.02), _latency(0.0), cfg
    )

    assert decision.status == DecisionStatus.quality_equivalent_ns
    assert decision.local_error_prob == 0.0


def test_insufficient_support_legacy():
    cfg = V14FitConfig(min_retained_pairs=10)
    d = decide_family(_rows(n=3), "fam_a", _quality(0.25), _latency(), cfg)
    assert d.status == DecisionStatus.legacy_fallback


def test_unrelated_canned_path():
    d = unrelated_spend_gate_path("unrelated")
    assert d.status == DecisionStatus.unrelated_canned


def test_empty_fdr_no_vacuous_pass():
    cfg = V14FitConfig()
    final, fdr, status = apply_complete_set_fdr([], cfg)
    assert status == "empty_candidate_set"
    assert fdr is None


def test_fdr_over_limit_activates_none():
    cfg = V14FitConfig(fdr_threshold=0.01)
    cands = [
        CandidateDecision("a", DecisionStatus.quality_ns, 0.5),
        CandidateDecision("b", DecisionStatus.quality_cc, 0.5),
    ]
    final, fdr, status = apply_complete_set_fdr(cands, cfg)
    assert status == "multiplicity_indecisive"
    assert all(c.status == DecisionStatus.multiplicity_indecisive for c in final)


def test_fdr_retains_largest_safe_subset():
    cfg = V14FitConfig(fdr_threshold=0.05)
    cands = [
        CandidateDecision("a", DecisionStatus.quality_ns, 0.01),
        CandidateDecision("b", DecisionStatus.quality_cc, 0.15),
    ]
    final, fdr, status = apply_complete_set_fdr(cands, cfg)
    assert status == "activated_subset"
    assert fdr == 0.01
    assert [(c.family, c.status, c.activated) for c in final] == [
        ("a", DecisionStatus.quality_ns, True),
        ("b", DecisionStatus.multiplicity_indecisive, False),
    ]


def test_fdr_subset_is_order_independent_and_preserves_non_winners():
    cfg = V14FitConfig(fdr_threshold=0.05)
    cands = [
        CandidateDecision("fallback", DecisionStatus.legacy_fallback, 0.0),
        CandidateDecision("weak", DecisionStatus.quality_cc, 0.20),
        CandidateDecision("strong_b", DecisionStatus.quality_cc, 0.04),
        CandidateDecision("strong_a", DecisionStatus.quality_ns, 0.01),
    ]

    final, fdr, status = apply_complete_set_fdr(cands, cfg)

    assert status == "activated_subset"
    assert fdr == 0.025
    by_family = {c.family: c for c in final}
    assert by_family["strong_a"].activated
    assert by_family["strong_b"].activated
    assert by_family["weak"].status == DecisionStatus.multiplicity_indecisive
    assert by_family["fallback"].status == DecisionStatus.legacy_fallback


def test_cost_does_not_affect_winner():
    rows = _rows()
    rows[0] = PairFitRow(**{**rows[0].model_dump(), "cost_usd": 999.0})
    cfg = V14FitConfig()
    d = decide_family(rows, "fam_a", _quality(0.25), _latency(), cfg)
    assert d.status == DecisionStatus.quality_ns


def test_mutation_latency_cannot_overturn_quality():
    cfg = V14FitConfig()
    d = decide_family(_rows(), "fam_a", _quality(0.25), _latency(5.0), cfg)
    assert d.status == DecisionStatus.quality_ns


def test_mutation_25pct_slowdown_cannot_win_latency():
    cfg = V14FitConfig()
    rows = _both_succeed_rows()
    # log_d ~ -0.22 is ~20% faster boundary; +0.22 is CC faster but not 20%
    d = decide_family(rows, "fam_a", _quality(0.02), _latency(0.22), cfg)
    assert d.status not in {DecisionStatus.latency_ns, DecisionStatus.latency_cc}


def test_mutation_pair_reversal_swaps_winner_direction():
    cfg = V14FitConfig()
    base = _rows()
    d_ns = decide_family(base, "fam_a", _quality(0.25), _latency(), cfg)
    reversed_rows = _rows(state=JointQualityState.container_cc_only_succeeds)
    d_cc = decide_family(reversed_rows, "fam_a", _quality(-0.25), _latency(), cfg)
    assert d_ns.status == DecisionStatus.quality_ns
    assert d_cc.status == DecisionStatus.quality_cc


def test_mutation_discordance_blocks_quality_not_latency():
    cfg = V14FitConfig(min_discordant_pairs=2)
    rows = _both_succeed_rows()
    q_blocked = decide_family(rows, "fam_a", _quality(0.25), _latency(), cfg)
    lat_ok = decide_family(rows, "fam_a", _quality(0.02), _latency(-0.7), cfg)
    assert q_blocked.status not in {DecisionStatus.quality_ns, DecisionStatus.quality_cc}
    assert lat_ok.status == DecisionStatus.latency_ns
