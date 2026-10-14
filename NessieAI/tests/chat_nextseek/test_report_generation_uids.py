"""Report generation builds on the result the parser named, unless the reporter or the user named UIDs, and does
not package a capped result silently (REVIEW-1 S1)."""
from types import SimpleNamespace

from chat_nextseek.orchestrator import _report_generation_uids

MICE = [{"uuid": f"MUS-990101ABC-{n}-PUB"} for n in range(231)]
SHOWN = [m["uuid"] for m in MICE[:5]]


def _session(**graph):
    return {"results_history": [{"id": 1, "user_query": "mice treated with a drug",
                                 "graph_result": {"ok": True, "data": MICE, **graph}}]}


def _plan(target=None, uids=()):
    return SimpleNamespace(target_result_id=target, filters=SimpleNamespace(uids=list(uids)))


def _uids(session, text, plan, reporter_uids=None):
    return _report_generation_uids(session, text, plan, SimpleNamespace(uids=reporter_uids))


def test_the_named_result_beats_the_uids_the_parser_carried():
    uids, reply = _uids(_session(), "a GEO package for those mice", _plan(1, SHOWN))
    assert uids == [m["uuid"] for m in MICE] and reply is None


def test_uids_the_user_typed_win():
    uids, reply = _uids(_session(), f"a GEO package for {SHOWN[2]}", _plan(1, [SHOWN[2]]))
    assert uids == [SHOWN[2]] and reply is None


def test_uids_the_reporter_named_win():
    uids, reply = _uids(_session(), "a GEO package for those", _plan(1, SHOWN[:1]), reporter_uids=[SHOWN[3]])
    assert uids == [SHOWN[3], SHOWN[0]] and reply is None


def test_reporter_uids_that_only_echo_the_carried_ones_do_not_hide_the_named_result():
    """The reporter copies the parser's carried UIDs into its own plan whenever it names none (REVIEW-2 MF2)."""
    uids, reply = _uids(_session(), "a GEO package for those mice", _plan(1, SHOWN), reporter_uids=list(SHOWN))
    assert uids == [m["uuid"] for m in MICE] and reply is None


def test_a_limit_the_user_asked_for_is_packaged():
    """"Show me 10 ..." runs LIMIT 10 and hits it: those 10 are the set, not a cut-short one (REVIEW-2 SF2)."""
    session = {"results_history": [{"id": 1, "user_query": "show me 10 mice treated with a drug", "graph_result": {
        "ok": True, "data": MICE[:10], "truncated": True, "total": 231, "limit": 10}}]}
    uids, reply = _uids(session, "a GEO package for those 10", _plan(1))
    assert uids == [m["uuid"] for m in MICE[:10]] and reply is None


def test_no_named_result_keeps_the_carried_uids():
    assert _uids(_session(), "a GEO package for those", _plan(None, SHOWN)) == (SHOWN, None)


def test_a_capped_graph_result_gets_a_narrowing_reply():
    uids, reply = _uids(_session(truncated=True, total=36622), "a GEO package for those", _plan(1))
    assert uids == [] and "231 of 36622" in reply and "narrow" in reply.lower()


def test_a_rest_total_above_its_rows_gets_a_narrowing_reply():
    session = {"results_history": [{"id": 1, "user_query": "find samples", "api_result_full": {
        "data": {"total": 5000, "rows": [{"uid": u} for u in SHOWN]}}}]}
    uids, reply = _uids(session, "a GEO package for those", _plan(1))
    assert uids == [] and "5 of 5000" in reply


def test_a_retrieve_packages_the_samples_asked_for():
    session = {"results_history": [{"id": 1, "user_query": "retrieve a tissue",
                                    "parser_plan": {"filters": {"uids": ["TIS-990101ABC-9-PUB"]}},
                                    "api_result_full": {"data": {"total_samples": 2, "data": [
                                        {"sample_type": "NHP", "samples": [{"uid": "NHP-990101ABC-9-PUB"}]},
                                        {"sample_type": "TIS", "samples": [{"uid": "TIS-990101ABC-9-PUB"}]}]}}}]}
    assert _uids(session, "a GEO package for those", _plan(1)) == (["TIS-990101ABC-9-PUB"], None)


def test_a_named_count_is_not_packaged_on_the_uids_the_parser_carried():
    """The M2 shape on the report path: "how many of those are female?" holds one count row, and its parser plan
    carried the 5 UIDs the chat showed. A package for "those" must not be built on those 5."""
    session = _session()
    session["results_history"].append({"id": 2, "user_query": "how many of those are female?",
                                       "parser_plan": {"filters": {"uids": SHOWN}},
                                       "graph_result": {"ok": True, "data": [{"female": 103}]}})
    uids, reply = _uids(session, "a GEO package for those", _plan(2, SHOWN), reporter_uids=list(SHOWN))
    assert uids == [] and "count or a breakdown" in reply and "UIDs" in reply


def test_a_named_search_that_found_nothing_says_so():
    session = _session()
    session["results_history"].append({"id": 2, "user_query": "female mice treated with a drug",
                                       "parser_plan": {"filters": {"uids": SHOWN}},
                                       "graph_result": {"ok": True, "data": []}})
    uids, reply = _uids(session, "a GEO package for those", _plan(2, SHOWN), reporter_uids=list(SHOWN))
    assert uids == [] and "found no samples" in reply


def test_a_named_summary_report_is_not_packaged_on_its_carried_uids():
    session = _session()
    session["results_history"].append({"id": 2, "mode": "reporter", "user_query": "give me a summary report of those",
                                       "parser_plan": {"filters": {"uids": SHOWN}},
                                       "reporter_plan": {"reporter_mode": "summary", "uids": SHOWN}})
    uids, reply = _uids(session, "a GEO package for those", _plan(2, SHOWN), reporter_uids=list(SHOWN))
    assert uids == [] and "UIDs" in reply


def test_a_generated_report_is_the_set_it_packaged():
    session = _session()
    session["results_history"].append({"id": 2, "mode": "reporter", "user_query": "a GEO package for those",
                                       "reporter_plan": {"reporter_mode": "report_generation", "uids": SHOWN}})
    assert _uids(session, "an SRA package for those", _plan(2)) == (SHOWN, None)


def test_rows_the_user_picked_out_of_the_named_result_win():
    """Operator 10-14 ("Package the 2"): "a GEO package for the first two mice you showed" packages those two, not
    the whole named result."""
    uids, reply = _uids(_session(), "make a GEO package for the first two mice you showed", _plan(1, SHOWN[:2]),
                        reporter_uids=SHOWN[:2])
    assert uids == SHOWN[:2] and reply is None


def test_the_examples_the_chat_showed_are_not_a_pick():
    """"Those" after a list: the parser's copy of the shown examples is not a pick, the named result wins."""
    uids, reply = _uids(_session(), "a GEO package for those mice", _plan(1, SHOWN), reporter_uids=list(SHOWN))
    assert uids == [m["uuid"] for m in MICE] and reply is None
