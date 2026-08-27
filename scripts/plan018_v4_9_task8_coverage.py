#!/usr/bin/env python3
"""Run and authenticate the hardware-bounded Task 8 startup coverage lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


SCHEMA = "plan018-v4-9-task8-startup-coverage/v1"
FLOOR = 95.0
RAW = Path("evidence/plan018-v4-9-task8-startup-coverage.raw.json")
JUNIT = Path("evidence/plan018-v4-9-task8-startup-coverage.junit.xml")
EVIDENCE = Path("evidence/plan018-v4-9-task8-startup-coverage.json")
MODULES = (
    "startup/cli.py",
    "startup/lib/docker_ops.py",
    "startup/steps/doctor.py",
    "startup/steps/registry_push.py",
    "startup/steps/validate.py",
)
TESTS = (
    "startup/tests/test_cli_commands.py",
    "startup/tests/test_docker_ops.py",
    "startup/tests/test_registry_push.py",
    "startup/tests/test_steps_coverage_gaps.py",
    "startup/tests/test_validate.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_command(_root: Path) -> list[str]:
    command = [
        "uv", "run", "--offline", "--no-project",
        "--with", "pytest", "--with", "pytest-cov", "--with", "typer",
        "--with", "rich", "--with", "orjson", "--with", "pyyaml",
        "python", "-m", "pytest", "-q", "--noconftest",
        "-p", "no:cacheprovider", *TESTS,
    ]
    for module in MODULES:
        command.append(f"--cov={module.removesuffix('.py').replace('/', '.')}")
    command.extend(
        [
            "--cov-branch",
            f"--cov-report=json:{RAW}",
            f"--junitxml={JUNIT}",
        ]
    )
    return command


def run(root: Path) -> int:
    (root / RAW).parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(_run_command(root), cwd=root, check=False)
    if completed.returncode:
        return completed.returncode
    evidence = build_evidence(root)
    (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return 0 if evidence["gate"] == "PASS" else 1


def _junit_summary(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        "tests": sum(int(suite.get("tests", "0")) for suite in suites),
        "failures": sum(int(suite.get("failures", "0")) for suite in suites),
        "errors": sum(int(suite.get("errors", "0")) for suite in suites),
        "skipped": sum(int(suite.get("skipped", "0")) for suite in suites),
        "duration_s": round(sum(float(suite.get("time", "0")) for suite in suites), 3),
    }


def build_evidence(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    raw_path = root / RAW
    junit_path = root / JUNIT
    try:
        raw = json.loads(raw_path.read_text())
        files = raw["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Task 8 raw coverage: {exc}") from exc
    if set(files) != set(MODULES):
        errors.append("raw coverage file set differs from the exact Task 8 module inventory")

    modules: dict[str, dict[str, Any]] = {}
    for path in MODULES:
        summary = (files.get(path) or {}).get("summary") or {}
        statements = int(summary.get("num_statements", 0))
        covered_statements = int(summary.get("covered_lines", 0))
        branches = int(summary.get("num_branches", 0))
        covered_branches = int(summary.get("covered_branches", 0))
        statement_pct = round(covered_statements * 100.0 / statements, 1) if statements else 0.0
        branch_pct = round(covered_branches * 100.0 / branches, 1) if branches else 100.0
        modules[path] = {
            "statements": statements,
            "covered_statements": covered_statements,
            "statement_pct": statement_pct,
            "branches": branches,
            "covered_branches": covered_branches,
            "branch_pct": branch_pct,
        }
        if statements <= 0 or covered_statements * 100 < statements * FLOOR:
            errors.append(f"{path} statement coverage is below {FLOOR:.1f}%")
        if branches and covered_branches * 100 < branches * FLOOR:
            errors.append(f"{path} branch coverage is below {FLOOR:.1f}%")

    try:
        tests = _junit_summary(junit_path)
    except (OSError, ValueError, ET.ParseError) as exc:
        raise ValueError(f"invalid Task 8 JUnit: {exc}") from exc
    if tests["tests"] <= 0 or any(tests[key] for key in ("failures", "errors", "skipped")):
        errors.append("Task 8 startup test lane is not completely green")

    source_hashes: dict[str, str] = {}
    for path in MODULES:
        source = root / path
        if not source.is_file():
            errors.append(f"Task 8 source module is missing: {path}")
        else:
            source_hashes[path] = sha256(source)
    return {
        "schema": SCHEMA,
        "gate": "PASS" if not errors else "FAIL",
        "threshold_statement_pct": FLOOR,
        "threshold_branch_pct": FLOOR,
        "command": _run_command(root),
        "tests": tests,
        "modules": modules,
        "source_sha256": source_hashes,
        "artifacts": {
            "raw": {"path": str(RAW), "sha256": sha256(raw_path)},
            "junit": {"path": str(JUNIT), "sha256": sha256(junit_path)},
        },
        "errors": errors,
    }


def validation_errors(root: Path) -> list[str]:
    try:
        actual = json.loads((root / EVIDENCE).read_text())
        expected = build_evidence(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors = list(expected["errors"])
    if actual != expected:
        errors.append("Task 8 startup coverage evidence is stale or not reproducible")
    if actual.get("gate") != "PASS":
        errors.append("Task 8 startup coverage gate is not PASS")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "finalize", "validate"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "run":
        return run(root)
    if args.command == "finalize":
        try:
            evidence = build_evidence(root)
        except ValueError as exc:
            print(f"Task 8 startup coverage FAIL: {exc}")
            return 1
        (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    errors = validation_errors(root)
    print("Task 8 startup coverage " + ("PASS" if not errors else "FAIL"))
    if errors:
        print("\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
