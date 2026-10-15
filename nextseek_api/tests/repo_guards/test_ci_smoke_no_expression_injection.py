"""No workflow step may splice inputs or event data into shell text."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
SPLICE = re.compile(r"\$\{\{[^}]*\b(inputs|github\.event|github\.head_ref)\b")


def test_no_run_script_splices_inputs_or_event_data():
    bad = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text())
        for job, body in wf["jobs"].items():
            for step in body.get("steps", []):
                if SPLICE.search(step.get("run", "")):
                    bad.append((path.name, job, step.get("name")))
    assert not bad, f"inputs or event data spliced into run scripts: {bad}"
