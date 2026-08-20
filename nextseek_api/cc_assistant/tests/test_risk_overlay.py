import pytest

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.cc_assistant.risk_overlay import assess
from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    GenerationManifest,
    activate_generation,
)
from nextseek_api.cc_assistant.tests.generation_test_factory import (
    _publish_generation_for_test,
)
from nextseek_api.eval.paired_run_registry import register_paired_run
from nextseek_api.cc_assistant.family_labels import corpus_snapshot

pytestmark = pytest.mark.django_db

_PAIRED_PROVENANCE = {
    "paired_run_id": "risk-overlay-test-run",
    "paired_run_content_hash": "risk-overlay-test-hash",
    "evidence_kind": "paired_experimental",
    "route_source": "forced",
}


@pytest.fixture(autouse=True)
def _approved_risk_overlay_run():
    register_paired_run(
        paired_run_id="risk-overlay-test-run",
        schema_version="paired_run/v1",
        content_hash="risk-overlay-test-hash",
    )


@pytest.fixture
def brittle_posterior(db):
    current = corpus_snapshot()
    manifest = GenerationManifest(
        input_hash="in",
        attempt_hash="attempt",
        aggregate_hash="aggregate",
        config_fingerprint="cfg",
        decision_status="activated_all",
        groups=[
            {
                "name": "batch_upload_preparation",
                "route": "container_cc",
                "posterior_mean": 0.2,
                "band": "Brittle",
                "n_total": 10,
            }
        ],
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts={"retained_pairs": 10},
        source_provenance=dict(_PAIRED_PROVENANCE),
    )
    generation = _publish_generation_for_test(manifest)
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    return FamilyPosterior.objects.get(task_family="batch_upload_preparation")


@pytest.fixture
def sparse_posterior(db):
    current = corpus_snapshot()
    manifest = GenerationManifest(
        input_hash="sparse",
        attempt_hash="sparse-a",
        aggregate_hash="sparse-g",
        config_fingerprint="cfg-sparse",
        decision_status="activated_all",
        groups=[
            {
                "name": "cross_session_memory",
                "route": "nextseek_query",
                "posterior_mean": 0.5,
                "band": "TooUncertain",
                "n_total": 2,
            }
        ],
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts={"retained_pairs": 10},
        source_provenance=dict(_PAIRED_PROVENANCE),
    )
    generation = _publish_generation_for_test(manifest)
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)


@pytest.fixture
def no_posteriors(db):
    PosteriorGeneration.objects.all().delete()
    FamilyPosterior.objects.all().delete()


def test_brittle_family_is_flagged(brittle_posterior, settings):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    verdict = assess("container_cc", "batch_upload_preparation")
    assert verdict.level == "high"


def test_unknown_family_falls_back_to_the_legacy_router(no_posteriors, settings):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    verdict = assess("container_cc", "cc_sandbox_contract")
    assert verdict.level == "unknown"


def test_too_uncertain_never_produces_a_confident_verdict(sparse_posterior, settings):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    assert assess("nextseek_query", "cross_session_memory").level == "unknown"


def test_overlay_can_never_authorise_a_reroute(brittle_posterior, settings):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    assert assess("container_cc", "batch_upload_preparation").may_reroute is False


def test_overlay_is_disabled_by_default(settings, brittle_posterior):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = False
    assert assess("container_cc", "batch_upload_preparation").level == "disabled"


def test_snapshot_without_matching_row_is_unknown(brittle_posterior, settings):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    assert assess("container_cc", "no_such_family").level == "unknown"


def test_reliable_band_is_low(db, settings):
    current = corpus_snapshot()
    manifest = GenerationManifest(
        input_hash="reliable",
        attempt_hash="reliable-a",
        aggregate_hash="reliable-g",
        config_fingerprint="cfg-reliable",
        decision_status="activated_all",
        groups=[
            {
                "name": "batch_upload_preparation",
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
        source_provenance=dict(_PAIRED_PROVENANCE),
    )
    generation = _publish_generation_for_test(manifest)
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    verdict = assess("container_cc", "batch_upload_preparation")
    assert verdict.level == "low"


def test_unlisted_band_is_medium(db, settings):
    current = corpus_snapshot()
    manifest = GenerationManifest(
        input_hash="watch",
        attempt_hash="watch-a",
        aggregate_hash="watch-g",
        config_fingerprint="cfg-watch",
        decision_status="activated_all",
        groups=[
            {
                "name": "batch_upload_preparation",
                "route": "container_cc",
                "posterior_mean": 0.6,
                "band": "Watch",
                "n_total": 10,
            }
        ],
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts={"retained_pairs": 10},
        source_provenance=dict(_PAIRED_PROVENANCE),
    )
    generation = _publish_generation_for_test(manifest)
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = True
    verdict = assess("container_cc", "batch_upload_preparation")
    assert verdict.level == "medium"
    assert "Watch" in verdict.reason
