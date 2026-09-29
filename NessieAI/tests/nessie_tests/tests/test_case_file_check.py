"""`case_file_check`: the checks a cases file gets before a paid run.

Every committed probe must pass (warnings allowed); each rule has a file that breaks it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import case_file_check as K
from NessieAI.tests.nessie_tests.scripts.pin_probe_truths import number_pattern

PROBES = sorted((Path(__file__).resolve().parents[1] / "probes").glob("*.json"))

GOOD = {
    "_name": "example",
    "families": {"sample_search": {"description": "x", "variants": [
        {"id": "ss.q1", "family": "sample_search", "name": "q1", "tags": ["nessie", "probe", "full"],
         "requires_env": [], "turns": [{"label": "main", "query": "How many mouse samples are there?",
                                        "pass_criteria": [
                                            {"field": "last_reply", "op": "nonempty", "value": None},
                                            {"field": "last_reply", "op": "matches_re", "value": number_pattern([705])}]}]},
        {"id": "ss.q2", "family": "sample_search", "name": "q2", "tags": [], "requires_env": [],
         "turns": [{"label": "main", "query": "List the NHP samples in the Impact study.",
                    "pass_criteria": [{"field": "last_reply", "op": "nonempty", "value": None}]}]}]}},
    "_measure": {"ss.q1": {"locals": [705], "cypher": "MATCH (m:T_MUS) RETURN count(m)"}},
}


def write(tmp_path, spec):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(spec))
    return p


@pytest.mark.parametrize("probe", PROBES, ids=[p.name for p in PROBES])
def test_every_committed_probe_passes(probe):
    r = K.check(probe)
    assert r.errors == [], r.errors


def test_a_good_file_passes_and_counts_itself(tmp_path):
    r = K.check(write(tmp_path, GOOD), instance="dev")
    assert r.errors == []
    assert r.summary["cases"] == 2 and r.summary["turns"] == 2 and r.summary["measured"] == 1
    assert any("re-pin it on dev" in w for w in r.warnings)


def _v(spec, i=0):
    return spec["families"]["sample_search"]["variants"][i]


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: _v(s)["turns"][0].update(pass_critera=_v(s)["turns"][0].pop("pass_criteria")), "unknown key"),
    (lambda s: s.update(famlies=s.pop("families")), "unknown key"),
    (lambda s: _v(s, 1).update(id="ss.q1"), "appears twice"),
    (lambda s: s.update(include_ids=["no.such.case"]), "not found in the corpus"),
    (lambda s: _v(s)["turns"][0]["pass_criteria"].append({"field": "last_reply", "op": "matches_re", "value": "(unclosed"}),
     "does not compile"),
    (lambda s: _v(s)["turns"][0]["pass_criteria"].append({"field": "graph_result.count", "op": "gte", "value": "ten"}),
     "compare numbers"),
    (lambda s: _v(s)["turns"][0]["pass_criteria"].append({"field": "last_reply", "op": "contains"}), "needs a value"),
    (lambda s: _v(s, 1)["turns"][0].update(pass_criteria=[{"field": "chat_log.x", "op": "eq", "value": 1}]),
     "asserting nothing"),
    (lambda s: s["_measure"].update({"ss.q9": {"locals": [1], "cypher": "RETURN 1"}}), "not a case in this file"),
    (lambda s: s["_measure"]["ss.q1"].update(locals=[706]), "no criterion carries the pattern"),
    (lambda s: s["_measure"]["ss.q1"].update(cypher="-- count\nMATCH (m) RETURN count(m)"), "cypher-shell rejects"),
])
def test_each_rule(tmp_path, mutate, needle):
    spec = copy.deepcopy(GOOD)
    mutate(spec)
    r = K.check(write(tmp_path, spec))
    assert any(needle in e for e in r.errors), r.errors


def test_writing_families_are_refused_on_prod_only(tmp_path):
    spec = copy.deepcopy(GOOD)
    _v(spec)["family"] = "entity_write"
    p = write(tmp_path, spec)
    assert not K.check(p, instance="dev").errors
    assert any("never on prod" in e for e in K.check(p, instance="prod").errors)


def test_diverse_turns_repeats_and_unknown_families_into_errors(tmp_path):
    spec = copy.deepcopy(GOOD)
    _v(spec, 1)["turns"][0]["query"] = "How many mouse samples are there?"
    _v(spec, 1)["family"] = "launch"
    p = write(tmp_path, spec)
    plain, diverse = K.check(p), K.check(p, diverse=True)
    assert not plain.errors and len(plain.warnings) >= 2
    assert any("repeats the question" in e for e in diverse.errors)
    assert any("not a corpus family" in e for e in diverse.errors)


def test_the_cli_exit_codes(tmp_path, capsys):
    assert K.main([str(write(tmp_path, GOOD))]) == 0
    bad = copy.deepcopy(GOOD)
    _v(bad, 1)["id"] = "ss.q1"
    assert K.main([str(write(tmp_path, bad))]) == 2
    assert "ERROR:" in capsys.readouterr().out
