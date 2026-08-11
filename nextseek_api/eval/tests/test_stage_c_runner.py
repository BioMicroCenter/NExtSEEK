"""Tests for exactly-three Stage C runner and replay."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.attempt_store import AttemptStore  # noqa: E402
from nextseek_api.eval.judge import STAGE_C_STATUS_COMPLETE, aggregate_outcome  # noqa: E402
from nextseek_api.eval.judge_models import FunctionalEvaluation, FunctionalOutcome, PrimaryIssue, ReviewPriority  # noqa: E402
from nextseek_api.eval.stage_c_runner import StageCRunner, StageCRunnerError  # noqa: E402


def _ev(outcome: str = "FullySatisfied") -> FunctionalEvaluation:
    return FunctionalEvaluation(
        outcome=FunctionalOutcome(outcome),
        usefulness_score=4,
        primary_issue=PrimaryIssue.NoIssue,
        needs_human_review=False,
        review_priority=ReviewPriority.Low,
        rationale="ok",
    )


def test_stage_c_exactly_three_calls(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)
    calls: list[int] = []

    def evaluator(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
        calls.append(call_index)
        return _ev()

    result = runner.run_arm("arm-1", "fp", evaluator)
    assert result.status == STAGE_C_STATUS_COMPLETE
    assert result.call_count == 3
    assert calls == [0, 1, 2]


def test_stage_c_replay_from_store(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)

    def evaluator(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
        return _ev("FullySatisfied" if call_index < 2 else "NotSatisfied")

    runner.run_arm("arm-1", "fp", evaluator)
    replay = runner.replay_arm("arm-1")
    assert replay.call_count == 3
    assert replay.aggregate["outcome"] == aggregate_outcome(
        ("FullySatisfied", "FullySatisfied", "NotSatisfied")
    )


def test_stage_c_rejects_non_three_max_calls(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)
    with pytest.raises(StageCRunnerError):
        runner.run_arm("arm-1", "fp", lambda *a: _ev(), max_calls=2)


def test_unknown_outcome_fail_closed_on_aggregate() -> None:
    with pytest.raises(ValueError, match="unknown outcome"):
        aggregate_outcome(("FullySatisfied", "FullySatisfied", "TotallyUnknown"))
