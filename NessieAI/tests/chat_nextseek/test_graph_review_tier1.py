"""graph_review Tier 1 against the offline replay fixture (110 graph turns: 11 should fire, 99 should stay quiet).

The fixture is scrubbed: people are `Person A`, `Person B`, ...; ids are `<run>-<task>`. Each record's `catalog`
block holds the (label, attribute) value lists the Tier 1 checks read, from the 2026-09-14 catalog snapshot, and
`DictCatalog` serves it as a `CatalogProvider`.

Two modes. Replay passes the recorded reply as `reply_draft`, as an offline review would. Live passes none, because
in the live graph turn the reviewer runs before the chatter writes the reply.
"""
import json
import pathlib
from collections import Counter

import pytest

from chat_nextseek.graph_review import VALUES_CAP, DictCatalog, ReviewInput, as_debug, review_tier1

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/graph_review_replay.json").read_text())

# Live, with no reply to read, these in-sample quiet turns open. Each is a known false open, not a regression.
LIVE_FALSE_OPENS = {
    "r4-616": "live there is no reply_draft: the zero stays quiet in replay only because the reply already offers "
              "to drop the scientist filter, so live zero_unproven_base opens (a chatter-side backstop is the fix)",
}


def _inp(r, reply=True):
    return ReviewInput(question=r["question"], cypher=r["cypher"], parameters=r["parameters"] or {},
                       keyword_fields=r.get("keyword_fields") or {},
                       rows=[dict(zip(r["columns"], row)) for row in r["rows"]],
                       count=r["count"], total=r["total"], ok=r["ok"] is not False, error=None,
                       reply_draft=r.get("reply_head") if reply else None)


def _catalog(r):
    return DictCatalog(r.get("catalog"))


def _rec(rid):
    return next(r for r in FIX if r["id"] == rid)


def _review(rid):
    r = _rec(rid)
    return review_tier1(_inp(r), _catalog(r))


def _check(rv, name):
    return next(c for c in rv.checks if c.name == name)


class _Down:
    """A provider whose every call fails, as a live one does when Neo4j is unreachable."""

    def values(self, label, attribute):
        raise RuntimeError("neo4j down")

    def attributes(self, label):
        raise RuntimeError("neo4j down")

    def type_name(self, label):
        raise RuntimeError("neo4j down")


class _Counting:
    """Counts every (method, args) the reviewer asks the provider for; optionally fails every call."""

    def __init__(self, block, fail=False):
        self._inner = DictCatalog(block)
        self._fail = fail
        self.calls = Counter()

    def _hit(self, method, *args):
        self.calls[(method, args)] += 1
        if self._fail:
            raise RuntimeError("neo4j down")
        return getattr(self._inner, method)(*args)

    def values(self, label, attribute):
        return self._hit("values", label, attribute)

    def attributes(self, label):
        return self._hit("attributes", label)

    def type_name(self, label):
        return self._hit("type_name", label)


def test_fixture_carries_the_labelled_set():
    assert len(FIX) == 110
    assert sum(r["label"] == "SHOULD_FIRE" for r in FIX) == 11
    assert sum(r["label"] == "SHOULD_STAY_QUIET" for r in FIX) == 99


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "SHOULD_FIRE"], ids=lambda r: r["id"])
def test_should_fire(r):
    rv = review_tier1(_inp(r), _catalog(r))
    assert rv.verdict in ("note", "suggest"), [c for c in rv.checks if c.fired]


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "SHOULD_STAY_QUIET"], ids=lambda r: r["id"])
def test_stays_quiet(r):
    rv = review_tier1(_inp(r), _catalog(r))
    assert rv.verdict == "ok", [c for c in rv.checks if c.fired]


def _live_cases():
    return [pytest.param(r, id=r["id"], marks=pytest.mark.xfail(strict=True, reason=LIVE_FALSE_OPENS[r["id"]])
                         if r["id"] in LIVE_FALSE_OPENS else ())
            for r in FIX]


@pytest.mark.parametrize("r", _live_cases())
def test_live_mode_has_no_reply_draft(r):
    rv = review_tier1(_inp(r, reply=False), _catalog(r))
    if r["label"] == "SHOULD_FIRE":
        assert rv.verdict in ("note", "suggest"), [c for c in rv.checks if c.fired]
    else:
        assert rv.verdict == "ok", [c for c in rv.checks if c.fired]


def test_the_live_false_open_is_the_zero_check_alone():
    r = _rec("r4-616")
    rv = review_tier1(_inp(r, reply=False), _catalog(r))
    assert [c.name for c in rv.checks if c.fired] == ["zero_unproven_base"]


def test_converter_split_numbers_are_in_the_disclosure():
    rv = _review("r7-709")
    assert all(n in rv.disclosure for n in ("57", "32", "9"))


def test_converter_split_offers_the_exact_non_negated_value():
    rv = _review("r7-709")
    assert rv.verdict == "suggest"
    assert rv.suggestion["label"] == "Only Converter"
    assert rv.suggestion["query"] == "show samples for human subjects classified as Converter"


def test_species_split_replaces_the_matched_word():
    rv = _review("live-717")
    assert "42" in rv.disclosure and "14" in rv.disclosure
    assert rv.suggestion["label"] == "Only Macaca fascicularis"
    assert rv.suggestion["query"].startswith("what Macaca fascicularis monkeys have both")


def test_stem_miss_names_the_other_spellings():
    rv = _review("r5-656")
    assert rv.verdict == "suggest"
    assert "'tif'" in rv.disclosure and "'TIF'" in rv.disclosure
    assert rv.suggestion["label"] == "Include all spellings"
    assert rv.suggestion["query"].endswith(" Include every spelling of tif.")


def test_narrowed_all_question_offers_every_defined_type():
    rv = _review("r7-712")
    assert rv.disclosure.startswith("The query counted only types that hold samples.")
    assert rv.suggestion["label"] == "Count every defined type"
    assert rv.suggestion["query"].endswith(" Include types with no samples.")


def test_breakage_is_a_note_without_a_chip():
    rv = _review("r2-587")
    assert rv.verdict == "note" and rv.suggestion is None and rv.disclosure


@pytest.mark.parametrize("rid", ["r3-601", "r4-607", "r6-1225"])
def test_zero_premise_and_unapplied_suggest_without_a_chip(rid):
    rv = _review(rid)
    assert rv.verdict == "suggest" and rv.suggestion is None and rv.disclosure


def test_disclosure_is_facts_only_and_short():
    for r in FIX:
        rv = review_tier1(_inp(r), _catalog(r))
        if rv.disclosure is None:
            assert rv.verdict == "ok"
            continue
        assert len(rv.disclosure) < 300, (r["id"], rv.disclosure)
        for bad in ("MATCH", "WHERE", "CONTAINS", "RETURN", "search_text", "sample_count", "$"):
            assert bad not in rv.disclosure, (r["id"], rv.disclosure)


def test_every_check_is_recorded_including_the_information_only_ones():
    rv = _review("r3-601")
    names = [c.name for c in rv.checks]
    for n in ("breakage", "negated_value", "value_split_rows", "value_split_catalog", "stem_miss",
              "all_question_narrowed", "zero_unproven_base", "unapplied_value", "premise_count",
              "title_contains_multi", "count_only"):
        assert n in names
    info = {c.name: c for c in rv.checks}
    assert info["count_only"].fired is False and info["count_only"].detail
    assert info["title_contains_multi"].fired is False


def test_a_split_the_reply_already_counts_stays_quiet():
    r = _rec("r7-709")
    inp = _inp(r, reply=False)
    assert _check(review_tier1(inp, DictCatalog(None)), "value_split_rows").fired  # the control: no reply, fires
    inp.reply_draft = "There are 98: 57 Non-converter, 32 Converter and 9 Reverter."
    split = _check(review_tier1(inp, DictCatalog(None)), "value_split_rows")
    assert not split.fired and not split.detail.startswith("error:")


def test_an_exception_inside_a_check_is_ok_not_a_crash():
    bad = ReviewInput(question="x", cypher="MATCH (s:T_MUS) RETURN s", parameters=None, keyword_fields=None,
                      rows=None, count=None, total=None, ok=True, error=None)
    assert review_tier1(bad, DictCatalog(None)).verdict == "ok"


def test_a_provider_that_raises_is_recorded_not_raised():
    r = _rec("r5-656")
    rv = review_tier1(_inp(r), _Down())
    assert rv.verdict in ("ok", "note", "suggest")
    assert _check(rv, "stem_miss").detail == "error: RuntimeError: neo4j down"


# Two CONTAINS filters and an equality on one attribute, and a zero: without the memo the reviewer asks for
# T_HSU.Status three times (twice in the value checks, once in the zero check).
_REPEATS = ReviewInput(
    question="how many human subjects convert or are converters",
    cypher="MATCH (s:T_HSU) WHERE toLower(s.Status) CONTAINS 'convert' AND toLower(s.Status) CONTAINS 'conv' "
           "AND toLower(s.Status) = 'converter' RETURN count(s) AS n",
    parameters={}, keyword_fields={}, rows=[{"n": 0}], count=0, total=0, ok=True, error=None)
_REPEATS_CATALOG = {"T_HSU.Status": [["Converter", 32], ["Non-converter", 57], ["Reverter", 9]],
                    "T_HSU.*": [["Status", 3]], "T_HSU.@name": [["Human subject", 98]]}


def test_the_provider_is_asked_each_key_once_per_review():
    cat = _Counting(_REPEATS_CATALOG)
    review_tier1(_REPEATS, cat)
    assert cat.calls[("values", ("T_HSU", "Status"))] == 1
    assert cat.calls and max(cat.calls.values()) == 1, cat.calls


def test_a_failed_key_is_remembered_and_reraised_to_each_check():
    cat = _Counting(_REPEATS_CATALOG, fail=True)
    rv = review_tier1(_REPEATS, cat)
    assert rv.verdict == "ok"
    assert max(cat.calls.values()) == 1, cat.calls
    assert _check(rv, "zero_unproven_base").detail == "error: RuntimeError: neo4j down"
    assert _check(rv, "negated_value").detail == "error: RuntimeError: neo4j down"


@pytest.mark.parametrize("r", FIX, ids=lambda r: r["id"])
def test_no_fixture_turn_asks_a_key_twice(r):
    cat = _Counting(r.get("catalog"))
    review_tier1(_inp(r), cat)
    assert not cat.calls or max(cat.calls.values()) == 1, cat.calls


def test_as_debug_is_plain_json():
    rv = _review("r7-709")
    d = as_debug(rv)
    assert json.loads(json.dumps(d))["verdict"] == "suggest"
    assert VALUES_CAP == 50
