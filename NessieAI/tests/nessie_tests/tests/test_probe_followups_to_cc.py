"""The two 2026-09-23 probes load, carry a _measure entry for every number they pin,
and assert what the follow-up ruling needs: routes on every turn, reuse on the trace."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import corpus
from NessieAI.tests.nessie_tests.scripts import pin_probe_truths as pin

PROBES = Path(__file__).resolve().parents[1] / "probes"
MAIN = PROBES / "probe-2026-09-23-followups-to-cc.json"
FORCED = PROBES / "probe-2026-09-23-cc-aggregate-forced.json"


@pytest.mark.parametrize("path", [MAIN, FORCED])
def test_the_probe_loads_as_a_case_file(path):
    include, variants = corpus.load_case_file(path)
    assert include == [] and variants
    spec = json.loads(path.read_text())
    assert {v.id for v in variants} == set(spec["_measure"])


@pytest.mark.parametrize("path", [MAIN, FORCED])
def test_every_measured_number_is_a_criterion_and_repins(path):
    spec = json.loads(path.read_text())
    same = {cid: m["locals"] for cid, m in spec["_measure"].items()}
    _, log = pin.pin(copy.deepcopy(spec), same)
    assert len(log) == sum(len(m["locals"]) for m in spec["_measure"].values())
    # and every guarded-number criterion in the file is one a _measure entry owns
    owned = {pin.number_pattern([n]) for m in spec["_measure"].values() for n in m["locals"]}
    for v in pin.variants(spec):
        for turn in v["turns"]:
            for c in turn["pass_criteria"]:
                if c["field"] == "last_reply" and c["value"] and c["value"].startswith("(?<![\\w.,/-])"):
                    assert c["value"] in owned, (v["id"], c["value"])


def test_seeds_are_ns_followups_are_cc_and_reuse_is_asserted():
    spec = json.loads(MAIN.read_text())
    cases = list(pin.variants(spec))
    assert 8 <= len(cases) <= 12
    reuse = 0
    for v in cases:
        for turn in v["turns"]:
            routes = [c["value"] for c in turn["pass_criteria"] if c["field"] == "route"]
            assert len(routes) == 1, (v["id"], turn["label"])
            if routes[0] == "container_cc":
                fields = {c["field"] for c in turn["pass_criteria"]}
                assert "route_source" in fields
                reuse += "cc_trace_text" in fields
    assert reuse >= 7
    sticky = next(v for v in cases if v["id"] == "fu.refers_back_stays_self_contained_leaves")
    assert [next(c["value"] for c in t["pass_criteria"] if c["field"] == "route")
            for t in sticky["turns"]] == ["nextseek_query", "container_cc", "nextseek_query", "container_cc"]
    negative = next(v for v in cases if v["id"] == "fu.fresh_session_first_question_stays_ns")
    assert len(negative["turns"]) == 1


def test_the_forced_probe_asserts_the_aggregate_op_and_no_route():
    spec = json.loads(FORCED.read_text())
    (case,) = list(pin.variants(spec))
    crits = case["turns"][0]["pass_criteria"]
    assert not any(c["field"] in ("route", "route_source") for c in crits)
    assert {"field": "cc_trace_text", "op": "matches_re", "value": "nextseek-aggregate"} in crits
