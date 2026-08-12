"""Mutation tests for DD-44 aggregation operators (V5-3 oracle)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval import judge  # noqa: E402
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


OUTCOME_ORACLE = (
    FunctionalOutcome.FullySatisfied.value,
    FunctionalOutcome.AppropriateClarification.value,
    FunctionalOutcome.AppropriateBoundary.value,
)

OUTCOME_TIEBREAK_ORACLE = (
    FunctionalOutcome.NotSatisfied.value,
    FunctionalOutcome.AppropriateClarification.value,
    FunctionalOutcome.AppropriateBoundary.value,
)


# Full mutant matrix — each entry must differ from the canonical operator on at least one oracle vote.
MUTANTS: list[tuple[str, str, Callable, tuple | None]] = [
    (
        "aggregate_outcome_first_vote",
        "outcome",
        lambda votes: votes[0],
        OUTCOME_ORACLE,
    ),
    (
        "aggregate_outcome_invert_failure_partition",
        "outcome",
        lambda votes: sorted(
            votes,
            key=lambda v: (0 if v not in judge._FAILURE_SIDE else 1, judge._OUTCOME_STRICT_ORDER[v]),
        )[0],
        OUTCOME_TIEBREAK_ORACLE,
    ),
    (
        "aggregate_outcome_plurality_bypass",
        "outcome",
        lambda votes: votes[-1],
        OUTCOME_ORACLE,
    ),
    (
        "aggregate_primary_issue_first_only",
        "primary_issue",
        lambda votes: votes[0],
        None,
    ),
    (
        "aggregate_primary_issue_reverse_severity",
        "primary_issue",
        lambda votes: sorted(votes, key=lambda v: -judge._PRIMARY_ISSUE_RANK[v])[0],
        None,
    ),
    (
        "aggregate_primary_issue_no_issue_always",
        "primary_issue",
        lambda votes: PrimaryIssue.NoIssue.value,
        None,
    ),
    (
        "aggregate_review_priority_min",
        "review_priority",
        lambda votes: min(votes, key=lambda v: {"Low": 0, "Medium": 1, "High": 2}[v]),
        None,
    ),
    (
        "aggregate_review_priority_first",
        "review_priority",
        lambda votes: votes[0],
        None,
    ),
    (
        "aggregate_usefulness_mean",
        "usefulness",
        lambda scores: sum(scores) // len(scores),
        None,
    ),
    (
        "aggregate_usefulness_max",
        "usefulness",
        lambda scores: max(scores),
        None,
    ),
    (
        "aggregate_needs_review_all",
        "needs_human_review",
        lambda flags: all(flags),
        None,
    ),
    (
        "aggregate_needs_review_none",
        "needs_human_review",
        lambda flags: False,
        None,
    ),
    (
        "aggregate_rationale_last_match",
        "rationale",
        lambda evs, outcome: next(
            ev.rationale for ev in reversed(evs) if ev.outcome.value == outcome
        ),
        None,
    ),
    (
        "aggregate_rationale_always_first",
        "rationale",
        lambda evs, outcome: evs[0].rationale,
        None,
    ),
]


PRIMARY_ISSUE_ORACLE = (
    PrimaryIssue.Timeout.value,
    PrimaryIssue.RuntimeFailure.value,
    PrimaryIssue.NoIssue.value,
)

PRIORITY_ORACLE = (
    ReviewPriority.Low.value,
    ReviewPriority.High.value,
    ReviewPriority.Medium.value,
)

USEFULNESS_ORACLE = (1, 3, 4)

NEEDS_REVIEW_ORACLE = (False, False, True)

RATIONALE_ORACLE = (
    _ev("NotSatisfied", rationale="bad"),
    _ev("FullySatisfied", rationale="good"),
    _ev("FullySatisfied", rationale="also good"),
)


def _canonical(field: str, outcome_oracle=OUTCOME_ORACLE):
    if field == "outcome":
        return judge.aggregate_outcome(outcome_oracle)
    if field == "primary_issue":
        return judge.aggregate_primary_issue(PRIMARY_ISSUE_ORACLE)
    if field == "review_priority":
        return judge.aggregate_review_priority(PRIORITY_ORACLE)
    if field == "usefulness":
        return judge.aggregate_usefulness_score_median(USEFULNESS_ORACLE)
    if field == "needs_human_review":
        return judge.aggregate_needs_human_review(NEEDS_REVIEW_ORACLE)
    if field == "rationale":
        outcome = judge.aggregate_outcome(
            tuple(ev.outcome.value for ev in RATIONALE_ORACLE)
        )
        return judge.aggregate_rationale(RATIONALE_ORACLE, outcome)
    raise AssertionError(f"unknown field {field!r}")


def _mutated(name: str, field: str, mutant: Callable, outcome_oracle=OUTCOME_ORACLE):
    if field == "outcome":
        return mutant(outcome_oracle)
    if field == "primary_issue":
        return mutant(PRIMARY_ISSUE_ORACLE)
    if field == "review_priority":
        return mutant(PRIORITY_ORACLE)
    if field == "usefulness":
        return mutant(USEFULNESS_ORACLE)
    if field == "needs_human_review":
        return mutant(NEEDS_REVIEW_ORACLE)
    if field == "rationale":
        outcome = judge.aggregate_outcome(
            tuple(ev.outcome.value for ev in RATIONALE_ORACLE)
        )
        return mutant(RATIONALE_ORACLE, outcome)
    raise AssertionError(f"unknown field {field!r}")


@pytest.mark.parametrize("name,field,mutant,outcome_oracle", MUTANTS)
def test_aggregation_mutants_are_detected(
    name: str,
    field: str,
    mutant: Callable,
    outcome_oracle: tuple | None,
) -> None:
    oracle = outcome_oracle or OUTCOME_ORACLE
    expected = _canonical(field, oracle if field == "outcome" else OUTCOME_ORACLE)
    actual = _mutated(name, field, mutant, oracle if field == "outcome" else OUTCOME_ORACLE)
    assert actual != expected, f"mutant {name} should change {field} aggregation"


def test_three_vote_outcome_permutations_match_oracle() -> None:
    """Boundary: all-distinct failure-partition tie-break."""
    votes = (
        FunctionalOutcome.NotAssessable.value,
        FunctionalOutcome.AppropriateClarification.value,
        FunctionalOutcome.AppropriateBoundary.value,
    )
    assert judge.aggregate_outcome(votes) == FunctionalOutcome.NotAssessable.value


def test_three_vote_primary_issue_majority() -> None:
    votes = (
        PrimaryIssue.NoIssue.value,
        PrimaryIssue.NoIssue.value,
        PrimaryIssue.Timeout.value,
    )
    assert judge.aggregate_primary_issue(votes) == PrimaryIssue.NoIssue.value
