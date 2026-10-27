"""What the curation agent is told on stdin (spec §5): the first run, and the one bounce stage 4 allows."""
from __future__ import annotations

from NessieAI.ns.paper.curation_models import Finding

INPUT_DIR = "/data/input"
OUTPUT_PATH = "/data/scratch/curation.json"
MAX_LISTED = 40


def first_prompt() -> str:
    return (
        "This is a paper reingest curation run, not a chat. Use the paper-curation skill "
        "(skills/paper-curation/SKILL.md) and follow it exactly. Your inputs are in "
        f"{INPUT_DIR}: paper.json, evidence.json, context.json and rulebook.json. Write the result to "
        f"{OUTPUT_PATH}, run nextseek-curation-check until it reports no errors, and reply with one line."
    )


def bounce_prompt(findings: list[Finding]) -> str:
    errors = [f for f in findings if f.severity == "error"]
    lines = []
    for f in errors[:MAX_LISTED]:
        where = ", ".join(x for x in (f.row_key, f.node_id, f.attribute) if x)
        lines.append(f"- {f.code}{f' ({where})' if where else ''}: {f.detail}")
    if len(errors) > MAX_LISTED:
        lines.append(f"- and {len(errors) - MAX_LISTED} more, in findings.json")
    return (
        first_prompt() + "\n\nThis is the second and last attempt. Your previous output is "
        f"{INPUT_DIR}/previous_curation.json and the server's findings on it are {INPUT_DIR}/findings.json. "
        "Fix these errors, keep everything else, and write the whole file again:\n" + "\n".join(lines)
    )
