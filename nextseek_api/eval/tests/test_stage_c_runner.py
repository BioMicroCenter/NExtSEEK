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


def test_stage_c_partial_on_evaluator_failure(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)

    def evaluator(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
        if call_index == 1:
            raise RuntimeError("provider blip")
        return _ev()

    result = runner.run_arm("arm-fail", "fp", evaluator)
    assert result.call_count == 2
    assert result.status != STAGE_C_STATUS_COMPLETE
    assert result.aggregate.get("partial") is True


def test_stage_c_failed_when_all_calls_fail(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)

    def evaluator(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
        raise RuntimeError("down")

    result = runner.run_arm("arm-down", "fp", evaluator)
    assert result.call_count == 0
    assert result.aggregate.get("failed") is True


def test_stage_c_replay_partial_when_incomplete(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    runner = StageCRunner(store)
    runner.run_arm("arm-partial", "fp", lambda *a: _ev())
    # only one attempt persisted manually by partial run - use evaluator that fails after 1
    store2 = AttemptStore(tmp_path / "other")
    runner2 = StageCRunner(store2)

    def flaky(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
        if call_index > 0:
            raise RuntimeError("stop")
        return _ev()

    runner2.run_arm("arm-x", "fp", flaky)
    replay = runner2.replay_arm("arm-x")
    assert replay.aggregate.get("replay_partial") is True


def test_stage_c_replay_refuses_unknown_arm_without_reachable_attempt_bytes(tmp_path: Path) -> None:
    """A replay request with no durable attempts cannot be promoted to a result."""
    with pytest.raises(StageCRunnerError, match="no attempts"):
        StageCRunner(AttemptStore(tmp_path)).replay_arm("absent-arm")
