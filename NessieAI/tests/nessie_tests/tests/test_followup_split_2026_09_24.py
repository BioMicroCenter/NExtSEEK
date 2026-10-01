"""Corpus route expectations under the follow-up split (operator 2026-09-24, routing review 5).

The product default is now NESSIE_FOLLOWUP_ROUTING=split (NessieAI/router/followup.py):

* A follow-up NExtSEEK can answer from the earlier result or by re-running the earlier
  search (count, filter, a breakdown by one or two fields, recall, a one-change re-run)
  routes nextseek_query.
* A follow-up that needs a file or download, a chart, code, a comparison, summary or
  analysis routes container_cc.
* Any follow-up in a chat that already has a completed container_cc turn stays there.

Before this, every follow-up turn in the corpus asserted container_cc inline (the
2026-09-23 ruling). Of the 40 that did: 29 now assert nextseek_query (one is a re-run of a
write, which NExtSEEK refuses), 4 whose shape is genuinely ambiguous accept either route, and
7 stay container_cc (a file or table, an open-ended summary, a chat that already used
Container-CC).

Also here, fix 9 of the 2026-09-24 review: the atlas case that pasted two turns into one
message is retired and replaced by a real two-turn case.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from NessieAI.router.followup import followup_cue, followup_shape
from NessieAI.tests.nessie_tests import corpus, evaluate
from NessieAI.tests.nessie_tests.route_observer import RouteObservation

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"

NS = ("eq", "nextseek_query")
CC = ("eq", "container_cc")
EITHER = ("matches_re", "(nextseek_query|container_cc)")

WHY_MARK = "2026-09-24: follow-up split (routing review 5)"

# (case id, turn index) -> the route the turn asserts under the split.
MOVED_TO_NS = (
    ("tree.then_ask_about", 1),
    ("sys.how_to_find_those_data_myself", 1),
    ("refrec.memory_how_many", 1),
    ("refrec.memory_unique_types", 1),
    ("refrec.what_sample_types_were_represe", 1),
    ("refrec.which_of_those_samples_are_fro", 1),
    ("refrec.from_the_last_search_which_sam", 1),
    ("refrec.how_many_results_did_that_retu", 1),
    ("refrec.how_many_d_seq_samples_did_the", 1),
    ("refrec.of_those_which_are_actually_fr", 1),
    ("refrec.going_way_back_how_many_ndma_m", 1),
    ("refrec.how_many_d_seq_impact_samples", 1),
    ("refrec.how_many_ndma_mice_did_the_fir", 1),
    ("refrec.of_those_monkeys_which_are_cd8", 1),
    ("refrec.which_of_those_are_males", 1),
    ("fu.assays_between_two_types_named", 1),
    ("fu.cc_mice_with_transcriptomics", 1),
    ("fu.which_labs_are_those_mice", 1),
    ("fu.human_flow_patients_other_data", 1),
    ("fu.investigators_of_those_patients", 1),
    ("refrec.refine_to_cd8", 1),
    ("refrec.refine_to_female", 1),
    ("refrec.refine_liver", 1),
    ("refrec.now_filter_those_to_only_cd8_d", 1),
    ("refrec.filter_the_last_search_to_cd8", 1),
    ("refrec.from_those_results_keep_only_t", 1),
    ("refrec.refine_those_results_to_cd8_de", 1),
    ("refrec.try_that_search_again_with_wat", 1),
    # A one-change re-run of a write: writes stay refused on NExtSEEK (operator 2026-09-24),
    # so it goes where the rule sends a re-run, and NExtSEEK refuses it.
    ("refrec.can_you_run_that_again_but_wit", 1),
)
AMBIGUOUS = (
    ("refrec.of_those_mice_can_you_summariz", 1),   # "summarize the sex statistics"
    ("fu.sequencing_samples_sha_in_uid", 1),        # a filter over a count-only seed
    ("sr.sequencing_filtered_by_uid_code", 1),      # a filter over a count-only seed
    ("fu.human_flow_patients_other_data", 2),       # open-ended "what other data"
)
STAYS_CC = (
    ("sys.why_only_twenty_rows", 1),                # a table for download
    ("sys.why_only_twenty_rows", 2),                # the chat already used Container-CC
    ("refrec.can_you_summarize_what_those_r", 1),   # an open-ended summary
    ("fu.cc_mice_with_transcriptomics", 2),         # "summarize those"
    ("artifact.table_of_a_filtered_set", 1),        # a table for download
    ("artifact.export_full_list_excel", 1),         # an export
    ("artifact.csv_of_cc_mice_with_transcriptomics", 1),  # a CSV file
)
EXPECTED = {**{k: NS for k in MOVED_TO_NS}, **{k: EITHER for k in AMBIGUOUS},
            **{k: CC for k in STAYS_CC}}

# A container_cc follow-up whose message the code guard would leave to the router:
# followup_shape says "ns" and no earlier turn ran on Container-CC, so only the router's
# judgement can deliver it. None today; listed so a new one has to be argued for.
ROUTER_JUDGEMENT_CC: set[tuple[str, int]] = set()

OLD_PASTED = "route.turn_1_find_the_ndma_treated_mic"
NEW_TWO_TURNS = "route.ndma_mice_then_female_two_turns"


def _raw():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    return {v["id"]: v for fam in payload["families"].values() for v in fam["variants"]}


def _route_rules(turn: dict) -> list[tuple[str, str]]:
    return [(c["op"], c["value"]) for c in turn["pass_criteria"] if c["field"] == "route"]


def _active_multi_turn():
    return [v for v in _raw().values() if v["status"] == "active" and len(v["turns"]) > 1]


# --------------------------------------------------------------------------- #
# The 40 follow-up turns that asserted container_cc inline
# --------------------------------------------------------------------------- #

def test_the_table_covers_forty_turns_in_thirty_seven_cases():
    assert (len(MOVED_TO_NS), len(AMBIGUOUS), len(STAYS_CC)) == (29, 4, 7)
    assert len(EXPECTED) == 40
    assert len({vid for vid, _ in EXPECTED}) == 37


@pytest.mark.parametrize(("vid", "turn"), sorted(EXPECTED))
def test_each_follow_up_turn_asserts_the_route_the_split_gives_it(vid, turn):
    assert _route_rules(_raw()[vid]["turns"][turn]) == [EXPECTED[(vid, turn)]]


def test_no_other_later_turn_asserts_container_cc():
    """Every active later turn that asserts container_cc is one of the seven kept on it."""
    later_cc = {(v["id"], i) for v in _active_multi_turn()
                for i, t in enumerate(v["turns"]) if i and CC in _route_rules(t)}
    assert later_cc == set(STAYS_CC)


def test_every_seed_of_the_forty_still_asserts_nextseek_query():
    for vid in sorted({vid for vid, _ in EXPECTED}):
        assert _route_rules(_raw()[vid]["turns"][0]) == [NS], vid


def test_each_changed_case_says_why_on_the_date():
    changed = {vid for vid, _ in MOVED_TO_NS + AMBIGUOUS}
    missing = sorted(vid for vid in changed if WHY_MARK not in (_raw()[vid].get("_why") or ""))
    assert not missing, missing
    untouched = sorted(vid for vid, _ in STAYS_CC
                       if vid not in changed and WHY_MARK in (_raw()[vid].get("_why") or ""))
    assert not untouched, untouched


def test_the_refine_recall_follow_up_still_asserts_no_route():
    """Its seed accepts either route (prod suite 2026-09-23), so a follow-up after a
    Container-CC seed is sticky and one after an NExtSEEK seed is not: no single route
    is right, and the turn keeps asserting the answer only."""
    assert _route_rules(_raw()["green.refine_recall"]["turns"][1]) == []


# --------------------------------------------------------------------------- #
# The corpus agrees with the router's own code guard
# --------------------------------------------------------------------------- #

def test_an_ns_follow_up_is_never_one_the_code_guard_sends_to_container_cc():
    """policy._decide_route turns an NExtSEEK decision into container_cc when the message
    is Container-CC-shaped or the chat already used Container-CC. A corpus turn asserting
    nextseek_query in either situation would be a guaranteed red."""
    bad = []
    for v in _active_multi_turn():
        for i, t in enumerate(v["turns"]):
            if not i or NS not in _route_rules(t):
                continue
            earlier_cc = any(CC in _route_rules(e) for e in v["turns"][:i])
            if followup_shape(t["query"]) == "cc" or earlier_cc:
                bad.append((v["id"], i, t["query"]))
    assert not bad, bad


def test_a_cc_follow_up_is_cc_shaped_or_sticky_or_named():
    unexplained = []
    for v in _active_multi_turn():
        for i, t in enumerate(v["turns"]):
            if not i or CC not in _route_rules(t):
                continue
            earlier_cc = any(CC in _route_rules(e) for e in v["turns"][:i])
            if followup_shape(t["query"]) == "cc" or earlier_cc:
                continue
            unexplained.append((v["id"], i))
    assert set(unexplained) == ROUTER_JUDGEMENT_CC, unexplained


def test_the_named_router_judgement_cases_really_are_ns_shaped():
    for vid, turn in ROUTER_JUDGEMENT_CC:
        assert followup_shape(_raw()[vid]["turns"][turn]["query"]) == "ns", vid


_CC_ONLY_REPLY = re.compile(r"/dmac/users/")


def test_no_ns_follow_up_carries_a_criterion_only_container_cc_can_satisfy():
    """cc_trace_text, the Container-CC delivery path and `mode eq cc` resolve only on a
    Container-CC turn; on a turn that asserts nextseek_query they could never pass."""
    bad = []
    for v in _active_multi_turn():
        for i, t in enumerate(v["turns"]):
            if not i or NS not in _route_rules(t):
                continue
            for c in t["pass_criteria"]:
                if (c["field"] == "cc_trace_text"
                        or (c["field"] == "mode" and c["value"] == "cc")
                        or (c["field"] == "last_reply" and isinstance(c["value"], str)
                            and _CC_ONLY_REPLY.search(c["value"]))):
                    bad.append((v["id"], i, c))
    assert not bad, bad


def test_an_ns_follow_up_passes_on_a_right_ns_answer_and_fails_on_container_cc():
    v = next(x for x in corpus.merged(CORPUS) if x.id == "refrec.of_those_monkeys_which_are_cd8")
    crits = list(v.turns[1].pass_criteria)

    def verdict(route, source):
        obs = RouteObservation(route, None, source, "", None, route)
        payload = {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "model_class": None,
                                                "source": source, "reasoning": ""}},
            {"event": "query_complete", "data": {"reply": "15 of those are CD8 depleted."}}]}
        return evaluate.evaluate_turn(payload, crits, obs,
                                      last_reply="15 of those are CD8 depleted.")[0]

    assert verdict("nextseek_query", "baml")
    assert not verdict("container_cc", "followup")


# --------------------------------------------------------------------------- #
# route_policy notes
# --------------------------------------------------------------------------- #

def _route_policy():
    return json.loads(CORPUS.read_text(encoding="utf-8"))["route_policy"]


def test_the_prod_researchers_note_no_longer_states_the_all_cc_rule():
    note = _route_policy()["_2026_09_23_prod_researchers"]
    assert "container_cc on every follow-up" not in note
    assert "2026-09-24" in note


def test_the_split_has_its_own_route_policy_note():
    note = _route_policy()["_2026_09_24_followup_split"]
    for word in ("nextseek_query", "container_cc", "NESSIE_FOLLOWUP_ROUTING", NEW_TWO_TURNS):
        assert word in note, word


# --------------------------------------------------------------------------- #
# Fix 9: the pasted two-turn message becomes a real two-turn case
# --------------------------------------------------------------------------- #

def test_the_pasted_two_turn_case_is_retired_with_its_reason():
    old = _raw()[OLD_PASTED]
    assert old["status"] == "retired"
    rec = old["retirement"]
    assert rec["retired_on"] == "2026-09-24"
    assert rec["family"] == "engine_routing"
    assert rec["decided_by"] and rec["source"]
    assert "one message" in rec["reason"] and NEW_TWO_TURNS in rec["reason"]
    assert "2026-09-24" in old["_why"]
    # the question text is kept as it was: retirement is a status flip
    assert old["turns"][0]["query"] == (
        "turn 1: 'Find the NDMA-treated mice.' turn 2: 'And how many of those were female?'")
    assert OLD_PASTED not in {v.id for v in corpus.merged(CORPUS)}


def test_the_retired_case_left_the_unreviewed_atlas_set():
    """It was read and ruled on, which is what leaving the atlas set means; `_atlas`
    stays as provenance."""
    old = _raw()[OLD_PASTED]
    assert old["origin"] == "overlay" and "atlas" not in old["tags"]
    assert old["_atlas"]["capability"]


def test_the_new_case_has_two_real_turns_on_nextseek_query():
    raw = _raw()[NEW_TWO_TURNS]
    assert raw["status"] == "active" and raw["family"] == "engine_routing"
    assert raw["is_bayesian"] is False
    assert raw["_atlas"] == _raw()[OLD_PASTED]["_atlas"]
    assert WHY_MARK not in raw["_why"] and "2026-09-24" in raw["_why"]

    v = next(x for x in corpus.merged(CORPUS) if x.id == NEW_TWO_TURNS)
    assert {"nessie", "full"} <= set(v.tags)
    assert [t.query for t in v.turns] == [
        "Find the NDMA-treated mice.", "And how many of those were female?"]
    seed, follow = ([(c.field, c.op, c.value) for c in t.pass_criteria] for t in v.turns)
    assert seed == [("route", "eq", "nextseek_query"), ("last_reply", "nonempty", None)]
    assert follow == [("route", "eq", "nextseek_query"), ("last_reply", "nonempty", None),
                      ("last_reply", "matches_re", r"\b\d+\b")]


def test_the_new_follow_up_is_ns_shaped():
    assert followup_cue("And how many of those were female?") is not None
    assert followup_shape("And how many of those were female?") == "ns"


def test_the_new_follow_up_passes_on_ns_and_fails_on_container_cc():
    v = next(x for x in corpus.merged(CORPUS) if x.id == NEW_TWO_TURNS)
    crits = list(v.turns[1].pass_criteria)
    reply = "Of the 207 NDMA-treated mice, 98 are female."

    def verdict(route):
        obs = RouteObservation(route, None, "baml", "", None, route)
        payload = {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": route, "model_class": None,
                                                "source": "baml", "reasoning": ""}},
            {"event": "query_complete", "data": {"reply": reply}}]}
        return evaluate.evaluate_turn(payload, crits, obs, last_reply=reply)[0]

    assert verdict("nextseek_query")
    assert not verdict("container_cc")
