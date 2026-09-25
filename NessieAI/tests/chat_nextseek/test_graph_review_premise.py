"""A number in the question is a stated count only when it sizes a set the user names (Phase F, F-b).

``premise_count`` (the graph turn's Tier 1, through ``stated_counts``) and ``check_premise`` (the follow-up loop's,
through ``SET_COUNT``) both read a number before a count word as the size the user says a set has. Neither reads a
year ("In 2023 ...", "Of the 2024 samples"), a rank or sample size ("the 100 most recent samples", "a random subset
of 200 samples", "the 500 samples with the highest RIN") or a threshold ("more than 100 samples"): one shared rule
(``graph_review._not_a_stated_count``) drops those for both. ``check_premise`` still needs a set-pointing word right
before the number; ``stated_counts`` does not.

The premise fact is one shared sentence (``PREMISE_FACT``), which the chatter's backstop finds in the reviewer's
note by ``PREMISE_FACT_RE``.
"""
from __future__ import annotations

import json
import pathlib

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

#: A number that picks which records (a rank, a random subset, a sample) or bounds them (a threshold) is no claim
#: about the size of a set. The 2-digit forms are the literal cases; the 3-digit ones are the same shapes at a size
#: the number scan reads at all (it skips numbers under 100 written without a comma).
NOT_A_SET_SIZE = [
    "Of the 100 most recent samples, how many are female?",
    "Of the first 20 samples, how many are female?",
    "Of the first 200 samples, how many are female?",
    "Of the top 50 samples, how many are female?",
    "Of the top 500 samples, how many are female?",
    "Of a random subset of 200 samples, how many are female?",
    "Of a sample of 300 mice, how many are female?",
    "Of the 500 samples with the highest RIN, how many are female?",
    "Of the 500 D.SEQ files with the most reads, how many are paired?",
    "Of the 300 latest samples, how many are female?",
    "Which projects have more than 100 samples?",
    "Show projects with at least 500 samples",
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
