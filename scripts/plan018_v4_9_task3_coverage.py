#!/usr/bin/env python3
"""Reproduce and authenticate Plan 018 V4-9 Task-3 coverage.

The app/Django lane is the deployed-image, repo-mounted ``dmac.test_settings``
recipe.  Statistical backend doubles make branch tests deterministic and fast;
they do not replace the separately authenticated V4-4 real-MCMC acceptance.
The disposable MySQL lane is run by ``scripts/plan018_lane_m_mysql.sh``.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET


OWNERSHIP = Path("evidence/plan018-v4-9-task3-ownership.json")
OWNED_SURFACE = Path("evidence/plan018-v4-9-owned-surface.json")
COLLECTION = Path("evidence/plan018-v4-9-task3-collection.txt")
JUNIT = Path("evidence/plan018-v4-9-task3.junit.xml")
RAW = Path("evidence/plan018-v4-9-task3-coverage.raw.json")
SUMMARY = Path("evidence/plan018-v4-9-task3-coverage.json")
EVIDENCE = Path("evidence/plan018-v4-9-task3-evidence.json")
LANE_M_LOG = Path("evidence/plan018-v4-9-task3-lane-m.log")
LANE_M_JUNIT = Path("evidence/plan018-v4-9-task3-lane-m.junit.xml")
LANE_M_SIDECAR = Path("evidence/plan018-v4-9-task3-lane-m.sidecar.json")

DEFAULT_IMAGE = "sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651"
FLOOR = 95.0

TEST_TARGETS = (
    "scripts/test_plan018_v4_9_task3_coverage.py",
    "nextseek_api/eval/tests/test_task3_fit_coverage.py",
    "nextseek_api/eval/tests/test_task3_publication_store_coverage.py",
    "nextseek_api/eval/tests/test_v14_censoring.py",
    "nextseek_api/eval/tests/test_v14_decision.py",
    "nextseek_api/eval/tests/test_v14_pair_input.py",
    "nextseek_api/eval/tests/test_v14_recovery_matrix.py",
    "nextseek_api/eval/tests/test_v4_7_fit_refuse.py",
    "nextseek_api/eval/tests/test_v4_7_mutation_killers.py",
    "nextseek_api/eval/tests/test_v4_7_schemas.py",
    "nextseek_api/eval/tests/test_generation_store_v45.py",
    "nextseek_api/cc_assistant/tests/test_generation_store_validation.py",
    "nextseek_api/cc_assistant/tests/test_eval_publish.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def critical_modules(root: Path) -> tuple[str, ...]:
    data = json.loads((root / OWNERSHIP).read_text(encoding="utf-8"))
    return tuple(data["critical_modules"])


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=env,
    )


def _docker(root: Path, image: str, *python_args: str, coverage_file: str | None = None) -> list[str]:
    command = [
        "docker", "run", "--rm", "--network", "none",
        "-e", "PYTHONPATH=/work:/work/dmac_assistant/src",
        "-e", "DJANGO_SETTINGS_MODULE=dmac.test_settings",
    ]
    if coverage_file:
        command.extend(("-e", f"COVERAGE_FILE={coverage_file}"))
    command.extend(
        (
            "-v", f"{root.resolve()}:/work", "-w", "/work", image,
            "/app/.venv/bin/python", *python_args,
        )
    )
    return command


def _node_ids(output: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in output.splitlines()
        if "::" in line and not line.startswith("=")
    )


def _deselected(output: str) -> int:
    matches = re.findall(r"(\d+) deselected", output)
    return int(matches[-1]) if matches else 0


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.fromstring(path.read_bytes())
    counts = collections.Counter(
        tests=0, passed=0, failed=0, errors=0, skipped=0, xfail=0
    )
    for case in root.findall(".//testcase"):
        counts["tests"] += 1
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif (skipped := case.find("skipped")) is not None:
            counts["xfail" if skipped.get("type") == "pytest.xfail" else "skipped"] += 1
        else:
            counts["passed"] += 1
    return dict(counts)


def _pct(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(covered * 100.0 / total, 1)


def evaluate_coverage(root: Path, raw: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if raw.get("meta", {}).get("branch_coverage") is not True:
        return ["coverage was not collected with branch coverage"], {}
    files = raw.get("files", {})
    modules: dict[str, dict[str, Any]] = {}
    aggregate = collections.Counter()
    for path in critical_modules(root):
        summary = files.get(path, {}).get("summary")
        if not isinstance(summary, dict):
            errors.append(f"missing coverage for {path}")
            continue
        statements = int(summary["num_statements"])
        covered_statements = int(summary["covered_lines"])
        branches = int(summary["num_branches"])
        covered_branches = int(summary["covered_branches"])
        if statements <= 0:
            errors.append(f"critical module has no executable statements: {path}")
            continue
        statement_pct = _pct(covered_statements, statements)
        branch_pct = _pct(covered_branches, branches)
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
    aggregate_result = dict(aggregate)
    aggregate_result["statement_pct"] = _pct(
        aggregate["covered_statements"], aggregate["statements"]
    )
    aggregate_result["branch_pct"] = _pct(
        aggregate["covered_branches"], aggregate["branches"]
    )
    if len(modules) != len(critical_modules(root)):
        errors.append("not every declared critical module has authenticated coverage")
    elif aggregate["covered_statements"] * 100 < aggregate["statements"] * FLOOR:
        errors.append("aggregate statement coverage is below 95%")
    elif aggregate["covered_branches"] * 100 < aggregate["branches"] * FLOOR:
        errors.append("aggregate branch coverage is below 95%")
    return errors, {"modules": modules, "aggregate": aggregate_result}


def _run_lane_m(root: Path, image: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "REPO": str(root.resolve()),
            "APP_IMAGE": image,
            "LANE_M_PYTEST": "nextseek_api/eval/tests/test_generation_store_mysql.py",
            "LANE_M_LOG": str((root / LANE_M_LOG).resolve()),
            # This path is consumed inside the app container, where the repo is
            # mounted at /work.  Host-absolute paths disappear with the container.
            "LANE_M_JUNIT": f"/work/{LANE_M_JUNIT}",
            "LANE_M_SIDECAR": str((root / LANE_M_SIDECAR).resolve()),
            "LANE_M_SIDECAR_SCHEMA": "plan018-v4-9-task3-lane-m/v1",
        }
    )
    result = _run(["bash", str(root / "scripts/plan018_lane_m_mysql.sh")], env=env)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise RuntimeError(f"Task 3 disposable MySQL lane failed with {result.returncode}")


def _validate_lane_m(root: Path) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if not (root / LANE_M_SIDECAR).is_file() or not (root / LANE_M_JUNIT).is_file():
        return ["Task 3 Lane M evidence is missing"], {}
    sidecar = json.loads((root / LANE_M_SIDECAR).read_text(encoding="utf-8"))
    counts = _junit_counts(root / LANE_M_JUNIT)
    if sidecar.get("schema") != "plan018-v4-9-task3-lane-m/v1" or sidecar.get("gate") != "PASS":
        errors.append("Task 3 Lane M sidecar is not a schema-matching PASS")
    if sidecar.get("paid_or_live_resources_used") is not False:
        errors.append("Task 3 Lane M does not attest zero paid/live resources")
    if counts != {"tests": 12, "passed": 12, "failed": 0, "errors": 0, "skipped": 0, "xfail": 0}:
        errors.append(f"Task 3 Lane M execution counts are not exact: {counts}")
    return errors, {
        "sidecar": str(LANE_M_SIDECAR),
        "sidecar_sha256": sha256(root / LANE_M_SIDECAR),
        "junit": str(LANE_M_JUNIT),
        "junit_sha256": sha256(root / LANE_M_JUNIT),
        "counts": counts,
    }


def run(root: Path, image: str) -> None:
    for path in (COLLECTION, JUNIT, RAW, SUMMARY, EVIDENCE, LANE_M_LOG, LANE_M_JUNIT, LANE_M_SIDECAR):
        candidate = root / path
        if candidate.is_file():
            candidate.unlink()
    coverage_file = "/work/evidence/.plan018-v4-9-task3.coverage"
    collection_result = _run(
        _docker(
            root,
            image,
            "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
            *TEST_TARGETS,
        )
    )
    if collection_result.returncode:
        raise RuntimeError("Task 3 collection failed:\n" + collection_result.stdout)
    nodes = _node_ids(collection_result.stdout)
    if not nodes:
        raise RuntimeError("Task 3 collection selected zero tests")
    (root / COLLECTION).write_text("\n".join(nodes) + "\n", encoding="utf-8")
    print(f"Task 3: running {len(nodes)} exact tests in the deployed app image", flush=True)
    test_result = _run(
        _docker(
            root,
            image,
            "-m", "coverage", "run", "--branch",
            "--source=nextseek_api.eval.fit.fit_boundary,nextseek_api.eval.fit.v14,nextseek_api.eval.generation_store,nextseek_api.eval.generation_validation,nextseek_api.eval.publish",
            "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--junitxml=/work/{JUNIT}", *nodes,
            coverage_file=coverage_file,
        )
    )
    print(test_result.stdout, end="", flush=True)
    if test_result.returncode:
        raise RuntimeError("Task 3 coverage tests failed")
    json_result = _run(
        _docker(
            root,
            image,
            "-m", "coverage", "json", "-o", f"/work/{RAW}",
            coverage_file=coverage_file,
        )
    )
    if json_result.returncode:
        raise RuntimeError("Task 3 coverage JSON failed:\n" + json_result.stdout)
    counts = _junit_counts(root / JUNIT)
    counts["deselected"] = _deselected(test_result.stdout)
    if counts["tests"] != len(nodes) or any(counts[key] for key in ("failed", "errors", "skipped", "xfail", "deselected")):
        raise RuntimeError(f"Task 3 unexpected execution counts: {counts}")
    _run_lane_m(root, image)
    finalize(root, image=image, expected_nodes=nodes, execution_counts=counts)


def finalize(
    root: Path,
    *,
    image: str = DEFAULT_IMAGE,
    expected_nodes: tuple[str, ...] | None = None,
    execution_counts: dict[str, int] | None = None,
) -> None:
    required = (OWNERSHIP, OWNED_SURFACE, COLLECTION, JUNIT, RAW)
    missing = [str(path) for path in required if not (root / path).is_file()]
    if missing:
        raise RuntimeError(f"Task 3 finalization inputs missing: {missing}")
    nodes = expected_nodes or tuple((root / COLLECTION).read_text(encoding="utf-8").splitlines())
    counts = execution_counts or {**_junit_counts(root / JUNIT), "deselected": 0}
    errors: list[str] = []
    if counts["tests"] != len(nodes) or any(counts[key] for key in ("failed", "errors", "skipped", "xfail", "deselected")):
        errors.append(f"unexpected Task 3 execution counts: {counts}")
    raw = json.loads((root / RAW).read_text(encoding="utf-8"))
    coverage_errors, coverage_result = evaluate_coverage(root, raw)
    errors.extend(coverage_errors)
    lane_errors, lane = _validate_lane_m(root)
    errors.extend(lane_errors)
    summary = {
        "schema": "plan018-v4-9-task3-coverage/v1",
        "gate": "PASS" if not coverage_errors else "FAIL",
        "threshold_statement_pct": FLOOR,
        "threshold_branch_pct": FLOOR,
        "critical_modules": list(critical_modules(root)),
        "ownership_sha256": sha256(root / OWNERSHIP),
        "raw_coverage_sha256": sha256(root / RAW),
        "errors": coverage_errors,
        **coverage_result,
    }
    (root / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = {
        "schema": "plan018-v4-9-task3-evidence/v1",
        "gate": "PASS" if not errors else "FAIL",
        "errors": errors,
        "image": image,
        "environment": "repo mount + dmac.test_settings in deployed app image",
        "network": "none for coverage lane; isolated disposable Docker network for Lane M",
        "paid_provider_or_live_resources_used": False,
        "statistical_backend_note": "fast deterministic doubles cover orchestration branches; real MCMC acceptance remains authenticated by V4-4 evidence",
        "ownership": str(OWNERSHIP),
        "ownership_sha256": sha256(root / OWNERSHIP),
        "owned_surface_sha256": sha256(root / OWNED_SURFACE),
        "collection": str(COLLECTION),
        "collection_sha256": sha256(root / COLLECTION),
        "junit": str(JUNIT),
        "junit_sha256": sha256(root / JUNIT),
        "raw_coverage": str(RAW),
        "raw_coverage_sha256": sha256(root / RAW),
        "coverage_summary": str(SUMMARY),
        "coverage_summary_sha256": sha256(root / SUMMARY),
        "execution_counts": counts,
        "lane_m": lane,
        "source_sha256": {path: sha256(root / path) for path in critical_modules(root)},
        "test_source_sha256": {path: sha256(root / path) for path in TEST_TARGETS},
    }
    (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        raise RuntimeError("Task 3 gate failed: " + "; ".join(errors))


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    if not (root / EVIDENCE).is_file() or not (root / SUMMARY).is_file():
        return ["Task 3 evidence or coverage summary is missing"]
    evidence = json.loads((root / EVIDENCE).read_text(encoding="utf-8"))
    for relative, expected in (
        (OWNERSHIP, evidence.get("ownership_sha256")),
        (OWNED_SURFACE, evidence.get("owned_surface_sha256")),
        (COLLECTION, evidence.get("collection_sha256")),
        (JUNIT, evidence.get("junit_sha256")),
        (RAW, evidence.get("raw_coverage_sha256")),
        (SUMMARY, evidence.get("coverage_summary_sha256")),
    ):
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            errors.append(f"stale or missing Task 3 artifact: {relative}")
    for field in ("source_sha256", "test_source_sha256"):
        for relative, expected in evidence.get(field, {}).items():
            path = root / relative
            if not path.is_file() or sha256(path) != expected:
                errors.append(f"stale Task 3 {field}: {relative}")
    lane_errors, lane = _validate_lane_m(root)
    errors.extend(lane_errors)
    if evidence.get("lane_m") != lane:
        errors.append("Task 3 Lane M evidence hashes are stale")
    raw = json.loads((root / RAW).read_text(encoding="utf-8")) if (root / RAW).is_file() else {}
    coverage_errors, _ = evaluate_coverage(root, raw)
    errors.extend(coverage_errors)
    if evidence.get("gate") != "PASS":
        errors.append("Task 3 evidence gate is not PASS")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--root", type=Path, default=Path("."))
    run_parser.add_argument("--image", default=DEFAULT_IMAGE)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--root", type=Path, default=Path("."))
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--root", type=Path, default=Path("."))
    finalize_parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    if args.command == "run":
        run(args.root.resolve(), args.image)
        print("Task 3 coverage + Lane M PASS")
        return 0
    if args.command == "finalize":
        finalize(args.root.resolve(), image=args.image)
        print("Task 3 existing coverage + Lane M finalization PASS")
        return 0
    errors = validate(args.root.resolve())
    print("Task 3 evidence " + ("PASS" if not errors else "FAIL"))
    if errors:
        print("\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
