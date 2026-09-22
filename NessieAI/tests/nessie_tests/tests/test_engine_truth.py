"""Ground-truth files and the oracle runner (graph_search Nessie POC, spec E5).

Host lane only: every oracle here is a fake executor, so nothing reaches Django,
Neo4j, MySQL or the graph_search endpoint. The real executors run only inside the
operator's venue (`scripts/graph_search/nessie_venue.sh exec`).
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import engine_truth as et
from NessieAI.tests.nessie_tests.scripts import derive_truth as dt

NOW = "2026-09-15T12:00:00+00:00"
LIVE = {"sample_count": 1084754, "catalog_hash": "h-live", "synced_at": "2026-09-15T01:00:00"}


# ── models ───────────────────────────────────────────────────────────────────

def _full_truth() -> et.TruthFile:
    return et.TruthFile(
        name="a_sample_search_1", group="A",
        fingerprint=et.Fingerprint(sample_count=1084754, catalog_hash="h", synced_at=None,
                                   derived_at=NOW),
        questions=[et.TruthQuestion(
            id="advanced.organ_lung", family="sample_search", group="A", source="corpus",
            turns=[et.TruthTurn(
                label="main", query="How many tissue samples come from the lung?",
                reading="Organ equals lung, ignoring case",
                oracle=et.Oracle(engine="graph_search", body={"sampletype": "TIS"}),
                second_oracle=et.Oracle(engine="sql", statement="SELECT 1", params={"a": 1}),
                expected=et.Expected(
                    kind="count", value=22734, required_numbers=[22734],
                    alternates=[et.Alternate(reading="exact spelling", required_numbers=[16841])],
                    sampletypes=["TIS"], attributes=["Organ"], relationships=[]),
                derived_at=NOW)],
            flags=["interpretive"], corpus_numbers=[16841.0])])


def test_models_round_trip_through_json():
    truth = _full_truth()
    again = et.TruthFile.model_validate_json(truth.model_dump_json())
    assert again == truth
    assert again.questions[0].turns[0].expected.alternates[0].required_numbers == [16841]


def test_an_unknown_engine_or_kind_is_refused():
    with pytest.raises(ValueError):
        et.Oracle(engine="rest")
    with pytest.raises(ValueError):
        et.Expected(kind="maybe")


# ── number_patterns ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "There are 107,412 samples.", "There are 107412 samples.", "There are 107 412 samples.",
    "There are 107 412 samples.", "**107,412** tissue samples", "(107,412)",
    "Total: 107,412", "107,412"])
def test_number_patterns_match_each_spelling(text):
    assert et.number_patterns(107412).search(text)


@pytest.mark.parametrize("text", [
    "There are 1,107,412 samples.", "There are 1107412 samples.", "107,4123 rows",
    "107,412,000 bytes", "2107412", "1 107 412 samples"])
def test_number_patterns_reject_a_longer_number(text):
    assert not et.number_patterns(107412).search(text)


@pytest.mark.parametrize("n,text", [
    (107412, "107,412.5 on average"), (107412, "107412.25"), (5, "about 5.5 ml"),
    (107, "107.412")])
def test_number_patterns_reject_a_decimal_continuation(n, text):
    assert not et.number_patterns(n).search(text)


@pytest.mark.parametrize("text", ["collected 2019-05-01", "on 01/05/2019", "2019/05/01"])
def test_number_patterns_reject_a_year_inside_a_date(text):
    assert not et.number_patterns(2019).search(text)


def test_number_patterns_still_find_a_bare_year_and_a_decimal():
    assert et.number_patterns(2019).search("collected in 2019.")
    assert et.number_patterns(12.5).search("a mean of 12.5 ml")
    assert not et.number_patterns(12.5).search("a mean of 112.5 ml")
    assert not et.number_patterns(12.5).search("a mean of 12.55 ml")
    assert et.number_patterns(107412.0).search("107,412 samples")


def test_number_patterns_skip_a_uid_tail():
    assert not et.number_patterns(26).search("sample TIS-220831FLY-26 is a tissue")
    assert et.number_patterns(26).search("26 samples")


def test_number_patterns_work_under_the_criteria_dsl_flags():
    """The case builder writes `.pattern` into a `matches_re` criterion, which the
    e2e DSL runs with re.IGNORECASE."""
    import re
    pattern = et.number_patterns(1084754).pattern
    assert re.search(pattern, "1,084,754 samples in all", flags=re.IGNORECASE)
    assert not re.search(pattern, "11,084,754 samples", flags=re.IGNORECASE)


# ── reply_satisfies ──────────────────────────────────────────────────────────

def _expected(**kw):
    base = dict(kind="count", value=22734, required_numbers=[22734],
                alternates=[et.Alternate(reading="exact spelling", required_numbers=[16841])])
    base.update(kw)
    return et.Expected(**base)


def test_reply_satisfies_the_primary_reading():
    ok, which = et.reply_satisfies("There are 22,734 lung tissue samples.", _expected())
    assert ok and which == "primary"


def test_reply_satisfies_an_alternate_and_says_which():
    ok, which = et.reply_satisfies("16,841 samples have Organ = Lung.", _expected())
    assert ok and which == "alternate: exact spelling"


def test_reply_satisfies_fails_with_a_reason():
    ok, why = et.reply_satisfies("There are 46,981 samples.", _expected())
    assert not ok and "22734" in why


def test_reply_satisfies_needs_every_required_number_and_item():
    exp = et.Expected(kind="list", value=["TIS-1", "TIS-2"], required_items=["TIS-1", "tis-2"],
                      required_numbers=[2])
    assert et.reply_satisfies("<p>The 2 samples are TIS-1 and TIS-2.</p>", exp)[0]
    assert not et.reply_satisfies("The 2 samples are TIS-1 and TIS-3.", exp)[0]
    assert not et.reply_satisfies("The samples are TIS-1 and TIS-2.", exp)[0]


def test_reply_satisfies_a_none_answer_and_an_empty_reply():
    exp = et.Expected(kind="none", value=0)
    assert et.reply_satisfies("No samples match that study.", exp)[0]
    assert et.reply_satisfies("I found 0 samples.", exp)[0]
    assert not et.reply_satisfies("", _expected())[0]
    assert not et.reply_satisfies(None, _expected())[0]


def test_reply_satisfies_uses_the_value_when_no_number_is_listed():
    exp = et.Expected(kind="count", value=195)
    assert et.reply_satisfies("There are 195 mice.", exp)[0]
    assert not et.reply_satisfies("There are 1195 mice.", exp)[0]


def test_reply_satisfies_an_unanswerable_expectation_is_never_satisfied():
    ok, why = et.reply_satisfies("anything", et.Expected(kind="list"))
    assert not ok and "nothing" in why


# ── the SQL guard ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("statement", [
    "UPDATE samples SET title = 'x'",
    "SELECT 1; SELECT 2",
    "SELECT * FROM samples INTO OUTFILE '/tmp/x'",
    "SELECT * INTO DUMPFILE '/tmp/x' FROM samples",
    "SELECT id FROM samples FOR UPDATE",
    "WITH t AS (SELECT 1) DELETE FROM samples",
    "DELETE FROM samples",
    "",
])
def test_check_sql_refuses_anything_but_one_read(statement):
    with pytest.raises(dt.OracleRefused):
        dt.check_sql(statement)


@pytest.mark.parametrize("statement", [
    "SELECT COUNT(*) FROM samples WHERE sample_type_id = 26",
    "  with t as (select 1 as n) select n from t;",
    "SELECT COUNT(*) FROM samples WHERE title LIKE '%;UPDATE%'",
    "SELECT updated_at FROM samples -- DELETE later\n LIMIT 1",
])
def test_check_sql_passes_a_single_read(statement):
    assert dt.check_sql(statement)


def test_check_cypher_refuses_a_write_and_passes_a_read():
    with pytest.raises(dt.OracleRefused):
        dt.check_cypher("MATCH (s:Sample) SET s.x = 1")
    with pytest.raises(dt.OracleRefused):
        dt.check_cypher("CALL apoc.refactor.mergeNodes([])")
    assert dt.check_cypher("MATCH (s:T_TIS) WHERE s.title = 'SET' RETURN count(s)")
    assert dt.check_cypher("CALL db.index.fulltext.queryNodes('x', 'lung') YIELD node RETURN count(node)")


# ── the fill, with fake executors ────────────────────────────────────────────

class FakeExecutors(dict):
    """engine -> callable(oracle, base_dir). Records every call."""

    def __init__(self, values, *, fingerprint=None, fail=()):
        super().__init__()
        self.calls = []
        self.values = values
        for engine in ("graph_search", "cypher", "sql", "measured"):
            self[engine] = self._make(engine, fail)
        self["fingerprint"] = lambda: dict(fingerprint or LIVE)

    def _make(self, engine, fail):
        def run(oracle, base_dir):
            key = (oracle.statement
                   or (json.dumps(oracle.body, sort_keys=True) if oracle.body is not None else None)
                   or oracle.source)
            self.calls.append((engine, key))
            if key in fail:
                raise dt.OracleFailed(f"{engine} failed")
            return self.values[key]
        return run


def _question(qid, *, oracle, second=None, flags=(), kind="count", single_source=False,
              scorable=True):
    return et.TruthQuestion(
        id=qid, family="sample_search", group="A", source="corpus",
        turns=[et.TruthTurn(label="main", query=f"question {qid}", reading="r",
                            oracle=oracle, second_oracle=second, single_source=single_source,
                            expected=et.Expected(kind=kind))],
        flags=list(flags), scorable=scorable)


def _truth(*questions, fingerprint=None):
    return et.TruthFile(name="t", group="A", fingerprint=fingerprint, questions=list(questions))


def test_fill_writes_counts_and_stamps_the_turn_and_the_file():
    truth = _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1")))
    ex = FakeExecutors({"C1": [{"n": 107412}]})
    report = dt.fill_truth(truth, ex, now=NOW)
    turn = truth.questions[0].turns[0]
    assert turn.expected.value == 107412
    assert turn.expected.required_numbers == [107412]
    assert turn.derived_at == NOW
    assert truth.fingerprint.catalog_hash == "h-live"
    assert truth.fingerprint.sample_count == 1084754
    assert truth.fingerprint.derived_at == NOW
    assert report["filled"] == ["q1"] and not report["errors"]


def test_fill_reads_each_engine_shape():
    truth = _truth(
        _question("gs", oracle=et.Oracle(engine="graph_search", body={"sampletype": "TIS"})),
        _question("sql", oracle=et.Oracle(engine="sql", statement="S1")),
        _question("list", oracle=et.Oracle(engine="cypher", statement="C2"), kind="set"),
        _question("m", oracle=et.Oracle(engine="measured", source="bench.json")))
    ex = FakeExecutors({json.dumps({"sampletype": "TIS"}, sort_keys=True): {"total": 107412},
                        "S1": [{"COUNT(*)": 195}], "C2": [{"t": "Lung"}, {"t": "Liver"}],
                        "bench.json": 22734})
    dt.fill_truth(truth, ex, now=NOW)
    values = {q.id: q.turns[0].expected for q in truth.questions}
    assert values["gs"].value == 107412
    assert values["sql"].required_numbers == [195]
    assert values["list"].value == ["Lung", "Liver"]
    assert values["list"].required_items == ["Lung", "Liver"]
    assert values["m"].value == 22734


def test_fill_records_a_second_oracle_disagreement():
    truth = _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1"),
                             second=et.Oracle(engine="sql", statement="S1"),
                             flags=["changed_by_merge"]))
    report = dt.fill_truth(truth, FakeExecutors({"C1": [{"n": 100}], "S1": [{"n": 101}]}),
                           now=NOW)
    turn = truth.questions[0].turns[0]
    assert turn.second_value == 101
    assert turn.disagreement and "100" in turn.disagreement and "101" in turn.disagreement
    assert [d["id"] for d in report["disagreements"]] == ["q1"]


def test_fill_agreement_leaves_no_disagreement():
    truth = _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1"),
                             second=et.Oracle(engine="sql", statement="S1")))
    report = dt.fill_truth(truth, FakeExecutors({"C1": [{"n": 100}], "S1": [{"n": 100.0}]}),
                           now=NOW)
    assert truth.questions[0].turns[0].disagreement is None
    assert not report["disagreements"]


def test_a_single_source_question_is_kept_without_complaint():
    truth = _truth(
        _question("graph_only", oracle=et.Oracle(engine="cypher", statement="C1"),
                  flags=["interpretive"], single_source=True),
        _question("needs_two", oracle=et.Oracle(engine="cypher", statement="C2"),
                  flags=["changed_by_merge"]))
    report = dt.fill_truth(truth, FakeExecutors({"C1": [{"n": 3}], "C2": [{"n": 4}]}), now=NOW)
    assert report["needs_second_oracle"] == ["needs_two"]
    assert truth.questions[0].turns[0].expected.value == 3


def test_fill_reports_an_oracle_failure_and_keeps_going():
    truth = _truth(_question("bad", oracle=et.Oracle(engine="cypher", statement="C1")),
                   _question("good", oracle=et.Oracle(engine="cypher", statement="C2")))
    report = dt.fill_truth(truth, FakeExecutors({"C2": [{"n": 4}]}, fail={"C1"}), now=NOW)
    assert [e["id"] for e in report["errors"]] == ["bad"]
    assert truth.questions[0].turns[0].expected.value is None
    assert truth.questions[1].turns[0].expected.value == 4


def test_fill_only_touches_the_named_ids_and_skips_excluded_questions():
    truth = _truth(_question("a", oracle=et.Oracle(engine="cypher", statement="C1")),
                   _question("b", oracle=et.Oracle(engine="cypher", statement="C2")),
                   _question("x", oracle=et.Oracle(engine="cypher", statement="C3"),
                             scorable=False))
    ex = FakeExecutors({"C1": [{"n": 1}], "C2": [{"n": 2}], "C3": [{"n": 3}]})
    dt.fill_truth(truth, ex, only={"b", "x"}, now=NOW)
    assert [c[1] for c in ex.calls] == ["C2"]


def test_a_count_oracle_must_return_one_value():
    truth = _truth(_question("q", oracle=et.Oracle(engine="cypher", statement="C1")))
    report = dt.fill_truth(truth, FakeExecutors({"C1": [{"a": 1, "b": 2}]}), now=NOW)
    assert report["errors"] and "one value" in report["errors"][0]["error"]


def test_measured_reads_the_latest_benchmark_cell(tmp_path):
    bench = {"format": 1, "runs": [
        {"run_id": "r1", "cells": [{"query": "attr_organ_exact", "arm": "G", "account": "demo",
                                    "samples": [{"status": 200, "total": 1}]}]},
        {"run_id": "r2", "cells": [
            {"query": "attr_organ_exact", "arm": "G", "account": "user",
             "samples": [{"status": 200, "total": 5}]},
            {"query": "attr_organ_exact", "arm": "G", "account": "demo",
             "samples": [{"status": 200, "total": 16841, "warmup": True},
                         {"status": 200, "total": 16841}]}]}]}
    (tmp_path / "ladder.json").write_text(json.dumps(bench), encoding="utf-8")
    oracle = et.Oracle(engine="measured", source="ladder.json", params={"query": "attr_organ_exact"})
    assert dt.measured_value(oracle, tmp_path) == 16841
    with pytest.raises(dt.OracleFailed):
        dt.measured_value(et.Oracle(engine="measured", source="ladder.json",
                                    params={"query": "nope"}), tmp_path)


# ── the command line ─────────────────────────────────────────────────────────

def _write(path: Path, truth: et.TruthFile) -> Path:
    path.write_text(truth.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_main_fills_the_file_in_place_mode_600_and_writes_nothing_else(tmp_path, capsys):
    path = _write(tmp_path / "a_x.json",
                  _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1"))))
    before = sorted(p.name for p in tmp_path.iterdir())
    rc = dt.main(["--truth", str(path)], executors=FakeExecutors({"C1": [{"n": 9}]}), now=NOW)
    assert rc == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = et.TruthFile.model_validate_json(path.read_text(encoding="utf-8"))
    assert again.questions[0].turns[0].expected.value == 9
    assert again.fingerprint.catalog_hash == "h-live"


def test_main_exits_non_zero_when_an_oracle_failed(tmp_path):
    path = _write(tmp_path / "a_x.json",
                  _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1"))))
    assert dt.main(["--truth", str(path)], executors=FakeExecutors({}, fail={"C1"}), now=NOW) == 1


def test_fingerprint_only_passes_on_the_same_graph_and_fails_on_a_changed_hash(tmp_path, capsys):
    stamped = et.Fingerprint(derived_at=NOW, **LIVE)
    _write(tmp_path / "a_x.json", _truth(_question("q1", oracle=None), fingerprint=stamped))
    _write(tmp_path / "b_x.json", _truth(_question("q2", oracle=None), fingerprint=stamped))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert dt.main(["--fingerprint-only", "--truth", str(tmp_path)],
                   executors=FakeExecutors({}), now=NOW) == 0
    changed = FakeExecutors({}, fingerprint={**LIVE, "catalog_hash": "h-new"})
    assert dt.main(["--fingerprint-only", "--truth", str(tmp_path)],
                   executors=changed, now=NOW) != 0
    assert "catalog_hash" in capsys.readouterr().out
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_fingerprint_only_ignores_a_moved_synced_at(tmp_path, capsys):
    """D16: GraphMeta.synced_at moves on every full, catalog or label-map sync, including the
    nightly reconcile and syncs that change nothing, so it is recorded but never compared."""
    stamped = et.Fingerprint(derived_at=NOW, **LIVE)
    _write(tmp_path / "a_x.json", _truth(_question("q1", oracle=None), fingerprint=stamped))
    resynced = FakeExecutors({}, fingerprint={**LIVE, "synced_at": "2026-09-17T20:32:19"})
    assert dt.main(["--fingerprint-only", "--truth", str(tmp_path)],
                   executors=resynced, now=NOW) == 0
    assert "differs" not in capsys.readouterr().out


def test_fingerprint_only_still_fails_on_a_changed_sample_count(tmp_path, capsys):
    stamped = et.Fingerprint(derived_at=NOW, **LIVE)
    _write(tmp_path / "a_x.json", _truth(_question("q1", oracle=None), fingerprint=stamped))
    grown = FakeExecutors({}, fingerprint={**LIVE, "sample_count": LIVE["sample_count"] + 1})
    assert dt.main(["--fingerprint-only", "--truth", str(tmp_path)],
                   executors=grown, now=NOW) != 0
    assert "sample_count" in capsys.readouterr().out


def test_a_refill_records_the_new_synced_at_without_calling_it_a_move():
    previous = et.Fingerprint(derived_at=NOW, **{**LIVE, "synced_at": "2026-09-17T19:26:41"})
    truth = _truth(_question("q1", oracle=et.Oracle(engine="cypher", statement="C1")),
                   fingerprint=previous)
    report = dt.fill_truth(truth, FakeExecutors({"C1": [{"n": 1}]}), now=NOW)
    assert report["fingerprint_changed"] is None
    assert truth.fingerprint.synced_at == LIVE["synced_at"]


def test_fingerprint_only_fails_on_an_unstamped_file(tmp_path):
    _write(tmp_path / "a_x.json", _truth(_question("q1", oracle=None)))
    assert dt.main(["--fingerprint-only", "--truth", str(tmp_path)],
                   executors=FakeExecutors({}), now=NOW) != 0


def test_summary_counts_per_group_source_and_family(tmp_path, capsys):
    t = _truth(
        _question("a", oracle=None, flags=["changed_by_merge", "broad_match"]),
        _question("b", oracle=None, flags=["interpretive"], single_source=True),
        _question("c", oracle=None, scorable=False))
    t.questions[2].exclusion = "no referent"
    merged = _question("d", oracle=None)
    merged.merged_into = "a"
    merged.source = "ladder"
    t.questions.append(merged)
    _write(tmp_path / "a_x.json", t)
    (tmp_path / "notes.json").write_text('{"not": "a truth file"}', encoding="utf-8")
    summary = dt.summarize([et.TruthFile.model_validate_json(
        (tmp_path / "a_x.json").read_text(encoding="utf-8"))])
    assert summary["groups"]["A"]["questions"] == 4
    assert summary["groups"]["A"]["by_source"] == {"corpus": 3, "ladder": 1}
    assert summary["groups"]["A"]["scorable"] == 2
    assert summary["changed_by_merge"] == ["a"] and summary["interpretive"] == ["b"]
    assert summary["single_source"] == ["b"] and summary["merged"] == {"d": "a"}
    assert summary["excluded"] == {"c": "no referent"} and summary["broad_match"] == ["a"]
    assert dt.main(["--summary", "--truth", str(tmp_path)], executors={}, now=NOW) == 0
    out = capsys.readouterr().out
    assert "broad_match" in out and "a_x" in out


def test_neither_module_imports_django_or_neo4j_at_module_scope():
    """The host lane and the scorer's `uv run --with pydantic` have neither."""
    for module in (dt, et):
        for line in Path(module.__file__).read_text(encoding="utf-8").splitlines():
            if line.startswith(("import ", "from ")):
                assert "django" not in line and "neo4j" not in line, line
