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
    return int(pytest_exit)


if __name__ == "__main__":
    raise SystemExit(main())
