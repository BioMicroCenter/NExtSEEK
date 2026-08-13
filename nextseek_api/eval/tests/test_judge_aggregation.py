"""Tests for DD-44 aggregation (Lane A host hermetic)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.judge import (  # noqa: E402
    aggregate_needs_human_review,
    aggregate_outcome,
    aggregate_primary_issue,
    aggregate_rationale,
    aggregate_review_priority,
    aggregate_three_evaluations,
    aggregate_usefulness_score_median,
    functional_success_from_outcome,
)
from nextseek_api.eval.judge_models import (  # noqa: E402
    FunctionalEvaluation,
    FunctionalOutcome,
    PrimaryIssue,
    ReviewPriority,
)


def _ev(
    outcome: str = "FullySatisfied",
    score: int = 4,
    issue: str = "NoIssue",
    priority: str = "Low",
    needs_review: bool = False,
    rationale: str = "OK.",
) -> FunctionalEvaluation:
    return FunctionalEvaluation(
        outcome=FunctionalOutcome(outcome),
        usefulness_score=score,
        primary_issue=PrimaryIssue(issue),
        needs_human_review=needs_review,
        review_priority=ReviewPriority(priority),
        rationale=rationale,
    )


def test_aggregate_outcome_2_to_1_majority() -> None:
    votes = (
        FunctionalOutcome.NotSatisfied.value,
        FunctionalOutcome.AppropriateClarification.value,
        FunctionalOutcome.AppropriateClarification.value,
    )
    assert aggregate_outcome(votes) == FunctionalOutcome.AppropriateClarification.value


def test_aggregate_outcome_all_distinct_failure_partition_first() -> None:
    votes = (
        FunctionalOutcome.NotAssessable.value,
        FunctionalOutcome.AppropriateClarification.value,
        FunctionalOutcome.AppropriateBoundary.value,
    )
    assert aggregate_outcome(votes) == FunctionalOutcome.NotAssessable.value


def test_aggregate_primary_issue_severity_tiebreak() -> None:
    votes = (
        PrimaryIssue.Timeout.value,
        PrimaryIssue.RuntimeFailure.value,
        PrimaryIssue.NoIssue.value,
    )
    assert aggregate_primary_issue(votes) == PrimaryIssue.RuntimeFailure.value


def test_aggregate_review_priority_max() -> None:
    votes = (ReviewPriority.Low.value, ReviewPriority.High.value, ReviewPriority.Medium.value)
    assert aggregate_review_priority(votes) == ReviewPriority.High.value


def test_aggregate_usefulness_median() -> None:
    assert aggregate_usefulness_score_median((1, 3, 4)) == 3


def test_aggregate_needs_review_or() -> None:
    assert aggregate_needs_human_review((False, False, True)) is True


def test_aggregate_rationale_first_matching_outcome() -> None:
    evs = (
        _ev("NotSatisfied", rationale="bad"),
        _ev("FullySatisfied", rationale="good"),
        _ev("FullySatisfied", rationale="also good"),
    )
    assert aggregate_rationale(evs, "FullySatisfied") == "good"


def test_aggregate_rationale_falls_back_when_corrupt_attempts_have_no_aggregate_match() -> None:
    """A corrupted retrieved attempt sequence still returns a deterministic rationale."""
    evs = (_ev("NotSatisfied", rationale="first"), _ev("NotSatisfied", rationale="second"),
           _ev("NotSatisfied", rationale="third"))
    assert aggregate_rationale(evs, "FullySatisfied") == "first"


def test_aggregate_three_evaluations_exactly_three() -> None:
    evs = (_ev(), _ev(), _ev())
    agg = aggregate_three_evaluations(evs)
    assert agg["stage_c_call_count"] == 3
    assert agg["functional_success"] is True


def test_aggregate_three_evaluations_rejects_wrong_count() -> None:
    with pytest.raises(ValueError):
        aggregate_three_evaluations((_ev(), _ev()))


def test_unknown_primary_issue_fail_closed() -> None:
    with pytest.raises(ValueError, match="unknown primary_issue"):
        aggregate_primary_issue(("NoIssue", "NoIssue", "TotallyUnknown"))


def test_no_confidence_field_in_judge_module() -> None:
    import nextseek_api.eval.judge as judge_mod

    assert "confidence" not in judge_mod.__all__
    src = Path(judge_mod.__file__).read_text()
    assert "confidence" not in src.lower()
