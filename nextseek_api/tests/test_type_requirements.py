"""The rule that turns counted derivation pairs into upload requirements."""

import pytest

from nextseek_api.services.type_requirements import (
    COMPANION_MIN,
    COVERAGE_FLOOR,
    DOMINANCE_FLOOR,
    MAX_SET,
    MIN_SUPPORT,
    Requirement,
    classify,
    classify_companions,
)


def test_thresholds_are_the_agreed_values():
    """Changing these changes which types are constrained; pin them.

    MIN_SUPPORT was 20 until the first live run showed every type it excluded
    had a single parent at 100% coverage. Lowering it to 10 added nine
    requirements, all of them at 100%.
    """
    assert (MIN_SUPPORT, COVERAGE_FLOOR, MAX_SET) == (10, 0.95, 3)


def test_a_single_parent_covering_everything_is_a_hard_requirement():
    out = classify([("D.SEQ", "DNA", 2055, ["Short Read Sequencing"])])
    assert out["D.SEQ"].parents == ["DNA"]
    assert out["D.SEQ"].coverage == pytest.approx(1.0)
    assert out["D.SEQ"].support == 2055
    assert out["D.SEQ"].assays == ["Short Read Sequencing"]


def test_the_real_pav_distribution_yields_nhp_and_pat_only():
    """PAV <- NHP alone is 79%, under the floor. MUS is the 2% tail and is
    excluded because NHP+PAT already clear it. This case is why the rule is
    disjunctive at all."""
    out = classify([
        ("PAV", "NHP", 4791, ["Patient Visit"]),
        ("PAV", "PAT", 1113, ["Patient Visit"]),
        ("PAV", "MUS", 123, ["Tissue Collection"]),
    ])
    assert out["PAV"].parents == ["NHP", "PAT"]
    assert out["PAV"].coverage == pytest.approx((4791 + 1113) / 6027, abs=0.005)


def test_parents_are_ordered_by_share_descending():
    out = classify([
        ("DNA", "TIS", 400, []),
        ("DNA", "BAC", 1390, []),
        ("DNA", "RNA", 232, []),
    ])
    assert out["DNA"].parents == ["BAC", "TIS", "RNA"]


def test_a_set_wider_than_max_set_is_not_a_requirement():
    """D.IMG really does need five parent types to reach the floor. A
    five-way choice is not something a user can act on."""
    out = classify([
        ("D.IMG", "OOC", 5859, []), ("D.IMG", "CEL", 3020, []),
        ("D.IMG", "TIS", 2200, []), ("D.IMG", "CHM", 1100, []),
        ("D.IMG", "PAV", 693, []),
    ])
    assert "D.IMG" not in out


def test_coverage_exactly_on_the_floor_is_kept():
    """95 of 100 is 0.95 to the bit, so this pins `>=` rather than `>`.

    Without it the comparison that defines the whole rule is unpinned:
    mutating it leaves every other case in this file passing, because they
    all land clear of the boundary on one side or the other. Here the first
    parent alone is exactly at the floor and must be taken alone -- a `>`
    would swallow the 5% tail into the requirement as a second alternative.
    """
    out = classify([("EXACT", "AAA", 95, []), ("EXACT", "BBB", 5, [])])
    assert out["EXACT"].parents == ["AAA"]
    assert out["EXACT"].coverage == COVERAGE_FLOOR


def test_support_below_the_floor_makes_no_claim():
    out = classify([("RARE", "TIS", MIN_SUPPORT - 1, [])])
    assert out == {}


def test_support_exactly_at_the_floor_is_kept():
    out = classify([("EDGE", "TIS", MIN_SUPPORT, [])])
    assert out["EDGE"].parents == ["TIS"]


def test_parents_that_never_reach_the_floor_yield_nothing():
    """Four parents at 25% each: the floor is unreachable within MAX_SET."""
    out = classify([("SPREAD", p, 100, []) for p in ("A", "B", "C", "D")])
    assert "SPREAD" not in out


def test_assays_are_unioned_across_the_chosen_parents_only():
    out = classify([
        ("PAV", "NHP", 4791, ["Patient Visit", "Patient Visit - Metadata"]),
        ("PAV", "PAT", 1113, ["Patient Visit"]),
        ("PAV", "MUS", 123, ["Tissue Collection"]),
    ])
    assert out["PAV"].assays == ["Patient Visit", "Patient Visit - Metadata"]


def test_a_null_assay_title_is_dropped_not_rendered():
    """8,398 of 522,465 real edges carry no title."""
    out = classify([("CEX", "NHP", 367, ["Tissue Collection", None, ""])])
    assert out["CEX"].assays == ["Tissue Collection"]


def test_ties_break_on_code_so_the_result_is_deterministic():
    out = classify([("X", "BBB", 50, []), ("X", "AAA", 50, [])])
    assert out["X"].parents == ["AAA", "BBB"]


def test_several_children_are_classified_independently():
    out = classify([
        ("D.SEQ", "DNA", 2055, []),
        ("CEX", "NHP", 367, []),
        ("RARE", "TIS", 3, []),
    ])
    assert sorted(out) == ["CEX", "D.SEQ"]


def test_empty_input_is_empty_output():
    assert classify([]) == {}


def test_a_requirement_of_one_parent_is_distinguishable_from_a_choice():
    out = classify([
        ("D.SEQ", "DNA", 2055, []),
        ("PAV", "NHP", 4791, []), ("PAV", "PAT", 1113, []),
    ])
    assert len(out["D.SEQ"].parents) == 1
    assert len(out["PAV"].parents) == 2


# ---------------------------------------------------------------------------
# Companions: the same counts read from the parent's side. A requirement says
# an upload cannot omit something; a companion predicts what a user is about to
# record next. The two are deliberately not symmetric.
# ---------------------------------------------------------------------------


def test_companion_thresholds_are_the_agreed_values():
    assert (DOMINANCE_FLOOR, COMPANION_MIN) == (0.80, 10)


def test_the_real_nhp_split_qualifies_at_eighty_percent():
    """82%, because an NHP also yields CEX. This pair is why the floor is 0.80
    and not 0.90 -- at 0.90 the case that motivated companions is excluded."""
    out = classify_companions([
        ("PAV", "NHP", 4791, ["Patient Visit"]),
        ("CEX", "NHP", 1057, ["Tissue Collection"]),
    ])
    assert out["NHP"].child == "PAV"
    assert out["NHP"].share == pytest.approx(4791 / 5848, abs=0.005)
    assert out["NHP"].assays == ["Patient Visit"]


def test_a_parent_whose_top_child_is_just_under_the_floor_predicts_nothing():
    out = classify_companions([("A", "P", 79, []), ("B", "P", 21, [])])
    assert out == {}


def test_a_parent_exactly_on_the_floor_is_kept():
    out = classify_companions([("A", "P", 80, []), ("B", "P", 20, [])])
    assert out["P"].child == "A"


def test_a_parent_with_no_dominant_child_predicts_nothing():
    """The real TIS distribution. Eleven outcomes, none dominant -- this is the
    cap that stops companions becoming 'add half the catalog'."""
    out = classify_companions([
        ("D.FLOW", "TIS", 1700, []), ("D.TITR", "TIS", 1600, []),
        ("BAC", "TIS", 1400, []), ("D.FCRB", "TIS", 1200, []),
        ("DNA", "TIS", 900, []), ("D.IMG", "TIS", 800, []),
    ])
    assert "TIS" not in out


def test_only_the_single_top_child_is_offered():
    out = classify_companions([("A", "P", 950, []), ("B", "P", 50, [])])
    assert out["P"].child == "A"
    assert not hasattr(out["P"], "children")


def test_a_parent_below_the_companion_minimum_makes_no_claim():
    out = classify_companions([("A", "P", COMPANION_MIN - 1, [])])
    assert out == {}


def test_companion_ties_break_on_the_child_code():
    """Neither can clear the floor, but the result must not depend on ordering."""
    out = classify_companions([("BBB", "P", 50, []), ("AAA", "P", 50, [])])
    assert out == {}


def test_a_requirement_and_a_companion_can_disagree_about_direction():
    """DNA <- BAC is a requirement AND BAC -> DNA is a companion, from one set
    of counts. They are different claims and both are true."""
    pairs = [("DNA", "BAC", 1390, []), ("D.BSRA", "BAC", 8, [])]
    assert classify(pairs)["DNA"].parents == ["BAC"]
    assert classify_companions(pairs)["BAC"].child == "DNA"


def test_companion_null_assay_titles_are_dropped():
    out = classify_companions([("PAV", "NHP", 300, ["Patient Visit", None, ""])])
    assert out["NHP"].assays == ["Patient Visit"]


def test_the_lowered_support_floor_admits_a_clean_single_parent():
    """A.GEX <- D.SEQ is 100% on 13 samples. At MIN_SUPPORT 20 it was hidden."""
    out = classify([("A.GEX", "D.SEQ", 13, [])])
    assert out["A.GEX"].parents == ["D.SEQ"]
    assert out["A.GEX"].coverage == pytest.approx(1.0)
