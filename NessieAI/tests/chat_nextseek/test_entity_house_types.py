"""The house sample-type mappings of the entity prompt are applied in code after the LLM (dtd2 F4).

entity_agent.txt says "PBMC -> sampletype TIS" under DOMAIN CONVENTIONS, but an entity result with
`sampletypes: []` left the graph agent anchoring any Sample. Code now adds the mapped type when its
keyword is in the question and the agent left it out.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from chat_nextseek.agents.entity import HOUSE_SAMPLE_TYPES, apply_house_sample_types
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.entity import EntityItem

PROMPT = (Path(__file__).resolve().parents[2] / "chat_nextseek/src/chat_nextseek/prompts/entity_agent.txt").read_text(
    encoding="utf-8")
CATALOG = [{"SampleType": "TIS", "Name": "Tissue"}, {"SampleType": "CEL", "Name": "Cells"}]

# every entry of the prompt's list: (words as a user writes them, the list's own word, the code)
ENTRIES = [
    ("PBMC", "PBMC", "TIS"), ("PBMCs", "PBMC", "TIS"),
    ("peripheral blood mononuclear cells", "peripheral blood mononuclear cell", "TIS"),
    ("BAL", "BAL", "TIS"), ("bronchoalveolar lavage", "bronchoalveolar lavage", "TIS"),
    ("T cell", "T cell", "CEL"), ("T cells", "T cell", "CEL"), ("T-cells", "T cell", "CEL"),
    ("NK cell", "NK cell", "CEL"), ("NK cells", "NK cell", "CEL"),
    ("natural killer cell", "natural killer cell", "CEL"), ("macrophage", "macrophage", "CEL"),
    ("monocyte", "monocyte", "CEL"), ("dendritic cell", "dendritic cell", "CEL"),
    ("neutrophil", "neutrophil", "CEL"), ("B cell", "B cell", "CEL"), ("B cells", "B cell", "CEL"),
]


@pytest.mark.parametrize("words,word,code", ENTRIES)
def test_each_entry_adds_its_type_and_the_lists_word_when_the_agent_left_it_out(words, word, code):
    out = EntityAgentOutput()
    apply_house_sample_types(f"Find {words} that were sequenced", out, CATALOG)
    assert [i.code for i in out.sampletypes] == [code]
    assert out.sampletypes[0].name in {"Tissue", "Cells"}
    assert out.keywords == [word]


@pytest.mark.parametrize("words,word,code", ENTRIES)
def test_each_entry_matches_in_any_letter_case(words, word, code):
    out = EntityAgentOutput()
    apply_house_sample_types(f"how many {words.upper()} samples", out, CATALOG)
    assert [i.code for i in out.sampletypes] == [code]
    assert out.keywords == [word]


def test_an_existing_type_and_keyword_are_not_duplicated():
    out = EntityAgentOutput(sampletypes=[EntityItem(code="TIS", name="Tissue")], keywords=["pbmc"])
    apply_house_sample_types("PBMC samples", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["TIS"]
    assert out.keywords == ["pbmc"]


def test_a_word_inside_another_word_does_not_match():
    out = EntityAgentOutput()
    apply_house_sample_types("the BALANCE of the cohort, a subcellular fraction, tcells", out, CATALOG)
    assert out.sampletypes == [] and out.keywords == []


def test_a_code_missing_from_the_catalog_is_not_added():
    out = EntityAgentOutput()
    apply_house_sample_types("PBMC samples", out, [{"SampleType": "CEL"}])
    assert out.sampletypes == []


def test_no_catalog_still_adds_the_code():
    out = EntityAgentOutput()
    apply_house_sample_types("PBMC samples", out, None)
    assert [i.code for i in out.sampletypes] == ["TIS"]


def test_the_prompt_block_and_the_code_list_say_the_same():
    block = PROMPT[PROMPT.index("These reflect how NExtSEEK specifically stores samples"):]
    block = block[:block.index("Any term matching a project/study name")]
    for words, code in HOUSE_SAMPLE_TYPES:
        for w in words:
            assert w.lower() in block.lower(), w
    assert {c for _, c in HOUSE_SAMPLE_TYPES} == {"TIS", "CEL"}
    assert "added by code" in block


@pytest.mark.parametrize("have", [["Macrophages"], ["MACROPHAGE"]])
def test_the_agents_own_keyword_in_any_form_is_not_duplicated(have):
    out = EntityAgentOutput(keywords=list(have))
    apply_house_sample_types("macrophages in the gut", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["CEL"]
    assert out.keywords == have


@pytest.mark.parametrize("quoted", [
    '"Neutrophil trails steer wound closure in a fictional fin model"',
    "\u201cNeutrophil trails steer wound closure in a fictional fin model\u201d",
    "'Neutrophil trails steer wound closure in a fictional fin model'",
])
def test_a_word_inside_a_quoted_title_adds_nothing(quoted):
    out = EntityAgentOutput()
    apply_house_sample_types(f"which samples belong to {quoted}?", out, CATALOG)
    assert out.sampletypes == [] and out.keywords == []


def test_the_same_word_outside_the_quotes_still_counts():
    out = EntityAgentOutput()
    apply_house_sample_types('macrophages like those in "Macrophage maps of a made-up reef sponge"', out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["CEL"]
    assert out.keywords == ["macrophage"]


def test_apostrophes_are_not_quotes():
    out = EntityAgentOutput()
    apply_house_sample_types("the lab's neutrophil samples from the Smiths' cohort", out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["CEL"]


@pytest.mark.parametrize("query", [
    "which samples are in this paper: Macrophage fate maps of a made-up reef sponge",
    "the study on dendritic cell drift in a fictional estuary, what did it sequence",
])
def test_a_question_about_a_paper_or_study_is_left_to_the_agent(query):
    out = EntityAgentOutput(sampletypes=[EntityItem(code="TIS", name="Tissue")], keywords=["fate maps"])
    apply_house_sample_types(query, out, CATALOG)
    assert [i.code for i in out.sampletypes] == ["TIS"]
    assert out.keywords == ["fate maps"]


# dtd3 G2: the result records which house word gave which type, so the graph agent can anchor it.
def test_house_types_records_the_word_and_code_when_code_added_the_type():
    out = EntityAgentOutput()
    apply_house_sample_types("how many PBMCs were sequenced", out, CATALOG)
    assert out.house_types == {"PBMC": "TIS"}


def test_house_types_records_the_word_when_the_agent_had_already_chosen_the_type():
    out = EntityAgentOutput(sampletypes=[EntityItem(code="TIS", name="Tissue")])
    apply_house_sample_types("PBMC samples", out, CATALOG)
    assert out.house_types == {"PBMC": "TIS"}


def test_house_types_is_empty_by_default_and_for_a_quoted_or_paper_question():
    assert EntityAgentOutput().house_types == {}
    out = EntityAgentOutput()
    apply_house_sample_types('samples of "Neutrophil trails in a made-up fin"', out, CATALOG)
    apply_house_sample_types("which samples are in this paper: Macrophage maps of a made-up sponge", out, CATALOG)
    assert out.house_types == {}


def test_house_types_skips_a_code_the_catalog_does_not_hold():
    out = EntityAgentOutput()
    apply_house_sample_types("PBMC samples", out, [{"SampleType": "CEL"}])
    assert out.house_types == {}
