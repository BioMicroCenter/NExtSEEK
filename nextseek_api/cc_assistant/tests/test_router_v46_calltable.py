"""V4-6 call-count table and transport tracing."""
from unittest import mock

import pytest

from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.cc_assistant import transport_trace

pytestmark = pytest.mark.django_db


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

