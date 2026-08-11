"""The graded table: runtime rows joined to both graders.

Two things this file exists to hold still.

FIRST, the three-valued logic. A human grade is binary, Stage C's is a 6-value
`FunctionalOutcome`, and `NotAssessable` is neither a success nor a failure --
it is excluded alongside the outages rather than counted as a loss. So
`llm_success` and `agree` are `bool | None`, and every place a `None` could
decay into a `False` (or into a `0`, or into a cell that reads as a real value)
is pinned below.

SECOND, the loud failure. A quietly shorter table is how a partial grading pass
gets read as a complete one, so an ungraded row raises rather than dropping. The
tests check not just THAT it raises but that the message carries the scale: 254
ungraded arms must not look like ten.
"""
from __future__ import annotations

import csv
import json

import pytest

from nessie_tests import export
from nessie_tests.manifest import NessieManifestEntry
from nessie_tests.output_skill_bayesian import merge_grades

SUCCESS_OUTCOMES = {"FullySatisfied", "AppropriateClarification", "AppropriateBoundary"}


# --- fixtures ----------------------------------------------------------------
# Rows come from `export.runtime_row` and are written by `export._write`, so the
# round trip under test is the REAL one: a hand-built dict would not carry the
# `None` cost or the `true`/`false` spelling that the join has to preserve.

def _row(vid="a.one", arm="ns", *, cost=None, status="passed", ops=3, artifacts=0):
    entry = NessieManifestEntry(id=vid, family="f", tier="full", status=status,
                                cost=cost, elapsed_s=1.5)
    return export.runtime_row(entry, arm=arm, family="f", subtype="Search-Basic",
                              artifact_count=artifacts, engine_ops=ops)


def _write_run(tmp_path, *, ns=(), cc=(), grades=None, llm=None):
    export._write(tmp_path / "hibayes_eval_rows_ns.csv",
                  export.HIBAYES_CSV_COLUMNS, list(ns))
    export._write(tmp_path / "hibayes_eval_rows_cc.csv",
                  export.HIBAYES_CSV_COLUMNS, list(cc))
    if grades is not None:
        (tmp_path / "grades.json").write_text(json.dumps(grades), encoding="utf-8")
    if llm is not None:
        (tmp_path / "stage_c.json").write_text(json.dumps(llm), encoding="utf-8")
    return tmp_path


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- the projection ----------------------------------------------------------

def test_llm_outcome_projects_to_binary_per_upstream_dd08():
    for o in SUCCESS_OUTCOMES:
        assert merge_grades.llm_success(o) is True
    for o in ("PartiallySatisfied", "NotSatisfied"):
        assert merge_grades.llm_success(o) is False


def test_not_assessable_is_neither_success_nor_failure():
    """It is excluded alongside the outages rather than counted as a loss."""
    assert merge_grades.llm_success("NotAssessable") is None


def test_an_absent_verdict_is_also_neither():
    """Stage C not having run is not evidence that the answer was bad."""
    assert merge_grades.llm_success(None) is None


def test_agreement_is_computed_only_where_both_graders_spoke():
    assert merge_grades.agreement(True, True) is True
    assert merge_grades.agreement(True, False) is False
    assert merge_grades.agreement(True, None) is None
    assert merge_grades.agreement(None, True) is None


# --- the loud failure --------------------------------------------------------

def test_a_missing_human_grade_fails_loudly():
    """A quietly shorter table is how a partial grading pass gets read as
    complete. It must be impossible to produce one by accident."""
    with pytest.raises(merge_grades.IncompleteGrading) as e:
        merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_query"}],
                           grades={}, llm={})
    assert "a.one" in str(e.value)


def test_the_error_states_the_scale_rather_than_showing_ten_of_two_hundred():
    """254 ungraded arms must not read as 10. The count and the per-arm split
    both appear, and the truncated list says it is truncated."""
    rows = ([{"query_id": f"v.{i}", "image": "nextseek_query"} for i in range(127)]
            + [{"query_id": f"v.{i}", "image": "container_cc"} for i in range(127)])
    with pytest.raises(merge_grades.IncompleteGrading) as e:
        merge_grades.merge(rows=rows, grades={}, llm={})
    msg = str(e.value)
    assert "254" in msg
    assert "ns 127" in msg and "cc 127" in msg
    assert "244 more" in msg


def test_the_exception_carries_every_ungraded_key_not_just_the_named_ten():
    rows = [{"query_id": f"v.{i}", "image": "container_cc"} for i in range(30)]
    with pytest.raises(merge_grades.IncompleteGrading) as e:
        merge_grades.merge(rows=rows, grades={}, llm={})
    assert len(e.value.ungraded) == 30


def test_a_grade_outside_the_vocabulary_is_named_as_a_mismatch_not_an_unfinished_pass():
    """`Pass` on every row means the report and this merge disagree about the
    vocabulary -- a code defect. Reporting it as 'finish the grading pass' would
    send the operator to re-grade 254 rows that are already graded."""
    with pytest.raises(merge_grades.IncompleteGrading) as e:
        merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_query"}],
                           grades={"a.one::ns": {"grade": "Pass"}}, llm={})
    msg = str(e.value)
    assert "vocabulary" in msg.lower()
    assert "Pass" in msg
    assert e.value.unrecognised == [("a.one::ns", "Pass")]


def test_an_unknown_image_names_the_row_it_came_from():
    """A bare KeyError('nextseek_qeury') out of a 254-row join identifies nothing."""
    with pytest.raises(ValueError) as e:
        merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_qeury"}],
                           grades={}, llm={})
    assert "a.one" in str(e.value) and "nextseek_qeury" in str(e.value)


# --- the join ----------------------------------------------------------------

def test_merged_row_carries_both_grades_and_the_agreement_flag():
    out = merge_grades.merge(
        rows=[{"query_id": "a.one", "image": "nextseek_query"}],
        grades={"a.one::ns": {"grade": "pass"}},
        llm={"a.one::ns": {"outcome": "NotSatisfied", "usefulness_score": 1,
                           "primary_issue": "InsufficientEvidence"}})
    assert out[0]["human_success"] is True
    assert out[0]["llm_success"] is False
    assert out[0]["agree"] is False
    assert out[0]["usefulness_score"] == 1
    assert out[0]["primary_issue"] == "InsufficientEvidence"


def test_the_join_key_is_export_s_and_not_a_second_separator(monkeypatch):
    """`export.stage_b_query_id` owns the separator, and moving it must move the
    join. A local `f'{id}::{arm}'` would produce the same string TODAY and would
    silently stop matching the day the separator changes -- which is why this
    asserts the coupling rather than the output."""
    monkeypatch.setattr(export, "STAGE_B_ID_SEP", "||")
    out = merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_query"}],
                             grades={"a.one||ns": {"grade": "fail"}}, llm={})
    assert out[0]["human_success"] is False


def test_every_arm_the_export_writes_can_be_read_back_off_its_image():
    """`ARM_FROM_IMAGE` is `export.ARM_IMAGE` inverted, not a second copy: a
    hardcoded pair would go stale the moment the export grew a third arm."""
    assert set(merge_grades.ARM_FROM_IMAGE.values()) == set(export.ARMS)
    assert merge_grades.ARM_FROM_IMAGE == {v: k for k, v in export.ARM_IMAGE.items()}


def test_a_variant_id_containing_the_separator_still_joins():
    """`split_stage_b_query_id` rsplits for this reason; the key builder must
    agree with it."""
    vid = "a::odd"
    out = merge_grades.merge(rows=[{"query_id": vid, "image": "container_cc"}],
                             grades={f"{vid}::cc": {"grade": "pass"}}, llm={})
    assert out[0]["human_success"] is True


def test_the_runtime_columns_are_carried_through_untouched():
    row = _row(cost=None)
    out = merge_grades.merge(rows=[row], grades={"a.one::ns": {"grade": "pass"}}, llm={})
    for column in export.HIBAYES_CSV_COLUMNS:
        assert out[0][column] == row[column]


def test_a_grade_for_a_row_that_was_never_exported_is_not_an_error():
    """The report is built from the MANIFEST, which includes the arms `export`
    excluded, so a human can legitimately grade a row that never reached a CSV.
    Those grades are surplus, not a defect, and must not raise."""
    out = merge_grades.merge(
        rows=[{"query_id": "a.one", "image": "nextseek_query"}],
        grades={"a.one::ns": {"grade": "pass"}, "b.two::cc": {"grade": "fail"}},
        llm={})
    assert len(out) == 1
    assert merge_grades.unmatched_grades(
        rows=[{"query_id": "a.one", "image": "nextseek_query"}],
        grades={"a.one::ns": {"grade": "pass"}, "b.two::cc": {"grade": "fail"}},
    ) == ["b.two::cc"]


def test_an_excluded_arm_cannot_trigger_the_loud_failure():
    """`export` writes outages and deadline aborts to `excluded.csv`, never to the
    per-arm CSVs, so an ungraded excluded arm never reaches `merge` at all. If it
    did, every run with one outage would refuse to produce a table."""
    entry = NessieManifestEntry(id="a.one", family="f", tier="full",
                                status="error", cost=None, elapsed_s=1.0, outage=True)
    assert export._exclusion(entry, []) is not None


# --- None is not zero --------------------------------------------------------

def test_a_missing_verdict_leaves_the_covariates_absent_not_zero():
    out = merge_grades.merge(rows=[{"query_id": "a.one", "image": "nextseek_query"}],
                             grades={"a.one::ns": {"grade": "pass"}}, llm={})
    assert out[0]["llm_success"] is None
    assert out[0]["agree"] is None
    assert out[0]["usefulness_score"] is None
    assert out[0]["primary_issue"] is None


def test_a_real_usefulness_score_of_zero_survives_as_zero(tmp_path):
    """0 is the bottom of Stage C's 0-4 scale, and an absent score is not it.
    Both reach the CSV, and they must not read the same."""
    _write_run(tmp_path, ns=[_row("a.one", "ns"), _row("b.two", "ns")],
               grades={"a.one::ns": {"grade": "pass"}, "b.two::ns": {"grade": "pass"}},
               llm={"a.one::ns": {"outcome": "NotSatisfied", "usefulness_score": 0,
                                  "primary_issue": "WrongAnswer"}})
    merge_grades.main(["--run", str(tmp_path)])
    rows = {r["query_id"]: r for r in _read(tmp_path / "graded_rows.csv")}
    assert rows["a.one"]["usefulness_score"] == "0"
    assert rows["b.two"]["usefulness_score"] == ""


def test_an_unobserved_cost_stays_empty_through_the_merge(tmp_path):
    """`cost_summary` reports `unmeasured` rather than `$0.00`; the NS arm emits
    no cost at all. An empty cell must not become a 0 on the way through."""
    _write_run(tmp_path, ns=[_row("a.one", "ns", cost=None)],
               cc=[_row("a.one", "cc", cost=0.12)],
               grades={"a.one::ns": {"grade": "pass"}, "a.one::cc": {"grade": "fail"}})
    merge_grades.main(["--run", str(tmp_path)])
    rows = {r["image"]: r for r in _read(tmp_path / "graded_rows.csv")}
    assert rows["nextseek_query"]["cost_usd"] == ""
    assert rows["container_cc"]["cost_usd"] == "0.12"


def test_a_primary_issue_of_the_literal_string_None_is_not_an_absent_one(tmp_path):
    """Stage C's issue enum carries a member meaning 'no issue'. Serialised, that
    is the four characters `None`; an ABSENT verdict is an empty cell. The two
    say opposite things -- one grader looked and found nothing wrong, the other
    never spoke -- and must not render alike."""
    _write_run(tmp_path, ns=[_row("a.one", "ns"), _row("b.two", "ns")],
               grades={"a.one::ns": {"grade": "pass"}, "b.two::ns": {"grade": "pass"}},
               llm={"a.one::ns": {"outcome": "FullySatisfied", "primary_issue": "None"}})
    merge_grades.main(["--run", str(tmp_path)])
    rows = {r["query_id"]: r for r in _read(tmp_path / "graded_rows.csv")}
    assert rows["a.one"]["primary_issue"] == "None"
    assert rows["b.two"]["primary_issue"] == ""


def test_a_not_assessable_verdict_still_carries_its_covariates(tmp_path):
    """`NotAssessable` withholds the SUCCESS judgement, not the score the grader
    reported alongside it. Blanking the covariate would discard a measurement to
    make a column look tidy."""
    _write_run(tmp_path, ns=[_row("a.one", "ns")],
               grades={"a.one::ns": {"grade": "pass"}},
               llm={"a.one::ns": {"outcome": "NotAssessable", "usefulness_score": 2}})
    merge_grades.main(["--run", str(tmp_path)])
    row = _read(tmp_path / "graded_rows.csv")[0]
    assert row["llm_success"] == "" and row["usefulness_score"] == "2"


def test_the_three_valued_columns_reach_the_csv_as_empty_not_false(tmp_path):
    _write_run(tmp_path, ns=[_row("a.one", "ns")],
               grades={"a.one::ns": {"grade": "pass"}},
               llm={"a.one::ns": {"outcome": "NotAssessable"}})
    merge_grades.main(["--run", str(tmp_path)])
    row = _read(tmp_path / "graded_rows.csv")[0]
    assert row["human_success"] == "true"
    assert row["llm_success"] == ""
    assert row["agree"] == ""


# --- main() ------------------------------------------------------------------

def test_main_writes_the_fourteen_runtime_columns_plus_the_five_grade_columns(tmp_path):
    _write_run(tmp_path, ns=[_row("a.one", "ns")], cc=[_row("a.one", "cc")],
               grades={"a.one::ns": {"grade": "pass"}, "a.one::cc": {"grade": "fail"}},
               llm={"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4,
                                  "primary_issue": "None"}})
    assert merge_grades.main(["--run", str(tmp_path)]) == 0
    rows = _read(tmp_path / "graded_rows.csv")
    assert list(rows[0]) == list(export.HIBAYES_CSV_COLUMNS) + list(merge_grades.GRADE_COLUMNS)
    assert len(rows) == 2
    ns = next(r for r in rows if r["image"] == "nextseek_query")
    assert ns["llm_success"] == "true" and ns["agree"] == "true"


def test_main_runs_before_stage_c_has(tmp_path):
    """Human grading comes first in the workflow, so the merge must work with no
    `stage_c.json` at all rather than requiring an empty one."""
    _write_run(tmp_path, ns=[_row("a.one", "ns")], grades={"a.one::ns": {"grade": "pass"}})
    assert merge_grades.main(["--run", str(tmp_path)]) == 0
    row = _read(tmp_path / "graded_rows.csv")[0]
    assert row["human_success"] == "true" and row["llm_success"] == ""


def test_main_refuses_a_run_whose_export_never_ran(tmp_path):
    (tmp_path / "grades.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as e:
        merge_grades.main(["--run", str(tmp_path)])
    assert "hibayes_eval_rows_ns.csv" in str(e.value)


def test_main_refuses_a_run_with_no_grades_file(tmp_path):
    _write_run(tmp_path, ns=[_row("a.one", "ns")])
    with pytest.raises(FileNotFoundError) as e:
        merge_grades.main(["--run", str(tmp_path)])
    assert "grades.json" in str(e.value)


def test_a_stage_c_file_of_the_wrong_shape_raises_rather_than_joining_to_nothing(tmp_path):
    """A list where a mapping was expected joins to zero rows, which is
    indistinguishable from Stage C never having run -- a quiet wrong answer."""
    _write_run(tmp_path, ns=[_row("a.one", "ns")], grades={"a.one::ns": {"grade": "pass"}})
    (tmp_path / "stage_c.json").write_text(json.dumps([{"query_id": "a.one::ns"}]),
                                           encoding="utf-8")
    with pytest.raises(ValueError) as e:
        merge_grades.main(["--run", str(tmp_path)])
    assert "stage_c.json" in str(e.value)


def test_a_drifted_export_header_raises_rather_than_joining_on_the_wrong_shape(tmp_path):
    _write_run(tmp_path, ns=[_row("a.one", "ns")], grades={"a.one::ns": {"grade": "pass"}})
    path = tmp_path / "hibayes_eval_rows_ns.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("query_id", "variant_id", 1),
                    encoding="utf-8")
    with pytest.raises(ValueError) as e:
        merge_grades.main(["--run", str(tmp_path)])
    assert "header" in str(e.value).lower()


def test_the_summary_counts_agreement_and_surplus_grades(tmp_path):
    _write_run(tmp_path, ns=[_row("a.one", "ns")], cc=[_row("a.one", "cc")],
               grades={"a.one::ns": {"grade": "pass"}, "a.one::cc": {"grade": "fail"},
                       "z.gone::cc": {"grade": "pass"}},
               llm={"a.one::ns": {"outcome": "FullySatisfied"},
                    "a.one::cc": {"outcome": "NotAssessable"}})
    summary = merge_grades.build(tmp_path)
    assert summary["rows"] == 2
    assert summary["agree"] == 1
    assert summary["disagree"] == 0
    assert summary["undetermined"] == 1
    assert summary["unmatched_grades"] == ["z.gone::cc"]


def test_the_skill_script_is_a_thin_wrapper_over_the_package():
    """The script exists so `SKILL.md` has a path to name. It must not carry a
    second copy of the logic: an entry point that cannot be imported cannot be
    tested, which is the whole reason the package exists."""
    import pathlib
    script = (pathlib.Path(__file__).resolve().parents[1]
              / "output-skill-bayesian" / "scripts" / "merge_grades.py")
    body = script.read_text(encoding="utf-8")
    assert "from nessie_tests.output_skill_bayesian import merge_grades" in body
    assert "SUCCESS_OUTCOMES" not in body
