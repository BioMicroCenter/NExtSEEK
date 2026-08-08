#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from coverage import Coverage

# Task-08 Review Blocker 6: `run_attribute_mutation`'s body only ever runs
# inside a real, separately spawned Celery worker subprocess
# (`DisposableAttributeBroker.start_worker`), and the synchronous-web-owner
# barrier nodes (`test_sync_recovery.py`'s `_spawn_web_owner`/
# `_spawn_slow_web_owner`) run `run_stored_job` in a real, separately
# spawned Python subprocess too -- both invisible to a `coverage.Coverage`
# object that only instruments the parent pytest process. `coverage.py`'s
# own documented mechanism for this is a `sitecustomize.py` that calls
# `coverage.process_startup()`, discoverable via `PYTHONPATH`, activated by
# `COVERAGE_PROCESS_START` naming a parallel-mode config file -- both
# written per-run into the disposable evidence root below and exported into
# `os.environ` before pytest starts, so every subprocess spawned during
# this lane (by any task, not just task-08) that inherits the parent
# environment is automatically measured. Harmless when no subprocess reads
# these env vars (every other task's coverage run): the write is a few
# bytes into an already-disposable directory, and `combine()` below is a
# no-op when no parallel `.coverage.*` files exist.

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


def _enable_subprocess_coverage(run_root: Path, data_file: Path) -> None:
    sitecustomize_dir = run_root / "subprocess_sitecustomize"
    sitecustomize_dir.mkdir(parents=True, exist_ok=True)
    (sitecustomize_dir / "sitecustomize.py").write_text(
        "import coverage\n"
        "# The image already carries an unconditional coverage-subprocess\n"
        "# .pth hook (site-packages/a1_coverage.pth: calls\n"
        "# coverage.process_startup(slug=\"pth\") whenever COVERAGE_PROCESS_\n"
        "# START/CONFIG is set) that runs before sitecustomize.py -- so by\n"
        "# the time this module imports, coverage.process_startup() has\n"
        "# already returned (and consumed) the instance; a second call here\n"
        "# always returns None (coverage.py's own re-entrancy guard).\n"
        "# Coverage.current() retrieves that already-started instance\n"
        "# regardless of which caller started it.\n"
        "_cov = coverage.Coverage.current()\n"
        "if _cov is not None:\n"
        "    try:\n"
        "        from celery.signals import task_postrun, worker_process_shutdown\n"
        "    except ImportError:\n"
        "        pass\n"
        "    else:\n"
        "        # The disposable-broker worker is always SIGKILLed at test\n"
        "        # teardown/crash points (real_boundary.py::kill_worker --\n"
        "        # a deliberate, unchanged hard-crash simulation other tests\n"
        "        # depend on), which bypasses atexit entirely, so coverage.py's\n"
        "        # own auto-save-on-exit never runs. Save explicitly after\n"
        "        # every task instead, so data is already on disk well before\n"
        "        # any kill.\n"
        "        task_postrun.connect(lambda *a, **k: _cov.save())\n"
        "        worker_process_shutdown.connect(lambda *a, **k: _cov.save())\n"
    )
    subprocess_coveragerc = run_root / "subprocess.coveragerc"
    subprocess_coveragerc.write_text(
        "[run]\n"
        f"data_file = {data_file.as_posix()}\n"
        "parallel = True\n"
        "branch = True\n"
        "source =\n" + "".join(f"    {root}\n" for root in source_roots())
    )
    os.environ["COVERAGE_PROCESS_START"] = str(subprocess_coveragerc)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = (
        f"{sitecustomize_dir.as_posix()}:{existing_pythonpath}" if existing_pythonpath
        else sitecustomize_dir.as_posix()
    )
    # Celery's default prefork pool executes every task in a forked child;
    # coverage.py's subprocess hook does not reliably survive that further
    # fork boundary (empirically confirmed). `real_boundary.py::start_worker`
    # honors this env var (coverage-lane only) to run the worker under
    # `--pool=solo` instead -- same single, already-instrumented process,
    # no fork.
    os.environ["ATTRIBUTE_COVERAGE_WORKER_POOL"] = "solo"


def main() -> int:
    raw_full = sys.argv[1:] == ["--raw-full"]
    if sys.argv[1:] not in ([], ["--raw-full"]):
        raise SystemExit("usage: run_attribute_coverage.py [--raw-full]")
    manifest = json.loads(MANIFEST.read_text())
    if tuple(manifest["coverage_contract"]["required_source_paths"]) != SOURCE_PATHS:
        raise SystemExit("coverage source contract drift")
    run_root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    output = run_root / "coverage.json"
    data_file = run_root / ".coverage"
    coverage = Coverage(
        data_file=str(data_file), branch=True, source=source_roots(),
    )
    coverage.start()
    _enable_subprocess_coverage(run_root, data_file)
    pytest_args = ["-q", "-p", "no:cacheprovider"]
    if raw_full:
        pytest_args += ["-p", "scripts.attribute_pytest_reporter", "--ignore=nextseek_api/attributes/tests/test_final_gate.py"]
    # T06's Cartesian 162×9 protocol belongs to the 7200s benchmark lane;
    # every task's coverage lane (1200s) proves sources via unit+db semantic
    # nodes only, so the two benchmark files are excluded unconditionally
    # (plan-008 Ruling 2, 2026-08-04 -- T06's own lane already excluded them;
    # this is parity, not weakening).
    pytest_args += [
        "--ignore=nextseek_api/attributes/tests/test_performance_metadata.py",
        "--ignore=nextseek_api/attributes/tests/test_metadata_benchmark.py",
    ]
    pytest_exit = pytest.main([*pytest_args, *PYTEST_SELECTION])
    coverage.stop()
    subprocess_data_files = sorted(str(p) for p in run_root.glob(".coverage.*"))
    if subprocess_data_files:
        # Merge the real Celery worker / web-owner subprocess measurements
        # (Review Blocker 6) into the parent process's own data before
        # reporting -- `combine()` both reads and deletes the per-process
        # parallel files (`keep=False`), leaving one coherent data file.
        coverage.combine(data_paths=subprocess_data_files, strict=True)
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
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-03":
        payload = json.loads(output.read_text())
        for suffix in ("startup/steps/schema_fixups.py", "nextseek_api/attributes/models_db.py"):
            matches = [row for name, row in payload["files"].items() if name.endswith(suffix)]
            if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 95.0:
                print(f"task-03 {suffix} coverage is below 95%")
                return 1
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-04":
        payload = json.loads(output.read_text())
        for suffix in (
            "nextseek_api/attributes/resolver.py",
            "nextseek_api/attributes/repository.py",
            "nextseek_api/attributes/pagination.py",
        ):
            matches = [row for name, row in payload["files"].items() if name.endswith(suffix)]
            if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 95.0:
                print(f"task-04 {suffix} coverage is below 95%")
                return 1
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-06":
        payload = json.loads(output.read_text())
        metadata_suffix = "nextseek_api/attributes/metadata.py"
        matches = [row for name, row in payload["files"].items() if name.endswith(metadata_suffix)]
        if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 95.0:
            print("task-06 metadata.py coverage is below 95%")
            return 1
    if os.environ.get("ATTRIBUTE_EVIDENCE_TASK_ID") == "task-08":
        # Spec Section 4: ">=95% of attributes/tasks.py and attributes/
        # jobs.py" (Review Blocker 6 -- previously unmet and undisclosed,
        # masked by the aggregate-only ACCEPT check above).
        payload = json.loads(output.read_text())
        for suffix in ("nextseek_api/attributes/jobs.py", "nextseek_api/attributes/tasks.py"):
            matches = [row for name, row in payload["files"].items() if name.endswith(suffix)]
            if len(matches) != 1 or matches[0]["summary"]["percent_covered"] < 95.0:
                observed = matches[0]["summary"]["percent_covered"] if len(matches) == 1 else None
                print(f"task-08 {suffix} coverage is below 95% (observed: {observed})")
                return 1
    return int(pytest_exit)


if __name__ == "__main__":
    raise SystemExit(main())
