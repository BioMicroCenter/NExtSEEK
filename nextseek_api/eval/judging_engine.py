"""Default-network-free judging engine with V4-8 reservation gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from nextseek_api.eval.fake_provider import FakeProviderTransport, ProviderCallResult
from nextseek_api.eval.paid_run_state import (
    build_cache_key,
    ensure_attempt_pending,
    get_attempt_state,
    mark_attempt_cached,
    mark_attempt_succeeded,
)
from nextseek_api.eval.provider_gate import guarded_provider_call
from nextseek_api.eval.run_authorization import AuthorizationError

__all__ = ["JudgingEngine", "JudgeAttemptSpec", "JudgeAttemptResult"]


@dataclass(frozen=True)
class JudgeAttemptSpec:
    arm_id: str
    attempt_id: str
    idempotency_key: str
    input_fingerprint: str
    max_cost_usd: Decimal
    model_version: str = "v1"


@dataclass
class JudgeAttemptResult:
    arm_id: str
    attempt_id: str
    status: str
    payload: str | None = None
    actual_cost_usd: Decimal = Decimal("0")
    cached: bool = False


@dataclass
class JudgingEngine:
    manifest_hash: str
    cap_usd: Decimal
    run_id: str = "default-run"
    transport: FakeProviderTransport = field(default_factory=FakeProviderTransport)
    completed: dict[tuple[str, str], JudgeAttemptResult] = field(default_factory=dict)
    cache: dict[str, ProviderCallResult] = field(default_factory=dict)

    def execute_attempt(self, spec: JudgeAttemptSpec) -> JudgeAttemptResult:
        key = (spec.arm_id, spec.attempt_id)
        existing = self.completed.get(key)
        if existing is not None and existing.status in {"succeeded", "cached"}:
            return existing

        durable = get_attempt_state(
            run_id=self.run_id, arm_id=spec.arm_id, attempt_id=spec.attempt_id
        )
        if durable is not None and durable.status in {
            "succeeded",
            "cached",
        }:
            result = JudgeAttemptResult(
                arm_id=spec.arm_id,
                attempt_id=spec.attempt_id,
                status=durable.status,
                payload=f"resumed:{spec.attempt_id}",
                actual_cost_usd=Decimal("0"),
                cached=durable.status == "cached",
            )
            self.completed[key] = result
            return result

        if self.cap_usd <= 0:
            raise AuthorizationError("cap_usd <= 0 makes zero calls")

        cache_key = build_cache_key(
            input_fingerprint=spec.input_fingerprint,
            manifest_hash=self.manifest_hash,
            model_version=spec.model_version,
        )
        if cache_key in self.cache:
            ensure_attempt_pending(
                run_id=self.run_id,
                manifest_hash=self.manifest_hash,
                arm_id=spec.arm_id,
                attempt_id=spec.attempt_id,
                cache_key=cache_key,
            )
            cached = self.cache[cache_key]
            state = get_attempt_state(
                run_id=self.run_id, arm_id=spec.arm_id, attempt_id=spec.attempt_id
            )
            if state is not None:
                mark_attempt_cached(state)
            result = JudgeAttemptResult(
                arm_id=spec.arm_id,
                attempt_id=spec.attempt_id,
                status="cached",
                payload=cached.payload,
                actual_cost_usd=Decimal("0"),
                cached=True,
            )
            self.completed[key] = result
            return result

        state = ensure_attempt_pending(
            run_id=self.run_id,
            manifest_hash=self.manifest_hash,
            arm_id=spec.arm_id,
            attempt_id=spec.attempt_id,
            cache_key=cache_key,
        )

        def _call() -> ProviderCallResult:
            return self.transport.invoke(
                attempt_id=spec.attempt_id,
                input_fingerprint=spec.input_fingerprint,
            )

        provider_result = guarded_provider_call(
            self.manifest_hash,
            attempt_id=spec.attempt_id,
            idempotency_key=spec.idempotency_key,
            max_cost_usd=spec.max_cost_usd,
            fn=_call,
            actual_cost_fn=lambda r: r.actual_cost_usd,
        )
        self.cache[cache_key] = provider_result
        mark_attempt_succeeded(state)
        result = JudgeAttemptResult(
            arm_id=spec.arm_id,
            attempt_id=spec.attempt_id,
            status="succeeded",
            payload=provider_result.payload,
            actual_cost_usd=provider_result.actual_cost_usd,
        )
        self.completed[key] = result
        return result
