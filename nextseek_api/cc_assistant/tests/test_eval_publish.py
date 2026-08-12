import pytest

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.eval.publish import FitGroup, FitResult, publish

pytestmark = pytest.mark.django_db


@pytest.fixture
def fit_result():
    return FitResult(
        groups=[
            FitGroup("batch_upload_preparation"),
            FitGroup("cc_sandbox_contract"),
        ],
        input_hash="input-a",
        attempt_hash="attempt-a",
        aggregate_hash="aggregate-a",
        config_fingerprint="cfg-a",
        compatibility_keys={"taxonomy_version": "v1", "corpus_hash": "corpus-a"},
        counts={"retained_pairs": 10},
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
