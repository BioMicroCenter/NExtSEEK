"""V4-6 call-count table and transport tracing."""
import json
from pathlib import Path
from unittest import mock

import pytest

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import transport_trace

pytestmark = pytest.mark.django_db

_REPO = Path(__file__).resolve().parents[3]
_FLAG_OFF_BASELINE = _REPO / "evidence" / "fixtures" / "plan018-v4-6-flag-off-baseline.json"


@pytest.fixture(autouse=True)
def _reset_transport_hooks():
    transport_trace.reset_transport_hooks()
    yield
    transport_trace.reset_transport_hooks()


def _install_traced_baml_fakes(monkeypatch, *, classify_impl=None, route_impl=None):
    """Install transport hooks on fake BAML dispatch (not _classify_query/_route_query)."""
    from dmac_assistant.router.baml_client import b
    from dmac_assistant.router.baml_client.types import (
        ClassificationDecision,
        Route,
        RouterDecision,
    )

    async def _default_classify(*args, **kwargs):
        return ClassificationDecision(task_family="sample_search", reasoning="ok")

    async def _default_route(*args, **kwargs):
        return RouterDecision(
            route=Route.NextseekQuery,
            model_class=None,
            reasoning="baml route",
        )

    monkeypatch.setattr(b, "ClassifyQuery", classify_impl or _default_classify)
    monkeypatch.setattr(b, "RouteQuery", route_impl or _default_route)
    transport_trace.install_transport_hooks(b)
    cc_router._load_router_deps()
    return b


def _route_decision(route="nextseek_query", source="baml", **kwargs):
    return cc_router.RouteDecision(
        route=route,
        model_class=None,
        model_id=None,
        reasoning=kwargs.get("reasoning", "test"),
        source=source,
        task_family=kwargs.get("task_family"),
        family_source=kwargs.get("family_source"),
        generation_id=kwargs.get("generation_id"),
        generation_hash=kwargs.get("generation_hash", ""),
    )


def test_flag_off_uses_single_route_call(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = False
    with transport_trace.trace_context() as trace:
        with mock.patch.object(cc_router, "_route_query", return_value=_route_decision()) as route_mock:
            with mock.patch.object(cc_router, "_classify_query") as classify_mock:
                decision = cc_router.decide("find mice")
    assert decision.route == "nextseek_query"
    route_mock.assert_called_once()
    classify_mock.assert_not_called()
    assert trace.classify_calls == 0
    assert trace.route_calls == 0  # mock bypasses traced b


def test_flag_on_unrelated_skips_route_llm(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(
                cc_router,
                "_classify_query",
                return_value=(None, None, "unrelated out of scope"),
            ):
                with mock.patch.object(cc_router, "_route_query") as route_mock:
                    decision = cc_router.decide("who won the superbowl")
    assert decision.route == cc_router.ROUTE_UNRELATED
    route_mock.assert_not_called()


def test_flag_on_pretransport_invalid_zero_classify(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    with mock.patch.object(cc_router, "corpus_snapshot", side_effect=ValueError("bad corpus")):
        with mock.patch.object(cc_router, "_classify_query") as classify_mock:
            with mock.patch.object(cc_router, "_route_query", return_value=_route_decision(source="baml")):
                cc_router.decide("find mice")
    classify_mock.assert_not_called()


def test_flag_on_posterior_decisive_skips_route_llm(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    from nextseek_api.cc_assistant import posterior_selector

    sel = posterior_selector.SelectorResult(
        route="container_cc",
        generation_id=1,
        generation_hash="b" * 64,
        decision_status="activated_all",
        reasoning="posterior",
    )
    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(cc_router, "_classify_query", return_value=("sample_search", "baml", "ok")):
                with mock.patch.object(posterior_selector, "select_route", return_value=sel):
                    with mock.patch.object(cc_router, "_route_query") as route_mock:
                        decision = cc_router.decide("find mice")
    assert decision.route == "container_cc"
    assert decision.source == "posterior"
    assert decision.generation_id == 1
    route_mock.assert_not_called()


def test_flag_on_indecisive_falls_back_to_route_llm(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    from nextseek_api.cc_assistant import posterior_selector

    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(cc_router, "_classify_query", return_value=("sample_search", "baml", "ok")):
                with mock.patch.object(posterior_selector, "select_route", return_value=None):
                    with mock.patch.object(cc_router, "_route_query", return_value=_route_decision()) as route_mock:
                        cc_router.decide("find mice")
    route_mock.assert_called_once()


def test_classification_failure_never_fabricates_family(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(cc_router, "_classify_query", return_value=(None, None, "parse error")):
                with mock.patch.object(cc_router, "_route_query", return_value=_route_decision()):
                    decision = cc_router.decide("find mice")
    assert decision.task_family is None
    assert decision.family_source is None


def test_flag_on_posttransport_failure_one_classify_one_route(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(cc_router, "_classify_query", return_value=(None, None, "provider timeout")) as classify_mock:
                with mock.patch.object(cc_router, "_route_query", return_value=_route_decision()) as route_mock:
                    cc_router.decide("find mice")
    classify_mock.assert_called_once()
    route_mock.assert_called_once()


def test_sticky_override_records_attempted_vs_actual(settings):
    from nextseek_api.cc_assistant import router_context
    from nextseek_api.services import cc_assistant as svc

    history = [
        router_context.HistoryTurn(
            position=1,
            user_message="write a script",
            assistant_reply="done",
            router_choice=cc_router.ROUTE_CC,
            status="completed",
        )
    ]
    attempted = _route_decision(route="nextseek_query", source="baml", task_family="sample_search")
    user = mock.Mock(is_staff=False, is_superuser=False)
    req = mock.Mock(query="find mice", force_route=None)
    with mock.patch.object(cc_router, "decide", return_value=attempted):
        final = svc._decide_route(user, req, force_cc=False, history=history)
    assert final.route == cc_router.ROUTE_CC
    assert final.source == "sticky"
    assert final.attempted_route == cc_router.ROUTE_NS
    assert final.attempted_source == "baml"


def test_flag_off_transport_integration_counts_real_baml_dispatch(settings, monkeypatch):
    """Call-table integration: no _classify_query/_route_query mocks; assert transport_trace."""
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = False
    _install_traced_baml_fakes(monkeypatch)
    with transport_trace.trace_context() as trace:
        decision = cc_router.decide("find mice treated with NDMA")
    assert decision.route == cc_router.ROUTE_NS
    assert trace.classify_calls == 0
    assert trace.route_calls == 1
    assert "RouteQuery" in trace.events


def test_flag_off_byte_equivalent_destination_model_vs_frozen_baseline(settings, monkeypatch):
    """Flag-off path must match frozen pre-split baseline bytes for destination/model."""
    baseline = json.loads(_FLAG_OFF_BASELINE.read_text())
    from dmac_assistant.router.baml_client.types import Route, RouterDecision

    async def frozen_route(*args, **kwargs):
        return RouterDecision(
            route=Route.NextseekQuery,
            model_class=baseline["model_class"],
            reasoning=baseline["reasoning_prefix"],
        )

    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = False
    _install_traced_baml_fakes(monkeypatch, route_impl=frozen_route)
    decision = cc_router.decide(baseline["query"])
    assert decision.route == baseline["destination"]
    assert decision.model_class == baseline["model_class"]
    assert decision.model_id == baseline["model_id"]
    assert decision.source == baseline["route_source"]


def test_flag_on_zero_variant_family_one_classify_one_route_transport(settings, monkeypatch):
    """Zero-variant family: classified family with no posterior rows falls back via real BAML."""
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    from dmac_assistant.router.baml_client.types import ClassificationDecision

    async def classify_family(*args, **kwargs):
        return ClassificationDecision(task_family="sample_search", reasoning="classified")

    _install_traced_baml_fakes(monkeypatch, classify_impl=classify_family)
    with transport_trace.trace_context() as trace:
        decision = cc_router.decide("find mice")
    assert decision.task_family == "sample_search"
    assert decision.route == cc_router.ROUTE_NS
    assert trace.classify_calls == 1
    assert trace.route_calls == 1


def test_flag_on_posterior_decisive_transport_skips_route_llm(settings, monkeypatch):
    """Variant coverage: decisive posterior skips RouteQuery; transport shows classify only."""
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    from dmac_assistant.router.baml_client.types import ClassificationDecision
    from nextseek_api.cc_assistant.family_labels import corpus_snapshot
    from nextseek_api.eval.generation_store import (
        EMPTY_ACTIVE_HASH,
        GenerationManifest,
        activate_generation,
        publish_generation,
    )
    from nextseek_api.eval.paired_run_registry import register_paired_run

    current = corpus_snapshot()
    paired_run_id = "v46-decisive-transport"
    register_paired_run(paired_run_id=paired_run_id, schema_version="v1", content_hash="0" * 64)
    gen = publish_generation(GenerationManifest(
        input_hash="input-z",
        attempt_hash="attempt-z",
        aggregate_hash="aggregate-z",
        config_fingerprint="cfg-z",
        decision_status="activated_all",
        groups=[
            {"name": "sample_search", "route": route, "posterior_mean": mean, "band": "Reliable", "n_total": 12}
            for route, mean in (("nextseek_query", 0.91), ("container_cc", 0.35))
        ],
        compatibility_keys={"taxonomy_version": current.taxonomy_version, "corpus_hash": current.corpus_sha256},
        counts={"retained_pairs": 12},
        source_provenance={"paired_run_id": paired_run_id, "evidence_kind": "paired_experimental", "route_source": "forced"},
    ))
    activate_generation(gen, expected_hash=EMPTY_ACTIVE_HASH)

    async def classify_family(*args, **kwargs):
        return ClassificationDecision(task_family="sample_search", reasoning="classified")

    _install_traced_baml_fakes(monkeypatch, classify_impl=classify_family)
    with transport_trace.trace_context() as trace:
        decision = cc_router.decide("find mice")
    assert decision.source == "posterior"
    assert decision.route == cc_router.ROUTE_NS
    assert trace.classify_calls == 1
    assert trace.route_calls == 0
