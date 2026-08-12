"""V4-8 reservation + conservation tests (Lane C)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from nextseek_api.assistant.models_db import ApprovedRunManifest, SpendReservation
from nextseek_api.eval.run_authorization import (
    AuthorizationError,
    approve_manifest,
    approve_run_manifest,
    expire_stale_reservations,
    mark_manifest_consumed,
    reconcile_reservation,
    release_reservation,
    reserve_budget,
)
from nextseek_api.eval.spend_conservation import compute_conservation
from nextseek_api.eval.run_manifest import manifest_body_hash
from nextseek_api.eval.tests.v4_8_fixtures import sample_manifest_dict, sample_run_manifest

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved():
    return approve_run_manifest(sample_run_manifest())


def test_create_once_returns_existing_same_body():
    body = sample_manifest_dict()
    first = approve_manifest(body)
    second = approve_manifest(dict(body))
    assert second.pk == first.pk


def test_changed_manifest_gets_new_approval():
    first = approve_manifest(sample_manifest_dict(corpus_id="corpus-a"))
    second = approve_manifest(sample_manifest_dict(corpus_id="corpus-b"))
    assert first.manifest_hash != second.manifest_hash
    assert second.pk != first.pk


def test_hash_collision_with_different_body_refused():
    body = sample_manifest_dict()
    fp = manifest_body_hash(body)
    from nextseek_api.assistant.models_db import ApprovedRunManifest
    from django.utils import timezone
    from decimal import Decimal

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


def test_reserve_budget_enforces_spend_cap(approved):
    reserve_budget(
        approved.manifest_hash,
        attempt_id="a1",
        idempotency_key="k1",
        max_cost_usd=Decimal("0.40"),
    )
    reserve_budget(
        approved.manifest_hash,
        attempt_id="a2",
        idempotency_key="k2",
        max_cost_usd=Decimal("0.40"),
    )
    with pytest.raises(AuthorizationError, match="spend cap"):
        reserve_budget(
            approved.manifest_hash,
            attempt_id="a3",
            idempotency_key="k3",
            max_cost_usd=Decimal("0.40"),
        )


def test_reserve_budget_enforces_call_cap(approved):
    approved.max_calls = 2
    approved.save(update_fields=["max_calls"])
    reserve_budget(
        approved.manifest_hash,
        attempt_id="c1",
        idempotency_key="ck1",
        max_cost_usd=Decimal("0.01"),
    )
    reserve_budget(
        approved.manifest_hash,
        attempt_id="c2",
        idempotency_key="ck2",
        max_cost_usd=Decimal("0.01"),
    )
    with pytest.raises(AuthorizationError, match="call cap"):
        reserve_budget(
            approved.manifest_hash,
            attempt_id="c3",
            idempotency_key="ck3",
            max_cost_usd=Decimal("0.01"),
        )


def test_idempotency_key_replays_same_reservation(approved):
    first = reserve_budget(
        approved.manifest_hash,
        attempt_id="a1",
        idempotency_key="same-key",
        max_cost_usd=Decimal("0.10"),
    )
    second = reserve_budget(
        approved.manifest_hash,
        attempt_id="a1",
        idempotency_key="same-key",
        max_cost_usd=Decimal("0.10"),
    )
    assert first.attempt_id == second.attempt_id
    assert SpendReservation.objects.count() == 1


def test_non_positive_reservation_refused(approved):
    with pytest.raises(AuthorizationError, match="non-positive"):
        reserve_budget(
            approved.manifest_hash,
            attempt_id="np",
            idempotency_key="npk",
            max_cost_usd=Decimal("0"),
        )


def test_expired_manifest_refused():
    record = approve_run_manifest(sample_run_manifest())
    record.expires_at = timezone.now() - timedelta(seconds=1)
    record.save(update_fields=["expires_at"])
    with pytest.raises(AuthorizationError, match="expired"):
        reserve_budget(
            record.manifest_hash,
            attempt_id="exp",
            idempotency_key="expk",
            max_cost_usd=Decimal("0.01"),
        )


def test_approve_manifest_refuses_cap_override():
    with pytest.raises(AuthorizationError, match="max_spend_usd override"):
        approve_manifest(sample_manifest_dict(), max_spend_usd=Decimal("9.99"))


def test_approve_manifest_refuses_call_override():
    with pytest.raises(AuthorizationError, match="max_calls override"):
        approve_manifest(sample_manifest_dict(), max_calls=999)


def test_approve_manifest_refuses_ttl_override():
    with pytest.raises(AuthorizationError, match="ttl_seconds override"):
        approve_manifest(sample_manifest_dict(), ttl_seconds=60)


def test_consumed_manifest_refused(approved):
    mark_manifest_consumed(approved.manifest_hash)
    with pytest.raises(AuthorizationError, match="consumed"):
        reserve_budget(
            approved.manifest_hash,
            attempt_id="cons",
            idempotency_key="conk",
            max_cost_usd=Decimal("0.01"),
        )


def test_conservation_balanced_after_reconcile_and_release(approved):
    reserve_budget(
        approved.manifest_hash,
        attempt_id="r1",
        idempotency_key="rk1",
        max_cost_usd=Decimal("0.20"),
    )
    reserve_budget(
        approved.manifest_hash,
        attempt_id="r2",
        idempotency_key="rk2",
        max_cost_usd=Decimal("0.10"),
    )
    reconcile_reservation("r1", actual_usd=Decimal("0.18"))
    release_reservation("r2")
    snap = compute_conservation(ApprovedRunManifest.objects.get(pk=approved.pk))
    snap.assert_balanced()


def test_expire_stale_reservations_frees_budget(approved):
    reserve_budget(
        approved.manifest_hash,
        attempt_id="old",
        idempotency_key="oldk",
        max_cost_usd=Decimal("0.50"),
    )
    reservation = SpendReservation.objects.get(attempt_id="old")
    reservation.created_at = timezone.now() - timedelta(hours=2)
    reservation.save(update_fields=["created_at"])
    assert expire_stale_reservations(older_than_seconds=60) == 1
    snap = compute_conservation(ApprovedRunManifest.objects.get(pk=approved.pk))
    snap.assert_balanced()
