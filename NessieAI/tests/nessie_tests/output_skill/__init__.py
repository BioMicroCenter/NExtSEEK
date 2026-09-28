"""The importable half of `output-skill` (the nessie-run-review skill).

The skill directory is named with a hyphen, which is not a Python identifier, so nothing
under it can be imported and nothing under it can be unit tested (see
`output_skill_bayesian/__init__.py`, which exists for the same reason). The forms the
skill's reviewer fills live here, each as a pydantic schema, a validator and a
deterministic writer:

* `triage`  the triage the review page (report.html) renders.
* `grades`  a grader's per-case, per-turn verdicts for a run: GRADES.md, and a triage.
* `notes`   the reviewer's downloaded notes, folded back into a triage as decisions.

Run them with `python -m NessieAI.tests.nessie_tests.output_skill <form> ...`, or through
`output-skill/scripts/review_forms.py`, which is a thin entry point over this package.
"""
