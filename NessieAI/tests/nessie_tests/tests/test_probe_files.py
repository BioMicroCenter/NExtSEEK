"""Every committed probe must resolve against the CURRENT corpus.

The mechanism is already well covered: tests/test_case_file.py calls select_cases
seven times, including test_an_unknown_include_id_fails_loudly. What was never
covered is the committed probe FILES resolving against the real corpus, so a
retirement could kill a probe and nothing would notice until a paid run died at
startup. That is exactly what happened when repro.cypher_uid_dot was retired on
2026-07-30: both 07-29 probes have raised on load ever since.
"""
import pathlib

import pytest

from NessieAI.tests.nessie_tests import corpus

PROBES = sorted((pathlib.Path(__file__).resolve().parents[1] / "probes").glob("*.json"))


def test_there_are_probe_files_to_check():
    """Anti-vacuity: an empty glob would make the parametrised test prove nothing."""
    assert PROBES


@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.name)
def test_probe_resolves_against_the_current_corpus(probe):
    picked = corpus.select_cases(corpus.merged(), *corpus.load_case_file(probe))
    assert picked, f"{probe.name} selected no cases"


R4_PROBES = [p for p in PROBES if p.name.startswith("probe-2026-10-03-r4-")]


def test_the_r4_probes_exist():
    assert {p.name for p in R4_PROBES} == {"probe-2026-10-03-r4-dev-routing.json", "probe-2026-10-03-r4-prod.json"}


@pytest.mark.parametrize("probe", R4_PROBES, ids=lambda p: p.name)
def test_r4_probe_fill_markers_are_documented_and_pass_the_pre_run_check(probe):
    """Per-instance values are __FILL:NAME__ markers (a UID, a PMID, a lab name) that the operator fills on the day;
    every marker must be explained in `_fill`, and no number may be pinned except through `_measure`."""
    import json
    import re

    from NessieAI.tests.nessie_tests import case_file_check

    text = probe.read_text(encoding="utf-8")
    spec = json.loads(text)
    used = set(re.findall(r"__FILL:([A-Z_]+)__", text)) - set(spec["_fill"])
    assert not used, f"undocumented fill markers: {sorted(used)}"
    result = case_file_check.check(probe, diverse=True)
    assert not result.errors, result.errors
    # A criterion that pins a number must be one `_measure` knows, so a box other than the one it was written on can
    # re-pin it.
    pinned = {v["id"] for fam in spec["families"].values() for v in fam["variants"]
              for t in v["turns"] for c in t["pass_criteria"]
              if c["op"] == "matches_re" and isinstance(c["value"], str) and c["value"].startswith("(?<![")}
    assert pinned <= set(spec["_measure"]), f"pinned numbers with no _measure: {sorted(pinned - set(spec['_measure']))}"
