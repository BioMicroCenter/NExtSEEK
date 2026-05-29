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
