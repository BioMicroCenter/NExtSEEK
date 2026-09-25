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

from chat_nextseek.graph_review import (SPELLINGS_MAX, VALUES_CAP, DictCatalog, ReviewInput, as_debug,
                                        review_tier1, value_spellings)

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


@pytest.mark.parametrize("rid", ["r3-601", "r4-607"])
def test_zero_and_premise_suggest_without_a_chip(rid):
    rv = _review(rid)
    assert rv.verdict == "suggest" and rv.suggestion is None and rv.disclosure


def test_an_unapplied_value_offers_the_narrowed_search():
    """Operator ruling 2026-09-25: 10,761 TCGA patients "with an RNA-Seq alignment" (miRNA-Seq alignments counted
    too) is an acceptable answer, but the reviewer must say so and offer "Only RNA-Seq". Before the ruling this case
    carried no chip by design."""
    rv = _review("r6-1225")
    assert rv.verdict == "suggest"
    assert rv.disclosure == "The question names 'RNA-Seq', but the search did not filter on it."
    assert rv.suggestion == {
        "kind": "narrow_value", "label": "Only RNA-Seq",
        "query": "How many TCGA patients have at least one RNA-Seq alignment derived from their samples? Count only "
                 "Sequence Alignment Analysis records whose DataType is RNA-Seq.",
        "reason": "The question names 'RNA-Seq', but the search did not filter on it."}
    from chat_nextseek.helpers.suggestions import check_suggestion
    assert check_suggestion(rv.suggestion) is None                    # it passes every chip guardrail


def test_the_narrowed_search_names_no_type_when_the_catalog_has_none():
    r = _rec("r6-1225")
    block = {k: v for k, v in r["catalog"].items() if not k.endswith(".@name")}
    rv = review_tier1(_inp(r), DictCatalog(block))
    assert rv.suggestion["query"].endswith("samples? Count only records whose DataType is RNA-Seq.")


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
    assert d["fired"] == [c.name for c in rv.checks if c.fired] and d["fired"]
    assert d["lookups"] == {}
    assert VALUES_CAP == 50


# ------------------------------------------------------------------ value_spellings -------------------------------
RNA_Q = "How many TCGA patients have at least one RNA-Seq alignment derived from their samples?"


def test_value_spellings_include_the_value_as_the_question_writes_it_and_its_usual_forms():
    spellings = value_spellings(RNA_Q, blob={"tcga", "derived", "from"}, type_words={"patient"})
    for form in ("RNA-Seq", "rna-seq", "RNA Seq", "RNA_Seq", "rna seq", "RNA-SEQ", "Rna-Seq", "RNA-seq"):
        assert form in spellings
    assert spellings == sorted(spellings) and len(spellings) == len(set(spellings))


def test_value_spellings_leave_out_what_the_check_would_never_accept():
    spellings = {s.lower() for s in value_spellings(RNA_Q, blob={"tcga", "patients"}, type_words={"alignment"})}
    assert "tcga" not in spellings and "tcga patients" not in spellings           # only words the query uses
    assert "alignment" not in spellings                                          # only the type's own words
    assert not any(s.startswith(("how ", "at ", "have ")) or s.endswith((" at", " one", " their")) for s in spellings)
    assert "rna-seq alignment" in spellings                                      # a run of up to four words
    assert not any(len(s.split()) > 4 for s in spellings)


def test_value_spellings_skip_digits_short_words_and_stop_values():
    assert value_spellings("How many female mice aged 12 are in it?", blob=set(), type_words=set()) == sorted(
        value_spellings("How many female mice aged 12 are in it?", blob=set(), type_words=set()))
    got = {s.lower() for s in value_spellings("How many female mice aged 12 are in it?", blob=set(),
                                              type_words=set())}
    assert "female" not in got and "12" not in got and "mice" in got and "female mice" in got


def test_value_spellings_are_capped_dropping_the_longest_phrases_first():
    long_q = " ".join(f"word{i}" for i in range(40))
    got = value_spellings(long_q, blob=set(), type_words=set())
    assert len(got) <= SPELLINGS_MAX
    assert all(len(s.split()) <= 2 for s in got)          # three- and four-word phrases were dropped to fit


# ------------------------------------------------------------------ live mode: the production provider -----------
# The live provider (graph_review_counts.live_values) with the production budget and a cold cache, its tool answering
# each record's own catalog block: value lists for ``values_statement`` and hit flags for ``probe_statement``. This is
# the mode the dev run of 2026-09-25 lacked: the offline acceptance used DictCatalog, the live provider read two
# uncached lists and stopped, and the reviewer never fired.
import re as _re                                                                   # noqa: E402
from types import SimpleNamespace as _NS                                           # noqa: E402

from chat_nextseek import graph_catalog as _gc                                     # noqa: E402
from chat_nextseek import graph_review_counts as _g2                               # noqa: E402
from chat_nextseek.graph_scope import SCOPE_ATTR, GraphScope                       # noqa: E402

_VALUES_RE = _re.compile(r"MATCH \(s:(T_\w+)\) WHERE s\.(\w+) IS NOT NULL")
_PROBE_RE = _re.compile(r"EXISTS \{ MATCH \(s:(T_\w+)\) WHERE s\.(\w+) IN \$spellings \}")


def _live(monkeypatch, record, *, seekable="all", cost_s=0.0, clock=None):
    block = record.get("catalog") or {}
    cat = DictCatalog(block)
    labels = sorted({k.split(".", 1)[0] for k in block})
    index = tuple(_gc.TypeIndexRow(title=lab, label=lab, name=cat.type_name(lab), clade=None, sample_count=None,
                                   deprecated=False, attributes_with_values=0) for lab in labels)
    guard = {lab: frozenset(cat.attributes(lab) or []) for lab in labels}
    snap = _gc.CatalogSnapshot(catalog_hash="h", synced_at=None, has_usage=False, index=index, guard=guard)
    monkeypatch.setattr(_gc, "get_snapshot", lambda config: snap)
    monkeypatch.setattr(_gc, "get_seekable", lambda config: guard if seekable == "all" else seekable)
    monkeypatch.setattr(_gc, "get_type_details", lambda config, titles: [
        _NS(attributes=[_NS(title=a, value_type="string") for a in guard.get(t, ())]) for t in titles])
    statements = []

    def tool(config, cypher, parameters=None, *, timeout_s=None, total_only=False):
        statements.append(cypher)
        if clock is not None:
            clock.t += cost_s
        m = _VALUES_RE.match(cypher)
        if m:
            rows = [{"v": v, "n": n} for v, n in (cat.values(m.group(1), m.group(2)) or [])]
            return {"ok": True, "data": rows, "count": len(rows)}
        probes = _PROBE_RE.findall(cypher)
        if probes:
            asked = set(parameters["spellings"])
            return {"ok": True, "count": 1, "data": [
                {f"a{i}": any(str(v) in asked for v, _n in (cat.values(lab, attr) or [])) for i, (lab, attr)
                 in enumerate(probes)}]}
        raise AssertionError(f"unexpected statement: {cypher}")
    monkeypatch.setattr(_g2, "tool_neo4j_query", tool)
    _g2.reset_values_cache()
    config = _NS(NEO4J_URI="bolt://graph:7687", NEO4J_DATABASE="neo4j",
                 **{SCOPE_ATTR: GraphScope.for_projects([1], source="test")})
    return config, statements


@pytest.mark.parametrize("seekable", ["all", None], ids=["indexed", "indexes-unknown"])
@pytest.mark.parametrize("r", _live_cases())
def test_live_provider_mode(monkeypatch, r, seekable):
    config, _statements = _live(monkeypatch, r, seekable=seekable)
    provider = _g2.live_values(config)
    rv = review_tier1(_inp(r, reply=False), provider)
    if r["label"] == "SHOULD_FIRE":
        assert rv.verdict in ("note", "suggest"), (rv.checks, provider.lookups())
    else:
        assert rv.verdict == "ok", [c for c in rv.checks if c.fired]


def test_live_mode_fires_unapplied_value_on_the_rna_seq_case(monkeypatch):
    config, statements = _live(monkeypatch, _rec("r6-1225"))
    provider = _g2.live_values(config)
    rv = review_tier1(_inp(_rec("r6-1225"), reply=False), provider)
    unapplied = _check(rv, "unapplied_value")
    assert unapplied.fired and "T_A_ALN.DataType='RNA-Seq'" in unapplied.detail
    assert rv.suggestion["label"] == "Only RNA-Seq"
    looked = provider.lookups()
    # a probe per queried type (T_PAT's 191 attributes need two statements), then the one value list it pointed at
    probed = [c["key"].split(" ")[0] for c in looked["calls"] if c["kind"] == "probe"]
    assert set(probed) <= {"T_PAT", "T_A_ALN", "T_RNA"} and len(probed) <= 4
    assert [c["key"] for c in looked["calls"] if c["kind"] == "values"] == ["T_A_ALN.DataType"]
    assert len(statements) <= 5


class _Clock:
    t = 100.0


# Per statement: the dev graph took 34 ms warm and 528 ms the first time for the largest probe (2026-09-25).
@pytest.mark.parametrize("budget_s, cost_s", [(2.0, 0.3), (1.0, 0.15)], ids=["full-first-run", "late-warm"])
def test_live_mode_stays_inside_its_budget_and_still_fires(monkeypatch, budget_s, cost_s):
    clock = _Clock()
    monkeypatch.setattr(_g2, "_clock", lambda: clock.t)
    config, statements = _live(monkeypatch, _rec("r6-1225"), cost_s=cost_s, clock=clock)
    provider = _g2.live_values(config, budget_s=budget_s)
    rv = review_tier1(_inp(_rec("r6-1225"), reply=False), provider)
    assert _check(rv, "unapplied_value").fired, provider.lookups()
    assert provider.lookups()["spent_ms"] <= budget_s * 1000 + cost_s * 1000   # the budget, plus one statement


def test_a_spent_budget_stops_the_reads_and_says_so(monkeypatch):
    """0.3 s a statement and a 1 s budget: T_PAT's 191 attributes take two probes, T_A_ALN's probe finds DataType, and
    no time is left to read it. The review stays quiet, and its lookups say the budget stopped it."""
    clock = _Clock()
    monkeypatch.setattr(_g2, "_clock", lambda: clock.t)
    config, _statements = _live(monkeypatch, _rec("r6-1225"), cost_s=0.3, clock=clock)
    provider = _g2.live_values(config, budget_s=1.0)
    rv = review_tier1(_inp(_rec("r6-1225"), reply=False), provider)
    assert rv.verdict == "ok"
    calls = provider.lookups()["calls"]
    assert {"kind": "values", "key": "T_A_ALN.DataType", "outcome": "budget", "ms": 0} in calls
    assert provider.lookups()["counts"]["budget"] >= 1
