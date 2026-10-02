# /// script
# requires-python = ">=3.10"
# dependencies = ["pydantic>=2,<3"]
# ///
"""Entry point for the review forms (triage, grades, notes). The logic lives in the package.

A hyphenated directory is not a Python identifier, so nothing under nessie-run-review/ can be
imported or unit tested; the forms live in `NessieAI/tests/nessie_tests/output_skill/`,
under test in `NessieAI/tests/nessie_tests/tests/test_output_skill_forms.py`.

    uv run scripts/review_forms.py triage --form triage.json --manifest run/manifest.json
    uv run scripts/review_forms.py grades --form grades-form.json --out-dir . --triage
    uv run scripts/review_forms.py notes fold --notes nessie-notes.json --triage triage.json --out fold.json
"""
from __future__ import annotations

import pathlib
import sys

# This script lives in .claude/skills/nessie-run-review/scripts/,
# so the repo root is four levels up.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from NessieAI.tests.nessie_tests.output_skill.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
