"""Resolver unit tests. Pure — no DB, no Django."""

import pytest

from dmac.vocab_resolver import (
    TIER_CONFLICT,
    TIER_EXACT,
    TIER_FUZZY,
    TIER_NONE,
    TIER_PRECEDENT,
    normalize,
    strip_disposition,
    suggest,
)

VOCAB = [
    (74, "Tissue Collection"),
    (58, "PET/CT Scan"),
    (40, "Library Prep"),
    (57, "PCR"),
]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tissue Collection - Metadata", "Tissue Collection"),
        ("Flow Cytometry - Data Linked", "Flow Cytometry"),
        ("Comet Chip Analysis - Data Attached", "Comet Chip Analysis"),
        ("Library Prep: Validation Data", "Library Prep"),
        ("Gene Expression Analysis: Training Data", "Gene Expression Analysis"),
        # en dash, not hyphen — the case a naive split silently misses
        ("Patient Visit – Metadata", "Patient Visit"),
        ("All Metadata", "All Metadata"),
    ],
)
def test_strip_disposition(raw, expected):
    assert strip_disposition(raw) == expected


def test_normalize_collapses_punctuation_and_case():
    assert normalize("PET-CT Scan") == normalize("PET/CT Scan")
    assert normalize("  Tissue   Collection ") == "tissue collection"


def test_exact_match_after_stripping():
    cands = suggest("Tissue Collection - Metadata", VOCAB, [])
    assert len(cands) == 1
    assert cands[0].vocabulary_id == 74
    assert cands[0].tier == TIER_EXACT


def test_exact_match_ignores_punctuation_difference():
    cands = suggest("PET-CT Scan - Data Linked", VOCAB, [])
    assert cands[0].vocabulary_id == 58
    assert cands[0].tier == TIER_EXACT


def test_no_candidate_is_a_tier_not_an_empty_list():
    cands = suggest("All Metadata", VOCAB, [])
    assert len(cands) == 1
    assert cands[0].tier == TIER_NONE
    assert cands[0].vocabulary_id is None


def test_blank_title_does_not_raise():
    assert suggest("", VOCAB, [])[0].tier == TIER_NONE
    assert suggest(None, VOCAB, [])[0].tier == TIER_NONE


VOCAB2 = VOCAB + [(12, "Cell Isolation"), (34, "Genome Alignment")]

# Two mapped assays share the stripped title "Cell Extraction" -> Cell Isolation.
PRECEDENTS_STRONG = [
    ("Cell Extraction", 12, "Cell Isolation"),
    ("Cell Extraction: Validation Data", 12, "Cell Isolation"),
]


def test_precedent_with_two_supporters_is_precedent_tier():
    cands = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)
    assert cands[0].tier == TIER_PRECEDENT
    assert cands[0].vocabulary_id == 12
    assert cands[0].support == 2


def test_precedent_basis_names_its_support():
    basis = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)[0].basis
    assert "2" in basis and "Cell Isolation" in basis


def test_single_precedent_is_demoted_to_fuzzy():
    """One prior mapping is an anecdote, not a convention."""
    cands = suggest(
        "Glycosylation Assay - Data Linked",
        VOCAB2,
        [("Glycosylation Assay", 34, "Genome Alignment")],
    )
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].support == 1


def test_disagreeing_precedents_produce_a_conflict_with_both_shown():
    cands = suggest(
        "Imaging Run - Metadata",
        VOCAB2,
        [
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 34, "Genome Alignment"),
            ("Imaging Run", 34, "Genome Alignment"),
        ],
    )
    assert all(c.tier == TIER_CONFLICT for c in cands)
    assert {c.vocabulary_id for c in cands} == {12, 34}


def test_exact_match_beats_precedent():
    cands = suggest("Library Prep - Metadata", VOCAB2, [("Library Prep", 12, "Cell Isolation")])
    assert cands[0].tier == TIER_EXACT
    assert cands[0].vocabulary_id == 40


VOCAB3 = VOCAB2 + [(49, "Mass Spectrometry Proteomics Analysis"), (42, "Luminex")]


def test_fuzzy_matches_a_qualified_variant():
    """'Real Time RT-PCR' is a PCR; the only PCR term should surface."""
    cands = suggest("Real Time RT-PCR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 57


def test_fuzzy_matches_a_shortened_variant():
    cands = suggest("Proteomics Analysis - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 49


def test_fuzzy_matches_a_prefixed_variant():
    cands = suggest("Cytokine Luminex - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 42


def test_unrelated_title_stays_none_rather_than_guessing():
    """The resolver declining is correct behaviour, not a failure."""
    cands = suggest("RaDR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_NONE


def test_fuzzy_never_outranks_exact():
    cands = suggest("PCR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_EXACT


def test_basis_grammar_agrees_in_the_plural():
    basis = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)[0].basis
    assert basis == "2 mapped entities with this title map to Cell Isolation."


def test_basis_grammar_agrees_in_the_singular():
    """Both bases surface verbatim in the curator-facing evidence pane."""
    basis = suggest(
        "Glycosylation Assay - Data Linked",
        VOCAB2,
        [("Glycosylation Assay", 34, "Genome Alignment")],
    )[0].basis
    assert basis == "1 mapped entity with this title maps to Genome Alignment."


def test_conflict_basis_grammar_agrees_in_the_singular():
    """A conflict side can rest on a single precedent, so its copy has to
    decline too — 'entity ... maps', never '1 mapped entities ... map'."""
    cands = suggest(
        "Imaging Run - Metadata",
        VOCAB2,
        [
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 34, "Genome Alignment"),
        ],
    )
    bases = {c.support: c.basis for c in cands}
    assert bases[2] == (
        "2 mapped entities with this title map to Cell Isolation, but others disagree."
    )
    assert bases[1] == (
        "1 mapped entity with this title maps to Genome Alignment, but others disagree."
    )
