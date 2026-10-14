from chat_nextseek.helpers.results import uids_from_last_search, summarize_pinned_bundle


def _session(history):
    return {"results_history": history}


def test_uids_from_api_rows_strips_html():
    sess = _session([
        {"api_result_full": {"data": {"rows": [{"uid": "<a>D.SEQ-1-PUB</a>"}, {"UID": "D.SEQ-2-PUB"}]}}}
    ])
    assert uids_from_last_search(sess) == ["D.SEQ-1-PUB", "D.SEQ-2-PUB"]


def test_uids_falls_back_to_reporter_plan():
    sess = _session([{"api_result_full": {}, "reporter_plan": {"uids": ["MUS-9-PUB"]}}])
    assert uids_from_last_search(sess) == ["MUS-9-PUB"]


def test_summarize_pinned_bundle_empty():
    assert summarize_pinned_bundle({"results_history": []}) == ""


def test_summarize_pinned_bundle_reports_rows():
    sess = _session([{"user_query": "find mice", "api_result_full": {"data": {"rows": [1, 2, 3]}}}])
    assert summarize_pinned_bundle(sess) == "last search: query='find mice', ~3 rows"


def test_uids_from_advanced_search_samples_key():
    # advanced_search / new_search results pin rows under data.samples, uid under 'uuid'
    sess = _session([
        {"api_result_full": {"data": {"total": 2, "samples": [
            {"id": "1", "uuid": "NHP-220913SED-16-PUB", "sample_type": "NHP"},
            {"id": "2", "uuid": "NHP-220913SED-17-PUB", "sample_type": "NHP"},
        ]}}}
    ])
    assert uids_from_last_search(sess) == ["NHP-220913SED-16-PUB", "NHP-220913SED-17-PUB"]


def test_summarize_counts_samples_key():
    sess = _session([{"user_query": "Find me NHP", "api_result_full": {"data": {"samples": [1, 2, 3]}}}])
    assert "~3 rows" in summarize_pinned_bundle(sess)


# A graph search keeps its rows in graph_result.data, a plan in its last step's output: the
# pipeline agent read neither, so "run rnaseq on those mice" after "find me mice treated with
# NDMA" (231 rows, dev 10-13) answered "no pinned search results".
NDMA = [{"id": f"MUS-200901ENG-{n}-PUB", "uuid": f"MUS-200901ENG-{n}-PUB", "type": "MUS", "Treatment1": "NDMA"}
        for n in (23, 24, 25)]


def test_uids_from_a_graph_search():
    sess = _session([{"id": 1, "mode": "graph_query", "user_query": "Find me mice treated with NDMA.",
                      "graph_result": {"ok": True, "count": 3, "data": NDMA}}])
    assert uids_from_last_search(sess) == [r["uuid"] for r in NDMA]
    assert summarize_pinned_bundle(sess) == "last search: query='Find me mice treated with NDMA.', ~3 rows"


def test_uids_from_a_graph_column_the_query_did_not_alias():
    sess = _session([{"graph_result": {"ok": True, "data": [{"s.uuid": "NHP-220913SED-16-PUB", "s.Sex": "F"}]}}])
    assert uids_from_last_search(sess) == ["NHP-220913SED-16-PUB"]


def test_a_graph_breakdown_holds_no_uids_so_the_search_before_it_is_used():
    sess = _session([
        {"graph_result": {"ok": True, "data": NDMA}},
        {"graph_result": {"ok": True, "data": [{"name": "RNA-seq", "n": 5}, {"name": "Mouse", "n": 2}]}},
    ])
    assert uids_from_last_search(sess) == [r["uuid"] for r in NDMA]


def test_uids_from_a_plan_final_step():
    sess = _session([{"mode": "plan", "step_results": {
        1: {"ok": True, "output": {"data": NDMA}},
        2: {"ok": True, "output": {"data": NDMA[:1]}},
    }}])
    assert uids_from_last_search(sess) == [NDMA[0]["uuid"]]


def test_uids_from_a_body_that_nests_rows_under_data():
    sess = _session([{"api_result_full": {"data": {"data": [{"uid": "D.SEQ-1-PUB"}]}}}])
    assert uids_from_last_search(sess) == ["D.SEQ-1-PUB"]
