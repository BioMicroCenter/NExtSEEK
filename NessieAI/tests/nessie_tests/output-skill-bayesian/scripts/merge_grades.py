#!/usr/bin/env python3
"""Entry point for `merge_grades`. The logic lives in the importable package.

A hyphenated directory is not a Python identifier, so nothing under this one can
be imported -- and a script that cannot be imported cannot be unit tested. Both
scripts in the sibling `output-skill/` rotted for exactly that reason, and
nothing noticed until `test_output_skill_scripts.py` loaded them by path. So this
file exists only to give SKILL.md a path to name; every decision it would
otherwise encode lives in `NessieAI/tests/nessie_tests/output_skill_bayesian/merge_grades.py`,
under test in `NessieAI/tests/nessie_tests/tests/test_merge_grades.py`.

    python merge_grades.py --run ./run-2026-08-04
"""
from __future__ import annotations

import pathlib
import sys

# This script ships INSIDE the harness (NessieAI/tests/nessie_tests/
# output-skill-bayesian/scripts/), so the repo root is six levels up.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[5]))

from NessieAI.tests.nessie_tests.output_skill_bayesian import merge_grades  # noqa: E402

if __name__ == "__main__":
    # The module raises, and its tests pin that. The command line maps every refusal to
    # exit 2 with the message on stderr, like the other forms' scripts: 0 is a written
    # graded_rows.csv and nothing else.
    try:
        sys.exit(merge_grades.main())
    except (merge_grades.IncompleteGrading, FileNotFoundError, ValueError) as e:
        print(f"merge_grades: {e}", file=sys.stderr)
        sys.exit(2)
