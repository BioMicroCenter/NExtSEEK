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
