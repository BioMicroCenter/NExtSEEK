"""The 2026-09-23 forced aggregate probe loads and carries a _measure entry for every number it pins.

2026-10-07 test-set review (SPEC-2): the other probe of the pair, probe-2026-09-23-followups-to-cc.json, asserted that
every follow-up goes to Container-CC (the 09-23 ruling, replaced by the 09-24 split rule). All ten of its cases
asked another case's question and were retired into retired.json; the file is gone and so are its tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import corpus
from NessieAI.tests.nessie_tests.scripts import pin_probe_truths as pin

PROBES = Path(__file__).resolve().parents[1] / "probes"
FORCED = PROBES / "probe-2026-09-23-cc-aggregate-forced.json"


@pytest.mark.parametrize("path", [FORCED])
def test_the_probe_loads_as_a_case_file(path):
    include, variants = corpus.load_case_file(path)
    assert include == [] and variants
    spec = json.loads(path.read_text())
    assert {v.id for v in variants} == set(spec["_measure"])


@pytest.mark.parametrize("path", [FORCED])
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


def test_the_forced_probe_asserts_the_aggregate_op_and_no_route():
    spec = json.loads(FORCED.read_text())
    (case,) = list(pin.variants(spec))
    crits = case["turns"][0]["pass_criteria"]
    assert not any(c["field"] in ("route", "route_source") for c in crits)
    assert {"field": "cc_trace_text", "op": "matches_re", "value": "nextseek-aggregate"} in crits
