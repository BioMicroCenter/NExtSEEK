"""Tests for conservation and fit-admission."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.conservation import (  # noqa: E402
    SupportGateConfig,
    build_conservation_report,
    build_fit_admission,
    check_support_gate,
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
