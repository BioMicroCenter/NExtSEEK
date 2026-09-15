"""Resolver unit tests. Pure — no DB, no Django."""

import pytest

from dmac.vocab_resolver import (
    EVIDENCE_LIMIT,
    TIER_CONFLICT,
    TIER_EXACT,
    TIER_FUZZY,
    TIER_NONE,
    TIER_PRECEDENT,
    normalize,
    split_disposition,
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


# --- evidence: the specific facts behind each tier ---------------------------
# `basis` says what KIND of reason a candidate has; `evidence` says WHICH facts
# produced it. The design spec accepted this resolver with no provenance
# migration on the explicit grounds that "the evidence pane always names the
# specific entities it is leaning on", so these are that promise, asserted.


def test_precedent_evidence_names_the_supporting_entities():
    cand = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)[0]
    assert cand.evidence == (
        "“Cell Extraction”",
        "“Cell Extraction: Validation Data”",
    )


def test_duplicate_supporters_collapse_to_one_line_with_a_count():
    """Two entities with the SAME title are one precedent spelling, not two."""
    cand = suggest(
        "Cell Extraction - Metadata",
        VOCAB2,
        PRECEDENTS_STRONG + [("Cell Extraction", 12, "Cell Isolation")],
    )[0]
    assert cand.support == 3
    assert cand.evidence[0] == "“Cell Extraction” ×2"


def test_evidence_is_ordered_by_count_then_title():
    """Deterministic output is the module's stated contract.

    All three spellings normalize to the same bucket key -- that is the ONLY
    way supporters group together -- so this also pins which spellings a
    curator is shown side by side.
    """
    precedents = [("ALPHA", 12, "Cell Isolation")] + [
        ("Alpha", 12, "Cell Isolation")
    ] * 2
    a = suggest("alpha", VOCAB2, precedents)
    b = suggest("alpha", VOCAB2, list(reversed(precedents)))
    assert a[0].evidence == b[0].evidence == (
        "“Alpha” ×2",
        "“ALPHA”",
    )


# Ten spellings of one title: case variants plus every disposition suffix. They
# share a bucket precisely because normalize(strip_disposition(...)) folds all
# ten to "variant". Ten DIFFERENT titles would be ten different buckets and
# would support nothing at all -- which is what the first draft of this test got
# wrong, and what the assertion below now pins.
TEN_SPELLINGS = [
    (spelling, 12, "Cell Isolation")
    for spelling in (
        "Variant", "variant", "VARIANT", "VaRiAnT", "vARIANT",
        "Variant - Metadata", "Variant - Data Linked", "Variant - Data Attached",
        "Variant: Training Data", "Variant: Validation Data",
    )
]


def test_long_precedent_lists_are_capped_but_count_the_remainder():
    cand = suggest("Variant - Metadata", VOCAB2, TEN_SPELLINGS)[0]
    # Every supporter is still counted; only the naming is truncated, or the
    # pane would quietly misreport how large the precedent is.
    assert cand.support == 10
    assert len(cand.evidence) == EVIDENCE_LIMIT + 1
    assert cand.evidence[-1] == "…and 4 more."
    named = [line for line in cand.evidence if line.startswith("“")]
    assert len(named) == EVIDENCE_LIMIT


def test_each_conflicting_candidate_carries_its_own_evidence():
    cands = suggest(
        "Imaging Run - Metadata",
        VOCAB2,
        [
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run: Training Data", 12, "Cell Isolation"),
            ("Imaging Run", 34, "Genome Alignment"),
        ],
    )
    by_vocab = {c.vocabulary_id: c.evidence for c in cands}
    assert by_vocab[12] == (
        "“Imaging Run”",
        "“Imaging Run: Training Data”",
    )
    assert by_vocab[34] == ("“Imaging Run”",)


def test_a_demoted_single_precedent_says_it_was_demoted():
    cand = suggest(
        "Glycosylation Assay - Data Linked",
        VOCAB2,
        [("Glycosylation Assay", 34, "Genome Alignment")],
    )[0]
    assert cand.tier == TIER_FUZZY
    assert cand.evidence[0] == "“Glycosylation Assay”"
    assert "not as an established convention" in cand.evidence[-1]


def test_exact_evidence_names_the_suffix_it_set_aside_and_the_key():
    cand = suggest("Tissue Collection - Metadata", VOCAB, [])[0]
    assert cand.tier == TIER_EXACT
    assert cand.evidence == (
        "Set aside the disposition suffix “- Metadata”.",
        "Compared as “tissue collection”.",
    )


def test_exact_evidence_omits_the_suffix_line_when_there_was_none():
    cand = suggest("PET-CT Scan", VOCAB, [])[0]
    assert cand.tier == TIER_EXACT
    assert cand.evidence == ("Compared as “pet ct scan”.",)


def test_fuzzy_evidence_names_the_shared_words_and_the_score():
    cand = suggest("Tissue Collection Protocol", VOCAB, [])[0]
    assert cand.tier == TIER_FUZZY
    joined = " | ".join(cand.evidence)
    assert "“collection”" in joined and "“tissue”" in joined
    assert "at or above the 0.60 threshold" in joined


def test_none_evidence_reports_what_was_compared():
    cand = suggest("All Metadata", VOCAB, [])[0]
    assert cand.tier == TIER_NONE
    assert cand.evidence[0] == "Compared as “all metadata”."


def test_blank_title_has_no_evidence_to_offer():
    """There is no comparison key, so there is nothing true to say."""
    assert suggest("", VOCAB, []).pop().evidence == ()


def test_split_disposition_returns_the_suffix_as_written():
    """Matching is dash- and case-insensitive; the report must not be."""
    assert split_disposition("Patient Visit – Metadata") == (
        "Patient Visit",
        " - Metadata",
    )
    assert split_disposition("All Metadata") == ("All Metadata", None)


def test_strip_disposition_still_agrees_with_the_split():
    for raw in ("Tissue Collection - Metadata", "All Metadata", "", None):
        assert strip_disposition(raw) == split_disposition(raw)[0]
