"""Task 4 censoring unit tests for V14 pair latency fields."""
from __future__ import annotations

import math

from nextseek_api.eval.fit.v14.latency_model import _extract_d_obs
from nextseek_api.eval.fit.v14.pair_rows import (
    JointQualityState,
    LatencyObservationKind,
    PairFitRow,
    _latency_fields,
)


def test_censoring_observed():
    kind, log_ns, log_cc, lo, hi = _latency_fields(
        True, True, 2.0, 4.0, False, False
    )
    assert kind == LatencyObservationKind.observed
    assert log_ns == math.log(2.0)
    assert log_cc == math.log(4.0)
    assert lo is None and hi is None


def test_censoring_ns_right_censored():
    kind, log_ns, log_cc, lo, hi = _latency_fields(
        True, True, None, 3.0, True, False
    )
    assert kind == LatencyObservationKind.ns_right_censored
    assert log_ns is None
    assert log_cc == math.log(3.0)
    assert lo == math.log(3.0)
    assert hi is None


def test_censoring_cc_right_censored():
    kind, log_ns, log_cc, lo, hi = _latency_fields(
        True, True, 5.0, None, False, True
    )
    assert kind == LatencyObservationKind.cc_right_censored
    assert log_ns == math.log(5.0)
    assert log_cc is None
    assert hi == math.log(5.0)
    assert lo is None


def test_censoring_both_censored():
    kind, log_ns, log_cc, lo, hi = _latency_fields(
        False, False, None, None, True, True
    )
    assert kind == LatencyObservationKind.both_censored
    assert log_ns is None and log_cc is None and lo is None and hi is None


def test_extract_d_obs_all_four_kinds():
    rows = [
        PairFitRow(
            pair_id="p0",
            query_id="q0",
            family="f",
            joint_state=JointQualityState.both_succeed,
            latency_kind=LatencyObservationKind.observed,
            log_latency_ns=0.0,
            log_latency_cc=0.5,
        ),
        PairFitRow(
            pair_id="p1",
            query_id="q1",
            family="f",
            joint_state=JointQualityState.both_succeed,
            latency_kind=LatencyObservationKind.ns_right_censored,
            log_d_lower=0.2,
        ),
        PairFitRow(
            pair_id="p2",
            query_id="q2",
            family="f",
            joint_state=JointQualityState.both_succeed,
            latency_kind=LatencyObservationKind.cc_right_censored,
            log_d_upper=-0.1,
        ),
    ]
    obs, kinds = _extract_d_obs(rows, "f")
    assert len(obs) == 3
    assert kinds == ["observed", "lower", "upper"]
    assert obs[0] == -0.5
