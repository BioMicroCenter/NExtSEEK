from nessie_tests import route_observer as ro

NS_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": "r"}},
    {"event": "agent_complete", "data": {"agent": "parser", "summary": {"mode": "new_search", "endpoint": "advanced_search"}}},
    {"event": "query_complete", "data": {"reply": "ok", "debug": {"parser_plan": {"mode": "new_search"}, "api_plan": {"endpoint": "advanced_search"}}, "bundle_id": 1}},
]}
CC_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": "r"}},
    {"event": "query_complete", "data": {"reply": "done", "total_cost_usd": 0.03, "cc_session_id": "s"}},
]}
EARLY_PAYLOAD = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": "r"}},
]}


def test_has_route_decided():
    assert ro.has_route_decided(EARLY_PAYLOAD) is True
    assert ro.has_route_decided({"progress": []}) is False


def test_observe_ns():
    obs = ro.observe(NS_PAYLOAD)
    assert obs.route == ro.ROUTE_NS
    assert obs.parser_mode == "new_search"
    assert obs.engine == "advanced_search"


def test_observe_cc():
    obs = ro.observe(CC_PAYLOAD)
    assert obs.route == ro.ROUTE_CC
    assert obs.model_class == "opus"
    assert obs.engine == "container_cc:opus"


def test_observe_early_has_route_no_mode():
    obs = ro.observe(EARLY_PAYLOAD)
    assert obs.route == ro.ROUTE_NS
    assert obs.parser_mode is None
