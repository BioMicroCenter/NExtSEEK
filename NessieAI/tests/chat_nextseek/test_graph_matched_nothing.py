"""A successful query that found nothing must be recognised, whatever shape it came back in.

The graph turn retries once when its query matched nothing. That retry keyed on the ROW
count, so it fired for `RETURN s.id ...` with no rows and never fired for
`RETURN count(s) AS total` returning one row holding zero.

The 2026-09-16 evaluation run measured what that costs. Of the five graph answers that were
a wrong zero, three were the single-row shape and the retry fired on none of them:

    search.hela_trap                    said 0, truth 4      (count=1, data=[{total: 0}])
    how_many_samples_are_from_the_ka    said 0, truth 9,821  (count=1, data=[{total: 0}])
    routing.lab_ooc_kamm_count          said 0, truth 683    (count=1, data=[{total: 0}])

Each was reported to the user as fact: "There are no samples from the Kamm lab currently
recorded in the database." A false negative is the one error a user cannot detect.
"""
from __future__ import annotations

import pytest

from chat_nextseek.helpers.tools.neo4j import matched_nothing


def agg(**cols):
    """One row, the shape `RETURN count(s) AS total` comes back in."""
    return {"ok": True, "count": 1, "data": [dict(cols)]}


class TestTheShapeThatWasMissed:
    """The regression these three ids stand for."""

    @pytest.mark.parametrize("qid", [
        "search.hela_trap", "how_many_samples_are_from_the_ka", "routing.lab_ooc_kamm_count",
    ])
    def test_a_single_row_aggregate_zero_is_nothing(self, qid):
        assert matched_nothing(agg(total=0)) is True, qid

    def test_several_zero_aggregates_in_one_row_are_still_nothing(self):
        assert matched_nothing(agg(roots=0, leaves=0)) is True


class TestWhatMustNotTrigger:
    """Retrying costs a turn; retrying a real answer would also risk replacing it."""

    def test_a_real_count_is_not_nothing(self):
        assert matched_nothing(agg(total=4)) is False

    def test_a_mixed_row_found_something(self):
        assert matched_nothing(agg(roots=0, leaves=5)) is False

    def test_a_data_row_that_happens_to_hold_a_zero_is_not_an_aggregate(self):
        # One returned sample whose count column is 0 is a row of data, not an empty answer.
        assert matched_nothing(agg(uuid="TIS-230830ENG-1", n=0)) is False

    def test_several_rows_found_something_whatever_the_numbers_say(self):
        assert matched_nothing({"ok": True, "count": 2, "data": [{"n": 0}, {"n": 0}]}) is False

    def test_a_boolean_is_not_a_number(self):
        assert matched_nothing(agg(exists=False)) is False


class TestTheCasesThatAlreadyWorked:
    def test_zero_rows_is_nothing(self):
        assert matched_nothing({"ok": True, "count": 0, "data": []}) is True

    def test_a_failed_query_is_not_this_branch(self):
        # An error has its own retry with its own context; this must not claim it.
        assert matched_nothing({"ok": False, "error": "SyntaxError", "data": None}) is False

    @pytest.mark.parametrize("result", [None, {}, {"ok": True, "count": 1, "data": None}])
    def test_malformed_results_do_not_raise(self, result):
        assert matched_nothing(result) in (True, False)
