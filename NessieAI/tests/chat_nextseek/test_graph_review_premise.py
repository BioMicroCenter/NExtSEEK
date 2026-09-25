"""A number in the question is a stated count only when it sizes a set the user names (Phase F, F-b).

``premise_count`` (the graph turn's Tier 1, through ``stated_counts``) and ``check_premise`` (the follow-up loop's,
through ``SET_COUNT``) both read a number before a count word as the size the user says a set has. Neither reads a
year ("In 2023 ...", "Of the 2024 samples"), a rank or sample size ("the 100 most recent samples", "a random subset
of 200 samples", "the 500 samples with the highest RIN") or a threshold ("more than 100 samples"): one shared rule
(``graph_review._not_a_stated_count``) drops those for both. A number right after "these" or "those" points back at a
set the user has seen, so only the year and threshold rules apply to it. ``check_premise`` still needs a
set-pointing word right before the number; ``stated_counts`` does not.

The premise fact is one shared sentence (``PREMISE_FACT``), which the chatter's backstop finds in the reviewer's
note by ``PREMISE_FACT_RE``.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

from chat_nextseek import graph_review as gr
from chat_nextseek.graph_review import DictCatalog, ReviewInput, review_tier1


@pytest.mark.parametrize("q", ["In 2023 how many D.SEQ samples were uploaded?",
                               "Of the 2024 samples, how many are female?",
                               "Show D.SEQ files from 1998 data"])
def test_a_year_is_not_a_stated_count(q):
    assert gr.stated_counts(q) == []


@pytest.mark.parametrize("q,n", [
    ("Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC' in their UID?", 4095),
    ("how many of these 1,206 mouse sample records have transcriptomic data?", 1206),
    ("Of the 2,023 samples, how many are mice?", 2023),
    ("Of the 1500 samples, how many are mice?", 1500),
])
def test_a_count_is_still_read(q, n):
    assert gr.stated_counts(q) == [n]


def _premise(question):
    inp = ReviewInput(question=question, cypher="MATCH (s:T_D_SEQ) WHERE s.uuid CONTAINS $t RETURN s.uuid AS uuid",
                      parameters={"t": "ABC"}, keyword_fields={}, rows=[{"uuid": f"D.SEQ-X-{i}"} for i in range(3)],
                      count=962, total=962, ok=True, error=None)
    rv = review_tier1(inp, DictCatalog(None))
    return next(c for c in rv.checks if c.name == "premise_count"), rv


def test_the_premise_fires_and_says_so_in_the_shared_sentence():
    check, rv = _premise("Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC' in their UID?")
    assert check.fired
    assert gr.PREMISE_FACT_RE.search(rv.disclosure)


def test_a_year_in_the_question_is_not_a_premise():
    check, _ = _premise("In 2023 how many D.SEQ files have 'ABC' in their UID?")
    assert not check.fired


def _fired(x):  # Task 13's check_premise returns a Check or a finding; read both
    return bool(x) and getattr(x, "fired", True)


def test_the_follow_up_premise_check_ignores_a_year_too():
    assert not _fired(gr.check_premise("Of the 2024 samples, how many are female?", stored_total=745))
    assert _fired(gr.check_premise("how many of these 1,206 mouse sample records have transcriptomic data?",
                                   stored_total=745))


# --------------------------------------------------------------------------- #
# Ranks, sample sizes and thresholds, for both scans (supervisor carry from Task 13's review)
# --------------------------------------------------------------------------- #

#: A number that picks which records (a rank, a random subset, a sample) or bounds them (a threshold, a range) is no
#: claim about the size of a set. The 2-digit forms are the literal cases; the 3-digit ones are the same shapes at a
#: size the number scan reads at all (it skips numbers under 100 written without a comma). Fix round 1 added the
#: mirror forms: a bound after the number or its count word, a range, a superlative before the number, "sorted by".
NOT_A_SET_SIZE = [
    "Of the 100 most recent samples, how many are female?",
    "Of the first 20 samples, how many are female?",
    "Of the first 200 samples, how many are female?",
    "Of the top 50 samples, how many are female?",
    "Of the top 500 samples, how many are female?",
    "Of a random subset of 200 samples, how many are female?",
    "Of a sample of 300 mice, how many are female?",
    "Of the 500 samples with the highest RIN, how many are female?",
    "Of the 500 D.SEQ files with the highest read count, how many are paired?",
    "Of the 500 D.SEQ files with the most reads, how many are paired?",
    "Of the 300 latest samples, how many are female?",
    "Which projects have more than 100 samples?",
    "Show projects with at least 500 samples",
    # fix round 1
    "Which projects have 500 or more samples?",
    "Which projects have 1,000 samples or more?",
    "Which projects have 200 or fewer samples?",
    "Which projects have between 100 and 500 samples?",
    "Which projects have from 100 to 500 samples?",
    "Which projects have 500+ samples?",
    "Which projects have 1,000 samples and up?",
    "Of the most recent 500 samples, how many are female?",
    "Of the smallest 500 samples by RIN, how many are female?",
    "Of the youngest 300 mice, how many are female?",
    "Of the 500 samples sorted by RIN, how many are female?",
    "Of the 500 samples ordered by date, how many are female?",
    "Of the 500 samples ranked by RIN, how many are female?",
    "Of a representative sample of 300 mice, how many are female?",
    # fix round 2: a top-N aggregate ranks by any superlative, and a hyphenated rank word between the number and its
    # count word ranks too
    "Of the 500 samples with the latest collection dates, how many are female?",
    "Of the 500 top-ranked D.SEQ files, how many are paired?",
    "the 200 highest-RIN samples",
    "Of the 500 most-recent samples, how many are female?",
    # fix round 3: a bare range reads neither bound, also after "these"
    "Which projects have 100 to 500 samples?",
    "Show projects with 100 to 500 samples",
    "Of these 100 to 500 samples, how many are mice?",
    "Which projects have 100 - 500 samples?",
    "Which projects have 100 \u2013 500 samples?",
    "Which projects have 100\u2013500 samples?",
]


@pytest.mark.parametrize("q", NOT_A_SET_SIZE)
def test_a_rank_sample_size_or_threshold_is_not_a_stated_count(q):
    assert gr.stated_counts(q) == []


@pytest.mark.parametrize("q", NOT_A_SET_SIZE)
def test_the_follow_up_premise_check_ignores_a_rank_sample_size_or_threshold(q):
    assert not _fired(gr.check_premise(q, stored_total=745))


@pytest.mark.parametrize("q", NOT_A_SET_SIZE)
def test_a_rank_sample_size_or_threshold_is_no_graph_premise(q):
    check, _ = _premise(q)
    assert not check.fired


@pytest.mark.parametrize("q,n", [
    ("Of the 2,023 samples, how many are mice?", 2023),
    ("Of the 1500 samples, how many are mice?", 1500),
    ("Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC' in their UID?", 4095),
])
def test_the_follow_up_premise_check_still_reads_a_set_size(q, n):
    check = gr.check_premise(q, stored_total=745)
    assert check.fired and check.detail == f"the earlier result had 745, not {n:,}"


@pytest.mark.parametrize("q,n", [
    ("Of the 4,095 D.SEQ files with most of their metadata filled, how many are paired?", 4095),
    ("Of the 1,641 NDMA-treated mice, how many are female?", 1641),
    ("Of the 4,095 files, how many were uploaded first?", 4095),
    ("Of the 4,095 files, the 100 with the highest RIN: how many are paired?", 4095),
])
def test_a_set_size_near_a_rank_word_is_still_read(q, n):
    """The rank words count only in their places: before the number, between it and its count word, or as a
    ranking right after the count word ("with the highest ...")."""
    assert gr.stated_counts(q) == [n]


@pytest.mark.parametrize("q,n", [
    ("Of the subset of 1,206 CC mice, how many are female?", 1206),
    ("In this subset of 1,206 mice, how many are female?", 1206),
    ("Of the 4,095 D.SEQ files uploaded by the latest pipeline, how many are paired?", 4095),
    ("Of these 1,206 mice with the most complete metadata, how many are female?", 1206),
    ("Of the 745 CC mice, how many have 500 or more reads?", 745),
    ("Are most of 1,206 samples female?", 1206),
    ("Can you provide a table of these 807 samples sorted by date?", 807),
    ("Show those 1,206 mice ordered by age", 1206),
    ("Of the 745 samples - 300 of them female - how many are mice?", 745),
])
def test_a_known_set_is_still_read_by_both_scans(q, n):
    """A set named with "the" or "this" before "subset of", a set described after its count word ("uploaded by the
    latest pipeline") and a set named by "these" or "those" (a back-reference, which no rank, sort or subset rule
    reads away) are set sizes (fix rounds 1 and 2)."""
    assert gr.stated_counts(q) == [n]
    check = gr.check_premise(q, stored_total=7)
    assert check.fired and check.detail == f"the earlier result had 7, not {n:,}"


@pytest.mark.parametrize("q", ["Of these 2024 samples, how many are female?",
                               "Of these 500 or more samples, how many are female?",
                               "Of those more than 100 samples, how many are female?"])
def test_a_back_reference_keeps_the_year_and_threshold_rules(q):
    assert gr.stated_counts(q) == [] and not _fired(gr.check_premise(q, stored_total=745))


def test_a_year_does_not_hide_a_count_after_it():
    """The year is dropped on its own: it does not take the words up to the count word with it."""
    q = "In 2023 we uploaded 4,095 D.SEQ files; how many have 'ABC' in their UID?"
    assert gr.stated_counts(q) == [4095]
    assert _premise(q)[0].fired


def test_a_comma_makes_a_four_digit_number_a_count():
    assert gr.stated_counts("Of the 2,024 samples, how many are female?") == [2024]


def test_the_premise_fact_is_the_shared_sentence():
    assert gr.PREMISE_FACT == "The question says {n}; this search did not reproduce that number."
    assert gr.PREMISE_FACT_RE.fullmatch(gr.PREMISE_FACT.format(n="4,095"))
    check, rv = _premise("Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC' in their UID?")
    assert gr.PREMISE_FACT.format(n="4,095") in rv.disclosure


def test_the_replayed_echo_turn_still_fires():
    """r4-607, the turn this fix is for: "all the 4,095 Sequencing Data (D.SEQ) files"."""
    fixture = json.loads((pathlib.Path(__file__).parent / "fixtures/graph_review_replay.json").read_text())
    r = next(r for r in fixture if r["id"] == "r4-607")
    inp = ReviewInput(question=r["question"], cypher=r["cypher"], parameters=r["parameters"] or {},
                      keyword_fields=r.get("keyword_fields") or {},
                      rows=[dict(zip(r["columns"], row)) for row in r["rows"]],
                      count=r["count"], total=r["total"], ok=r["ok"] is not False, error=None)
    rv = review_tier1(inp, DictCatalog(r.get("catalog")))
    assert next(c for c in rv.checks if c.name == "premise_count").fired
    assert gr.PREMISE_FACT.format(n="4,095") in rv.disclosure


@pytest.mark.parametrize("tail,n", [(" 500 samples", 500), (" and 500 samples", 500), (" to 500 samples", None)])
def test_a_long_comma_joined_digit_run_is_read_quickly(tail, n):
    """The range rule in ``_THRESHOLD`` may start only where a number starts, never after a comma inside one: from
    every digit of a run like 1,1,1 it re-read the rest of the run, 6 to 9 s on 40,000 characters. The readings
    stay: the 500 after the run is a stated count, and the run followed by "to 500 samples" is a range."""
    text = ",".join(["1"] * 20000) + tail
    assert len(text) > 40000
    t0 = time.perf_counter()
    counts = gr.stated_counts(text)
    assert time.perf_counter() - t0 < 0.5
    assert counts == ([n] if n else [])
