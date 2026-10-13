"""`python -m NessieAI.tests.nessie_tests.output_skill <triage|grades|notes> ...`

Exit codes: 0 ok · 2 the form is wrong (every problem is printed) · 3 the output exists (--force).
"""
from __future__ import annotations

import argparse
import sys

from NessieAI.tests.nessie_tests.output_skill import grades, notes, triage
from NessieAI.tests.nessie_tests.output_skill.common import FormError


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="output_skill", description="the nessie-run-review forms")
    sub = ap.add_subparsers(dest="form", required=True)
    triage.add_cli(sub)
    grades.add_cli(sub)
    notes.add_cli(sub)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except FormError as e:
        print("\n".join(e.problems), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
