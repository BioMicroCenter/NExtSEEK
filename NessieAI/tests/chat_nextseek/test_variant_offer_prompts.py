"""A question asking which variants, mutations, fusions or HLA alleles a user's sequenced
samples carry reaches the pipeline agent, which offers the run that calls them.

On 2026-10-29 such a question went to graph_query, found no variant-call records and
offered nothing; nf-core/rnavar was the run that answers it. The prompts are read by path
from this checkout, as in test_compare_offer_prompts.py.
"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "chat_nextseek" / "src" / "chat_nextseek" / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def test_the_parser_sends_a_what_do_the_samples_carry_question_to_nfcore():
    text = _read("parser_core_routing.txt")
    assert "Questions asking what the user's sequenced samples carry" in text
    assert "does not make it a metadata or expression lookup" in text
    assert "HLA alleles the user's sequenced samples carry or express, naming no registered analysis records (NFCORE)" in text


def test_registered_variant_records_stay_a_graph_query():
    text = _read("parser_core_routing.txt")
    assert "A request for analysis records already registered" in text


def test_no_case_from_the_session_is_a_prompt_example():
    """House rule (85b25b8b): a corpus or user case is never a prompt example."""
    for name in ("parser_core_routing.txt", "reporter_agent.txt", "pipeline_agent.txt"):
        text = _read(name)
        assert "breast cancer" not in text.lower(), name
        assert "Kamm" not in text, name


def test_the_reporter_keeps_a_variant_question_on_report_generation():
    text = _read("reporter_agent.txt")
    assert "HLA alleles the user's sequenced samples carry or express: the pipeline agent offers the run that calls them" in text


def test_the_pipeline_agent_does_not_hand_a_variant_question_off():
    text = _read("pipeline_agent.txt")
    assert "So does a question asking which variants, mutations, gene fusions or HLA alleles the samples carry or express." in text
    assert "rnavar: the SNVs and short indels called in each sample's expressed genes" in text
