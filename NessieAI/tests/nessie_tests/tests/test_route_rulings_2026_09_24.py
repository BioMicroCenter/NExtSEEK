"""Route expectations brought in line with the operator's 2026-09-23 and 2026-09-24 rulings.

Every route mismatch in the seven runs of 2026-09-23 was the corpus, not the router:
the router followed route_capabilities.json each time. The rulings behind each fix:

* Writes stay refused on NExtSEEK (2026-09-24: there is no Container-CC write path). The
  create, update and delete cases expected container_cc and a confirmation; the prod
  suite (seed 17) routed both run cases to nextseek_query, which refused.
* Submissions stay on the NS reporter (2026-09-23). The GEO case in writes_unsupported
  inherited the family's container_cc rule while its only criterion is a reporter field.
* Open-ended project summaries go to container_cc (2026-09-23). The MetNet summary
  case still asserted the NS reporter plan.
* A self-contained seed search is routed on its own merits: the refine_recall seed
  went to nextseek_query in the prod suite and found both NHP UIDs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import corpus, evaluate
from NessieAI.tests.nessie_tests.route_observer import RouteObservation

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"

EITHER = ("matches_re", "(nextseek_query|container_cc)")
WRITES = ("write.create_me_investigation_testin",
          "write.update_scientist_must_confirm_first",
          "write.delete_sample_must_confirm_first")
GEO = "write.generate_a_geo_submission_for_d"
SUMMARY = "report.build_me_a_sample_summary_for"
REFINE_RECALL = "green.refine_recall"

# The reply the prod suite recorded for the create case (task in manifest seed 17).
NS_REFUSAL = ("I can't do that one from here. I can search, count and compare sample "
              "metadata, follow samples through their lineage, build reports and "
              "submission workbooks, and explain what NExtSEEK holds. If you tell me "
              "what you're after in those terms, I'll try again.")


def _merged():
    return {v.id: v for v in corpus.merged(CORPUS)}


def _route(vid, turn=0):
    crits = [c for c in _merged()[vid].turns[turn].pass_criteria if c.field == "route"]
    assert len(crits) == 1, f"{vid} turn {turn}: {crits}"
    return crits[0].op, crits[0].value


def _overrides():
    return json.loads(CORPUS.read_text(encoding="utf-8"))["route_policy"]["overrides"]


def _ns_verdict(vid, reply):
    obs = RouteObservation("nextseek_query", None, "baml", "", "unsupported", "nextseek_query")
    payload = {"status": "completed", "progress": [
        {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None,
                                            "source": "baml", "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": reply}},
    ]}
    ok, results, _ = evaluate.evaluate_turn(
        payload, list(_merged()[vid].turns[0].pass_criteria), obs, last_reply=reply)
    return ok, [r for r in results if not r.get("passed")]


@pytest.mark.parametrize("vid", WRITES + (GEO,))
def test_writes_and_submissions_expect_nextseek_query(vid):
    assert _route(vid) == ("eq", "nextseek_query")
    assert _overrides().get(vid) == {"op": "eq", "value": "nextseek_query"}


def test_the_recorded_ns_refusal_passes_the_create_case():
    ok, failing = _ns_verdict(WRITES[0], NS_REFUSAL)
    assert ok, failing


def test_a_claimed_creation_without_a_question_fails_the_create_case():
    ok, _ = _ns_verdict(WRITES[0], "Done. I created the investigation Testing Investigation.")
    assert not ok


def test_the_create_case_no_longer_counts_created_as_a_pass():
    regexes = [c.value for c in _merged()[WRITES[0]].turns[0].pass_criteria
               if c.field == "last_reply" and c.op == "matches_re" and "(?!" not in c.value]
    assert regexes and all("created" not in r for r in regexes), regexes


def test_the_summary_case_expects_container_cc_and_no_reporter_plan():
    assert _route(SUMMARY) == ("eq", "container_cc")
    fields = {c.field for c in _merged()[SUMMARY].turns[0].pass_criteria}
    assert not {f for f in fields if f.startswith(("reporter_plan.", "parser_plan."))}, fields
    assert any(c.field == "last_reply" and c.op == "matches_re" and "MetNet" in c.value
               for c in _merged()[SUMMARY].turns[0].pass_criteria)


def test_the_refine_recall_seed_accepts_either_route():
    assert _route(REFINE_RECALL, 0) == EITHER
    assert _overrides().get(REFINE_RECALL) == {"op": EITHER[0], "value": EITHER[1]}
