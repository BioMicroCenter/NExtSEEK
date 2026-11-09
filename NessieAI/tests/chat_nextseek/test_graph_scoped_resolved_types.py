"""r5 F2 (2026-11-03): a question scoped to a project or investigation reads the sample type the entity step
resolved for the data it asks about.

On dev 11-02 the entity step resolved PAT + A.ALN for a count of an investigation's patients "with sequencing
data", and the graph agent wrote the D.SEQ type its own example uses for that phrase, answering zero. The rule is
general (the operator's ruling): it names no project, investigation or type. A prompt pin, no model call.
"""
from __future__ import annotations

from pathlib import Path

import chat_nextseek

GRAPH = (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")

RULE = ("Some projects store their data as different sample types than others: when the question is scoped to a "
        "project or investigation and `resolved.sampletypes` holds a type for the data it asks about, trust that "
        "type rather than choosing one yourself.")


def resolved_entities_paragraph(text: str) -> str:
    return next(p for p in text.split("\n") if p.startswith("- **The resolved entities and filters**"))


def test_the_scoped_resolved_type_rule_is_in_the_resolved_entities_paragraph():
    para = resolved_entities_paragraph(GRAPH)
    assert RULE in para
    assert para.index("never widen a search to a guessed entity") < para.index(RULE)


def test_the_rule_names_no_project_investigation_or_type():
    for word in ("TCGA", "A.ALN", "D.SEQ", "T_A_ALN", "T_D_SEQ", "LUAD"):
        assert word not in RULE
