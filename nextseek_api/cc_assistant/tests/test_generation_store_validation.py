import pytest
from dataclasses import replace

from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    GenerationManifest,
    PermissionError,
    activate_generation,
    create_generation,
    get_active_snapshot,
    get_pinned_snapshot_for_turn,
    pin_generation_for_turn,
    publish_generation,
)
from nextseek_api.eval.generation_validation import ValidationError, validate_generation_for_activation
from nextseek_api.assistant.models_db import ChatSession, GenerationActivationAudit, TurnLedger
from nextseek_api.cc_assistant.family_labels import corpus_snapshot
from nextseek_api.eval.paired_run_registry import register_paired_run
from nextseek_api.assistant.models_db import FamilyPosterior

pytestmark = pytest.mark.django_db


def _manifest(**overrides):
    current = corpus_snapshot()
    paired_run_id = "generation-store-validation"
    register_paired_run(
        paired_run_id=paired_run_id,
        schema_version="v1",
        content_hash="0" * 64,
    )
    base = GenerationManifest(
        input_hash="input-a",
        attempt_hash="attempt-a",
        aggregate_hash="aggregate-a",
        config_fingerprint="cfg-a",
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
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts={"retained_pairs": 10},
        source_provenance={
            "paired_run_id": paired_run_id,
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
        },
    )
    return replace(base, **overrides)


def test_validate_refuses_missing_compatibility_keys():
    generation = publish_generation(_manifest(compatibility_keys={}))
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("compatibility" in reason for reason in result.reasons)


def test_validate_refuses_compatibility_with_stale_current_corpus():
    generation = publish_generation(
        _manifest(compatibility_keys={"taxonomy_version": "old", "corpus_hash": "stale"})
    )
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert sum("compatibility" in reason for reason in result.reasons) >= 2


def test_validate_refuses_mutated_posterior_row():
    generation = publish_generation(_manifest())
    row = FamilyPosterior.objects.get(generation=generation)
    row.posterior_mean = 0.1
    row.save(update_fields=["posterior_mean"])
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("posterior rows" in reason for reason in result.reasons)


def test_validate_refuses_payload_drift_from_canonical_hash_inputs():
    generation = publish_generation(_manifest())
    generation.payload = {**generation.payload, "counts": {"retained_pairs": 999}}
    generation.save(update_fields=["payload"])
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("payload counts" in reason for reason in result.reasons)


def test_validate_refuses_partial_publish():
    generation = publish_generation(_manifest())
    generation.payload = {**(generation.payload or {}), "partial_publish": True}
    generation.save(update_fields=["payload"])
    result = validate_generation_for_activation(generation)
    assert not result.ok


def test_validate_refuses_filename_only_validation():
    generation = publish_generation(_manifest())
    generation.payload = {**(generation.payload or {}), "filename_only_validation": True}
    generation.save(update_fields=["payload"])
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("filename" in reason for reason in result.reasons)


def test_validate_refuses_stale_generation():
    generation = publish_generation(_manifest())
    generation.payload = {**(generation.payload or {}), "stale": True}
    generation.save(update_fields=["payload"])
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("stale" in reason for reason in result.reasons)


def test_validate_refuses_invalid_decision_status():
    generation = publish_generation(_manifest(decision_status="not_a_real_status"))
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("decision_status" in reason for reason in result.reasons)


def test_validate_refuses_precision_floor_on_retained_pairs():
    generation = publish_generation(_manifest(counts={"retained_pairs": 1}))
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("precision" in reason or "retained" in reason for reason in result.reasons)


def test_validate_refuses_precision_floor_on_n_total():
    generation = publish_generation(
        _manifest(
            groups=[
                {
                    "name": "sample_search",
                    "route": "container_cc",
                    "posterior_mean": 0.9,
                    "band": "Reliable",
                    "n_total": 0,
                }
            ]
        )
    )
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("n_total" in reason for reason in result.reasons)


def test_activate_refuses_invalid_generation():
    generation = publish_generation(_manifest(compatibility_keys={}))
    with pytest.raises(ValidationError):
        activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)


def test_activation_writes_audit_row():
    generation = publish_generation(_manifest())
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    assert GenerationActivationAudit.objects.filter(action="activate").count() == 1


@pytest.fixture
def turn(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="pin-user", password="x")
    session = ChatSession.objects.create(user=user)
    return TurnLedger.objects.create(
        session=session,
        turn_number=1,
        route="container_cc",
        route_source="legacy",
        task_family="sample_search",
        family_source="classifier",
    )


def test_pin_generation_for_turn_is_stable_after_activation(turn):
    gen_a = publish_generation(_manifest(input_hash="a"))
    gen_b = publish_generation(_manifest(input_hash="b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    pin_generation_for_turn(turn)
    pinned = get_pinned_snapshot_for_turn(turn)
    assert pinned is not None
    assert pinned.generation_hash == gen_a.generation_hash
    activate_generation(gen_b, expected_hash=gen_a.generation_hash)
    assert get_active_snapshot().generation_hash == gen_b.generation_hash
    pinned_after = get_pinned_snapshot_for_turn(turn)
    assert pinned_after.generation_hash == gen_a.generation_hash


def test_live_publish_requires_maintainer_approval():
    with pytest.raises(PermissionError, match="live publish"):
        create_generation(_manifest(input_hash="live-pub"), actor="live:maintainer")


def test_live_activate_requires_maintainer_approval():
    generation = publish_generation(_manifest(input_hash="live-act"))
    with pytest.raises(PermissionError, match="live activation"):
        activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH, activated_by="live:ops")
