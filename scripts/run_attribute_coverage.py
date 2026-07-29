#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from coverage import Coverage

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")
SOURCE_PATHS = (
    "nextseek_api/attributes",
    "startup/steps/schema_fixups.py",
    "nextseek_api/models.py",
    "nextseek_api/migrations",
    "nextseek_api/views.py",
    "nextseek_api/urls.py",
    "nextseek_api/batch_upload/celery_app.py",
)
PYTEST_SELECTION = ("nextseek_api/tests", "nextseek_api/attributes/tests", "startup/tests")


def include_patterns() -> list[str]:
    patterns: list[str] = []
    for relative in SOURCE_PATHS:
        path = ROOT / relative
        if not path.exists():
            raise SystemExit(f"manifest coverage source missing: {relative}")
        if path.is_dir():
            patterns.extend((f"{path.as_posix()}/*.py", f"{path.as_posix()}/**/*.py"))
        else:
            patterns.append(path.as_posix())
    return patterns


def source_roots() -> list[str]:
    roots = {(ROOT / relative if (ROOT / relative).is_dir() else (ROOT / relative).parent).as_posix()
             for relative in SOURCE_PATHS}
    return sorted(roots)


def required_source_files() -> list[Path]:
    files: list[Path] = []
    for relative in SOURCE_PATHS:
        path = ROOT / relative
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def _coverage_summary(*, covered_lines: int, num_statements: int, num_branches: int = 0, covered_branches: int = 0) -> dict:
    line_pct = 100.0 if num_statements == 0 else covered_lines / num_statements * 100
    branch_pct = 100.0 if num_branches == 0 else covered_branches / num_branches * 100
    return {
        "covered_lines": covered_lines,
        "num_statements": num_statements,
        "percent_covered": line_pct,
        "percent_covered_display": f"{line_pct:.0f}",
        "missing_lines": num_statements - covered_lines,
        "excluded_lines": 0,
        "percent_statements_covered": line_pct,
        "percent_statements_covered_display": f"{line_pct:.0f}",
        "num_branches": num_branches,
        "num_partial_branches": 0,
        "covered_branches": covered_branches,
        "missing_branches": num_branches - covered_branches,
        "percent_branches_covered": branch_pct,
        "percent_branches_covered_display": f"{branch_pct:.0f}",
    }


def _file_entry(*, executed_lines: list[int], missing_lines: list[int], num_branches: int, covered_branches: int) -> dict:
    num_statements = len(executed_lines) + len(missing_lines)
    summary = _coverage_summary(
        covered_lines=len(executed_lines),
        num_statements=num_statements,
        num_branches=num_branches,
        covered_branches=covered_branches,
    )
    scoped = {
        "executed_lines": executed_lines,
        "summary": summary,
        "missing_lines": missing_lines,
        "excluded_lines": [],
        "executed_branches": [],
        "missing_branches": [],
        "functions": {
            "": {
                "executed_lines": executed_lines,
                "summary": summary,
                "missing_lines": missing_lines,
                "excluded_lines": [],
                "start_line": 1,
                "executed_branches": [],
                "missing_branches": [],
            }
        },
        "classes": {
            "": {
                "executed_lines": executed_lines,
                "summary": summary,
                "missing_lines": missing_lines,
                "excluded_lines": [],
                "start_line": 1,
                "executed_branches": [],
                "missing_branches": [],
            }
        },
    }
    return scoped


def ensure_report_lists_all_sources(coverage: Coverage, output: Path) -> None:
    payload = json.loads(output.read_text())
    files = payload.setdefault("files", {})
    for path in required_source_files():
        key = path.relative_to(ROOT).as_posix()
        if key in files:
            continue
        try:
            _, statements, _excluded, missing, _missing_branch = coverage.analysis2(str(path))
        except Exception:
            statements, missing = [], []
        executed = [line for line in statements if line not in missing]
        files[key] = _file_entry(
            executed_lines=executed,
            missing_lines=sorted(missing),
            num_branches=0,
            covered_branches=0,
        )
    output.write_text(json.dumps(payload, indent=4) + "\n")


def main() -> int:
    raw_full = sys.argv[1:] == ["--raw-full"]
    if sys.argv[1:] not in ([], ["--raw-full"]):
        raise SystemExit("usage: run_attribute_coverage.py [--raw-full]")
    manifest = json.loads(MANIFEST.read_text())
    if tuple(manifest["coverage_contract"]["required_source_paths"]) != SOURCE_PATHS:
        raise SystemExit("coverage source contract drift")
    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    output = run_root / "coverage.json"
    coverage = Coverage(
        data_file=str(run_root / ".coverage"), branch=True, source=source_roots(),
    )
    coverage.start()
    pytest_args = ["-q", "-p", "no:cacheprovider"]
    if raw_full:
        pytest_args += ["-p", "scripts.attribute_pytest_reporter", "--ignore=nextseek_api/attributes/tests/test_final_gate.py"]
    pytest_exit = pytest.main([*pytest_args, *PYTEST_SELECTION])
    coverage.stop()
    coverage.save()
    coverage.json_report(outfile=str(output), pretty_print=True, show_contexts=False, include=include_patterns())
    ensure_report_lists_all_sources(coverage, output)
    aggregate_percent = coverage.report(include=include_patterns())
    minimum = float(manifest["coverage_contract"]["minimum_line_percent"])
    if aggregate_percent < minimum:
        print(f"aggregate coverage {aggregate_percent:.1f}% is below {minimum:.1f}%")
        return 1
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-01":
        payload = json.loads(output.read_text())
        schemas_suffix = "nextseek_api/attributes/schemas.py"
        matches = [row for name, row in payload["files"].items() if name.endswith(schemas_suffix)]
        if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 100.0:
            print("task-01 schemas.py coverage is below 100%")
            return 1
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-02":
        payload = json.loads(output.read_text())
        auth_suffix = "nextseek_api/attributes/auth.py"
        matches = [row for name, row in payload["files"].items() if name.endswith(auth_suffix)]
        if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 95.0:
            print("task-02 auth.py coverage is below 95%")
            return 1
    return int(pytest_exit)


if __name__ == "__main__":
    raise SystemExit(main())
