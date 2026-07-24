from nessie_tests import evaluate
from nessie_tests.route_observer import RouteObservation

NS_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}},
    {"event": "search_complete", "data": {"api_ok": True}},
    {"event": "query_complete", "data": {"reply": "found", "debug": {"parser_plan": {"mode": "new_search"}, "api_plan": {"endpoint": "advanced_search"}}}},
]}
OBS_NS = RouteObservation("nextseek_query", None, "baml", "", "new_search", "advanced_search")


def test_build_debug_backfills_api_ok():
    debug = evaluate.build_observed_debug(NS_PAYLOAD)
    assert debug["parser_plan"]["mode"] == "new_search"
    assert debug["api_result_meta"]["ok"] is True


def test_route_and_mode_criteria_pass_via_injection():
    criteria = [
        {"field": "route", "op": "eq", "value": "nextseek_query"},
        {"field": "engine", "op": "eq", "value": "advanced_search"},
        {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
        {"field": "api_ok", "op": "true"},
    ]
    passed, results = evaluate.evaluate_turn(NS_PAYLOAD, criteria, OBS_NS, last_reply="found")
    assert passed, results


def test_bundle_richness_criteria():
    ok, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                   OBS_NS, bundle_summary={"has_json_metadata": True})
    assert ok
    bad, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                    OBS_NS, bundle_summary={"has_json_metadata": False})
    assert not bad
