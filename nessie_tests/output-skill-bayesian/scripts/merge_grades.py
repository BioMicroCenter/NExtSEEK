#!/usr/bin/env python3
"""Entry point for `merge_grades`. The logic lives in the importable package.

A hyphenated directory is not a Python identifier, so nothing under this one can
be imported -- and a script that cannot be imported cannot be unit tested. Both
scripts in the sibling `output-skill/` rotted for exactly that reason, and
nothing noticed until `test_output_skill_scripts.py` loaded them by path. So this
file exists only to give SKILL.md a path to name; every decision it would
otherwise encode lives in `nessie_tests/output_skill_bayesian/merge_grades.py`,
under test in `nessie_tests/tests/test_merge_grades.py`.

    python merge_grades.py --run ./run-2026-08-04
"""
from __future__ import annotations

import pathlib
import sys

# This script ships INSIDE nessie_tests, so the repo root is three levels up.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from nessie_tests.output_skill_bayesian import merge_grades  # noqa: E402

if __name__ == "__main__":
    sys.exit(merge_grades.main())
