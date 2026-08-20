"""V4-8 PaidRunState resume + schedule refuse tests (Lane C)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nextseek_api.assistant.models_db import PaidRunState
from nextseek_api.eval.fake_provider import FakeProviderTransport
from nextseek_api.eval.judging_engine import JudgeAttemptSpec, JudgingEngine
from nextseek_api.eval.paid_run_schedule import ScheduleRefused, default_schedule_entrypoint
from nextseek_api.eval.paid_run_state import (
    ResumeError,
    acquire_overlap_lock,
    build_cache_key,
    ensure_attempt_pending,
    get_attempt_state,
    mark_attempt_succeeded,
)
from nextseek_api.eval.run_authorization import approve_run_manifest
from nextseek_api.eval.tests.v4_8_fixtures import sample_run_manifest

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved():
    return approve_run_manifest(sample_run_manifest())


def test_overlap_lock_refuses_second_run(approved):
    acquire_overlap_lock(run_id="run-1", manifest_hash=approved.manifest_hash)
    with pytest.raises(ResumeError, match="overlap lock"):
        acquire_overlap_lock(run_id="run-1", manifest_hash=approved.manifest_hash)


def test_completed_attempt_never_repeated(approved):
    transport = FakeProviderTransport()
    engine = JudgingEngine(
        manifest_hash=approved.manifest_hash,
        cap_usd=Decimal("1.00"),
        run_id="run-resume",
        transport=transport,
    )
    spec = JudgeAttemptSpec(
        arm_id="arm-a",
        attempt_id="att-1",
        idempotency_key="jk-1",
        input_fingerprint="fp-1",
        max_cost_usd=Decimal("0.05"),
    )
    first = engine.execute_attempt(spec)
    second = engine.execute_attempt(spec)
    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert transport.call_count == 1


def test_cache_key_binds_input_and_versions(approved):
    key = build_cache_key(
        input_fingerprint="fp",
        manifest_hash=approved.manifest_hash,
        model_version="v2",
    )
    assert approved.manifest_hash in key
    assert "fp" in key
    assert key.endswith(":v2")


def test_durable_state_tracks_pending_to_succeeded(approved):
    cache_key = build_cache_key(
        input_fingerprint="fp",
        manifest_hash=approved.manifest_hash,
        model_version="v1",
    )
    state = ensure_attempt_pending(
        run_id="run-db",
        manifest_hash=approved.manifest_hash,
        arm_id="arm-a",
        attempt_id="att-1",
        cache_key=cache_key,
    )
    assert state.status == PaidRunState.STATUS_PENDING
    mark_attempt_succeeded(state)
    loaded = get_attempt_state(run_id="run-db", arm_id="arm-a", attempt_id="att-1")
    assert loaded is not None
    assert loaded.status == PaidRunState.STATUS_SUCCEEDED


def test_default_schedule_entrypoint_refuses():
    with pytest.raises(ScheduleRefused):
        default_schedule_entrypoint(enabled=False)
    with pytest.raises(ScheduleRefused):
        default_schedule_entrypoint(enabled=True)
