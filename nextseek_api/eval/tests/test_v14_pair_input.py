"""Tests for V14 pair-preserving fit input."""
from __future__ import annotations

import math

import pytest

from nextseek_api.eval.conservation import FitAdmission
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, config_fingerprint, contrast_basis_B
from nextseek_api.eval.fit.v14.pair_rows import (
    JointQualityState,
    LatencyObservationKind,
    RouteFamilyAggregateRejected,
    build_pair_rows,
    joint_state_from_success,
    reject_aggregate_input,
)
from nextseek_api.eval.router_models_proposal import RouteFamilyAggregate


def test_contrast_basis_orthonormal():
    b = contrast_basis_B()
    assert b.shape == (4, 3)
    gram = b.T @ b
    assert gram.shape == (3, 3)
    assert abs(gram - __import__("numpy").eye(3)).max() < 1e-5
    assert abs(b.sum(axis=0)).max() < 1e-5


def test_config_fingerprint_stable():
    cfg = V14FitConfig()
    assert config_fingerprint(cfg) == config_fingerprint(cfg)


def test_joint_state_mapping():
    assert joint_state_from_success(True, True) == JointQualityState.both_succeed
    assert joint_state_from_success(True, False) == JointQualityState.nextseek_only_succeeds


def test_build_pair_rows_preserves_query_id():
    admission = FitAdmission(
        retained_pairs=[("p1", "q1", "fam_a")],
        excluded_pair_ids=[],
        pending_pair_ids=[],
    )
    arms = {
        "ns1": {"pair_id": "p1", "route": "nextseek", "combined_success": True, "latency_seconds": 2.0, "latency_censored": False},
        "cc1": {"pair_id": "p1", "route": "container_cc", "combined_success": False, "latency_seconds": 3.0, "latency_censored": False},
    }
    rows = build_pair_rows(admission, arms)
    assert len(rows) == 1
    assert rows[0].query_id == "q1"
    assert rows[0].joint_state == JointQualityState.nextseek_only_succeeds


def test_excluded_pairs_never_enter_fit_rows():
    admission = FitAdmission(retained_pairs=[], excluded_pair_ids=["p9"], pending_pair_ids=["p8"])
    assert build_pair_rows(admission, {}) == []


def test_route_family_aggregate_rejected():
    agg = RouteFamilyAggregate(
        task_family="x",
        route="nextseek_query",
        n_total=1,
        n_success=1,
        avg_latency_seconds=1.0,
    )
    with pytest.raises(RouteFamilyAggregateRejected):
        reject_aggregate_input(agg)
