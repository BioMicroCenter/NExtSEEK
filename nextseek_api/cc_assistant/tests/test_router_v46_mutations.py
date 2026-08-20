"""V4-6 mutation killers — prove oracle tests fail on forbidden edits."""
import hashlib
from pathlib import Path
from unittest import mock

import pytest

from nextseek_api.cc_assistant import router as cc_router

_REPO = Path(__file__).resolve().parents[3]


def test_mutation_route_field_on_classifier_baml_fails_schema_oracle():
    text = (_REPO / "dmac_assistant" / "baml_src" / "classifier.baml").read_text()
    body = text.split("class ClassificationDecision")[1].split("function")[0]
    assert "route" not in body
    assert "model_class" not in body


def test_mutation_legacy_router_prompt_pin_still_holds():
    text = (_REPO / "dmac_assistant" / "baml_src" / "router.baml").read_text()
    assert text.count("{{ input.user_query }}") == 1


def test_mutation_swallowed_classification_failure_still_has_no_family(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    with mock.patch.object(cc_router, "corpus_snapshot", return_value=mock.Mock()):
        with mock.patch.object(cc_router, "type_builder", return_value={}):
            with mock.patch.object(cc_router, "_classify_query", return_value=(None, None, "boom")):
                with mock.patch.object(cc_router, "_route_query", return_value=cc_router.RouteDecision(
                    route="nextseek_query",
                    model_class=None,
                    model_id=None,
                    reasoning="fallback",
                    source="baml",
                )):
                    decision = cc_router.decide("find mice")
    assert decision.task_family is None


def test_mutation_sticky_without_attempted_fields_is_detectable():
    """Unrecorded override: sticky must carry attempted_route/source."""
    from nextseek_api.cc_assistant import router_context
    from nextseek_api.services import cc_assistant as svc

    history = [
        router_context.HistoryTurn(
            position=1,
            user_message="write code",
            assistant_reply="ok",
            router_choice=cc_router.ROUTE_CC,
            status="completed",
        )
    ]
    attempted = cc_router.RouteDecision(
        route=cc_router.ROUTE_NS,
        model_class=None,
        model_id=None,
        reasoning="baml",
        source="baml",
    )
    user = mock.Mock(is_staff=False, is_superuser=False)
    req = mock.Mock(query="list samples", force_route=None)
    with mock.patch.object(cc_router, "decide", return_value=attempted):
        final = svc._decide_route(user, req, force_cc=False, history=history)
    assert final.attempted_route is not None
    assert final.attempted_source is not None


def test_mutation_extra_route_on_flag_off_still_single_call(settings):
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = False
    with mock.patch.object(cc_router, "_route_query", return_value=cc_router.RouteDecision(
        route="nextseek_query",
        model_class=None,
        model_id=None,
        reasoning="baml",
        source="baml",
    )) as route_mock:
        with mock.patch.object(cc_router, "_classify_query") as classify_mock:
            cc_router.decide("find mice")
    route_mock.assert_called_once()
    classify_mock.assert_not_called()


def test_mutation_dual_router_baml_identity():
    a = _REPO / "dmac_assistant" / "baml_src" / "router.baml"
    b = _REPO / "docker" / "cc-runtime" / "baml_src" / "router.baml"
    assert hashlib.sha256(a.read_bytes()).hexdigest() == hashlib.sha256(b.read_bytes()).hexdigest()
