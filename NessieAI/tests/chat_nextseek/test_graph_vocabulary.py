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
