"""The re-pinning tool, and a standing drift check on the probe that travels.

Why it exists: every number in an acceptance probe is a measurement of one graph, so the
same probe run against the dev box or production turns each pinned number into a false
red. The tool prints the Cypher that measures them and rewrites the criteria from the
answers.

The last test is the one that will catch a mistake months from now: it re-pins the
committed dev-box probe with its own recorded values and requires the result to be
byte-identical. That fails the moment a criterion is edited without its `_measure` entry,
which is exactly the drift that makes a travelling probe untrustworthy.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROBES = Path(__file__).resolve().parents[1] / "probes"
DEV_BOX_PROBE = PROBES / "probe-2026-09-22-dev-box-acceptance.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_nessie_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pin_probe_truths = _load("pin_probe_truths")


def _probe(criteria, *, measure):
    return {
        "include_ids": [],
        "families": {"f": {"variants": [
            {"id": "c1", "family": "f", "tags": [], "requires_env": [],
             "turns": [{"label": "main", "query": "q", "pass_criteria": criteria}]},
        ]}},
        "_measure": measure,
    }


def _num(*values):
    return pin_probe_truths.number_pattern(list(values))


def test_a_number_pattern_allows_the_thousands_separator_or_not():
    assert _num(57426) == r"(?<![\w.,/-])57,?426(?![\w]|[.,]\d|[-/]\d)"
    assert _num(618, 2270) == r"(?<![\w.,/-])(618|2,?270)(?![\w]|[.,]\d|[-/]\d)"


def test_pinning_rewrites_the_criterion_and_the_recorded_value():
    spec = _probe([{"field": "last_reply", "op": "matches_re", "value": _num(100)}],
                  measure={"c1": {"locals": [100], "cypher": "RETURN 1"}})

    spec, log = pin_probe_truths.pin(spec, {"c1": 250})

    crit = spec["families"]["f"]["variants"][0]["turns"][0]["pass_criteria"][0]
    assert crit["value"] == _num(250)
    assert spec["_measure"]["c1"]["locals"] == [250]
    assert len(log) == 1


def test_mode_each_rewrites_one_criterion_per_number():
    """A spellings question names every spelling; an alternation would pass on one."""
    spec = _probe([{"field": "last_reply", "op": "matches_re", "value": _num(10)},
                   {"field": "last_reply", "op": "matches_re", "value": _num(20)}],
                  measure={"c1": {"locals": [10, 20], "mode": "each", "cypher": "RETURN 1"}})

    spec, _ = pin_probe_truths.pin(spec, {"c1": [11, 22]})

    values = [c["value"] for c in spec["families"]["f"]["variants"][0]["turns"][0]["pass_criteria"]]
    assert values == [_num(11), _num(22)]


def test_mode_any_writes_one_alternation():
    spec = _probe([{"field": "last_reply", "op": "matches_re", "value": _num(1, 2)}],
                  measure={"c1": {"locals": [1, 2], "cypher": "RETURN 1"}})

    spec, _ = pin_probe_truths.pin(spec, {"c1": [3, 4]})

    crit = spec["families"]["f"]["variants"][0]["turns"][0]["pass_criteria"][0]
    assert crit["value"] == _num(3, 4)


def test_a_measure_block_that_no_criterion_matches_is_a_loud_failure():
    spec = _probe([{"field": "last_reply", "op": "matches_re", "value": _num(100)}],
                  measure={"c1": {"locals": [999], "cypher": "RETURN 1"}})

    with pytest.raises(SystemExit) as exc:
        pin_probe_truths.pin(spec, {"c1": 1})
    assert "drifted apart" in str(exc.value)


def test_mode_each_requires_one_number_per_criterion():
    spec = _probe([{"field": "last_reply", "op": "matches_re", "value": _num(10)},
                   {"field": "last_reply", "op": "matches_re", "value": _num(20)}],
                  measure={"c1": {"locals": [10, 20], "mode": "each", "cypher": "RETURN 1"}})

    with pytest.raises(SystemExit):
        pin_probe_truths.pin(spec, {"c1": [11]})


def test_an_unknown_case_is_refused_rather_than_ignored():
    spec = _probe([], measure={})
    for measured in ({"nope": 1},):
        with pytest.raises(SystemExit):
            pin_probe_truths.pin(spec, measured)


def test_the_cypher_batch_names_every_measured_case():
    spec = json.loads(DEV_BOX_PROBE.read_text(encoding="utf-8"))
    batch = pin_probe_truths.emit_cypher(spec)

    for case_id in spec["_measure"]:
        assert case_id in batch, case_id
    assert "Read-only" in batch


def test_the_committed_dev_box_probe_has_not_drifted_from_its_measure_block():
    """Re-pinning it with its own values must change nothing, byte for byte."""
    text = DEV_BOX_PROBE.read_text(encoding="utf-8")
    spec = json.loads(text)
    measured = {case_id: entry["locals"] for case_id, entry in spec["_measure"].items()}

    repinned, _ = pin_probe_truths.pin(json.loads(text), measured)

    assert json.dumps(repinned, indent=2, ensure_ascii=False) + "\n" == text
