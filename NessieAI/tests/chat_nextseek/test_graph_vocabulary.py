"""The graph agent's vocabulary blocks: which ones a question gets, and how they are held to a byte budget.

``graph_context.render_vocabulary`` is the live catalog's path and ``agents/graph.py``'s ``_fallback_vocabulary`` the
committed files' path; both use the one gate, ``graph_context.mentions``. Every fixture here is synthetic.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_context as gc
from chat_nextseek.agents import graph as graph_mod


def fallback_config(protocol_titles=("P.ABC-100_Blood-Draw.docx",),
                    connections=({"assay": "Bulk RNA Sequencing", "parent_type": "RNA", "child_type": "D.SEQ"},)):
    config = MagicMock()
    config.PROTOCOL_SCHEMA = {"protocol_titles": list(protocol_titles)}
    config.ASSAY_SAMPLE_CONNECTIONS = {"connections": list(connections)}
    return config


# ---------------------------------------------------------------------------------------------------------------
# The gate: whole words, inflected, never a substring of another word
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Which samples went through an assay?",
    "list every assay", "which assays ran", "Which samples were assayed?", "Samples still assaying",
    "which subassays exist", "What resequencing runs exist?", "Were any of them reprocessed?",
    "How many RNA-seq datasets do we have?", "Is there a dataset for the lung samples?",
    "show me assay data", "samples with proteomics data", "the sequencing-sample count",
    "IMAGING SAMPLES", "samples that underwent extraction", "tissue collections from 2020",
])
def test_the_assay_gate_fires_on_assay_language(question):
    assert gc.mentions(gc.ASSAY_WORDS, question)


@pytest.mark.parametrize("question", [
    "How many mouse samples does the database hold?",  # "data" inside "database" fired the substring test
    "Which samples have metadata for Organ?",
    "Is that trivial to count?",  # "via" inside "trivial"
    "Show the candidate lung samples",
    "How many tissue samples are there?",
])
def test_the_assay_gate_ignores_a_gate_word_inside_another_word(question):
    assert not gc.mentions(gc.ASSAY_WORDS, question)


@pytest.mark.parametrize("question, fires", [
    ("Which protocol was used?", True), ("Which protocols were used?", True), ("what methods exist", True),
    ("the technique's name", True), ("list the procedures", True),
    ("which methodology was used", False), ("How many mouse samples are there?", False),
])
def test_the_protocol_gate(question, fires):
    assert gc.mentions(gc.PROTOCOL_WORDS, question) is fires


def test_the_gate_word_lists():
    assert gc.PROTOCOL_WORDS == ("protocol", "method", "procedure", "technique")
    assert gc.ASSAY_WORDS == ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "dataset",
                              "processed", "associated", "underwent", "via", "collection", "extraction")


def test_mentions_with_nothing_to_match():
    assert not gc.mentions((), "assay")
    assert not gc.mentions(gc.ASSAY_WORDS, "")
    assert not gc.mentions(gc.ASSAY_WORDS, None)


def test_mentions_keeps_nothing_per_call():
    """A caller passing its own word lists must not grow the module: the verdict's unbounded cache."""
    def sizes():
        return {name: len(value) for name, value in vars(gc).items() if isinstance(value, (dict, list, set))}

    before = sizes()
    for i in range(600):
        gc.mentions((f"word{i}",), f"a word{i} here")
    assert sizes() == before


def test_render_vocabulary_uses_the_whole_word_gate():
    vocab = SimpleNamespace(investigation_titles=("Impact",), project_titles=(), study_titles=(),
                            published_studies=(), assay_titles=("Bulk RNA Sequencing",), protocol_titles=("P-1",),
                            assay_connections=())
    assert "ASSAY TITLES" not in gc.render_vocabulary(vocab, "How many mouse samples does the database hold?")
    assert "ASSAY TITLES" in gc.render_vocabulary(vocab, "How many RNA-seq datasets do we have?")
    assert "PROTOCOL TITLES" not in gc.render_vocabulary(vocab, "which methodology was used")


# ---------------------------------------------------------------------------------------------------------------
# The catalog-down path uses the same gate
# ---------------------------------------------------------------------------------------------------------------

def test_the_fallback_vocabulary_uses_the_whole_word_gate():
    config = fallback_config()
    assert graph_mod._fallback_vocabulary(config, "How many mouse samples does the database hold?") == []
    assert graph_mod._fallback_vocabulary(config, "which methodology was used") == []
    assayed = "\n\n".join(graph_mod._fallback_vocabulary(config, "Which samples were assayed?"))
    assert "ASSAY-SAMPLE CONNECTIONS" in assayed and "PROTOCOL VOCABULARY" not in assayed
    both = "\n\n".join(graph_mod._fallback_vocabulary(config, "Which protocols made the RNA-seq datasets?"))
    assert "ASSAY-SAMPLE CONNECTIONS" in both and "PROTOCOL VOCABULARY" in both


# ---------------------------------------------------------------------------------------------------------------
# The byte budget: bounded, and never at the cost of an entry the question names
# ---------------------------------------------------------------------------------------------------------------

def big_vocab():
    """A synthetic vocabulary well over the budget, sorted as the catalog reader sorts it.

    The entries a flow cytometry or read-length question needs sit late in the alphabet, where a cap that keeps
    the head of each sorted list would cut them.
    """
    protocols = [f"P.A{i:03d}-200101-V1_Generic-Step-{i:03d}.docx" for i in range(260)]
    protocols += [f"P.Z{i:02d}-200101-V1_Flow-Cytometry-Panel-{i:02d}.docx" for i in range(12)]
    protocols += ["P.ZZZ-200101-V1_Tissue-Collection-at-Necropsy.docx"]
    assays = [f"Generic Assay {i:03d}" for i in range(140)] + [
        "Flow Cytometry", "Flow Cytometry Analysis", "Long Read Sequencing", "Short Read Sequencing"]
    connections = [{"assay": f"Generic Assay {i % 140:03d}", "parent_type": f"P{i:03d}", "child_type": "D.GEN"}
                   for i in range(300)]
    connections.append({"assay": "Short Read Sequencing", "parent_type": "RNA", "child_type": "D.SEQ"})
    return SimpleNamespace(
        investigation_titles=tuple(f"Investigation {i:02d}" for i in range(12)),
        project_titles=tuple(f"Project {i:02d}" for i in range(12)),
        study_titles=tuple(f"A synthetic study of generic samples, number {i:03d}" for i in range(100)),
        published_studies=tuple({"title": f"A synthetic paper {i:03d}", "DOI": f"10.1/{i:04d}", "PMID": str(i)}
                                for i in range(50)),
        assay_titles=tuple(sorted(assays)), protocol_titles=tuple(sorted(protocols)),
        assay_connections=tuple(connections),
    )


EVERY_GATE = "Which studies and protocols used flow cytometry or short read sequencing assays?"


def size(text):
    return len(text.encode("utf-8"))


def test_the_vocabulary_budget():
    assert gc.VOCAB_BUDGET_BYTES == 28_672


def test_a_vocabulary_under_budget_renders_as_before():
    small = SimpleNamespace(
        investigation_titles=("Impact", "MetNet"), project_titles=("Core",), study_titles=("A lung paper",),
        published_studies=({"title": "A lung paper", "doi": "10.1/x", "pmid": "123"},),
        assay_titles=("Bulk RNA Sequencing", "Flow Cytometry"), protocol_titles=("P.ABC-1.pdf",),
        assay_connections=({"assay": "Bulk RNA Sequencing", "parent_type": "RNA", "child_type": "D.SEQ"},
                           {"assay": "Bulk RNA Sequencing", "parent_type": "TIS", "child_type": "D.SEQ"}))
    assert gc.render_vocabulary(small, EVERY_GATE) == (
        'INVESTIGATION TITLES (Investigation.title):\n"Impact", "MetNet"\n\n'
        'PROJECT TITLES (Project.title):\n"Core"\n\n'
        'STUDY TITLES (Study.title):\n"A lung paper"\n\n'
        'PUBLISHED STUDIES (Study nodes with a DOI or PMID):\n- "A lung paper", DOI 10.1/x, PMID 123\n\n'
        'ASSAY TITLES (DERIVED_FROM.internal_assay_title values):\n"Bulk RNA Sequencing", "Flow Cytometry"\n\n'
        "ASSAY-SAMPLE CONNECTIONS (assay: parent type -> child type; shows which side of an assay a sample type "
        'sits on):\n- "Bulk RNA Sequencing": RNA -> D.SEQ, TIS -> D.SEQ\n\n'
        'PROTOCOL TITLES (DERIVED_FROM.protocol_title values):\n"P.ABC-1.pdf"')


def test_over_budget_every_entry_the_question_names_is_kept():
    vocab = big_vocab()
    assert size(gc.render_vocabulary(vocab, EVERY_GATE, budget=10**9)) > gc.VOCAB_BUDGET_BYTES
    text = gc.render_vocabulary(vocab, EVERY_GATE)
    assert size(text) <= gc.VOCAB_BUDGET_BYTES
    for title in ("Flow Cytometry", "Flow Cytometry Analysis", "Short Read Sequencing", "Long Read Sequencing"):
        assert f'"{title}"' in text
    flow_protocols = [t for t in vocab.protocol_titles if "Flow-Cytometry" in t]
    assert all(f'"{t}"' in text for t in flow_protocols)
    assert '- "Short Read Sequencing": RNA -> D.SEQ' in text


@pytest.mark.parametrize("budget", [20_000, 12_000, 6_000])
def test_a_named_entry_outlives_every_entry_the_question_does_not_name(budget):
    vocab = big_vocab()
    text = gc.render_vocabulary(vocab, EVERY_GATE, budget=budget)
    assert size(text) <= budget
    assert '"Flow Cytometry"' in text and '"Short Read Sequencing"' in text
    assert all(f'"{t}"' in text for t in vocab.protocol_titles if "Flow-Cytometry" in t)
    assert "none of them shares a word with the question" in text


def test_a_trimmed_block_says_how_many_it_left_out():
    vocab = big_vocab()
    text = gc.render_vocabulary(vocab, "Which protocol was used for tissue collection?", budget=4_000)
    protocols = text[text.index("PROTOCOL TITLES"):]
    shown = protocols.count(".docx")
    note = protocols.splitlines()[-1]
    assert note == (f"(and {len(vocab.protocol_titles) - shown:,} more not shown here; none of them shares a word "
                    "with the question)")
    assert '"P.ZZZ-200101-V1_Tissue-Collection-at-Necropsy.docx"' in protocols


def test_the_largest_block_is_trimmed_first():
    vocab = big_vocab()
    question = "Which protocols were used?"  # investigations, projects and protocols
    whole = gc.render_vocabulary(vocab, question, budget=10**9)
    text = gc.render_vocabulary(vocab, question, budget=size(whole) - 500)
    assert size(text) <= size(whole) - 500
    blocks_before, blocks_after = whole.split("\n\n"), text.split("\n\n")
    assert blocks_after[:2] == blocks_before[:2]  # the two small blocks are untouched
    assert blocks_after[2] != blocks_before[2]


def test_named_entries_are_trimmed_only_when_they_alone_are_over_budget():
    vocab = big_vocab()
    named_only = gc.render_vocabulary(vocab, "flow cytometry protocols", budget=2_500)
    assert size(named_only) <= 2_500
    assert "shares a word" in named_only or "sharing a word" in named_only
    squeezed = gc.render_vocabulary(vocab, "flow cytometry protocols", budget=900)
    assert size(squeezed) <= 900
    assert "sharing a word with the question" in squeezed  # it says a named entry was cut
    assert "Flow-Cytometry" in squeezed  # but the block keeps its best one


def test_blocks_are_dropped_from_the_end_only_when_one_entry_each_is_too_big():
    vocab = big_vocab()
    text = gc.render_vocabulary(vocab, EVERY_GATE, budget=300)
    assert size(text) <= 300
    assert text.startswith("INVESTIGATION TITLES")
    assert gc.render_vocabulary(vocab, EVERY_GATE, budget=0) == ""


def test_question_words_drop_the_words_every_question_uses():
    assert gc.question_words("Which samples were collected using the tissue collection protocol?") == {
        "collected", "tissue", "collection"}
    assert gc.question_words("How many RNA-seq datasets do we have in the database?") == {"rna", "seq"}
    assert gc.question_words("") == frozenset()


@pytest.mark.parametrize("entry, named", [
    ("Flow Cytometry", True), ("P.ABC-200101-V1_Flow-Cytometry-Panel.docx", True),
    ("P.ABC-200101-V1_3DOpticalFlowAlgorithm.docx", True),  # a filename runs the words together
    ("P.ABC-200101-V1_Standard_Workflow.doc", True),  # as a CONTAINS 'flow' would; a spare entry costs bytes only
    ("Short Read Sequencing", False), ("P.ABC-200101-V1_Generic-Step.docx", False),
])
def test_an_entry_is_named_by_a_question_word_inside_it(entry, named):
    assert bool(gc._named_by(entry, gc.question_words("flow protocols"))) is named


def test_an_entry_is_named_by_a_shared_root():
    words = gc.question_words("Which samples were collected using the tissue collection protocol?")
    assert gc._named_by("P.ABC-200101-V1_Tissue-Collection-at-Necropsy.docx", words) == 3
    assert gc._named_by("Short Read Sequencing", gc.question_words("what was sequenced")) == 1
    assert gc._named_by("NHP", gc.question_words("nhp samples")) == 1
    assert gc._named_by("NHPX", gc.question_words("nhp samples")) == 0  # three letters must match whole


def test_fit_vocabulary_never_returns_more_than_the_budget():
    blocks = [gc.VocabularyBlock(head=f"BLOCK {n}:\n", entries=tuple(f'"entry {n} {i:03d}"' for i in range(200)),
                                 sep=", ") for n in range(4)]
    for budget in (0, 10, 50, 500, 5_000, 10**9):
        assert size("\n\n".join(gc.fit_vocabulary(blocks, "entry", budget=budget))) <= budget


# ---------------------------------------------------------------------------------------------------------------
# The catalog-down path has the same bound
# ---------------------------------------------------------------------------------------------------------------

def test_the_fallback_vocabulary_is_unchanged_under_budget():
    import json
    config = fallback_config()
    blocks = graph_mod._fallback_vocabulary(config, "Which protocols made the RNA-seq datasets?")
    assert blocks == [
        "PROTOCOL VOCABULARY (DERIVED_FROM.protocol_title values):\n"
        + json.dumps(config.PROTOCOL_SCHEMA["protocol_titles"], indent=2),
        "ASSAY-SAMPLE CONNECTIONS (assay → parent_type → child_type, use to determine which side a sample type "
        "sits on for a given assay):\n" + json.dumps(config.ASSAY_SAMPLE_CONNECTIONS["connections"], indent=2),
    ]


def test_the_fallback_vocabulary_is_bounded_and_keeps_what_the_question_names():
    vocab = big_vocab()
    config = fallback_config(vocab.protocol_titles, vocab.assay_connections)
    question = "Which protocols and assays made the short read sequencing samples from tissue collection?"
    blocks = graph_mod._fallback_vocabulary(config, question)
    text = "\n\n".join(blocks)
    assert size(text) <= gc.VOCAB_BUDGET_BYTES
    assert '"P.ZZZ-200101-V1_Tissue-Collection-at-Necropsy.docx"' in text
    assert '"assay": "Short Read Sequencing"' in text
    assert "none of them shares a word with the question" in text
