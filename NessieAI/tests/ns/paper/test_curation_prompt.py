from NessieAI.ns.paper.curation_models import Finding
from NessieAI.ns.paper.curation_prompt import INPUT_DIR, OUTPUT_PATH, bounce_prompt, first_prompt


def test_the_first_prompt_names_the_skill_inputs_and_output():
    text = first_prompt()
    assert "paper-curation" in text and INPUT_DIR in text and OUTPUT_PATH in text


def test_the_bounce_prompt_lists_the_errors_not_the_warnings():
    findings = [Finding(code="value_not_in_sources", severity="error", detail="not in its cited text",
                        row_key="geo:GSM1", attribute="Age"),
                Finding(code="reuse_without_evidence", severity="warn", detail="no evidence", row_key="node:mus:1")]
    text = bounce_prompt(findings)
    assert "geo:GSM1" in text and "Age" in text and "node:mus:1" not in text
    assert "previous_curation.json" in text and "findings.json" in text


def test_the_bounce_prompt_caps_the_list():
    many = [Finding(code="value_not_in_sources", severity="error", detail="x", row_key=f"r{i}") for i in range(100)]
    assert "and 60 more" in bounce_prompt(many)
