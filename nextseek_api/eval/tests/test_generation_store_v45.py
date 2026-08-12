import pytest
from dataclasses import replace

from nextseek_api.eval.generation_validation import validate_generation_for_activation
from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    ActivationError,
    GenerationManifest,
    PublishError,
    activate_generation,
    create_generation,
    get_current_active_hash,
    publish_generation,
    rollback_generation,
)

pytestmark = pytest.mark.django_db


def _group(name="sample_search", **kwargs):
    return {
        "name": name,
        "route": kwargs.get("route", "container_cc"),
        "posterior_mean": kwargs.get("posterior_mean", 0.9),
        "band": kwargs.get("band", "Reliable"),
        "n_total": kwargs.get("n_total", 10),
    }


def _manifest(**overrides):
    base = GenerationManifest(
        input_hash="input-a",
        attempt_hash="attempt-a",
        aggregate_hash="aggregate-a",
        config_fingerprint="cfg-a",
        decision_status="activated_all",
        groups=[_group()],
        compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "corpus-a"},
        counts={"retained_pairs": 10},
        source_provenance={"origin": "test"},
    )
    return replace(base, **overrides)


def test_create_generation_is_idempotent():
    manifest = _manifest()
    first = create_generation(manifest)
    second = create_generation(manifest)
    assert first.id == second.id


def test_create_generation_refuses_conflicting_overwrite():
    manifest = _manifest()
    generation = create_generation(manifest)
    generation.generation_hash = "0" * 64
    generation.save(update_fields=["generation_hash"])
    result = validate_generation_for_activation(generation)
    assert not result.ok
    assert any("hash" in reason for reason in result.reasons)


def test_activate_generation_a_to_b_with_cas():
    gen_a = publish_generation(_manifest(input_hash="a"))
    gen_b = publish_generation(_manifest(input_hash="b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    assert get_current_active_hash() == gen_a.generation_hash
    activate_generation(gen_b, expected_hash=gen_a.generation_hash)
    assert get_current_active_hash() == gen_b.generation_hash


def test_activate_generation_refuses_stale_cas():
    gen_a = publish_generation(_manifest(input_hash="a"))
    gen_b = publish_generation(_manifest(input_hash="b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    with pytest.raises(ActivationError, match="stale CAS"):
        activate_generation(gen_b, expected_hash=gen_b.generation_hash)


def test_rollback_restores_previous_generation():
    gen_a = publish_generation(_manifest(input_hash="a"))
    gen_b = publish_generation(_manifest(input_hash="b"))
    activate_generation(gen_a, expected_hash=EMPTY_ACTIVE_HASH)
    activate_generation(gen_b, expected_hash=gen_a.generation_hash)
    rollback_generation(expected_hash=gen_b.generation_hash)
    assert get_current_active_hash() == gen_a.generation_hash
