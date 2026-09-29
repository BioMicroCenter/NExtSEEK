from chat_nextseek.helpers.tools.nextseek_api import build_recent_results_summary


def test_summary_includes_bundle_mode():
    session = {
        "results_history": [
            {"id": 1, "user_query": "mice in GBM study", "endpoint": "neo4j",
             "mode": "graph_query", "api_result_slim": {}},
        ]
    }
    out = build_recent_results_summary(session)
    assert "mode=graph_query" in out
    assert "id=1" in out


# --------------------------------------------------------------------------- #
# The predicate: what the previous search constrained
#
# The summary gave the parser one line per bundle ("id, mode, query, endpoint,
# total") and no filters at all, so a follow-up could not know what the previous
# search had constrained, and could not tell two searches of the same endpoint
# apart except by their wording. Each line now carries the predicate that RAN: the
# request's non-empty filter fields for a REST search (paging dropped), the Cypher
# up to its RETURN plus its parameters for a graph query.
# --------------------------------------------------------------------------- #

import json

from chat_nextseek.helpers.tools import nextseek_api as api_tool

ADVANCED = "/nextseek_api/samples/advanced_search/"


def _line(bundle):
    out = build_recent_results_summary({"results_history": [bundle]})
    return out.splitlines()[1]


def _rest(body=None, params=None, **extra):
    bundle = {"id": 1, "mode": "new_search", "user_query": "q", "endpoint": ADVANCED,
              "request_body": body or {}, "query_params": params or {},
              "api_result_slim": {"data": {"total": 3}}}
    bundle.update(extra)
    return bundle


def _graph(cypher, parameters=None):
    return {"id": 2, "mode": "graph_query", "user_query": "q", "endpoint": "neo4j",
            "graph_plan": {"cypher": cypher, "parameters": parameters or {}},
            "graph_result": {"ok": True, "count": 3, "total": 3, "data": [{"uuid": "A"}, {"uuid": "B"}, {"uuid": "C"}]}}


def test_a_rest_line_carries_the_request_filters_that_were_sent():
    body = {"sampletype": "D.FILE", "filter_searchText": "BRI", "filter_matchType": "PARTIAL"}
    line = _line(_rest(body, params={"page_size": 50}))
    assert "predicate=" + json.dumps(body) in line
    assert "page_size" not in line


def test_empty_and_paging_fields_are_not_a_predicate():
    line = _line(_rest({"sampletype": "MUS", "filter_searchText": "", "filter_projects": [], "page": 2}))
    assert 'predicate={"sampletype": "MUS"}' in line


def test_a_get_search_constrained_by_query_parameters_carries_them():
    line = _line(_rest(params={"sampletype": "TIS", "page": 3, "page_size": 100}))
    assert 'predicate={"sampletype": "TIS"}' in line


def test_an_older_bundle_without_the_top_level_body_reads_the_api_plan():
    bundle = _rest(api_plan={"endpoint": ADVANCED, "method": "POST",
                             "requestBody": {"filter_searchText": "NDMA"}, "queryParameters": {}})
    del bundle["request_body"], bundle["query_params"]
    assert 'predicate={"filter_searchText": "NDMA"}' in _line(bundle)


def test_a_long_list_value_is_capped():
    uids = [f"MUS-{i}" for i in range(12)]
    line = _line(_rest({"filter_uids": uids}))
    assert '"MUS-4", "+7 more"' in line
    assert "MUS-5" not in line


def test_a_graph_line_carries_the_cypher_up_to_its_return_and_its_parameters():
    cypher = ("MATCH (s:T_RNA)-[:IN_PROJECT]->(p:Project)\n"
              "WHERE s.Treatment = $treatment\n"
              "RETURN s.uuid AS uuid ORDER BY uuid LIMIT 50")
    line = _line(_graph(cypher, {"treatment": "NDMA"}))
    assert 'predicate="MATCH (s:T_RNA)-[:IN_PROJECT]->(p:Project) WHERE s.Treatment = $treatment"' in line
    assert 'params={"treatment": "NDMA"}' in line
    assert "RETURN" not in line and "LIMIT" not in line


def test_a_return_inside_a_string_literal_does_not_cut_the_graph_predicate():
    cypher = "MATCH (s:T_TIS) WHERE s.Note = 'no RETURN here' RETURN count(s) AS n"
    line = _line(_graph(cypher))
    assert "WHERE s.Note = 'no RETURN here'" in line
    assert "params=" not in line


def test_a_long_predicate_is_cut_to_the_cap():
    cypher = "MATCH (s:T_TIS) WHERE " + " OR ".join(f"s.uuid = 'TIS-{i:04d}'" for i in range(80)) + " RETURN s"
    line = _line(_graph(cypher))
    predicate = json.loads(line.split("predicate=", 1)[1])
    assert len(predicate) == api_tool.PREDICATE_MAX_CHARS
    assert predicate.endswith("…")


def test_a_bundle_with_no_search_carries_no_predicate():
    bundle = {"id": 3, "mode": "reporter", "user_query": "report", "endpoint": None, "api_result_slim": None}
    assert "predicate=" not in _line(bundle)


# --------------------------------------------------------------------------- #
# The total
#
# Every graph bundle said total=None: the total was read off api_result_slim, which
# only a REST turn writes. A graph turn keeps its result in graph_result, whose
# `total` is the real row total (probed past a LIMIT) and `count` the rows returned
# (all a planner graph step records). A count query answers in ONE row, so its total
# is 1 and the number the user asked for is in that row: it is shown as values=.
# A REST total of 0 used to read as None too (`total or total_samples or ...`).
# --------------------------------------------------------------------------- #


def _graph_result(**result):
    bundle = _graph("MATCH (s:T_TIS) RETURN s.uuid AS uuid")
    bundle["graph_result"] = {"ok": True, **result}
    return bundle


def test_a_graph_line_reads_the_total_from_the_graph_result():
    line = _line(_graph_result(total=1234, count=50, truncated=True, data=[{"uuid": "A"}] * 50))
    assert "total=1234," in line


def test_a_graph_result_with_only_a_row_count_reports_that_count():
    line = _line(_graph_result(count=7, data=[{"uuid": "A"}] * 7))
    assert "total=7," in line


def test_a_capped_graph_result_whose_total_is_unknown_does_not_pass_the_cap_off_as_the_total():
    line = _line(_graph_result(total=None, count=50, truncated=True, data=[{"uuid": "A"}] * 50))
    assert "total=at least 50 (capped)," in line


def test_a_count_query_shows_the_number_it_answered():
    line = _line(_graph_result(total=1, count=1, truncated=False, data=[{"n_samples": 4}]))
    assert 'total=1, values={"n_samples": 4},' in line


def test_a_single_row_of_data_is_not_mistaken_for_an_aggregate():
    line = _line(_graph_result(total=1, count=1, data=[{"uuid": "TIS-1", "age": 4}]))
    assert "values=" not in line


def test_a_failed_graph_query_has_no_total():
    line = _line(_graph_result(ok=False, error="boom", data=None))
    assert "total=None," in line


def test_a_rest_search_that_found_nothing_reports_zero():
    bundle = _rest({"filter_searchText": "none"})
    bundle["api_result_slim"] = {"ok": True, "data": {"total": 0, "results": []}}
    assert "total=0," in _line(bundle)


def test_a_rest_total_is_still_read_from_the_slim_result():
    bundle = _rest({"filter_searchText": "lung"})
    bundle["api_result_slim"] = {"ok": True, "data": {"total_samples": 40, "rows": []}}
    assert "total=40," in _line(bundle)
