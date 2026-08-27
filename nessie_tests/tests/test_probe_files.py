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

from nessie_tests import corpus

PROBES = sorted((pathlib.Path(__file__).resolve().parents[1] / "probes").glob("*.json"))


def test_there_are_probe_files_to_check():
    """Anti-vacuity: an empty glob would make the parametrised test prove nothing."""
    assert PROBES


@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.name)
def test_probe_resolves_against_the_current_corpus(probe):
    picked = corpus.select_cases(corpus.merged(), *corpus.load_case_file(probe))
    assert picked, f"{probe.name} selected no cases"
