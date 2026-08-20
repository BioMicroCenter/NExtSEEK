"""V4-8 fake transport gate + judging engine tests (Lane C)."""
from __future__ import annotations

from decimal import Decimal

import pytest

import nextseek_api.eval.provider_gate as provider_gate
from nextseek_api.assistant.models_db import SpendReservation
from nextseek_api.eval.fake_provider import FakeProviderTransport
from nextseek_api.eval.judging_engine import JudgeAttemptSpec, JudgingEngine
from nextseek_api.eval.provider_gate import AuthorizationError, guarded_provider_call
from nextseek_api.eval.run_authorization import approve_run_manifest, reserve_budget
from nextseek_api.eval.tests.v4_8_fixtures import sample_run_manifest

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved():
    return approve_run_manifest(sample_run_manifest())


@pytest.fixture(autouse=True)
def reset_crash_flags():
    provider_gate.CRASH_BEFORE_RESERVE = False
    provider_gate.CRASH_AFTER_RESERVE = False
    provider_gate.CRASH_AFTER_PROVIDER = False
    provider_gate.CRASH_BEFORE_RECONCILE = False
    yield


def test_guarded_provider_call_reconciles_on_success(approved):
    transport_calls = {"n": 0}

    def _provider():
        transport_calls["n"] += 1
        return "ok"

    result = guarded_provider_call(
        approved.manifest_hash,
        attempt_id="attempt-1",
        idempotency_key="guard-1",
        max_cost_usd=Decimal("0.05"),
        fn=_provider,
        actual_cost_fn=lambda _: Decimal("0.04"),
    )
    assert result == "ok"
    assert transport_calls["n"] == 1
    reservation = SpendReservation.objects.get(attempt_id="attempt-1")
    assert reservation.status == SpendReservation.STATUS_RECONCILED
    assert reservation.actual_usd == Decimal("0.04")


def test_guarded_provider_call_releases_on_exception(approved):
    def _boom():
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        guarded_provider_call(
            approved.manifest_hash,
            attempt_id="attempt-2",
            idempotency_key="guard-2",
            max_cost_usd=Decimal("0.05"),
            fn=_boom,
        )
    reservation = SpendReservation.objects.get(attempt_id="attempt-2")
    assert reservation.status == SpendReservation.STATUS_RELEASED


@pytest.mark.parametrize(
    "flag_name",
    [
        "CRASH_BEFORE_RESERVE",
        "CRASH_AFTER_RESERVE",
        "CRASH_AFTER_PROVIDER",
        "CRASH_BEFORE_RECONCILE",
    ],
)
def test_crash_injection_flags(approved, flag_name):
    setattr(provider_gate, flag_name, True)
    with pytest.raises(RuntimeError, match="crash"):
        guarded_provider_call(
            approved.manifest_hash,
            attempt_id=f"crash-{flag_name}",
            idempotency_key=f"key-{flag_name}",
            max_cost_usd=Decimal("0.05"),
            fn=lambda: "ok",
        )


def test_judging_engine_increments_transport_after_reserve(approved):
    transport = FakeProviderTransport()
    engine = JudgingEngine(
        manifest_hash=approved.manifest_hash,
        cap_usd=Decimal("1.00"),
        transport=transport,
    )
    spec = JudgeAttemptSpec(
        arm_id="arm-a",
        attempt_id="att-1",
        idempotency_key="jk-1",
        input_fingerprint="fp-1",
        max_cost_usd=Decimal("0.05"),
    )
    result = engine.execute_attempt(spec)
    assert result.status == "succeeded"
    assert transport.call_count == 1


def test_judging_engine_zero_cap_makes_zero_transport_calls(approved):
    transport = FakeProviderTransport()
    engine = JudgingEngine(
        manifest_hash=approved.manifest_hash,
        cap_usd=Decimal("0"),
        transport=transport,
    )
    spec = JudgeAttemptSpec(
        arm_id="arm-a",
        attempt_id="att-1",
        idempotency_key="jk-1",
        input_fingerprint="fp-1",
        max_cost_usd=Decimal("0.05"),
    )
    with pytest.raises(AuthorizationError, match="zero calls"):
        engine.execute_attempt(spec)
    assert transport.call_count == 0


def test_judging_engine_cache_hit_skips_transport(approved):
    transport = FakeProviderTransport()
    engine = JudgingEngine(
        manifest_hash=approved.manifest_hash,
        cap_usd=Decimal("1.00"),
        transport=transport,
    )
    spec1 = JudgeAttemptSpec(
        arm_id="arm-a",
        attempt_id="att-1",
        idempotency_key="jk-1",
        input_fingerprint="same-fp",
        max_cost_usd=Decimal("0.05"),
    )
    spec2 = JudgeAttemptSpec(
        arm_id="arm-b",
        attempt_id="att-2",
        idempotency_key="jk-2",
        input_fingerprint="same-fp",
        max_cost_usd=Decimal("0.05"),
    )
    engine.execute_attempt(spec1)
    engine.execute_attempt(spec2)
    assert transport.call_count == 1
    assert engine.completed[("arm-b", "att-2")].status == "cached"
