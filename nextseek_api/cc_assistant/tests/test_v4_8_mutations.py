"""V4-8 mutation killers — reservation invariants must fail closed (Lane C)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nextseek_api.eval.paid_run_schedule import ScheduleRefused, default_schedule_entrypoint
from nextseek_api.eval.run_authorization import AuthorizationError, approve_manifest, reserve_budget
from nextseek_api.eval.tests.v4_8_fixtures import sample_manifest_dict

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved():
    return approve_manifest(sample_manifest_dict())


def test_mutation_skip_reserve_still_requires_gate(approved, monkeypatch):
    """Direct transport without reserve must fail at require_reservation."""
    from nextseek_api.eval import provider_gate

    def _skip_reserve(*args, **kwargs):
        raise AssertionError("reserve must not be skipped in production path")

    monkeypatch.setattr(provider_gate, "reserve_budget", _skip_reserve)
    with pytest.raises(AssertionError):
        provider_gate.guarded_provider_call(
            approved.manifest_hash,
            attempt_id="mut-1",
            idempotency_key="mut-k1",
            max_cost_usd=Decimal("0.05"),
            fn=lambda: "ok",
        )


def test_mutation_double_charge_retry_refused(approved):
    reserve_budget(
        approved.manifest_hash,
        attempt_id="dup",
        idempotency_key="dup-k",
        max_cost_usd=Decimal("0.90"),
    )
    with pytest.raises(AuthorizationError, match="spend cap"):
        reserve_budget(
            approved.manifest_hash,
            attempt_id="dup2",
            idempotency_key="dup-k2",
            max_cost_usd=Decimal("0.90"),
        )


def test_mutation_changed_manifest_gets_distinct_approval():
    body_a = sample_manifest_dict(corpus_id="corpus-a")
    body_b = sample_manifest_dict(corpus_id="corpus-b")
    first = approve_manifest(body_a)
    second = approve_manifest(body_b)
    assert first.manifest_hash != second.manifest_hash


def test_mutation_forged_collision_refused():
    body = sample_manifest_dict()
    from nextseek_api.assistant.models_db import ApprovedRunManifest
    from nextseek_api.eval.run_manifest import manifest_body_hash
    from datetime import timedelta
    from django.utils import timezone

    fp = manifest_body_hash(body)
    ApprovedRunManifest.objects.create(
        manifest_hash=fp,
        manifest={"forged": True},
        approved_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
        max_spend_usd=Decimal("1.00"),
        max_calls=10,
        consumed=False,
    )
    with pytest.raises(AuthorizationError, match="collision"):
        approve_manifest(body)


def test_mutation_schedule_entry_refused():
    with pytest.raises(ScheduleRefused):
        default_schedule_entrypoint()
