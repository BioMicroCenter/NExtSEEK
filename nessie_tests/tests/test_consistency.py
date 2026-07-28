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


from nessie_tests import consistency

# --------------------------------------------------------------------------- #
# T3.2 / T3.3 — the group used to prove nothing.
#
# consistency.py read api_result_meta.count. The orchestrator writes `row_count` on
# the new_search path (orchestrator.py:1027) and NO count at all on the recall path
# (line 594). Tasks 838/839 confirm it: row_count 139, no `count` key. So either the
# group compared api_result_full.data.total (real but contaminated by a shared
# session) or both counts were None and every assertion passed while evaluating
# nothing.
# --------------------------------------------------------------------------- #

def _payload(debug):
    return {"progress": [{"event": "query_complete", "data": {"debug": debug}}]}


def test_row_count_is_the_key_the_orchestrator_actually_writes():
    """The 838/839 shape: row_count present, `count` absent."""
    assert consistency.get_result_count(_payload({"api_result_meta": {"row_count": 139}})) == 139


def test_the_count_chain_falls_back_in_order():
    assert consistency.get_result_count(_payload({"api_result_meta": {"count": 7}})) == 7
    assert consistency.get_result_count(
        _payload({"api_result_full": {"data": {"total": 42}}})) == 42
    # graph total (the probed true total) beats graph count (len(records))
    assert consistency.get_result_count(
        _payload({"graph_result": {"total": 10688, "count": 5000}})) == 10688
    assert consistency.get_result_count(_payload({"graph_result": {"count": 11}})) == 11


def test_an_unresolvable_count_returns_none():
    assert consistency.get_result_count(_payload({})) is None
    assert consistency.get_result_count({"progress": []}) is None


def _group(**assertions):
    return {"id": "g", "queries": ["q1", "q2"], "assert": assertions}


def test_a_group_with_unresolved_counts_fails_instead_of_passing_vacuously():
    """The core defect: two Nones satisfied `same_count` and proved nothing."""
    gr = consistency.run_group(
        _group(same_count=True),
        lambda q: {"route": "nextseek_query", "count": None},
    )

    assert gr.passed is False
    assert any("could not be resolved" in r for r in gr.reasons)


def test_a_partially_unresolved_group_also_fails():
    counts = iter([139, None])
    gr = consistency.run_group(
        _group(same_count=True), lambda q: {"route": "nextseek_query", "count": next(counts)})

    assert gr.passed is False
    assert any("1 of 2" in r for r in gr.reasons)


def test_a_route_only_group_does_not_require_counts():
    """same_route alone is meaningful without a count, so do not fail it."""
    gr = consistency.run_group(
        _group(same_route=True), lambda q: {"route": "nextseek_query", "count": None})

    assert gr.passed is True


def test_matching_counts_still_pass():
    gr = consistency.run_group(
        _group(same_route=True, same_count=True),
        lambda q: {"route": "nextseek_query", "count": 139})

    assert gr.passed is True


def test_differing_counts_still_fail():
    counts = iter([139, 250])
    gr = consistency.run_group(
        _group(same_count=True), lambda q: {"route": "nextseek_query", "count": next(counts)})

    assert gr.passed is False
    assert any("counts differ" in r for r in gr.reasons)


def test_count_not_limit_catches_every_known_graph_limit():
    from nessie_tests.limits import GRAPH_LIMIT_SENTINELS

    for limit in GRAPH_LIMIT_SENTINELS:
        gr = consistency.run_group(
            _group(count_not_limit=True),
            lambda q, _l=limit: {"route": "nextseek_query", "count": _l},
        )
        assert gr.passed is False, f"a count sitting on LIMIT {limit} was accepted"
        assert any("LIMIT" in r for r in gr.reasons)


def test_count_not_limit_accepts_a_count_that_is_not_a_limit():
    gr = consistency.run_group(
        _group(count_not_limit=True), lambda q: {"route": "nextseek_query", "count": 10688})

    assert gr.passed is True


def test_the_observations_carry_the_per_query_evidence():
    gr = consistency.run_group(
        _group(same_count=True), lambda q: {"route": "nextseek_query", "count": 139})

    assert [o["query"] for o in gr.observations] == ["q1", "q2"]
    assert all(o["count"] == 139 for o in gr.observations)
