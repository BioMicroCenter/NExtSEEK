"""A question comparing expression between groups of samples reaches the pipeline agent,
which answers it with an offer, never a launch.

The prompts are read by path from this checkout, as in test_default_prompt_content.py.
"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def test_the_parser_sends_an_expression_comparison_to_nfcore():
    text = _read("parser_core_routing.txt")
    assert "Questions that compare molecular readouts between groups of samples" in text
    assert "Which genes change between them?" in text


def test_the_parser_no_longer_calls_every_group_comparison_unsupported():
    text = _read("parser_core_routing.txt")
    assert "A comparison between two groups is unsupported even if" not in text
    assert "a comparison of genes, transcripts or expression between them is NFCORE" in text


def test_the_reporter_keeps_an_nfcore_comparison_on_report_generation():
    text = _read("reporter_agent.txt")
    assert "never a summary" in text
    assert "Which genes change between them?" in text


def test_the_pipeline_agent_frames_a_question_as_an_offer():
    text = _read("pipeline_agent.txt")
    assert "this message is an OFFER: nothing has been submitted" in text
    assert 'Reply "submit" to send it to Luria, or tell me what to change.' in text


def test_the_offer_says_what_the_run_does_not_answer():
    text = _read("pipeline_agent.txt")
    assert "nf-core/differentialabundance" in text
    assert "NExtSEEK cannot launch it yet" in text
    assert "at least two, ideally three, replicates per group" in text


def test_a_comparison_that_fits_no_pipeline_is_never_forced():
    text = _read("pipeline_agent.txt")
    assert "Never force a pick." in text
    assert "follow its message instead" in text


def test_a_comparison_question_is_not_handed_off():
    text = _read("pipeline_agent.txt")
    assert "A question comparing genes, transcripts or expression between groups of samples counts" in text


def test_the_confirmation_rule_is_still_there():
    text = _read("pipeline_agent.txt")
    assert "ALWAYS confirm with the user before submit_to_luria." in text
