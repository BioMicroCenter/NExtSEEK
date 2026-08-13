import pytest
from decimal import Decimal

from nextseek_api.assistant.models_db import SpendReservation
from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    ActivationError,
    GenerationManifest,
    activate_generation,
    publish_generation,
)
from nextseek_api.eval.paired_run_registry import register_paired_run
from nextseek_api.eval.provider_gate import AuthorizationError, guarded_provider_call
from nextseek_api.eval.run_authorization import approve_run_manifest, manifest_hash, reserve_budget
from nextseek_api.eval.tests.v4_8_fixtures import sample_manifest_dict, sample_run_manifest

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved_manifest():
    return approve_run_manifest(sample_run_manifest(max_calls=3))


def _generation_manifest(
    *, input_hash: str, attempt_hash: str, aggregate_hash: str, n_total: int
) -> GenerationManifest:
    paired_run_id = "run-authorization-generation"
    register_paired_run(
        paired_run_id=paired_run_id,
        schema_version="v1",
        content_hash="0" * 64,
    )
    return GenerationManifest(
        input_hash=input_hash,
        attempt_hash=attempt_hash,
        aggregate_hash=aggregate_hash,
        config_fingerprint="cfg",
        decision_status="activated_all",
        groups=[
            {
                "name": "sample_search",
                "route": "container_cc",
                "posterior_mean": 0.9,
                "band": "Reliable",
                "n_total": n_total,
            }
        ],
        compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "abc"},
        counts={"retained_pairs": 10},
        source_provenance={
            "paired_run_id": paired_run_id,
            "paired_run_content_hash": "0" * 64,
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
        },
    )


def test_reserve_budget_enforces_cap(approved_manifest):
    reserve_budget(
        approved_manifest.manifest_hash,
        attempt_id="a1",
        idempotency_key="k1",
        max_cost_usd=Decimal("0.40"),
    )
    reserve_budget(
        approved_manifest.manifest_hash,
        attempt_id="a2",
        idempotency_key="k2",
        max_cost_usd=Decimal("0.40"),
    )
    with pytest.raises(AuthorizationError, match="spend cap"):
        reserve_budget(
            approved_manifest.manifest_hash,
            attempt_id="a3",
            idempotency_key="k3",
            max_cost_usd=Decimal("0.40"),
        )


def test_idempotency_key_replays_same_reservation(approved_manifest):
    first = reserve_budget(
        approved_manifest.manifest_hash,
        attempt_id="a1",
        idempotency_key="same-key",
        max_cost_usd=Decimal("0.10"),
    )
    second = reserve_budget(
        approved_manifest.manifest_hash,
        attempt_id="a1",
        idempotency_key="same-key",
        max_cost_usd=Decimal("0.10"),
    )
    assert first.attempt_id == second.attempt_id
    assert SpendReservation.objects.count() == 1


def test_guarded_provider_call_requires_reservation(approved_manifest):
    calls = {"n": 0}

    def _provider():
        calls["n"] += 1
        return "ok"

    result = guarded_provider_call(
        approved_manifest.manifest_hash,
        attempt_id="attempt-1",
        idempotency_key="guard-1",
        max_cost_usd=Decimal("0.05"),
        fn=_provider,
        actual_cost_fn=lambda _: Decimal("0.04"),
    )
    assert result == "ok"
    assert calls["n"] == 1
    reservation = SpendReservation.objects.get(attempt_id="attempt-1")
    assert reservation.status == SpendReservation.STATUS_RECONCILED
    assert reservation.actual_usd == Decimal("0.04")


def test_activate_generation_cas_refuses_stale_hash():
    manifest_a = _generation_manifest(
        input_hash="in-a",
        attempt_hash="attempt-a",
        aggregate_hash="aggregate-a",
        n_total=10,
    )
    manifest_b = _generation_manifest(
        input_hash="in-b",
        attempt_hash="attempt-b",
        aggregate_hash="aggregate-b",
        n_total=11,
    )
    gen_a = publish_generation(manifest_a)
    gen_b = publish_generation(manifest_b)
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    with pytest.raises(ActivationError, match="stale CAS"):
        activate_generation(gen_b, expected_hash=gen_b.generation_hash)


def test_manifest_hash_is_stable():
    manifest = sample_manifest_dict()
    assert manifest_hash(manifest) == manifest_hash(dict(manifest))
