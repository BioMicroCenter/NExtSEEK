"""graph_review Tier 1 against the offline replay fixture (110 graph turns: 11 should fire, 99 should stay quiet).

The fixture is scrubbed: people are `Person A`, `Person B`, ...; ids are `<run>-<task>`. Each record's `catalog`
block holds the (label, attribute) value lists the Tier 1 checks read, from the 2026-09-14 catalog snapshot.
"""
import json
import pathlib

import pytest

from chat_nextseek.graph_review import VALUES_CAP, ReviewInput, as_debug, review_tier1

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/graph_review_replay.json").read_text())


def _inp(r):
    return ReviewInput(question=r["question"], cypher=r["cypher"], parameters=r["parameters"] or {},
                       keyword_fields=r.get("keyword_fields") or {},
                       rows=[dict(zip(r["columns"], row)) for row in r["rows"]],
                       count=r["count"], total=r["total"], ok=r["ok"] is not False, error=None,
                       reply_draft=r.get("reply_head"))


def _values(r):
    cat = r.get("catalog") or {}
    return lambda label, attr: [tuple(v) for v in cat.get(f"{label}.{attr}", [])] or None


def _review(rid):
    r = next(r for r in FIX if r["id"] == rid)
    return review_tier1(_inp(r), _values(r))


def test_fixture_carries_the_labelled_set():
    assert len(FIX) == 110
    assert sum(r["label"] == "SHOULD_FIRE" for r in FIX) == 11
    assert sum(r["label"] == "SHOULD_STAY_QUIET" for r in FIX) == 99


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "SHOULD_FIRE"], ids=lambda r: r["id"])
def test_should_fire(r):
    rv = review_tier1(_inp(r), _values(r))
    assert rv.verdict in ("note", "suggest"), [c for c in rv.checks if c.fired]


@pytest.mark.parametrize("r", [r for r in FIX if r["label"] == "SHOULD_STAY_QUIET"], ids=lambda r: r["id"])
def test_stays_quiet(r):
    rv = review_tier1(_inp(r), _values(r))
    assert rv.verdict == "ok", [c for c in rv.checks if c.fired]


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
        rv = review_tier1(_inp(r), _values(r))
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
    r = next(r for r in FIX if r["id"] == "r7-709")
    inp = _inp(r)
    inp.reply_draft = "There are 98: 57 Non-converter, 32 Converter and 9 Reverter."
    rv = review_tier1(inp, lambda label, attr: None)
    assert not any(c.fired for c in rv.checks if c.name == "value_split_rows")


def test_an_exception_inside_a_check_is_ok_not_a_crash():
    bad = ReviewInput(question="x", cypher="MATCH (s:T_MUS) RETURN s", parameters=None, keyword_fields=None,
                      rows=None, count=None, total=None, ok=True, error=None)
    assert review_tier1(bad, lambda *_: None).verdict == "ok"


def test_a_provider_that_raises_is_recorded_not_raised():
    r = next(r for r in FIX if r["id"] == "r5-656")

    def boom(label, attr):
        raise RuntimeError("neo4j down")

    rv = review_tier1(_inp(r), boom)
    assert rv.verdict in ("ok", "note", "suggest")
    assert any(c.detail.startswith("error:") for c in rv.checks)


def test_as_debug_is_plain_json():
    rv = _review("r7-709")
    d = as_debug(rv)
    assert json.loads(json.dumps(d))["verdict"] == "suggest"
    assert VALUES_CAP == 50
