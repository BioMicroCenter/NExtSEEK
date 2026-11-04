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

# r5c (2026-11-04): F2 + the r5b clause still moved two answers on dev. The IMPAcTb spelling was fixed by the
# clause, but "samples from the Impact project that have flow cytometry data, of human origin" returned the D.FLOW
# records themselves (343) instead of the 257 samples they derive from: the agent "trusted" the resolved type as the
# thing to return. The operator (11-04): "still enforce the fix bit" and "only keep it on the few points we want it".
# So the rule now names its one place, the has-this-data condition, and says what it does not change.
RULE = ("Some projects store their data as different sample types than others. When the question is scoped to a "
        "project or investigation and asks which records have a kind of data (for example patients with sequencing "
        "data), and `resolved.sampletypes` holds a type for that data, use that type in the has-this-data condition "
        "rather than choosing one yourself.")
LIMITS = ("It changes only that condition: the records you return are still the ones the question asks for, and a "
          "project or investigation is still matched by the title it is stored under (the PROJECTS NAMED block or "
          "the vocabulary), never the user's spelling.")


def resolved_entities_paragraph(text: str) -> str:
    return next(p for p in text.split("\n") if p.startswith("- **The resolved entities and filters**"))


def test_the_scoped_resolved_type_rule_is_in_the_resolved_entities_paragraph():
    para = resolved_entities_paragraph(GRAPH)
    assert RULE in para
    assert para.index("never widen a search to a guessed entity") < para.index(RULE)


def test_the_limits_follow_the_rule():
    assert RULE + " " + LIMITS in resolved_entities_paragraph(GRAPH)


def test_the_rule_is_confined_to_the_has_this_data_condition():
    # the TCGA fix: a resolved type replaces the agent's own choice inside the existence condition
    assert "use that type in the has-this-data condition" in RULE
    # the flow regression: the returned records are not replaced by the resolved type
    assert "the records you return are still the ones the question asks for" in LIMITS
    # the IMPAcTb regression: a project keeps its stored title
    assert "never the user's spelling" in LIMITS
    # the r5 wording that over-applied is gone
    assert "trust that type rather than choosing one yourself" not in GRAPH


def test_the_rule_names_no_project_investigation_or_type():
    for word in ("TCGA", "A.ALN", "D.SEQ", "D.FLOW", "T_A_ALN", "T_D_SEQ", "LUAD", "Impact", "IMPAcTb"):
        assert word not in RULE and word not in LIMITS
