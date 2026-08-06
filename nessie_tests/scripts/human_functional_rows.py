"""Human grades -> HiBayes *functional usefulness* input, across one or more runs.

WHY THE FUNCTIONAL AXIS AND NOT THE RUNTIME ONE. The runtime axis
(`RuntimeEvalRow`) is `extra="forbid"` AND enforces
`runtime_success == answer_provided and not is_error and not timed_out`. A human
verdict cannot ride there without fabricating the three runtime flags to make the
validator agree. The functional axis (`FunctionalUsefulnessRow`) requires exactly
`query_id`, `task_family`, `functional_success` and is `extra="allow"` -- and
`functional_success` is precisely "a judge said this was good". The human is the
judge, so no LLM grader has to run for these rows to exist.

ONE FILE PER ARM, because `aggregate_by_task_family` groups on `task_family`
alone. A combined file pools NS and CC into one posterior per family and destroys
the engine comparison the router is being trained to make.

MULTIPLE RUNS. Pass `--run` once per run directory. A study assembled from a base
run plus a delta run is merged HERE, at the CSV layer, rather than by resuming
onto a paid manifest. Duplicate `<variant_id>::<arm>` keys across runs are an
error, not a silent last-one-wins: two runs disagreeing about the same arm is a
fact somebody has to look at.

USE
    python nessie_tests/scripts/human_functional_rows.py \
        --run ./nessie_bayes_full --run ./nessie_bayes_delta \
        --out ./nessie_bayes_study
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ARM_FROM_IMAGE = {"nextseek_query": "ns", "container_cc": "cc"}

# Required three first (the contract), then covariates.
COLUMNS = [
    "query_id", "task_family", "functional_success",
    "grade_key", "arm", "image", "task_subtype", "route", "source_run",
    "harness_status", "harness_status_agrees_with_human",
    "runtime_success", "runtime_success_agrees_with_human",
    "answer_provided", "is_error", "timed_out", "failure_mode", "n_failed_criteria",
    "latency_seconds", "cost_usd", "tool_calls_total", "artifact_count", "is_opus",
    "operator_note",
]


def as_bool(cell) -> bool:
    return str(cell).strip().lower() == "true"


def rows_for(run: pathlib.Path) -> list[dict]:
    graded = list(csv.DictReader((run / "graded_rows.csv").open()))
    grades = json.loads((run / "grades.json").read_text())
    manifest = json.loads((run / "bayes_manifest.json").read_text())

    entries = {f"{p['id']}::{arm}": (p.get(arm) or {})
               for p in manifest["pairs"] for arm in ("ns", "cc")}

    out = []
    for r in graded:
        arm = ARM_FROM_IMAGE[r["image"]]
        key = f"{r['query_id']}::{arm}"
        grade, entry = grades.get(key), entries.get(key)
        if grade is None:
            sys.exit(f"{run.name}: no human grade for {key}")
        if entry is None:
            sys.exit(f"{run.name}: no manifest entry for {key}")

        human = as_bool(r["human_success"])
        harness = as_bool(r["runtime_success"])
        status = entry.get("status", "")
        out.append({
            "query_id": r["query_id"], "task_family": r["task_family"],
            "functional_success": "true" if human else "false",
            "grade_key": key, "arm": arm, "image": r["image"],
            "task_subtype": r["task_subtype"], "route": entry.get("route", ""),
            "source_run": run.name,
            "harness_status": status,
            "harness_status_agrees_with_human": "true" if (status == "passed") == human else "false",
            "runtime_success": r["runtime_success"],
            "runtime_success_agrees_with_human": "true" if harness == human else "false",
            "answer_provided": r["answer_provided"], "is_error": r["is_error"],
            "timed_out": r["timed_out"], "failure_mode": r["failure_mode"],
            "n_failed_criteria": len(entry.get("failed_criteria") or []),
            "latency_seconds": r["latency_seconds"], "cost_usd": r["cost_usd"],
            "tool_calls_total": r["tool_calls_total"], "artifact_count": r["artifact_count"],
            "is_opus": r["is_opus"], "operator_note": (grade.get("note") or "").strip(),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=pathlib.Path, action="append", required=True,
                    dest="runs", help="a run directory; repeat to merge several")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)

    merged: dict[str, dict] = {}
    for run in a.runs:
        for row in rows_for(run):
            prior = merged.get(row["grade_key"])
            if prior is not None:
                sys.exit(
                    f"{row['grade_key']} appears in both {prior['source_run']} and "
                    f"{row['source_run']}. Two runs holding the same arm is not a "
                    f"merge, it is a disagreement -- decide which run is the record "
                    f"before combining them.")
            merged[row["grade_key"]] = row

    a.out.mkdir(parents=True, exist_ok=True)
    for arm in ("ns", "cc"):
        rows = sorted((r for r in merged.values() if r["arm"] == arm),
                      key=lambda r: (r["task_family"], r["query_id"]))
        path = a.out / f"hibayes_functional_usefulness_human_{arm}.csv"
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        passes = sum(r["functional_success"] == "true" for r in rows)
        fams = len({r["task_family"] for r in rows})
        disagree = sum(r["harness_status_agrees_with_human"] == "false" for r in rows)
        print(f"{path.name}: {len(rows)} rows, {fams} families, "
              f"human pass {passes}/{len(rows)}, harness disagrees {disagree}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
