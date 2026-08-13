"""V4-6 posterior selector differential tests."""
import pytest
from django.utils import timezone

from nextseek_api.assistant.models_db import FamilyPosterior, PosteriorGeneration
from nextseek_api.cc_assistant import posterior_selector
from nextseek_api.cc_assistant.family_labels import corpus_snapshot
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
    current = corpus_snapshot()
    snap = GenerationSnapshot(
        generation_id=1,
        generation_hash="a" * 64,
        decision_status="activated_all",
        posteriors=(
            _posterior_row("sample_search", "nextseek_query", 0.92, "Reliable"),
            _posterior_row("sample_search", "container_cc", 0.40, "Reliable"),
        ),
        taxonomy_version=current.taxonomy_version,
        corpus_hash=current.corpus_sha256,
        content_valid=True,
    )
    result = posterior_selector.select_route("sample_search", snapshot=snap)
    assert result is not None
    assert result.route == "nextseek_query"
    assert result.generation_id == 1


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


def test_stale_generation_fallback(settings):
    snap = _snapshot(
        decision_status="legacy_fallback",
        posteriors=(
            _posterior_row("sample_search", "nextseek_query", 0.9, "Reliable"),
        ),
    )
    assert posterior_selector.select_route("sample_search", snapshot=snap) is None


def test_malformed_generation_fallback_invalid_route(settings):
    snap = _snapshot(
        posteriors=(
            _posterior_row("sample_search", "not_a_valid_route", 0.9, "Reliable"),
        ),
    )
    assert posterior_selector.select_route("sample_search", snapshot=snap) is None


def test_incompatible_generation_fallback_indecisive_tie(settings):
    snap = _snapshot(
        posteriors=(
            _posterior_row("sample_search", "nextseek_query", 0.50, "Reliable"),
            _posterior_row("sample_search", "container_cc", 0.52, "Reliable"),
        ),
    )
    assert posterior_selector.select_route("sample_search", snapshot=snap) is None


def test_poisoned_store_non_blocking_on_decide_path(settings, monkeypatch):
    """Poisoned active generation must not block the user turn."""
    _activate_generation(decision_status="multiplicity_indecisive")
    from dmac_assistant.router.baml_client import b
    from dmac_assistant.router.baml_client.types import ClassificationDecision, Route, RouterDecision

    async def classify_ok(*args, **kwargs):
        return ClassificationDecision(task_family="sample_search", reasoning="ok")

    async def route_ok(*args, **kwargs):
        return RouterDecision(route=Route.NextseekQuery, model_class=None, reasoning="fallback")

    from nextseek_api.cc_assistant import router as cc_router
    from nextseek_api.cc_assistant import transport_trace

    transport_trace.reset_transport_hooks()
    monkeypatch.setattr(b, "ClassifyQuery", classify_ok)
    monkeypatch.setattr(b, "RouteQuery", route_ok)
    transport_trace.install_transport_hooks(b)
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    decision = cc_router.decide("find mice")
    assert decision.route == cc_router.ROUTE_NS
    assert decision.task_family == "sample_search"


def _posterior_row(task_family, route, mean, band):
    from nextseek_api.assistant.models_db import FamilyPosterior

    return FamilyPosterior(
        task_family=task_family,
        route=route,
        posterior_mean=mean,
        band=band,
        n_total=10,
        fitted_at=timezone.now(),
    )


def _snapshot(*, generation_id=99, generation_hash="e" * 64, decision_status="activated_all", posteriors=()):
    from nextseek_api.eval.generation_store import GenerationSnapshot
    current = corpus_snapshot()

    return GenerationSnapshot(
        generation_id=generation_id,
        generation_hash=generation_hash,
        decision_status=decision_status,
        posteriors=tuple(posteriors),
        taxonomy_version=current.taxonomy_version,
        corpus_hash=current.corpus_sha256,
        content_valid=True,
    )
