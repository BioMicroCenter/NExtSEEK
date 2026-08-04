"""Join the runtime rows, the LLM verdicts and the human grades into one table.

The last stage of a `--bayesian` run. `export.py` has already written the 14
runtime columns, one CSV per arm; the operator has graded the split report blind
and downloaded `grades.json`; Stage C has (optionally) produced `stage_c.json`.
This module puts the three side by side in `graded_rows.csv` and stops. The
HiBayes model itself is deliberately out of scope.

TWO GRADERS, THREE VALUES. The human emits binary. Stage C emits a 6-value
`FunctionalOutcome`, and upstream DD-08 already fixes the projection:
`{FullySatisfied, AppropriateClarification, AppropriateBoundary}` are successes
and `{PartiallySatisfied, NotSatisfied}` are failures. `NotAssessable` is
NEITHER -- it is excluded alongside the outages rather than counted as a loss,
because "this answer cannot be judged" and "this answer was wrong" are different
claims and folding them together silently invents evidence against one arm.
An ABSENT verdict is treated the same way, for the same reason: Stage C not
having run is not a finding about the answer.

So `llm_success` and `agree` are `bool | None`, and the three-valued cells reach
the CSV EMPTY. `None` is not zero, and unobserved is not absent -- the same
discipline `cost_summary` follows in reporting `unmeasured` rather than `$0.00`,
and that `export.py` follows in writing `unobserved.csv` rather than passing an
uncollected count off as a measured 0. A `False` in `agree` means the two graders
were both heard and disagreed. An empty cell means one of them said nothing.

AN UNGRADED ROW RAISES. It does not drop, and it does not emit with an empty
`human_success`. A quietly shorter table is how a partial grading pass gets read
as a complete one, and the resulting posterior would be computed over whichever
rows the operator happened to reach before stopping -- a selection effect
pointing in an unknown direction. `IncompleteGrading` names the rows, states the
total, and splits the count by arm, because "all 127 of cc is ungraded" and "10
scattered rows are ungraded" have different causes and different remedies.

WHAT CANNOT REACH HERE. `export._exclusion` drops provider outages, never-executed
arms and deadline aborts into `excluded.csv` and out of the per-arm CSVs, so an
arm the run never got an answer from is not a row this module sees, and cannot
trip the loud failure. The reverse direction is open by design: Task 4 builds its
report from the MANIFEST, which still contains those arms, so `grades.json` can
carry keys with no runtime row. Those are surplus, not a defect -- `main` counts
them and says so rather than raising.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from collections import Counter

from nessie_tests import export

# Imported, never restated. `export` owns the runtime column tuple, the arm
# vocabulary, the Stage B key separator and the None-to-empty-cell rule; a second
# copy of any of them here is a join that drifts silently out of agreement with
# the file it is joining. `_fmt` and `_write` are private to `export` in the same
# sense `_TERMINAL` is private to `collect` -- and `export` imports that.
from nessie_tests.export import HIBAYES_CSV_COLUMNS, _fmt, _write  # noqa: F401

# The DISCRIMINATOR, inverted. `export.ARM_IMAGE` maps arm -> image on the way
# out; this is the only way back, and deriving it means the two cannot disagree.
ARM_FROM_IMAGE: dict[str, str] = {image: arm for arm, image in export.ARM_IMAGE.items()}

# Upstream DD-08. These three outcomes are successes; NotAssessable is neither.
SUCCESS_OUTCOMES = frozenset({"FullySatisfied", "AppropriateClarification",
                              "AppropriateBoundary"})

# The human's vocabulary, and the whole of it. Anything else is a mismatch
# between the report that wrote `grades.json` and this module, not an unfinished
# grading pass, and is reported as such.
GRADES = {"pass": True, "fail": False}

GRADE_COLUMNS: tuple[str, ...] = ("human_success", "llm_success", "agree",
                                  "usefulness_score", "primary_issue")

GRADED_COLUMNS: tuple[str, ...] = HIBAYES_CSV_COLUMNS + GRADE_COLUMNS

GRADES_NAME = "grades.json"
STAGE_C_NAME = "stage_c.json"
GRADED_NAME = "graded_rows.csv"

# How many keys the error message names before it says how many it did not. Ten
# is enough to see a pattern; the COUNT is what tells you the scale, so it is
# printed first and the truncation announces itself.
_MAX_NAMED = 10


class IncompleteGrading(RuntimeError):
    """A row has no usable human grade. Refusing to emit is the point: a quietly
    shorter table is how a partial grading pass gets read as a complete one.

    `.ungraded` and `.unrecognised` carry EVERY offending key, not the ten the
    message names, so a caller can act on the full set.
    """

    def __init__(self, message: str, *, ungraded=(), unrecognised=()):
        super().__init__(message)
        self.ungraded = list(ungraded)
        self.unrecognised = list(unrecognised)


def llm_success(outcome: str | None) -> bool | None:
    """Stage C's 6-value outcome projected to binary, per upstream DD-08.

    `None` for `NotAssessable` and for an absent verdict: neither is a claim
    that the answer failed.
    """
    if outcome == "NotAssessable" or outcome is None:
        return None
    return outcome in SUCCESS_OUTCOMES


def agreement(human: bool | None, llm: bool | None) -> bool | None:
    """`None` unless BOTH graders spoke. `False` means they were both heard and
    disagreed, which is the set the whole paired design exists to produce."""
    if human is None or llm is None:
        return None
    return human == llm


def arm_of(row: dict) -> str:
    """The row's arm, from its `image`. Raises naming the row, because a bare
    `KeyError('nextseek_qeury')` out of a 254-row join identifies nothing."""
    image = row.get("image")
    try:
        return ARM_FROM_IMAGE[image]
    except KeyError:
        raise ValueError(
            f"row {row.get('query_id')!r} carries image {image!r}, which is not "
            f"one of {sorted(ARM_FROM_IMAGE)}") from None


def grade_key(row: dict) -> str:
    """The `grades.json` / `stage_c.json` key for a runtime row.

    Built by `export.stage_b_query_id`, never by a local f-string: the separator
    has exactly one definition and `split_stage_b_query_id` is its inverse.
    """
    return export.stage_b_query_id(row["query_id"], arm_of(row))


def unmatched_grades(*, rows, grades) -> list[str]:
    """Grade keys with no runtime row, sorted.

    NOT an error. Task 4's report is built from the manifest, which still holds
    the arms `export` excluded, so an operator can legitimately grade a provider
    outage. Worth counting, because a LARGE number here means the report and the
    export disagree about which rows exist.
    """
    return sorted(set(grades) - {grade_key(r) for r in rows})


def _named(keys) -> str:
    keys = sorted(keys)
    shown = ", ".join(repr(k) for k in keys[:_MAX_NAMED])
    if len(keys) > _MAX_NAMED:
        shown += f", ... and {len(keys) - _MAX_NAMED} more"
    return shown


def _by_arm(keys) -> str:
    counts = Counter(export.split_stage_b_query_id(k)[1] for k in keys)
    return ", ".join(f"{arm} {counts[arm]}" for arm in export.ARMS if counts[arm])


def merge(*, rows, grades, llm) -> list[dict]:
    """The runtime rows with both graders' verdicts attached.

    Raises `IncompleteGrading` if ANY row lacks a usable human grade. The partial
    result built before the raise is discarded deliberately: it is exactly the
    shorter table the exception exists to prevent, and returning it alongside a
    warning would put the decision in the hands of whoever ignores warnings.
    """
    out: list[dict] = []
    ungraded: list[str] = []
    unrecognised: list[tuple[str, object]] = []
    for row in rows:
        key = grade_key(row)
        recorded = (grades.get(key) or {}).get("grade")
        if recorded is None:
            ungraded.append(key)
            continue
        if recorded not in GRADES:
            unrecognised.append((key, recorded))
            continue
        human = GRADES[recorded]
        verdict = llm.get(key) or {}
        machine = llm_success(verdict.get("outcome"))
        out.append({**row,
                    "human_success": human,
                    "llm_success": machine,
                    "agree": agreement(human, machine),
                    "usefulness_score": verdict.get("usefulness_score"),
                    "primary_issue": verdict.get("primary_issue")})
    if ungraded or unrecognised:
        raise IncompleteGrading(_incomplete_message(ungraded, unrecognised),
                                ungraded=ungraded, unrecognised=unrecognised)
    return out


def _incomplete_message(ungraded, unrecognised) -> str:
    parts = []
    if ungraded:
        parts.append(
            f"{len(ungraded)} row(s) have no human grade ({_by_arm(ungraded)}): "
            f"{_named(ungraded)}. Finish the grading pass, re-download "
            f"{GRADES_NAME}, and rerun.")
    if unrecognised:
        values = sorted({repr(v) for _k, v in unrecognised})
        parts.append(
            f"{len(unrecognised)} row(s) carry a grade outside "
            f"{{{', '.join(sorted(GRADES))}}} -- {', '.join(values)}: "
            f"{_named(k for k, _v in unrecognised)}. That is a vocabulary "
            f"mismatch between the report and this merge, not an unfinished "
            f"grading pass; re-grading them will not help.")
    return "\n".join(parts)


# --- disk ---------------------------------------------------------------------

def read_runtime_rows(run_dir) -> list[dict]:
    """Both per-arm CSVs, concatenated, as STRINGS.

    Values are not re-typed on the way in. `export._fmt` already decided how a
    `None` cost, a `bool` and an int reach the file, and re-parsing them here
    would need a second copy of that decision -- the copy in which an empty
    `cost_usd` becomes `0.0`, or `"false"` becomes the truthy string it already
    is. Carrying the cells verbatim makes the runtime half of `graded_rows.csv`
    byte-identical to what `export` wrote, which is also what makes a diff
    between the two files mean something.
    """
    run_dir = pathlib.Path(run_dir)
    rows: list[dict] = []
    for arm in export.ARMS:
        path = run_dir / f"hibayes_eval_rows_{arm}.csv"
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} does not exist. Run `export.export` for this run first; "
                f"it writes one CSV per arm even when an arm has no rows.")
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = tuple(reader.fieldnames or ())
            if header != HIBAYES_CSV_COLUMNS:
                raise ValueError(
                    f"{path} has header {header}, not {HIBAYES_CSV_COLUMNS}. "
                    f"The export has drifted; joining on it would produce a "
                    f"graded table of the wrong shape.")
            rows.extend(reader)
    return rows


def read_map(path, *, required: bool) -> dict:
    """A `{key: record}` JSON file, or `{}` when an optional one is absent.

    A file of the WRONG shape raises rather than joining to nothing: a list where
    a mapping was expected yields zero matches, which on the page is
    indistinguishable from Stage C never having run.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        if required:
            raise FileNotFoundError(
                f"{path} does not exist. Grade the report, download {GRADES_NAME} "
                f"into the run directory, and rerun.")
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path.name} holds a {type(loaded).__name__}, not an object keyed by "
            f"'<variant_id>{export.STAGE_B_ID_SEP}<arm>'. Joining on it would "
            f"match nothing and read as a stage that never ran.")
    return loaded


def build(run_dir, *, grades_path=None, llm_path=None, out_path=None) -> dict:
    """Merge one run directory and write `graded_rows.csv`. Returns a summary."""
    run_dir = pathlib.Path(run_dir)
    rows = read_runtime_rows(run_dir)
    grades = read_map(grades_path or run_dir / GRADES_NAME, required=True)
    llm = read_map(llm_path or run_dir / STAGE_C_NAME, required=False)
    graded = merge(rows=rows, grades=grades, llm=llm)
    out_path = pathlib.Path(out_path or run_dir / GRADED_NAME)
    _write(out_path, GRADED_COLUMNS, graded)
    agree = Counter(r["agree"] for r in graded)
    return {
        "rows": len(graded),
        "out": str(out_path),
        # Three buckets, never two. `undetermined` is where NotAssessable and the
        # un-run grader land, and rolling it into `disagree` would manufacture a
        # disagreement out of a silence.
        "agree": agree[True],
        "disagree": agree[False],
        "undetermined": agree[None],
        "graded_with_no_runtime_row": len(unmatched_grades(rows=rows, grades=grades)),
        "unmatched_grades": unmatched_grades(rows=rows, grades=grades),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Join the runtime rows, the LLM verdicts and the human "
                    "grades into graded_rows.csv.")
    ap.add_argument("--run", required=True,
                    help="run directory holding hibayes_eval_rows_{ns,cc}.csv")
    ap.add_argument("--grades", default=None, help=f"default <run>/{GRADES_NAME}")
    ap.add_argument("--stage-c", default=None,
                    help=f"default <run>/{STAGE_C_NAME}; optional, so the merge "
                         f"works before the LLM grader has run")
    ap.add_argument("--out", default=None, help=f"default <run>/{GRADED_NAME}")
    args = ap.parse_args(argv)
    summary = build(args.run, grades_path=args.grades, llm_path=args.stage_c,
                    out_path=args.out)
    print(f"{summary['rows']} graded row(s) -> {summary['out']}")
    print(f"  agree {summary['agree']}  disagree {summary['disagree']}  "
          f"undetermined {summary['undetermined']}")
    if summary["unmatched_grades"]:
        # Expected and benign at small counts -- the report renders the arms the
        # export excluded. A LARGE count means the two disagree about the run.
        print(f"  {summary['graded_with_no_runtime_row']} grade(s) have no runtime "
              f"row (excluded arms are graded but not scored): "
              f"{_named(summary['unmatched_grades'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
