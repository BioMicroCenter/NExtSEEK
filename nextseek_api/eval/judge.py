"""DD-44 aggregation operators (ported from dmac-assistant@dcca50c functional_evaluator.py)."""
from __future__ import annotations

from collections import Counter

from nextseek_api.eval.judge_models import FunctionalEvaluation

__all__ = [
    "STAGE_C_STATUS_COMPLETE",
    "STAGE_C_STATUS_FAILED",
    "STAGE_C_STATUS_PARTIAL",
    "aggregate_needs_human_review",
    "aggregate_outcome",
    "aggregate_primary_issue",
    "aggregate_rationale",
    "aggregate_review_priority",
    "aggregate_usefulness_score_median",
    "functional_success_from_outcome",
]

STAGE_C_STATUS_COMPLETE = "Complete"
STAGE_C_STATUS_PARTIAL = "PartialSuccess"
STAGE_C_STATUS_FAILED = "Failed"

_FAILURE_SIDE = {"NotSatisfied", "PartiallySatisfied", "NotAssessable"}
_OUTCOME_STRICT_ORDER = {
    "NotSatisfied": 0,
    "PartiallySatisfied": 1,
    "NotAssessable": 2,
    "AppropriateClarification": 3,
    "AppropriateBoundary": 4,
    "FullySatisfied": 5,
}

_PRIMARY_ISSUE_SEVERITY = [
    "RuntimeFailure",
    "Timeout",
    "MissingArtifact",
    "InvalidArtifact",
    "IncompleteArtifact",
    "UpstreamApiError",
    "OverclaimedSuccess",
    "InsufficientEvidence",
    "RefusalError",
    "UnsupportedRequest",
    "MissingContext",
    "AmbiguousRequest",
    "OverBroadSearch",
    "Other",
    "NoIssue",
]
_PRIMARY_ISSUE_RANK = {name: i for i, name in enumerate(_PRIMARY_ISSUE_SEVERITY)}

_FUNCTIONAL_SUCCESS_SET = {
    "FullySatisfied",
    "AppropriateClarification",
    "AppropriateBoundary",
}


def aggregate_outcome(votes: tuple[str, str, str]) -> str:
    """DD-44: plurality with failure-partition tie-break."""
    for v in votes:
        if v not in _OUTCOME_STRICT_ORDER:
            raise ValueError(f"unknown outcome enum fails closed: {v!r}")
    counter = Counter(votes)
    winner, count = counter.most_common(1)[0]
    if count >= 2:
        return winner

    def sort_key(v: str) -> tuple[int, int]:
        partition_rank = 0 if v in _FAILURE_SIDE else 1
        return (partition_rank, _OUTCOME_STRICT_ORDER[v])

    return sorted(votes, key=sort_key)[0]


def aggregate_primary_issue(votes: tuple[str, str, str]) -> str:
    """DD-44: majority; tie-break = severity order (most-severe-first)."""
    for v in votes:
        if v not in _PRIMARY_ISSUE_RANK:
            raise ValueError(f"unknown primary_issue enum fails closed: {v!r}")
    counter = Counter(votes)
    winner, count = counter.most_common(1)[0]
    if count >= 2:
        return winner
    return sorted(votes, key=lambda v: _PRIMARY_ISSUE_RANK.get(v, 999))[0]


def aggregate_review_priority(votes: tuple[str, str, str]) -> str:
    """DD-44: max of 3 (Low<Medium<High)."""
    order = {"Low": 0, "Medium": 1, "High": 2}
    return max(votes, key=lambda v: order.get(v, -1))


def aggregate_usefulness_score_median(scores: tuple[int, int, int]) -> int:
    return sorted(scores)[1]


def aggregate_needs_human_review(votes: tuple[bool, bool, bool]) -> bool:
    return any(votes)


def aggregate_rationale(
    evaluations: tuple[FunctionalEvaluation, FunctionalEvaluation, FunctionalEvaluation],
    aggregate_outcome_value: str,
) -> str:
    """DD-44: rationale of the first call matching aggregate outcome."""
    for ev in evaluations:
        if ev.outcome.value == aggregate_outcome_value:
            return ev.rationale
    return evaluations[0].rationale


def functional_success_from_outcome(outcome: str) -> bool:
    return outcome in _FUNCTIONAL_SUCCESS_SET


def aggregate_three_evaluations(
    evaluations: tuple[FunctionalEvaluation, FunctionalEvaluation, FunctionalEvaluation],
) -> dict[str, object]:
    """Aggregate exactly three evaluations per DD-44."""
    if len(evaluations) != 3:
        raise ValueError(f"expected exactly 3 evaluations, got {len(evaluations)}")
    outcome_votes = tuple(ev.outcome.value for ev in evaluations)
    issue_votes = tuple(ev.primary_issue.value for ev in evaluations)
    priority_votes = tuple(ev.review_priority.value for ev in evaluations)
    usefulness_scores = tuple(ev.usefulness_score for ev in evaluations)
    review_flags = tuple(ev.needs_human_review for ev in evaluations)
    agg_outcome = aggregate_outcome(outcome_votes)
    return {
        "outcome": agg_outcome,
        "primary_issue": aggregate_primary_issue(issue_votes),
        "review_priority": aggregate_review_priority(priority_votes),
        "usefulness_score": aggregate_usefulness_score_median(usefulness_scores),
        "needs_human_review": aggregate_needs_human_review(review_flags),
        "rationale": aggregate_rationale(evaluations, agg_outcome),
        "functional_success": functional_success_from_outcome(agg_outcome),
        "stage_c_call_count": 3,
    }
