"""
Regression lock for T0.3: `OPTIONAL MATCH` directly followed by `WHERE` silently
discards graph filters.

In Cypher, a WHERE that immediately follows an OPTIONAL MATCH becomes part of the
optional pattern rather than a row filter, so non-matching rows survive with nulls
instead of being removed. Measured live on task 783's exact Cypher: 50,161 rows
without the intervening `WITH`, 705 with it. 50,161 is exactly
`MATCH (s:Sample)-[:IN_STUDY]->(:Study) RETURN count(DISTINCT s)` — the result is
independent of both bound parameters.

The prompt is not wrong; graph_agent.txt already carries the `WITH`. The model drops
it roughly 40% of the time, so this is a deterministic post-generation guard.

Every CYPHER constant below is verbatim from the 2026-07-27 run
(nessie-review-2026-07-27-postfix.html, `gplan.cypher`) — not hand-written.
"""
from __future__ import annotations

import pytest

from chat_nextseek.agents.graph import (
    optional_match_filter_leaks,
    repair_optional_match_filters,
)


# --------------------------------------------------------------- real bad shapes

# task 792 — "Find me studies in MetNet" — returned 51 (the whole catalog); truth is 10.
CYPHER_792 = """MATCH (st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE toLower(st.title) CONTAINS toLower($project)
   OR (inv IS NOT NULL AND toLower(inv.title) CONTAINS toLower($project))
RETURN DISTINCT st.title AS title, st.id AS id, inv.title AS investigation"""

# task 793 — "Find all tissue and cell samples from IMPACT." — capped at 5000; truth is 10,688.
CYPHER_793 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE s.type IN $types AND (toLower(st.title) CONTAINS toLower($project) OR toLower(inv.title) CONTAINS toLower($project))
RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type
LIMIT 5000"""

# task 817 — "How many samples are in the GBM study?" — reported 50,161; truth is 0.
CYPHER_817 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE toLower(st.title) CONTAINS toLower($project)
   OR toLower(inv.title) CONTAINS toLower($project)
RETURN count(DISTINCT s) AS total"""

BAD = {"792": CYPHER_792, "793": CYPHER_793, "817": CYPHER_817}


# -------------------------------------------------------------- real good shapes

# task 789 — OPTIONAL MATCH is followed by RETURN, not WHERE.
CYPHER_789 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
WHERE s.type = $sample_type
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
RETURN DISTINCT st.title AS study_title, st.id AS study_id, inv.title AS investigation_title"""

# task 790 — carries the WITH. Also has a second, legitimate MATCH...WHERE pair.
CYPHER_790 = """MATCH (tis:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WITH tis, st, inv
WHERE (toLower(st.title) CONTAINS toLower($project) OR toLower(inv.title) CONTAINS toLower($project))
  AND tis.type = $parent_type
MATCH (dseq:Sample)-[:DERIVED_FROM*1..]->(tis)
WHERE dseq.type = $child_type
RETURN DISTINCT tis.id AS id, tis.uuid AS uuid, tis.type AS type
LIMIT 5000"""

# task 791 — the correct answer to the same question task 783 got wrong (705).
CYPHER_791 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WITH s, st, inv
WHERE s.type = $type
  AND (toLower(st.title) CONTAINS toLower($project)
       OR toLower(inv.title) CONTAINS toLower($project))
RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type
LIMIT 5000"""

# task 794 — UNWIND-bound variable, relationship variable, OPTIONAL MATCH then RETURN.
CYPHER_794 = """MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)
WHERE r.internal_assay_title = $assay
WITH child, parent
UNWIND [child, parent] AS s
MATCH (s)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
RETURN DISTINCT inv.title AS project, st.title AS study"""

# task 822 — carries the WITH; returned the correct 408.
CYPHER_822 = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WITH s, st, inv
WHERE s.type = $type
  AND (toLower(st.title) CONTAINS toLower($project) OR toLower(inv.title) CONTAINS toLower($project))
RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type
LIMIT 5000"""

GOOD = {
    "789": CYPHER_789,
    "790": CYPHER_790,
    "791": CYPHER_791,
    "794": CYPHER_794,
    "822": CYPHER_822,
}


# ------------------------------------------------------------------- detection


@pytest.mark.parametrize("task", sorted(BAD))
def test_real_bad_turns_are_flagged(task):
    assert optional_match_filter_leaks(BAD[task]), f"task {task} leak not detected"


@pytest.mark.parametrize("task", sorted(GOOD))
def test_real_good_turns_are_not_flagged(task):
    assert optional_match_filter_leaks(GOOD[task]) == [], f"task {task} falsely flagged"


@pytest.mark.parametrize("task", sorted(GOOD))
def test_real_good_turns_are_returned_byte_identical(task):
    repaired, notes = repair_optional_match_filters(GOOD[task])
    assert repaired == GOOD[task]
    assert notes == []


# ---------------------------------------------------------------------- repair


def test_793_repairs_to_the_known_good_shape():
    """The inserted WITH must match task 791/822, which answered the same question correctly."""
    repaired, notes = repair_optional_match_filters(CYPHER_793)

    assert "WITH s, st, inv\nWHERE s.type IN $types" in repaired
    assert len(notes) == 1
    # Everything else is untouched.
    assert repaired.replace("WITH s, st, inv\n", "", 1) == CYPHER_793


def test_817_repairs_to_the_known_good_shape():
    repaired, _ = repair_optional_match_filters(CYPHER_817)

    assert "WITH s, st, inv\nWHERE toLower(st.title)" in repaired
    assert repaired.replace("WITH s, st, inv\n", "", 1) == CYPHER_817


def test_792_binds_only_the_variables_that_exist():
    """792 never binds `s` — inserting `WITH s, st, inv` would be a syntax error."""
    repaired, _ = repair_optional_match_filters(CYPHER_792)

    assert "WITH st, inv\nWHERE toLower(st.title)" in repaired
    assert "WITH s," not in repaired


def test_repaired_bad_turns_are_no_longer_flagged():
    for task, cypher in BAD.items():
        repaired, _ = repair_optional_match_filters(cypher)
        assert optional_match_filter_leaks(repaired) == [], f"task {task} still leaking after repair"


def test_repair_is_idempotent():
    once, _ = repair_optional_match_filters(CYPHER_793)
    twice, notes = repair_optional_match_filters(once)
    assert twice == once
    assert notes == []


# ------------------------------------------------------- masking and edge cases


def test_a_where_that_only_constrains_the_optional_pattern_is_left_alone():
    """
    The legitimate use. The predicate touches only `p`, which the optional pattern
    introduced, so Cypher's scoping is what the author meant.
    """
    cypher = """MATCH (s:Sample)
OPTIONAL MATCH (s)-[:DERIVED_FROM]->(p:Sample)
WHERE p.type = 'PAT'
RETURN s.id AS id, p.id AS parent_id"""

    assert optional_match_filter_leaks(cypher) == []


def test_the_canonical_exists_form_from_the_prompt_is_not_flagged():
    """graph_agent.txt now teaches this shape; the guard must leave it alone."""
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
WHERE toLower(st.title) CONTAINS toLower($project)
   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
               WHERE toLower(inv.title) CONTAINS toLower($project) }
RETURN count(DISTINCT s) AS total"""

    assert optional_match_filter_leaks(cypher) == []
    assert repair_optional_match_filters(cypher) == (cypher, [])


def test_a_comment_containing_where_does_not_create_a_clause():
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
// WHERE we would have filtered on st.title here
WITH s, st, inv
WHERE toLower(st.title) CONTAINS toLower($project)
RETURN count(s) AS total"""

    assert optional_match_filter_leaks(cypher) == []


def test_a_block_comment_containing_where_does_not_create_a_clause():
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
/* WHERE st.title = 'x' */
WITH s, st, inv
WHERE toLower(st.title) CONTAINS toLower($project)
RETURN count(s) AS total"""

    assert optional_match_filter_leaks(cypher) == []


def test_a_string_literal_containing_optional_match_is_not_a_clause():
    cypher = """MATCH (s:Sample)
WHERE s.description = 'OPTIONAL MATCH (x) WHERE x.id = 1'
RETURN s.id AS id"""

    assert optional_match_filter_leaks(cypher) == []


def test_a_parameter_named_where_is_not_a_clause():
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WITH s, st, inv
WHERE st.title = $where
RETURN count(s) AS total"""

    assert optional_match_filter_leaks(cypher) == []


def test_only_the_offending_optional_match_of_two_is_repaired():
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
OPTIONAL MATCH (s)-[:DERIVED_FROM]->(p:Sample)
WHERE p.type = 'PAT'
OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
WHERE toLower(st.title) CONTAINS toLower($project)
RETURN count(DISTINCT s) AS total"""

    leaks = optional_match_filter_leaks(cypher)
    assert len(leaks) == 1

    repaired, notes = repair_optional_match_filters(cypher)
    assert len(notes) == 1
    assert "WHERE p.type = 'PAT'" in repaired, "the legitimate predicate must be untouched"
    assert "WITH s, st, p, inv\nWHERE toLower(st.title)" in repaired


def test_indentation_of_the_where_line_is_preserved():
    cypher = """MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
    OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)
    WHERE toLower(st.title) CONTAINS toLower($project)
    RETURN count(s) AS total"""

    repaired, _ = repair_optional_match_filters(cypher)
    assert "\n    WITH s, st, inv\n    WHERE toLower(st.title)" in repaired


# ------------------------------------------------------------------- fail soft


@pytest.mark.parametrize("cypher", ["", None, "   ", "not cypher at all", "MATCH ((("])
def test_garbage_input_is_returned_untouched(cypher):
    repaired, notes = repair_optional_match_filters(cypher)
    assert repaired == cypher
    assert notes == []


def test_an_internal_error_never_breaks_a_working_query(monkeypatch):
    """The guard must never be able to take down a query that would have worked."""
    import chat_nextseek.agents.graph as graph_mod

    def boom(*a, **k):
        raise RuntimeError("guard exploded")

    monkeypatch.setattr(graph_mod, "optional_match_filter_leaks", boom)

    repaired, notes = repair_optional_match_filters(CYPHER_793)
    assert repaired == CYPHER_793
    assert notes == []
