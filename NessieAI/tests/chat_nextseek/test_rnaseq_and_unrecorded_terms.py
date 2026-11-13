"""RNA-seq means the RNA-Seq strategy, and a term no field records is described by what the data lacks.

The failure this guards: "Which TCGA patients have lymphocyte infiltration of 40% or more on their slides and RNA-seq
to check it?" counted 659 patients through any gene-expression result or a library strategy containing 'rna', so
miRNA-Seq and scRNA-Seq came in, and the reply led with that caveat. On dev 650 have RNA-Seq and 9 only miRNA-Seq.
The reply also said "'Stroma' was treated as context only", the query note's words, where the point is that no slide
field records where the infiltrate sits.
"""
from __future__ import annotations

import pathlib

import chat_nextseek

PROMPTS = pathlib.Path(chat_nextseek.__file__).resolve().parent / "prompts"


def _read(name):
    return (PROMPTS / name).read_text(encoding="utf-8")


def _rule(text, start):
    rule = text[text.index(start):]
    return rule[:rule.index("\n- ")]


def test_rnaseq_is_the_whole_strategy_never_a_substring():
    rule = _rule(_read("graph_agent.txt"), '- **"RNA-SEQ" IS BULK RNA-SEQ')
    assert "toLower(toString(d.LibraryStrategy)) IN $rnaseq" in rule
    assert "`mirna-seq` contains `rna-seq`" in rule
    assert "its presence alone is not RNA-seq" in rule  # A.GEX holds miRNA-Seq results too
    assert "WITH count(DISTINCT p) AS mirna_seq_only" in rule


def test_the_rnaseq_rule_sits_in_the_return_rules():
    text = _read("graph_agent.txt")
    step6 = text[text.index("## STEP 6:"):text.index("## When a query matched nothing")]
    assert '- **"RNA-SEQ" IS BULK RNA-SEQ' in step6


def test_the_graph_agent_names_the_missing_field_not_a_context_word():
    text = _read("graph_agent.txt")
    rule = text[text.index("**A part of the question no field holds.**"):]
    assert "say in the explanation which fields you looked at and that none of them records it" in rule
    assert 'Never write that a word was "context only"' in rule


def test_the_chatter_says_what_the_data_lacks_and_names_the_mirna_only_patients():
    chatter = _read("chatter_agent.txt")
    graph_mode = chatter[chatter.index("MODE: graph_query"):chatter.index("MODE: memory_computed")]
    assert 'Never write that a word was "treated as context"' in graph_mode
    assert "A `mirna_seq_only` column" in graph_mode
