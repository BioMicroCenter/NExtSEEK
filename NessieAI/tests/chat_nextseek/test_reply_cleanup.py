"""Lane D reply cleanup: no internal block labels in a reply, no false number warning on a breakdown, catalog names.

The LLM is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import pytest

from chat_nextseek import graph_review as gr
from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1
from chat_nextseek.schemas.entity import EntityAgentOutput, EntityItem
from chat_nextseek.schemas.router import ParserPlan


class _Config:
    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


def _answer(monkeypatch, replies):
    """Run the chatter with a model that says ``replies`` in turn; returns (body, number of model calls)."""
    calls = []

    def llm(*a, **k):
        calls.append(k.get("messages"))
        return replies[min(len(calls), len(replies)) - 1]

    monkeypatch.setattr(chatter_mod, "call_llm_text", llm)
    out = chatter_mod.chatter_agent_answer(
        _Config(), "How many QQ samples are there?",
        EntityAgentOutput(sampletypes=[EntityItem(code="QQ", name="Quux")]).model_dump(),
        ParserPlan(mode="graph_query").model_dump(),
        graph_plan={"cypher": "MATCH (s:T_QQ) RETURN count(s) AS n", "parameters": {}},
        graph_result={"ok": True, "count": 1, "total": 1, "data": [{"n": 7}]}, log_dir="")
    return out.split("**Debug info**")[0].strip(), len(calls)


def test_the_label_list_comes_from_the_prompt_files():
    labels = chatter_mod.internal_labels()
    for want in ("NOT APPLIED", "Constrained by", "Executed query", "UID CHECK", "PROJECTS NAMED IN THIS QUESTION"):
        assert want in labels


def test_the_block_headings_built_in_code_are_labels_too():
    """Review F5: headings the code builds (not only those the prompt files name) are internal labels."""
    from chat_nextseek.helpers.field_words import FIELDS_HEADING
    labels = chatter_mod.internal_labels()
    for want in ("FIELDS NAMED IN THIS QUESTION", "LAB CODE PEOPLE", "SUBJECT CHECK", "GRAPH VOCABULARY",
                 "PARSER PLAN", "RESOLVED ENTITIES", "STEP RESULTS"):
        assert want in labels, want
    assert any(FIELDS_HEADING.startswith(label) for label in labels)
    assert not {"OPTIONAL MATCH", "PUBLIC API"} & set(labels), "a Cypher clause or a docstring is not a block"


def test_a_new_heading_added_in_code_is_covered(tmp_path):
    module = tmp_path / "new_block.py"
    module.write_text('"""PRIVATE NOTES: a docstring, not a block."""\n'
                      'HEADING = "BRAND NEW BLOCK (read before your query):\\n"\n'
                      'def f(x):\n    return f"intro\\nSECOND NEW BLOCK: {x}"\n'
                      'CY = "OPTIONAL MATCH (s:Sample)"\n')
    assert chatter_mod.code_headings([module]) == {"BRAND NEW BLOCK", "SECOND NEW BLOCK"}


def test_an_unreadable_module_costs_its_headings_only(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def (:\n")
    assert chatter_mod.code_headings([bad, tmp_path / "missing.py"]) == set()


def test_a_reply_with_labels_is_rewritten_once(monkeypatch):
    body, n = _answer(monkeypatch, ["There is no NOT APPLIED line, so all 7 were counted. The Executed query ran.",
                                    "All the conditions you gave were used, and 7 samples matched."])
    assert body == "All the conditions you gave were used, and 7 samples matched."
    assert n == 2


def test_a_reply_that_keeps_a_label_after_the_rewrite_loses_that_sentence(monkeypatch):
    body, n = _answer(monkeypatch, ["7 samples matched. The UID CHECK block found it.",
                                    "7 samples matched. The UID CHECK block found it."])
    assert body == "7 samples matched." and n == 2


def test_a_reply_without_labels_is_untouched(monkeypatch):
    body, n = _answer(monkeypatch, ["7 samples matched. Nothing was left out."])
    assert body == "7 samples matched. Nothing was left out." and n == 1


def _premise(question, rows):
    inp = ReviewInput(question=question,
                      cypher="MATCH (s:T_QQ) WITH s.lab AS lab, count(*) AS n RETURN lab, n",
                      parameters={}, keyword_fields={}, rows=rows, count=len(rows), total=len(rows), ok=True,
                      error=None)
    return next(c for c in review_tier1(inp, DictCatalog(None)).checks if c.name == "premise_count")


def test_a_breakdown_that_adds_up_to_the_question_number_is_no_mismatch():
    rows = [{"lab": "AAA", "n": 1000}, {"lab": "BBB", "n": 234}]
    assert not _premise("Which labs are the 1,234 quux samples from?", rows).fired


def test_a_breakdown_that_does_not_add_up_still_warns():
    rows = [{"lab": "AAA", "n": 1000}, {"lab": "BBB", "n": 240}]
    assert _premise("Which labs are the 1,234 quux samples from?", rows).fired


def test_a_breakdown_of_files_adds_up_on_another_type():
    rows = [{"kind": "x", "files": 1500}, {"kind": "y", "files": 500}]
    assert not _premise("Break down the 2,000 files by kind", rows).fired


def test_type_codes_in_rows_get_the_catalog_name():
    type_names_in_rows = chatter_mod.type_names_in_rows
    cfg = type("C", (), {"MIN_SAMPLETYPES": [{"SampleType": "PAV", "Name": "Patient Visit"},
                                              {"SampleType": "TIS", "Name": "Tissue Sample"}]})()
    rows = [{"animal": "A1", "t": "PAV", "n": 18}, {"animal": "A1", "t": "TIS", "n": 38}, {"animal": "A2", "t": "ZZZ"}]
    assert type_names_in_rows(cfg, rows) == {"PAV": "Patient Visit", "TIS": "Tissue Sample"}
    assert type_names_in_rows(cfg, [{"x": 1}]) == {}


def test_the_graph_op_result_carries_catalog_names_for_type_codes():
    from NessieAI.ns import granular
    cfg = type("C", (), {"MIN_SAMPLETYPES": [{"SampleType": "PAV", "Name": "Patient Visit"}]})()
    out = {}
    granular._add_type_names(out, cfg, [{"t": "PAV", "n": 3}])
    assert out == {"type_names": {"PAV": "Patient Visit"}}
    out = {}
    granular._add_type_names(out, cfg, None)
    assert out == {}
