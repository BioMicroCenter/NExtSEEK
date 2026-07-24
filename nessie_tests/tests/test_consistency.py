from nessie_tests import consistency as c


def _fake_drive(mapping):
    return lambda q: mapping[q]


def test_group_passes_when_route_and_count_agree():
    g = {"id": "nhp", "name": "nhp", "queries": ["a", "b"],
         "assert": {"same_route": True, "same_count": True}}
    res = c.run_group(g, _fake_drive({"a": {"route": "nextseek_query", "count": 139},
                                      "b": {"route": "nextseek_query", "count": 139}}))
    assert res.passed and res.reasons == []


def test_group_fails_on_route_split():
    g = {"id": "nhp", "name": "nhp", "queries": ["a", "b"], "assert": {"same_route": True}}
    res = c.run_group(g, _fake_drive({"a": {"route": "nextseek_query", "count": 139},
                                      "b": {"route": "nextseek_query", "count": 250}}))
    # routes agree here → passes route check
    assert res.passed
    g2 = {**g, "assert": {"same_count": True, "count_not": 250}}
    res2 = c.run_group(g2, _fake_drive({"a": {"route": "x", "count": 139},
                                        "b": {"route": "x", "count": 250}}))
    assert not res2.passed
    assert any("differ" in r for r in res2.reasons)
    assert any("250" in r for r in res2.reasons)


def test_group_fails_when_routes_differ():
    # same_route asserted + TWO differing routes → group fails with a route reason.
    g = {"id": "nhp", "name": "nhp", "queries": ["a", "b"], "assert": {"same_route": True}}
    res = c.run_group(g, _fake_drive({"a": {"route": "nextseek_query", "count": 1},
                                      "b": {"route": "container_cc", "count": 1}}))
    assert res.passed is False
    assert any("differ" in r and "route" in r for r in res.reasons)
    assert any("nextseek_query" in r and "container_cc" in r for r in res.reasons)


def test_get_result_count_from_debug():
    payload = {"progress": [{"event": "query_complete",
                             "data": {"debug": {"api_result_meta": {"count": 42}}}}]}
    assert c.get_result_count(payload) == 42
