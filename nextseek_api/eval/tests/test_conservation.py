"""Tests for conservation and fit-admission."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.conservation import (  # noqa: E402
    SupportGateConfig,
    build_conservation_report,
    build_differential_attrition_report,
    build_fit_admission,
    check_support_gate,
    compute_sensitivity_bounds,
    count_discordant_pairs,
)
from nextseek_api.eval.disposition import ArmBucket, OutcomeBucket  # noqa: E402


def test_conservation_identity() -> None:
    buckets = [
        ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        ArmBucket(query_id="q2", route="ns", bucket=OutcomeBucket.not_desired, scored_value=False),
        ArmBucket(query_id="q3", route="ns", bucket=OutcomeBucket.excluded),
        ArmBucket(query_id="q4", route="ns", bucket=OutcomeBucket.pending),
    ]
    report = build_conservation_report(buckets)
    assert report.balanced
    assert report.input_count == 4


def test_fit_admission_excludes_pending_and_excluded() -> None:
    pairs = [
        {"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"},
        {"pair_id": "p2", "query_id": "q2", "family": "f", "ns_arm_id": "ns2", "cc_arm_id": "cc2"},
    ]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
        "ns2": ArmBucket(query_id="q2", route="ns", bucket=OutcomeBucket.pending),
        "cc2": ArmBucket(query_id="q2", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
    }
    admission = build_fit_admission(pairs, buckets)
    assert len(admission.retained_pairs) == 1
    assert "p2" in admission.pending_pair_ids


def test_support_gate_defaults() -> None:
    admission = build_fit_admission([], {})
    gate = check_support_gate(admission, SupportGateConfig(min_retained_pairs=5, min_discordant_pairs=2))
    assert gate["passes"] is False


def test_discordant_pair_count() -> None:
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.not_desired, scored_value=False),
    }
    assert count_discordant_pairs(pairs, buckets) == 1


def test_single_arm_pair_pending() -> None:
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
    }
    admission = build_fit_admission(pairs, buckets)
    assert admission.retained_pairs == []
    assert "p1" in admission.pending_pair_ids


def test_differential_attrition_flags_route_imbalance() -> None:
    buckets = [
        ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.excluded),
        ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.excluded),
        ArmBucket(query_id="q2", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
        ArmBucket(query_id="q2", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
    ]
    report = build_differential_attrition_report(buckets)
    assert report.by_route["ns"]["excluded"] == 2
    assert report.by_route["cc"]["scored"] == 2
    assert report.route_imbalance is True
    assert report.exclusion_rate_delta > 0.25


def test_sensitivity_bounds_include_pending_and_excluded() -> None:
    pairs = [
        {"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"},
        {"pair_id": "p2", "query_id": "q2", "family": "f", "ns_arm_id": "ns2", "cc_arm_id": "cc2"},
    ]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
        "ns2": ArmBucket(query_id="q2", route="ns", bucket=OutcomeBucket.excluded),
        "cc2": ArmBucket(query_id="q2", route="cc", bucket=OutcomeBucket.excluded),
    }
    admission = build_fit_admission(pairs, buckets)
    bounds = compute_sensitivity_bounds(
        admission,
        pairs,
        buckets,
        config=SupportGateConfig(min_retained_pairs=2, min_discordant_pairs=1),
    )
    assert bounds["retained_pairs"]["observed"] == 1
    assert bounds["retained_pairs"]["max"] == 2
    assert bounds["pending_pairs"] == 0
    assert bounds["excluded_pairs"] == 1


def test_support_gate_includes_attrition_and_sensitivity() -> None:
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    buckets_by_arm = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.not_desired, scored_value=False),
    }
    admission = build_fit_admission(pairs, buckets_by_arm)
    gate = check_support_gate(
        admission,
        SupportGateConfig(min_retained_pairs=1, min_discordant_pairs=1),
        discordant_pairs=1,
        buckets=list(buckets_by_arm.values()),
        pairs=pairs,
        buckets_by_arm=buckets_by_arm,
    )
    assert gate["differential_attrition"] is not None
    assert gate["sensitivity_bounds"] is not None
    assert "excluded_pairs" in gate["attrition_report"]


def test_differential_attrition_empty_buckets() -> None:
    report = build_differential_attrition_report([])
    assert report.route_imbalance is False
    assert report.detail == "no buckets"


def test_differential_attrition_single_route() -> None:
    buckets = [
        ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.pending),
        ArmBucket(query_id="q2", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
    ]
    report = build_differential_attrition_report(buckets)
    assert report.route_imbalance is False
    assert "single route attrition rate" in report.detail


def test_count_discordant_skips_missing_and_excluded_arms() -> None:
    pairs = [
        {"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"},
        {"pair_id": "p2", "query_id": "q2", "family": "f", "ns_arm_id": "ns2", "cc_arm_id": "cc2"},
    ]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.excluded),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
    }
    assert count_discordant_pairs(pairs, buckets) == 0


def test_fit_and_discordance_fail_closed_for_corrupt_non_scored_bucket_values() -> None:
    """A corrupted bucket type is excluded rather than admitted to a paired fit."""
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    corrupted = type("CorruptBucket", (), {"bucket": object()})()
    buckets = {
        "ns1": corrupted,
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.desired, scored_value=True),
    }
    assert build_fit_admission(pairs, buckets).excluded_pair_ids == ["p1"]
    assert count_discordant_pairs(pairs, buckets) == 0

    buckets["ns1"] = ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True)
    buckets["cc1"] = corrupted
    assert build_fit_admission(pairs, buckets).excluded_pair_ids == ["p1"]
    assert count_discordant_pairs(pairs, buckets) == 0


def test_fit_admission_rejects_non_scored_bucket_types() -> None:
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    buckets = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.pending),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.pending),
    }
    admission = build_fit_admission(pairs, buckets)
    assert admission.retained_pairs == []
    assert "p1" in admission.pending_pair_ids


def test_build_fit_admission_honors_paired_batch_boundary() -> None:
    from nextseek_api.eval.paired_run import PairedExperimentalBatch

    batch = PairedExperimentalBatch(paired_run_id="paired-run-1", pairs=[])
    with (
        patch("nextseek_api.eval.fit.fit_boundary.assert_paired_experimental_only") as assert_only,
        patch("nextseek_api.eval.fit.fit_boundary.require_approved_paired_run") as require_run,
    ):
        build_fit_admission([], {}, paired_batch=batch)
    assert_only.assert_called_once_with(batch)
    require_run.assert_called_once_with("paired-run-1")
