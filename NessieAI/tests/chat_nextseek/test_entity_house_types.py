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

# every entry of the prompt's list: (words as a user writes them, the code)
ENTRIES = [
    ("PBMC", "TIS"), ("PBMCs", "TIS"), ("peripheral blood mononuclear cells", "TIS"),
    ("BAL", "TIS"), ("bronchoalveolar lavage", "TIS"),
    ("T cell", "CEL"), ("T cells", "CEL"), ("NK cell", "CEL"), ("NK cells", "CEL"),
    ("natural killer cell", "CEL"), ("macrophage", "CEL"), ("monocyte", "CEL"),
    ("dendritic cell", "CEL"), ("neutrophil", "CEL"), ("B cell", "CEL"), ("B cells", "CEL"),
]


@pytest.mark.parametrize("words,code", ENTRIES)
def test_each_entry_adds_its_type_and_keyword_when_the_agent_left_it_out(words, code):
    out = EntityAgentOutput()
    apply_house_sample_types(f"Find {words} that were sequenced", out, CATALOG)
    assert [i.code for i in out.sampletypes] == [code]
    assert out.sampletypes[0].name in {"Tissue", "Cells"}
    assert words.lower() in [k.lower() for k in out.keywords]


@pytest.mark.parametrize("words,code", ENTRIES)
def test_each_entry_matches_in_any_letter_case(words, code):
    out = EntityAgentOutput()
    apply_house_sample_types(f"how many {words.upper()} samples", out, CATALOG)
    assert [i.code for i in out.sampletypes] == [code]


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
