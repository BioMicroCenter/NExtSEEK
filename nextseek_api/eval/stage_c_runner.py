"""Exactly-three Stage C runner with hermetic evaluator injection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import orjson

from nextseek_api.eval.attempt_store import AttemptStore
from nextseek_api.eval.judge import (
    STAGE_C_STATUS_COMPLETE,
    STAGE_C_STATUS_FAILED,
    STAGE_C_STATUS_PARTIAL,
    aggregate_three_evaluations,
)
from nextseek_api.eval.judge_models import FunctionalEvaluation

__all__ = [
    "StageCResult",
    "StageCRunner",
    "StageCRunnerError",
]


class StageCRunnerError(ValueError):
    pass


class EvaluatorFn(Protocol):
    def __call__(self, arm_id: str, call_index: int, input_fingerprint: str) -> FunctionalEvaluation: ...


@dataclass(frozen=True)
class StageCResult:
    arm_id: str
    status: str
    call_count: int
    aggregate: dict[str, object]
    attempt_ids: tuple[str, ...]


class StageCRunner:
    REQUIRED_CALLS = 3

    def __init__(
        self,
        store: AttemptStore,
        *,
        model_id: str = "mock-evaluator",
        prompt_version: str = "v1",
        evaluator_version: str = "v1",
    ) -> None:
        self.store = store
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.evaluator_version = evaluator_version

    def run_arm(
        self,
        arm_id: str,
        input_fingerprint: str,
        evaluator: EvaluatorFn,
        *,
        max_calls: int | None = None,
    ) -> StageCResult:
        max_calls = max_calls or self.REQUIRED_CALLS
        if max_calls != self.REQUIRED_CALLS:
            raise StageCRunnerError("DD-44 requires exactly three sequential calls")
        evaluations: list[FunctionalEvaluation] = []
        attempt_ids: list[str] = []
        failures = 0
        for call_index in range(self.REQUIRED_CALLS):
            try:
                evaluation = evaluator(arm_id, call_index, input_fingerprint)
            except Exception as exc:  # noqa: BLE001 — record failed attempt
                failures += 1
                record = self.store.write_attempt(
                    arm_id=arm_id,
                    call_index=call_index,
                    input_fingerprint=input_fingerprint,
                    model_id=self.model_id,
                    prompt_version=self.prompt_version,
                    evaluator_version=self.evaluator_version,
                    request_bytes=orjson.dumps({"arm_id": arm_id, "call_index": call_index}),
                    response_bytes=orjson.dumps({"error": str(exc)}),
                    status="failed",
                    error_class="provider_outage",
                )
                attempt_ids.append(record.attempt_id)
                continue
            record = self.store.write_attempt(
                arm_id=arm_id,
                call_index=call_index,
                input_fingerprint=input_fingerprint,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                evaluator_version=self.evaluator_version,
                request_bytes=orjson.dumps({"arm_id": arm_id, "call_index": call_index}),
                response_bytes=evaluation.model_dump_json().encode(),
                status="succeeded",
            )
            attempt_ids.append(record.attempt_id)
            evaluations.append(evaluation)
        if len(evaluations) == self.REQUIRED_CALLS:
            status = STAGE_C_STATUS_COMPLETE
            aggregate = aggregate_three_evaluations(
                (evaluations[0], evaluations[1], evaluations[2])
            )
        elif evaluations:
            status = STAGE_C_STATUS_PARTIAL
            aggregate = {"stage_c_call_count": len(evaluations), "partial": True}
        else:
            status = STAGE_C_STATUS_FAILED
            aggregate = {"stage_c_call_count": failures, "failed": True}
        return StageCResult(
            arm_id=arm_id,
            status=status,
            call_count=len(evaluations),
            aggregate=aggregate,
            attempt_ids=tuple(attempt_ids),
        )

    def replay_arm(self, arm_id: str) -> StageCResult:
        attempts = self.store.list_arm_attempts(arm_id)
        if not attempts:
            raise StageCRunnerError(f"no attempts for arm {arm_id}")
        evaluations: list[FunctionalEvaluation] = []
        for attempt in attempts:
            self.store.verify_attempt(attempt.attempt_id)
            if attempt.status != "succeeded":
                continue
            payload = self.store.read_payload(attempt.response_sha256)
            evaluations.append(FunctionalEvaluation.model_validate_json(payload))
        if len(evaluations) != self.REQUIRED_CALLS:
            return StageCResult(
                arm_id=arm_id,
                status=STAGE_C_STATUS_PARTIAL if evaluations else STAGE_C_STATUS_FAILED,
                call_count=len(evaluations),
                aggregate={"stage_c_call_count": len(evaluations), "replay_partial": True},
                attempt_ids=tuple(a.attempt_id for a in attempts),
            )
        aggregate = aggregate_three_evaluations(
            (evaluations[0], evaluations[1], evaluations[2])
        )
        return StageCResult(
            arm_id=arm_id,
            status=STAGE_C_STATUS_COMPLETE,
            call_count=3,
            aggregate=aggregate,
            attempt_ids=tuple(a.attempt_id for a in attempts),
        )
