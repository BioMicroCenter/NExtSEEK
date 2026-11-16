"""What the Container-CC agent is told about questions over TCGA beside the user's own samples (2026-11-16).

On dev the router sent "Our esophageal tumors all show CCND1 amplification. What TCGA patients could I analyze
to see if they also have this amplification?" to Container-CC. The agent asked `nextseek-aggregate` two counts in
its place ("how many esophageal patients", "how many A.CNV samples"), so the gene, the histology split and the
lab's own patients never reached the graph, and the reply gave a count of copy-number samples beside the patient
count, said what it had not checked, and placed CCND1 in "values in the A.CNV records". The router now keeps such
questions on NExtSEEK (pinned in NessieAI/tests/router/test_followups_to_cc.py); these rules are for a turn that
still reaches the agent. The sentences are pinned so a later edit is a conscious one.
"""
from __future__ import annotations

import re

from NessieAI import paths

SKILL_MD = paths.CC_PLUGIN_DIR / "skills" / "nextseek" / "SKILL.md"
MANIFEST_MD = paths.CC_PLUGIN_DIR / "context" / "MANIFEST.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"SKILL.md has no '## {heading}' section"
    return _flat(match.group(1))


def _skill() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_the_agent_sends_the_whole_question_to_the_graph():
    section = _section(_skill(), "Choosing the op for a task")
    assert "**Keep the whole question.** Send the user's question to `nextseek-graph` in their own words" in section
    for condition in ('a gene or finding ("CCND1 amplification")', "a diagnosis or histology",
                      "a public cohort (TCGA)", "the user's own samples when they set them beside it"):
        assert condition in section, condition
    assert "a condition you leave out is one it can never answer" in section


def test_which_patients_is_a_list_never_reworded_into_counts():
    section = _section(_skill(), "Choosing the op for a task")
    assert ('"Which" or "what patients could I analyze" asks for the patients: it goes to `nextseek-graph`, '
            'never reworded into "how many" parts for `nextseek-aggregate`.') in section
    manifest = _flat(MANIFEST_MD.read_text(encoding="utf-8"))
    assert "**Which patients or samples could I analyze**" in manifest
    assert 'Never reworded into "how many" parts for `nextseek-aggregate`.' in manifest


def test_the_reply_counts_patients_and_names_types_in_words():
    section = _section(_skill(), "Composing the reply")
    assert "A question about patients is answered in patients." in section
    assert "A count of files or analysis samples is not a number of patients" in section
    assert "never write what you did not check" in section
    assert "not a sample-type code alone (`PAT`, `A.CNV`)" in section


def test_the_reply_places_a_gene_in_the_files_not_a_missing_field():
    section = _section(_skill(), "Composing the reply")
    assert "TCGA's gene-level copy-number files hold every gene" in section
    assert "Never say the records lack a gene-level field" in section
    assert "say which patients have the files that hold the gene, and offer to read its values" in section


def test_the_reply_keeps_the_users_samples_apart_from_tcga():
    section = _section(_skill(), "Composing the reply")
    assert "report theirs first and TCGA's on their own, never as one total" in section
