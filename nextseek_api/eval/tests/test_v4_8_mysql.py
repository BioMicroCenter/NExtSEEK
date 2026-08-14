"""V4-8 MySQL real-store reservation oracles (Lane M)."""
from __future__ import annotations

import multiprocessing as mp
from decimal import Decimal

import pytest
from django.utils import timezone

from nextseek_api.assistant.models_db import SpendReservation
from nextseek_api.eval.provider_gate import guarded_provider_call
from nextseek_api.eval.run_authorization import (
    AuthorizationError,
    approve_run_manifest,
    expire_stale_reservations,
    release_reservation,
    reserve_budget,
)
from nextseek_api.eval.tests.v4_8_fixtures import sample_run_manifest
from nextseek_api.eval.tests.v4_8_mysql_workers import (
    mp_broker_redelivery_worker,
    mp_crash_worker,
    mp_idempotency_replay_worker,
    mp_reserve_worker,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _manifest_body(suffix: str) -> dict:
    return sample_run_manifest(corpus_id=f"corpus-{suffix}").model_dump(mode="json")


def _approved(suffix: str):
    return approve_run_manifest(sample_run_manifest(corpus_id=f"corpus-{suffix}"))


@pytest.mark.django_db(transaction=False)
def test_mysql_nway_contention_no_overspend():
    body = _manifest_body("mp-contention")
    approved = approve_run_manifest(sample_run_manifest(corpus_id="corpus-mp-contention"))
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    barrier = manager.Barrier(4)
    payloads = [
        {
            "manifest_hash": approved.manifest_hash,
            "manifest_body": body,
            "attempt_id": f"w{idx}",
            "idempotency_key": f"k{idx}",
            "max_cost_usd": "0.30",
            "barrier": barrier,
        }
        for idx in range(4)
    ]
    with ctx.Pool(4) as pool:
        results = pool.map(mp_reserve_worker, payloads)
    assert results.count("ok") == 3
    assert results.count("refused") == 1


@pytest.mark.django_db(transaction=False)
def test_mysql_idempotency_replay_under_contention():
    body = _manifest_body("mp-replay")
    approved = approve_run_manifest(sample_run_manifest(corpus_id="corpus-mp-replay"))
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    barrier = manager.Barrier(2)
    payload = {
        "manifest_hash": approved.manifest_hash,
        "manifest_body": body,
        "attempt_id": "same",
        "idempotency_key": "same-key",
        "max_cost_usd": "0.10",
        "barrier": barrier,
    }
    with ctx.Pool(2) as pool:
        results = pool.map(mp_idempotency_replay_worker, [payload, dict(payload)])
    assert all(r["status"] == "ok" for r in results)
    assert len({r["attempt_id"] for r in results}) == 1
    assert all(r["key_count"] == 1 for r in results)


@pytest.mark.django_db(transaction=False)
@pytest.mark.parametrize(
    "flag_name,expect_exists,expect_status",
    [
        ("CRASH_BEFORE_RESERVE", False, "missing"),
        ("CRASH_AFTER_RESERVE", True, SpendReservation.STATUS_PENDING),
        ("CRASH_AFTER_PROVIDER", True, SpendReservation.STATUS_PENDING),
        ("CRASH_BEFORE_RECONCILE", True, SpendReservation.STATUS_PENDING),
    ],
)
def test_mysql_crash_injection_family(flag_name, expect_exists, expect_status):
    suffix = f"crash-{flag_name.lower()}"
    body = _manifest_body(suffix)
    approved = approve_run_manifest(sample_run_manifest(corpus_id=f"corpus-{suffix}"))
    attempt_id = f"crash-{flag_name}"
    payload = {
        "manifest_hash": approved.manifest_hash,
        "manifest_body": body,
        "attempt_id": attempt_id,
        "idempotency_key": f"key-{flag_name}",
        "max_cost_usd": "0.05",
        "flag_name": flag_name,
    }
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        outcome = pool.apply(mp_crash_worker, (payload,))
    assert outcome["raised"] is not None
    assert outcome["exists"] is expect_exists
    assert outcome["status"] == expect_status


def test_mysql_crash_after_provider_releases_on_exception():
    approved = _approved("crash")

    def _fail():
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        guarded_provider_call(
            approved.manifest_hash,
            attempt_id="crash-1",
            idempotency_key="crash-k",
            max_cost_usd=Decimal("0.05"),
            fn=_fail,
        )
    reservation = SpendReservation.objects.get(attempt_id="crash-1")
    assert reservation.status == SpendReservation.STATUS_RELEASED


@pytest.mark.django_db(transaction=False)
def test_mysql_broker_redelivery_replays_without_double_spend():
    body = _manifest_body("redelivery")
    approved = approve_run_manifest(sample_run_manifest(corpus_id="corpus-redelivery"))
    base = {
        "manifest_hash": approved.manifest_hash,
        "manifest_body": body,
        "attempt_id": "broker-1",
        "idempotency_key": "broker-k",
        "max_cost_usd": "0.10",
    }
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        first = pool.apply(
            mp_broker_redelivery_worker,
            (dict(base, phase="first_delivery"),),
        )
        second = pool.apply(
            mp_broker_redelivery_worker,
            (dict(base, phase="redelivery"),),
        )
    assert first["count"] == 1
    assert second["count"] == 1
    assert second["rows"][0]["attempt_id"] == "broker-1"


def test_mysql_orphan_pending_can_be_released():
    approved = _approved("orphan")
    reserve_budget(
        approved.manifest_hash,
        attempt_id="orphan-1",
        idempotency_key="orphan-k",
        max_cost_usd=Decimal("0.05"),
    )
    release_reservation("orphan-1")
    reservation = SpendReservation.objects.get(attempt_id="orphan-1")
    assert reservation.status == SpendReservation.STATUS_RELEASED


def test_mysql_expiry_sweep():
    approved = _approved("expiry")
    reserve_budget(
        approved.manifest_hash,
        attempt_id="exp-1",
        idempotency_key="exp-k",
        max_cost_usd=Decimal("0.05"),
    )
    reservation = SpendReservation.objects.get(attempt_id="exp-1")
    reservation.created_at = timezone.now() - timezone.timedelta(hours=2)
    reservation.save(update_fields=["created_at"])
    assert expire_stale_reservations(older_than_seconds=60) >= 1
    reservation.refresh_from_db()
    assert reservation.status == SpendReservation.STATUS_EXPIRED
