#!/usr/bin/env python3
"""Build and validate the Plan 018 V4-9 global critical-module coverage gate."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


OWNED_SURFACE = Path("evidence/plan018-v4-9-owned-surface.json")
REPORT = Path("evidence/plan018-v4-9-global-coverage.json")
FLOOR = 95.0

COMPONENTS = {
    "task2": {
        "ownership": Path("evidence/plan018-v4-9-task2-ownership.json"),
        "summary": Path("evidence/plan018-v4-9-task2-coverage.json"),
        "evidence": Path("evidence/plan018-v4-9-task2-evidence.json"),
    },
    "task3": {
        "ownership": Path("evidence/plan018-v4-9-task3-ownership.json"),
        "summary": Path("evidence/plan018-v4-9-task3-coverage.json"),
        "evidence": Path("evidence/plan018-v4-9-task3-evidence.json"),
    },
    "task4": {
        "ownership": Path("evidence/plan018-v4-9-task4-ownership.json"),
        "summary": Path("evidence/plan018-v4-9-task4-coverage.json"),
        "evidence": Path("evidence/plan018-v4-9-task4-evidence.json"),
    },
    "task7": {
        "summary": Path("evidence/plan018-v4-9-task7-evidence.json"),
        "evidence": Path("evidence/plan018-v4-9-task7-evidence.json"),
        "raw": Path("evidence/plan018-v4-9-task7-coverage.raw.json"),
    },
}

TASK7_MODULES = (
    "nextseek_api/eval/deploy_record.py",
    "nextseek_api/eval/mixed_version_recovery.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(root: Path, relative: Path) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def critical_groups(root: Path) -> dict[str, tuple[str, ...]]:
    task2 = _json(root, COMPONENTS["task2"]["ownership"])["task_2"]["critical_modules"]
    task3 = _json(root, COMPONENTS["task3"]["ownership"])["critical_modules"]
    task4 = _json(root, COMPONENTS["task4"]["ownership"])["critical_modules"]
    return {
        "task2": tuple(task2),
        "task3": tuple(task3),
        "task4": tuple(task4),
        "task7": TASK7_MODULES,
    }


def inventory_errors(
    root: Path, groups: dict[str, tuple[str, ...]] | None = None
) -> list[str]:
    groups = groups or critical_groups(root)
    errors: list[str] = []
    modules = [path for paths in groups.values() for path in paths]
    duplicates = sorted(path for path, count in collections.Counter(modules).items() if count > 1)
    errors.extend(f"critical module appears in multiple task groups: {path}" for path in duplicates)

    manifest = _json(root, OWNED_SURFACE)
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    for path in modules:
        entry = entries.get(path)
        if entry is None:
            errors.append(f"critical module absent from owned-surface manifest: {path}")
        elif entry.get("classification") != "production" or not entry.get("exists_at_source"):
            errors.append(f"critical module is not an existing production surface: {path}")
        if not (root / path).is_file():
            errors.append(f"critical module missing from checkout: {path}")

    task7 = _json(root, COMPONENTS["task7"]["summary"])
    if tuple(task7.get("coverage", {}).get("files", {})) != TASK7_MODULES:
        errors.append("Task 7 coverage module set differs from the global critical inventory")
    return errors


def _normalize_component_modules(root: Path, name: str) -> dict[str, dict[str, int]]:
    data = _json(root, COMPONENTS[name]["summary"])
    source = data["coverage"]["files"] if name == "task7" else data["modules"]
    normalized: dict[str, dict[str, int]] = {}
    for path, values in source.items():
        normalized[path] = {
            "statements": int(values["statements"]),
            "covered_statements": int(
                values.get("covered_statements", values.get("covered_lines"))
            ),
            "branches": int(values["branches"]),
            "covered_branches": int(values["covered_branches"]),
        }
    return normalized


def evaluate(
    files: dict[str, dict[str, int]], critical_modules: tuple[str, ...]
) -> tuple[list[str], dict[str, Any]]:
    duplicates = sorted(
        path for path, count in collections.Counter(critical_modules).items() if count > 1
    )
    if duplicates:
        return [f"duplicate critical module: {path}" for path in duplicates], {}

    errors: list[str] = []
    modules: dict[str, dict[str, Any]] = {}
    aggregate = collections.Counter()
    for path in critical_modules:
        values = files.get(path)
        if values is None:
            errors.append(f"missing coverage for {path}")
            continue
        statements = int(values["statements"])
        covered_statements = int(values["covered_statements"])
        branches = int(values["branches"])
        covered_branches = int(values["covered_branches"])
        if statements <= 0:
            errors.append(f"critical module has no executable statements: {path}")
            continue
        if not 0 <= covered_statements <= statements or not 0 <= covered_branches <= branches:
            errors.append(f"impossible coverage counters for {path}")
            continue
        statement_pct = round(covered_statements * 100.0 / statements, 1)
        branch_pct = round(covered_branches * 100.0 / branches, 1) if branches else 100.0
        modules[path] = {
            "statements": statements,
            "covered_statements": covered_statements,
            "statement_pct": statement_pct,
            "branches": branches,
            "covered_branches": covered_branches,
            "branch_pct": branch_pct,
            "branch_floor_applicable": branches > 0,
        }
        aggregate.update(
            statements=statements,
            covered_statements=covered_statements,
            branches=branches,
            covered_branches=covered_branches,
        )
        if covered_statements * 100 < statements * FLOOR:
            errors.append(f"{path} statement coverage {statement_pct:.1f}% is below {FLOOR:.1f}%")
        if branches and covered_branches * 100 < branches * FLOOR:
            errors.append(f"{path} branch coverage {branch_pct:.1f}% is below {FLOOR:.1f}%")

    result = dict(aggregate)
    result["statement_pct"] = (
        round(aggregate["covered_statements"] * 100.0 / aggregate["statements"], 1)
        if aggregate["statements"] else 0.0
    )
    result["branch_pct"] = (
        round(aggregate["covered_branches"] * 100.0 / aggregate["branches"], 1)
        if aggregate["branches"] else 100.0
    )
    if not errors and aggregate["covered_statements"] * 100 < aggregate["statements"] * FLOOR:
        errors.append("global aggregate statement coverage is below 95.0%")
    if not errors and aggregate["branches"] and aggregate["covered_branches"] * 100 < aggregate["branches"] * FLOOR:
        errors.append("global aggregate branch coverage is below 95.0%")
    return errors, {"modules": modules, "aggregate": result}


def _component_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for name, paths in COMPONENTS.items():
        evidence = _json(root, paths["evidence"])
        if evidence.get("gate") != "PASS":
            errors.append(f"{name} evidence gate is not PASS")
        if name != "task7" and _json(root, paths["summary"]).get("gate") != "PASS":
            errors.append(f"{name} coverage summary gate is not PASS")
        for path, expected in evidence.get("source_sha256", {}).items():
            source = root / path
            if not source.is_file() or sha256(source) != expected:
                errors.append(f"{name} source hash is stale: {path}")
        if name == "task7":
            for path in TASK7_MODULES:
                expected = evidence.get("source", {}).get(path, {}).get("sha256")
                if not expected or sha256(root / path) != expected:
                    errors.append(f"task7 source hash is stale: {path}")
    return errors


def build_report(root: Path) -> dict[str, Any]:
    groups = critical_groups(root)
    critical = tuple(path for paths in groups.values() for path in paths)
    files: dict[str, dict[str, int]] = {}
    for name in groups:
        files.update(_normalize_component_modules(root, name))
    errors = inventory_errors(root, groups)
    coverage_errors, coverage = evaluate(files, critical)
    errors.extend(coverage_errors)
    errors.extend(_component_errors(root))

    component_hashes = {
        name: {
            key: {"path": str(path), "sha256": sha256(root / path)}
            for key, path in paths.items()
        }
        for name, paths in COMPONENTS.items()
    }
    return {
        "schema": "plan018-v4-9-global-coverage/v1",
        "gate": "PASS" if not errors else "FAIL",
        "threshold_statement_pct": FLOOR,
        "threshold_branch_pct": FLOOR,
        "critical_groups": {name: list(paths) for name, paths in groups.items()},
        "critical_modules": list(critical),
        "owned_surface": {
            "path": str(OWNED_SURFACE),
            "sha256": sha256(root / OWNED_SURFACE),
        },
        "components": component_hashes,
        "source_sha256": {path: sha256(root / path) for path in critical},
        "errors": errors,
        **coverage,
    }


def validation_errors(root: Path) -> list[str]:
    if not (root / REPORT).is_file():
        return ["global coverage report is missing"]
    actual = _json(root, REPORT)
    expected = build_report(root)
    errors = list(expected["errors"])
    if actual != expected:
        errors.append("global coverage report is stale or not reproducible")
    if actual.get("gate") != "PASS":
        errors.append("global coverage report gate is not PASS")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "generate":
        report = build_report(root)
        (root / REPORT).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            f"WROTE: {REPORT} ({len(report['critical_modules'])} critical modules, "
            f"gate={report['gate']})"
        )
        return 0 if report["gate"] == "PASS" else 1
    errors = validation_errors(root)
    print("Plan 018 V4-9 global coverage " + ("PASS" if not errors else "FAIL"))
    if errors:
        print("\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
