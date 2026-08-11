"""Deterministic V14 decision boundary tests (no MCMC)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from nextseek_api.eval.fit.v14.decision import (
    DecisionStatus,
    apply_complete_set_fdr,
    decide_family,
    evaluate_generation,
    legacy_fallback,
    unrelated_spend_gate_path,
)
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, config_fingerprint
from nextseek_api.eval.fit.v14.latency_model import LatencyFitResult
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
        type("C", (), {"family": "a", "status": DecisionStatus.quality_ns, "local_error_prob": 0.5, "activated": False})(),
        type("C", (), {"family": "b", "status": DecisionStatus.quality_cc, "local_error_prob": 0.5, "activated": False})(),
    ]
    from nextseek_api.eval.fit.v14.decision import CandidateDecision

    cands = [
        CandidateDecision("a", DecisionStatus.quality_ns, 0.5),
        CandidateDecision("b", DecisionStatus.quality_cc, 0.5),
    ]
    final, fdr, status = apply_complete_set_fdr(cands, cfg)
    assert status == "multiplicity_indecisive"
    assert all(c.status == DecisionStatus.multiplicity_indecisive for c in final)


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
