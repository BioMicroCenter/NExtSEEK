"""Runtime-critical Bayesian router wiring and fail-open behavior."""
from pathlib import Path
from unittest import mock

import pytest

from nextseek_api.cc_assistant import posterior_selector
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant.family_labels import corpus_snapshot, runtime_type_builder
from nextseek_api.eval.generation_store import GenerationSnapshot


_REPO = Path(__file__).resolve().parents[3]


def _snapshot(**overrides):
    current = corpus_snapshot()
    values = {
        "generation_id": 1,
        "generation_hash": "a" * 64,
        "decision_status": "activated_all",
        "posteriors": (),
        "taxonomy_version": current.taxonomy_version,
        "corpus_hash": current.corpus_sha256,
        "content_valid": True,
    }
    values.update(overrides)
    return GenerationSnapshot(**values)


def test_generated_type_builder_contains_every_corpus_family():
    builder = runtime_type_builder()
    from dmac_assistant.router.baml_client.type_builder import TypeBuilder

    assert isinstance(builder, TypeBuilder)
    assert builder.ClassifiedFamily.type() is not None


def test_classifier_supplies_generated_type_builder_to_baml(monkeypatch):
    from dmac_assistant.router.baml_client import b
    from dmac_assistant.router.baml_client.types import ClassificationDecision

    captured = {}

    async def classify(*, input, baml_options):
        captured["tb"] = baml_options["tb"]
        return ClassificationDecision(task_family="sample_search", reasoning="ok")

    monkeypatch.setattr(b, "ClassifyQuery", classify)
    result = cc_router._classify_query("find mice")
    assert result[:2] == ("sample_search", "baml")
    assert captured["tb"].ClassifiedFamily is not None


def test_store_exception_falls_back_without_blocking(monkeypatch):
    monkeypatch.setattr(
        posterior_selector,
        "get_active_snapshot",
        mock.Mock(side_effect=RuntimeError("database unavailable")),
    )
    assert posterior_selector.select_route("sample_search") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_valid": False},
        {"taxonomy_version": "stale"},
        {"corpus_hash": "0" * 64},
    ],
)
def test_selector_rejects_corrupt_or_incompatible_snapshot(overrides):
    assert posterior_selector.select_route("sample_search", snapshot=_snapshot(**overrides)) is None


def test_router_falls_back_when_selector_itself_raises(settings, monkeypatch):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    monkeypatch.setattr(cc_router, "corpus_snapshot", mock.Mock())
    monkeypatch.setattr(cc_router, "runtime_type_builder", mock.Mock())
    monkeypatch.setattr(cc_router, "_classify_query", lambda *_: ("sample_search", "baml", "ok"))
    monkeypatch.setattr(posterior_selector, "select_route", mock.Mock(side_effect=RuntimeError("db")))
    fallback = cc_router.RouteDecision(
        route=cc_router.ROUTE_NS,
        model_class=None,
        model_id=None,
        reasoning="legacy",
        source="baml",
    )
    monkeypatch.setattr(cc_router, "_route_query", lambda *_: fallback)
    assert cc_router.decide("find mice") == fallback.__class__(
        **{**fallback.__dict__, "task_family": "sample_search", "family_source": "baml", "reasoning": "posterior fallback: legacy"}
    )


def test_deploy_templates_default_posterior_router_off():
    for relative in ("docker/nextseek.env.example", "startup/templates/nextseek.env.template"):
        assert 'NEXTSEEK_POSTERIOR_ROUTING_ENABLED="0"' in (_REPO / relative).read_text()
