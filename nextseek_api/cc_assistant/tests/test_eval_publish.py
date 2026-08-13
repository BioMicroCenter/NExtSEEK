import numpy as np
import pytest

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.eval.fit.v14.combined import CombinedFitResult
from nextseek_api.eval.fit.v14.decision import CandidateDecision, DecisionStatus, GenerationDecision
from nextseek_api.eval.fit.v14.latency_model import LatencyFitResult
from nextseek_api.eval.fit.v14.quality_model import QualityFitResult
from nextseek_api.eval.paired_run_registry import register_paired_run
from nextseek_api.eval.publish import (
    FitGroup,
    FitResult,
    PublicationEvidence,
    PublicationEvidenceRequired,
    publish,
)

pytestmark = pytest.mark.django_db

_PAIRED_PROVENANCE = {
    "paired_run_id": "publish-test-run",
    "evidence_kind": "paired_experimental",
    "route_source": "forced",
}


@pytest.fixture(autouse=True)
def _approved_publish_run():
    for run_id, content_hash in (
        ("publish-test-run", "publish-test-hash"),
        ("combined-fit-local", "combined-fit-local-hash"),
    ):
        register_paired_run(
            paired_run_id=run_id,
            schema_version="paired_run/v1",
            content_hash=content_hash,
        )


@pytest.fixture
def fit_result():
    return FitResult(
        groups=[
            FitGroup("batch_upload_preparation", "container_cc", 0.97, "Reliable", 5),
            FitGroup("cc_sandbox_contract", "container_cc", 0.97, "Reliable", 5),
        ],
        input_hash="input-a",
        attempt_hash="attempt-a",
        aggregate_hash="aggregate-a",
        config_fingerprint="cfg-a",
        compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "corpus-a"},
        counts={"retained_pairs": 10},
        fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
        source_provenance={
            **_PAIRED_PROVENANCE,
            "model_mode": "authoritative_mcmc",
            "functional_success_source": "stored_judgments",
        },
    )


@pytest.fixture
def sparse_fit_result():
    return FitResult(
        groups=[
            FitGroup(
                "cross_session_memory",
                route="nextseek_query",
                posterior_mean=0.5,
                band="TooUncertain",
                n_total=2,
            )
        ],
        input_hash="input-sparse",
        attempt_hash="attempt-sparse",
        aggregate_hash="aggregate-sparse",
        config_fingerprint="cfg-sparse",
        decision_status="empty_candidate_set",
        compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "sparse"},
        counts={"retained_pairs": 10},
        fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
        source_provenance={
            **_PAIRED_PROVENANCE,
            "model_mode": "authoritative_mcmc",
            "functional_success_source": "stored_judgments",
        },
    )


def test_publish_stores_one_row_per_family_route_pair(fit_result):
    assert publish(fit_result) == len(fit_result.groups)
    assert FamilyPosterior.objects.count() == 2
    assert PosteriorGeneration.objects.count() == 1


def test_band_and_n_are_persisted_for_consumers(fit_result):
    publish(fit_result)
    row = FamilyPosterior.objects.first()
    assert row.band in {"Reliable", "Watch", "Brittle", "TooUncertain"}
    assert row.n_total >= 0


def test_a_family_below_the_floor_is_too_uncertain(sparse_fit_result):
    publish(sparse_fit_result)
    assert (
        FamilyPosterior.objects.get(task_family="cross_session_memory").band
        == "TooUncertain"
    )


def test_republishing_replaces_rather_than_duplicates(fit_result):
    publish(fit_result)
    publish(fit_result)
    assert PosteriorGeneration.objects.count() == 1
    assert (
        FamilyPosterior.objects.filter(task_family=fit_result.groups[0].name).count()
        == 1
    )


def test_generic_publish_refuses_non_authoritative_result_without_override(fit_result):
    fit_result.fit_diagnostics = {"authoritative": False, "diagnostics_ok": False}
    with pytest.raises(PublicationEvidenceRequired, match="authoritative diagnostics"):
        publish(fit_result)
    assert PosteriorGeneration.objects.count() == 0


def test_fit_group_has_no_fabricated_statistical_defaults():
    with pytest.raises(TypeError):
        FitGroup("family-only")


def test_publish_combined_fit_result_uses_decision_bands():
    decision = GenerationDecision(
        candidates=(
            CandidateDecision(
                family="sample_search",
                status=DecisionStatus.quality_cc,
                local_error_prob=0.05,
                activated=True,
            ),
        ),
        posterior_expected_fdr=0.01,
        activated_families=("sample_search",),
        generation_status="activated_all",
        config_fingerprint="fp-combined-test",
    )
    combined = CombinedFitResult(
        quality={
            "sample_search": QualityFitResult(
                family="sample_search",
                state_probs=np.array([0.7, 0.1, 0.1, 0.1]),
                quality_advantage_ns=0.2,
                posterior_samples_advantage=np.array([0.2, 0.19, 0.21]),
                divergences=0,
                rhat_max=1.0,
                ess_bulk_min=100.0,
                ess_tail_min=100.0,
            )
        },
        latency={
            "sample_search": LatencyFitResult(
                family="sample_search",
                posterior_log_d=np.array([-0.1, 0.0, 0.1]),
                posterior_ns_faster_prob=0.4,
                divergences=0,
                rhat_max=1.0,
                ess_bulk_min=100.0,
                ess_tail_min=100.0,
            )
        },
        decision=decision,
        diagnostics_ok=True,
    )
    evidence = PublicationEvidence(
        input_hash="input-combined-test",
        attempt_hash="attempt-combined-test",
        aggregate_hash="aggregate-combined-test",
        compatibility_keys={"taxonomy_version": "2", "corpus_hash": "corpus-combined-test"},
        counts={"retained_pairs": 5},
        exclusions={},
        fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
        source_provenance={
            "paired_run_id": "combined-fit-local",
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
            "model_mode": "authoritative_mcmc",
            "functional_success_source": "stored_judgments",
        },
        family_retained_pairs={"sample_search": 5},
    )
    count = publish(combined, evidence=evidence)
    assert count == 1
    row = FamilyPosterior.objects.get(task_family="sample_search")
    assert row.band == "Reliable"
    assert row.route == "container_cc"
