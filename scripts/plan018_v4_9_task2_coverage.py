#!/usr/bin/env python3
"""Source-bound coverage gate for Plan 018 V4-9 Task 2.

The set is the complete intersection of the accepted owned-surface manifest's
coverage-bearing V4-2/V4-3 entries and the Task 2 nouns: paired producer,
schema/export/artifact facts, attempts, DD-44 judgment, disposition,
conservation, and Stage C.  Later fit, store, router, monitoring, and spend
modules are intentionally owned by Tasks 3 and 4; declared-absent Task-7b
artifact modules cannot be covered before they exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CRITICAL_MODULES = (
    "nessie_tests/bayes_manifest.py",
    "nessie_tests/bayesian.py",
    "nessie_tests/export.py",
    "nextseek_api/eval/human_annotations.py",
    "nextseek_api/eval/conservation.py",
    "nextseek_api/eval/disposition.py",
    "nextseek_api/eval/judge.py",
    "nextseek_api/eval/judge_models.py",
    "nextseek_api/eval/attempt_store.py",
    "nextseek_api/eval/stage_c_runner.py",
)

FLOOR_PCT = 95.0


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round((covered / total) * 100, 1)


def evaluate_coverage(coverage: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return independent per-module and aggregate floor results.

    The report consumes coverage.py JSON rather than terminal text, so a
    truncated terminal summary cannot hide an unvisited file or branch.
    """
    errors: list[str] = []
    if coverage.get("meta", {}).get("branch_coverage") is not True:
        return ["coverage report was not collected with branch coverage"], {"modules": {}, "aggregate": {}}

    files = coverage.get("files", {})
    modules: dict[str, dict[str, float | int]] = {}
    total_statements = total_covered_lines = total_branches = total_covered_branches = 0
    for path in CRITICAL_MODULES:
        summary = files.get(path, {}).get("summary")
        if not isinstance(summary, dict):
            errors.append(f"missing coverage for {path}")
            continue
        statements = int(summary.get("num_statements", 0))
        covered_lines = int(summary.get("covered_lines", 0))
        branches = int(summary.get("num_branches", 0))
        covered_branches = int(summary.get("covered_branches", 0))
        line_pct = _percent(covered_lines, statements)
        branch_pct = _percent(covered_branches, branches)
        modules[path] = {
            "statements": statements,
            "covered_lines": covered_lines,
            "statement_pct": line_pct,
            "branches": branches,
            "covered_branches": covered_branches,
            "branch_pct": branch_pct,
        }
        total_statements += statements
        total_covered_lines += covered_lines
        total_branches += branches
        total_covered_branches += covered_branches
        if line_pct < FLOOR_PCT:
            errors.append(f"{path} statement coverage {line_pct:.1f}% is below {FLOOR_PCT:.1f}%")
        if branch_pct < FLOOR_PCT:
            errors.append(f"{path} branch coverage {branch_pct:.1f}% is below {FLOOR_PCT:.1f}%")

    aggregate = {
        "statements": total_statements,
        "covered_lines": total_covered_lines,
        "statement_pct": _percent(total_covered_lines, total_statements),
        "branches": total_branches,
        "covered_branches": total_covered_branches,
        "branch_pct": _percent(total_covered_branches, total_branches),
    }
    if len(modules) == len(CRITICAL_MODULES):
        if aggregate["statement_pct"] < FLOOR_PCT:
            errors.append(f"aggregate statement coverage {aggregate['statement_pct']:.1f}% is below {FLOOR_PCT:.1f}%")
        if aggregate["branch_pct"] < FLOOR_PCT:
            errors.append(f"aggregate branch coverage {aggregate['branch_pct']:.1f}% is below {FLOOR_PCT:.1f}%")
    return errors, {"modules": modules, "aggregate": aggregate}


def main() -> int:
    parser = argparse.ArgumentParser(description="verify V4-9 Task 2 branch coverage")
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    errors, report = evaluate_coverage(json.loads(args.coverage_json.read_text(encoding="utf-8")))
    result = {
        "schema": "plan018-v4-9-task2-coverage/v1",
        "critical_modules": list(CRITICAL_MODULES),
        "threshold_statement_pct": FLOOR_PCT,
        "threshold_branch_pct": FLOOR_PCT,
        "gate": "PASS" if not errors else "FAIL",
        "errors": errors,
        **report,
    }
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Task 2 coverage {'PASS' if not errors else 'FAIL'} ({len(report['modules'])}/{len(CRITICAL_MODULES)} modules)")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
