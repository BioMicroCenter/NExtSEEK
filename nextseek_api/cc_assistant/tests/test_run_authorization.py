import pytest
from decimal import Decimal

from nextseek_api.assistant.models_db import SpendReservation
from nextseek_api.eval.generation_store import ActivationError, activate_generation, create_generation
from nextseek_api.eval.provider_gate import AuthorizationError, guarded_provider_call
from nextseek_api.eval.run_authorization import approve_manifest, manifest_hash, reserve_budget

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved_manifest():
    manifest = {"corpus": "test", "pairs": ["a.one"]}
    return approve_manifest(
        manifest,
        max_spend_usd=Decimal("1.00"),
        max_calls=3,
        ttl_seconds=3600,
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
    generation = create_generation(
        input_hash="in",
        config_fingerprint="cfg",
        decision_status="activated_all",
        groups=[
            {
                "name": "sample_search",
                "route": "container_cc",
                "posterior_mean": 0.9,
                "band": "Reliable",
                "n_total": 10,
            }
        ],
    )
    activate_generation(generation, expected_hash=generation.generation_hash)
    from nextseek_api.assistant.models_db import ActiveGenerationPointer

    pointer = ActiveGenerationPointer.objects.get(pk=1)
    pointer.expected_hash = "stale-hash"
    pointer.save(update_fields=["expected_hash"])
    with pytest.raises(ActivationError, match="stale CAS"):
        activate_generation(generation, expected_hash=generation.generation_hash)


def test_manifest_hash_is_stable():
    manifest = {"pairs": ["a.one"], "taxonomy": "v1"}
    assert manifest_hash(manifest) == manifest_hash(dict(pairs=["a.one"], taxonomy="v1"))
