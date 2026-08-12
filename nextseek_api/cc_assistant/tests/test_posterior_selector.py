"""V4-6 posterior selector differential tests."""
import pytest
from django.utils import timezone

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.cc_assistant import posterior_selector
from nextseek_api.eval.generation_store import GenerationSnapshot, get_active_snapshot

pytestmark = pytest.mark.django_db


def _activate_generation(**kwargs):
    gen = PosteriorGeneration.objects.create(
        generation_hash=kwargs.get("generation_hash", "a" * 64),
        decision_status=kwargs.get("decision_status", "activated_all"),
        payload={},
    )
    from nextseek_api.eval.generation_store import ActiveGenerationPointer

    ptr, _ = ActiveGenerationPointer.objects.get_or_create(id=1)
    ptr.active = gen
    ptr.save(update_fields=["active"])
    return gen


def test_posterior_routing_disabled_by_default(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = False
    assert not posterior_selector.posterior_routing_enabled()


def test_decisive_posterior_selects_route(settings):
    gen = _activate_generation()
    FamilyPosterior.objects.create(
        generation=gen,
        task_family="sample_search",
        route="nextseek_query",
        posterior_mean=0.92,
        band="Reliable",
        n_total=10,
        fitted_at=timezone.now(),
    )
    FamilyPosterior.objects.create(
        generation=gen,
        task_family="sample_search",
        route="container_cc",
        posterior_mean=0.40,
        band="Reliable",
        n_total=10,
        fitted_at=timezone.now(),
    )
    result = posterior_selector.select_route("sample_search")
    assert result is not None
    assert result.route == "nextseek_query"
    assert result.generation_id == gen.id


def test_too_uncertain_falls_back(settings):
    gen = _activate_generation()
    FamilyPosterior.objects.create(
        generation=gen,
        task_family="sample_search",
        route="nextseek_query",
        posterior_mean=0.5,
        band="TooUncertain",
        n_total=3,
        fitted_at=timezone.now(),
    )
    assert posterior_selector.select_route("sample_search") is None


def test_missing_posterior_falls_back(settings):
    _activate_generation()
    assert posterior_selector.select_route("unknown_family") is None


def test_poisoned_generation_status_falls_back(settings):
    _activate_generation(decision_status="multiplicity_indecisive")
    assert posterior_selector.select_route("sample_search") is None
