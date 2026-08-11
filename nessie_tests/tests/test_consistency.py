from pathlib import Path

import pytest

from nessie_tests import consistency as c
from nessie_tests.conftest import path_accessible


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


# --------------------------------------------------------------------------- #
# B2 — the hard half of the outage fix.
#
# A consistency group DISCARDS its members' replies and reports its own summary,
# so an outage inside a group is invisible in the manifest. In the 2026-08-03
# seed-6 run cons.nhp_sequencing_engine recorded
#   "count could not be resolved for 2 of 2 queries (...); the count assertions
#    evaluated nothing"
# and a careful human reviewer read that and filed it as drift. Both of its
# turns (ids 1054/1055) carried the Bedrock outage marker. The outage check must
# therefore run BEFORE that message is composed.
# --------------------------------------------------------------------------- #

import json
from pathlib import Path

import pytest

OUTAGE_REPLY = (
    "**The request could not be completed.**\n\nAll provider fallbacks exhausted "
    "� agent 'parser': An error occurred (ServiceUnavailableException) when "
    "calling the Converse operation (reached max retries: 4)."
)


def _drive(route="nextseek_query", count=None, reply=None):
    return lambda q: {"route": route, "count": count, "reply": reply}


def test_get_last_reply_reads_the_final_query_complete():
    assert consistency.get_last_reply(_payload({})) is None  # no reply key
    payload = {"progress": [
        {"event": "query_complete", "data": {"reply": "first"}},
        {"event": "query_complete", "data": {"reply": "last"}}]}
    assert consistency.get_last_reply(payload) == "last"
    assert consistency.get_last_reply({"progress": []}) is None
    assert consistency.get_last_reply({}) is None


def test_a_group_whose_turns_outaged_reports_outage_not_a_count_failure():
    """The exact case the triage missed."""
    gr = consistency.run_group(
        _group(same_route=True, same_count=True, count_not_limit=True),
        _drive(count=None, reply=OUTAGE_REPLY))

    assert gr.outage is True
    assert gr.passed is False
    assert any("provider outage" in r for r in gr.reasons)
    # the message that read as drift must not be what this group reports
    assert not any("could not be resolved" in r for r in gr.reasons), gr.reasons


def test_one_outaged_member_is_enough_to_flag_the_group():
    replies = iter([None, OUTAGE_REPLY])
    gr = consistency.run_group(
        _group(same_count=True),
        lambda q: {"route": "nextseek_query", "count": 139, "reply": next(replies)})

    assert gr.outage is True
    assert any("1 of 2" in r for r in gr.reasons)


def test_a_group_that_did_not_outage_still_reports_the_count_failure():
    """Non-vacuity: the outage branch must not swallow the real check."""
    gr = consistency.run_group(_group(same_count=True),
                               _drive(count=None, reply="I found nothing."))

    assert gr.outage is False
    assert gr.passed is False
    assert any("could not be resolved" in r for r in gr.reasons)


def test_a_healthy_group_is_unaffected_by_the_outage_check():
    gr = consistency.run_group(_group(same_route=True, same_count=True),
                               _drive(count=139, reply="I found 139 samples."))

    assert gr.passed is True and gr.outage is False


def test_a_drive_fn_that_reports_no_reply_at_all_is_tolerated():
    """Back-compat: the older drive_fn shape returned only route+count."""
    gr = consistency.run_group(_group(same_count=True),
                               lambda q: {"route": "x", "count": 139})

    assert gr.passed is True and gr.outage is False


def test_the_group_observations_still_carry_the_per_query_evidence_on_an_outage():
    gr = consistency.run_group(_group(same_count=True), _drive(reply=OUTAGE_REPLY))

    assert [o["query"] for o in gr.observations] == ["q1", "q2"]


# --------------------------------------------------------------------------- #
# Replay: the tenth case, reconstructed from the stored run.
# --------------------------------------------------------------------------- #

_TURNS = Path("/home/cdemu/nessie-run-seed6b/turns.json")
_CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"


@pytest.mark.skipif(not path_accessible(_TURNS), reason=f"stored run evidence absent: {_TURNS}")
def test_replay_the_tenth_case_the_triage_missed():
    """cons.nhp_sequencing_engine, driven from its OWN turns in the stored run.

    The group and its two queries come from corpus.json; the replies come from
    turns.json. Nothing here is hand-authored, so the test cannot claim evidence
    the run did not produce.
    """
    group = next(g for g in json.loads(_CORPUS.read_text(encoding="utf-8"))
                 ["consistency_groups"] if g["id"] == "cons.nhp_sequencing_engine")
    turns = json.loads(_TURNS.read_text(encoding="utf-8"))
    # last turn matching each query — the group forces a new session per query
    by_query = {t["q"]: t for t in turns}
    replayed = [by_query[q] for q in group["queries"]]
    assert all(consistency.is_provider_outage(t["reply"]) for t in replayed), (
        "both of this group's turns must carry the outage marker; if not, the "
        "stored evidence changed and this replay is no longer about the outage"
    )

    gr = consistency.run_group(
        group, lambda q: {"route": by_query[q]["route"], "count": None,
                          "reply": by_query[q]["reply"]})

    assert gr.outage is True
    assert not any("could not be resolved" in r for r in gr.reasons), (
        "this is the message the 2026-08-03 triage read as drift: " f"{gr.reasons}")
