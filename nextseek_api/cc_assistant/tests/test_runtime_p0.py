"""Runtime-critical Bayesian router wiring and fail-open behavior."""
import asyncio
import json
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


def test_generated_type_builder_contains_every_corpus_family(monkeypatch):
    monkeypatch.setenv("GCP_API_KEY", "type-builder-contract-only")
    snapshot = corpus_snapshot()
    builder = runtime_type_builder(snapshot)
    from dmac_assistant.router.baml_client import b
    from dmac_assistant.router.baml_client.type_builder import TypeBuilder
    from dmac_assistant.router.baml_client.types import ClassificationInput

    assert isinstance(builder, TypeBuilder)
    request = asyncio.run(
        b.request.ClassifyQuery(
            input=ClassificationInput(user_query="contract probe", history=[]),
            baml_options={"tb": builder},
        )
    )
    prompt = request.body.json()["contents"][0]["parts"][0]["text"]
    enum_section = prompt.split("ClassifiedFamily\n----\n", 1)[1].split(
        "\n\nAnswer in JSON", 1
    )[0]
    effective_families = {
        line[2:].split(":", 1)[0]
        for line in enum_section.splitlines()
        if line.startswith("- ")
    }
    assert effective_families == set(snapshot.families)

    parsed_families = {
        str(
            getattr(
                b.parse.ClassifyQuery(
                    json.dumps({"task_family": family, "reasoning": "runtime enum proof"}),
                    baml_options={"tb": builder},
                ).task_family,
                "value",
                family,
            )
        )
        for family in snapshot.families
    }
    assert parsed_families == set(snapshot.families)

    unrelated = b.parse.ClassifyQuery(
        json.dumps({"task_family": None, "reasoning": "unrelated"}),
        baml_options={"tb": builder},
    )
    assert unrelated.task_family is None


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
    parsed = b.parse.ClassifyQuery(
        json.dumps({"task_family": "sample_search", "reasoning": "captured builder"}),
        baml_options={"tb": captured["tb"]},
    )
    assert getattr(parsed.task_family, "value", parsed.task_family) == "sample_search"


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
    current = corpus_snapshot()
    monkeypatch.setattr(cc_router, "corpus_snapshot", lambda: current)
    monkeypatch.setattr(cc_router, "_classify_query", lambda *_: ("sample_search", "baml", "ok"))
    selector = mock.Mock(side_effect=RuntimeError("db"))
    monkeypatch.setattr(posterior_selector, "select_route", selector)
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
    selector.assert_called_once_with("sample_search")


def test_deploy_templates_default_posterior_router_off():
    for relative in ("docker/nextseek.env.example", "startup/templates/nextseek.env.template"):
        assert 'NEXTSEEK_POSTERIOR_ROUTING_ENABLED="0"' in (_REPO / relative).read_text()
