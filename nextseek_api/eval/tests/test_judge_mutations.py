"""Mutation tests for DD-44 aggregation operators (V5-3 oracle)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval import judge  # noqa: E402
from nextseek_api.eval.judge_models import FunctionalOutcome  # noqa: E402


MUTANTS = [
    ("aggregate_outcome_wrong_winner", "outcome", lambda v: FunctionalOutcome.FullySatisfied.value),
    ("aggregate_primary_issue_first_only", "primary_issue", lambda v: v[0]),
]


@pytest.mark.parametrize("name,field,mutant", MUTANTS)
def test_aggregation_mutants_are_detected(name: str, field: str, mutant) -> None:
    if field == "outcome":
        votes = (
            FunctionalOutcome.NotSatisfied.value,
            FunctionalOutcome.NotSatisfied.value,
            FunctionalOutcome.FullySatisfied.value,
        )
        expected = judge.aggregate_outcome(votes)
        assert expected == FunctionalOutcome.NotSatisfied.value
    else:
        votes = (
            "Timeout",
            "RuntimeFailure",
            "NoIssue",
        )
        expected = judge.aggregate_primary_issue(votes)
    assert mutant(votes) != expected, f"mutant {name} should change {field} aggregation"
