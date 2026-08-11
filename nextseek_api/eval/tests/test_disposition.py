"""Tests for V8-D disposition mapping."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.disposition import (  # noqa: E402
    OutcomeBucket,
    classify_arm,
    classify_unknown_value,
    combined_success,
    should_call_judge,
)
from nextseek_api.eval.router_models_proposal import (  # noqa: E402
    ArtifactStatus,
    ErrorClass,
    EvalRow,
    FailureMode,
    RouteSource,
    FamilySource,
)


def _row(**overrides) -> EvalRow:
    base = dict(
        query_id="q1",
        route="nextseek_query",
        task_family="Search-Basic",
        route_source=RouteSource.forced,
        family_source=FamilySource.corpus,
        stack_id="stack-1",
        answer_provided=True,
        is_error=False,
        timed_out=False,
        runtime_success=True,
        failure_mode=FailureMode.none,
        error_class=ErrorClass.none,
        latency_seconds=1.0,
        cost_usd=None,
        artifact_expected=False,
        artifact_status=ArtifactStatus.not_expected,
        artifact_success=True,
        functional_success=True,
    )
    base.update(overrides)
    return EvalRow(**base)


def test_combined_success_and_gate() -> None:
    assert combined_success(runtime_success=True, artifact_success=True, functional_success=True) is True
    assert combined_success(runtime_success=False, artifact_success=True, functional_success=True) is False
    assert combined_success(runtime_success=True, artifact_success=True, functional_success=None) is None


def test_provider_outage_excluded() -> None:
    row = _row(error_class=ErrorClass.provider_outage)
    bucket = classify_arm(row)
    assert bucket.bucket is OutcomeBucket.excluded


def test_unjudged_excluded_not_scored_zero() -> None:
    row = _row(functional_success=None)
    bucket = classify_arm(row)
    assert bucket.bucket is OutcomeBucket.excluded
    assert row.outcome() is None


def test_deterministic_gate_failure_skips_judge() -> None:
    row = _row(answer_provided=False, is_error=True, runtime_success=False, functional_success=None)
    assert should_call_judge(row) is False


def test_zero_criteria_excluded() -> None:
    row = _row()
    bucket = classify_arm(row, zero_criteria=True)
    assert bucket.bucket is OutcomeBucket.excluded


def test_missing_arm_pending() -> None:
    bucket = classify_arm(None, missing=True)
    assert bucket.bucket is OutcomeBucket.pending


def test_unknown_value_fail_closed() -> None:
    with pytest.raises(ValueError):
        classify_unknown_value("totally_unknown")
