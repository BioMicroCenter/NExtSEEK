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
import collections
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET


OWNERSHIP_MAP = Path("evidence/plan018-v4-9-task2-ownership.json")
OWNED_SURFACE = Path("evidence/plan018-v4-9-owned-surface.json")
FULL_COLLECTION = Path("evidence/plan018-v4-9-task2-full-collection.txt")
CHUNK_MANIFEST = Path("evidence/plan018-v4-9-task2-chunks.json")
EVIDENCE = Path("evidence/plan018-v4-9-task2-evidence.json")
RAW_COVERAGE = Path("evidence/plan018-v4-9-task2-coverage.raw.json")
COVERAGE_SUMMARY = Path("evidence/plan018-v4-9-task2-coverage.json")

# These are intentionally independent pins: changing the map or owned surface does
# not turn an old green coverage result into proof for a new scope.
PINNED_OWNERSHIP_MAP_SHA256 = "8550d6a0497578546536ec8216f2d53bb9d68aecff6f563a102a25cb564159df"
PINNED_OWNED_SURFACE_SHA256 = "2adadcc580eafbac512b3bbeb98a08d322c4d08464deb73afc7d18da1f61a614"
TRANSFERRED_EVIDENCE_DIR = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")
PINNED_TRANSFERRED_EVIDENCE_SHA256 = {
    "testquestions.zip": "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814",
    "MANIFEST.json": "d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2",
}

TEST_TARGETS = (
    "nessie_tests/tests/test_bayes_report.py", "nessie_tests/tests/test_bayesian.py",
    "nessie_tests/tests/test_cli.py", "nessie_tests/tests/test_collect.py",
    "nessie_tests/tests/test_corpus.py", "nessie_tests/tests/test_evaluate.py",
    "nessie_tests/tests/test_export.py", "nessie_tests/tests/test_merge_grades.py",
    "nessie_tests/tests/test_retired.py", "nessie_tests/tests/test_run_case.py",
    "nessie_tests/tests/test_runner.py", "nessie_tests/tests/test_sources.py",
    "nessie_tests/tests/test_strict_manifest_schema.py", "nessie_tests/tests/test_unified_corpus.py",
    "nessie_tests/tests/test_v4_2_set3_replay.py",
    "nextseek_api/eval/tests/test_attempt_store.py", "nextseek_api/eval/tests/test_conservation.py",
    "nextseek_api/eval/tests/test_disposition.py", "nextseek_api/eval/tests/test_human_annotations.py",
    "nextseek_api/eval/tests/test_judge_aggregation.py", "nextseek_api/eval/tests/test_judge_mutations.py",
    "nextseek_api/eval/tests/test_stage_c_runner.py", "nextseek_api/eval/tests/test_v14_pair_input.py",
    "nextseek_api/eval/tests/test_v4_7_fit_refuse.py", "nextseek_api/eval/tests/test_v4_7_mutation_killers.py",
    "nextseek_api/eval/tests/test_v4_7_schemas.py", "nextseek_api/eval/tests/test_v4_9_task2_behavior.py",
    "nextseek_api/cc_assistant/tests/test_eval_export.py",
    "nextseek_api/cc_assistant/tests/test_eval_vendoring.py",
    "nextseek_api/cc_assistant/tests/test_judge_cache.py",
    "nextseek_api/cc_assistant/tests/test_v4_7_route_monitoring.py",
    "nextseek_api/eval/tests/test_exporter_contract.py",
    "nextseek_api/eval/tests/test_functional_inputs_contract.py",
    "nextseek_api/eval/tests/test_task2_coverage_edges.py",
    "nextseek_api/eval/tests/test_artifact_validity_proposal.py",
)

def critical_modules(root: Path = Path(".")) -> tuple[str, ...]:
    """Derive the cluster from the checked-in task ownership map, never a duplicate."""
    data = json.loads((root / OWNERSHIP_MAP).read_text(encoding="utf-8"))
    return tuple(data["task_2"]["critical_modules"])

CRITICAL_MODULES = critical_modules()

FLOOR_PCT = 95.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transferred_evidence_hashes(
    directory: Path = TRANSFERRED_EVIDENCE_DIR, *, expected_sha256: dict[str, str] = PINNED_TRANSFERRED_EVIDENCE_SHA256
) -> dict[str, str]:
    """Authenticate every external file read by the selected V4-2 verifier."""
    actual: dict[str, str] = {}
    for name, expected in expected_sha256.items():
        path = directory / name
        if not path.is_file():
            raise RuntimeError(f"transferred V4-2 evidence is missing: {path}")
        actual[name] = sha256(path)
        if actual[name] != expected:
            raise RuntimeError(f"transferred V4-2 evidence SHA-256 is not pinned for {name}: {actual[name]}")
    return actual


def transferred_evidence_hash(path: Path, *, expected_sha256: str) -> str:
    """Single-file helper retained for focused fault-injection tests."""
    if not path.is_file():
        raise RuntimeError(f"transferred V4-2 evidence is missing: {path}")
    actual = sha256(path)
    if actual != expected_sha256:
        raise RuntimeError(f"transferred V4-2 evidence SHA-256 is not pinned: {actual}")
    return actual


def validate_independent_pins(root: Path) -> list[str]:
    """Keep scope identities outside regenerable evidence and refuse drift."""
    pins = ((root / OWNERSHIP_MAP, PINNED_OWNERSHIP_MAP_SHA256, "ownership map"),
            (root / OWNED_SURFACE, PINNED_OWNED_SURFACE_SHA256, "owned surface"))
    return [f"independent {label} SHA-256 pin is stale" for path, expected, label in pins
            if not expected or not path.is_file() or sha256(path) != expected]


def clear_prior_generated_artifacts(root: Path) -> None:
    """Clear only this runner's interrupted outputs before an authoritative run."""
    evidence = root / "evidence"
    for pattern in ("plan018-v4-9-task2-chunk-*.junit.xml", ".plan018-v4-9-task2.coverage.*"):
        for path in evidence.glob(pattern):
            if path.is_file():
                path.unlink()
    for relative in (RAW_COVERAGE, FULL_COLLECTION, CHUNK_MANIFEST):
        path = root / relative
        if path.is_file():
            path.unlink()


def partition_node_ids(node_ids: tuple[str, ...], *, max_chunk_size: int) -> tuple[tuple[str, ...], ...]:
    """Make deterministic, bounded contiguous chunks without changing selection."""
    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size must be positive")
    return tuple(tuple(node_ids[index:index + max_chunk_size]) for index in range(0, len(node_ids), max_chunk_size))


def validate_partition(full: tuple[str, ...], chunks: tuple[tuple[str, ...], ...]) -> list[str]:
    """Prove chunking is a disjoint cover, including the final collected node."""
    errors: list[str] = []
    flattened = tuple(node for chunk in chunks for node in chunk)
    if set(flattened) != set(full) or len(flattened) != len(full):
        errors.append("chunk union does not equal the full intended collection")
    for left, left_chunk in enumerate(chunks):
        for right in range(left + 1, len(chunks)):
            if set(left_chunk).intersection(chunks[right]):
                errors.append(f"chunk {left} intersects chunk {right}")
    return errors


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.fromstring(path.read_bytes())
    cases = root.findall(".//testcase")
    counts = collections.Counter(tests=len(cases), passed=0, failed=0, errors=0, skipped=0, xfail=0)
    for case in cases:
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif (skipped := case.find("skipped")) is not None:
            if skipped.get("type") == "pytest.xfail":
                counts["xfail"] += 1
            else:
                counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return dict(counts)


def _deselected(output: str) -> int:
    matches = re.findall(r"(\\d+) deselected", output)
    return int(matches[-1]) if matches else 0


def validate_evidence_inputs(
    evidence: dict[str, Any], *, root: Path, ownership_map: Path, raw_coverage: Path
) -> list[str]:
    """Authenticate every reusable Task-2 input; stale bytes fail closed."""
    errors: list[str] = []
    if evidence.get("ownership_map_sha256") != sha256(ownership_map):
        errors.append("ownership map SHA-256 is stale")
    if evidence.get("raw_coverage_sha256") != sha256(raw_coverage):
        errors.append("raw coverage SHA-256 is stale")
    for relative, expected in evidence.get("source_sha256", {}).items():
        source = root / relative
        if not source.is_file() or expected != sha256(source):
            errors.append(f"source SHA-256 is stale for {relative}")
    for chunk in evidence.get("chunks", []):
        junit = root / chunk.get("junit", "")
        if not junit.is_file() or chunk.get("junit_sha256") != sha256(junit):
            errors.append(f"JUnit SHA-256 is stale for {chunk.get('junit', '<missing>')}")
    return errors


def validate_completed_collection(root: Path, manifest: dict[str, Any], full: tuple[str, ...]) -> list[str]:
    """Authenticate the bounded run before finalizing; never trust a summary alone."""
    chunks = tuple(tuple(chunk.get("node_ids", ())) for chunk in manifest.get("chunks", ()))
    errors = validate_partition(full, chunks)
    for chunk in manifest.get("chunks", ()):
        junit = root / chunk.get("junit", "")
        if not junit.is_file() or chunk.get("junit_sha256") != sha256(junit):
            errors.append(f"JUnit SHA-256 is stale for {chunk.get('junit', '<missing>')}")
            continue
        counts = _junit_counts(junit)
        if counts["tests"] != len(chunk.get("node_ids", ())):
            errors.append(f"JUnit selected count differs for chunk {chunk.get('index', '<missing>')}")
        if any(counts[key] for key in ("failed", "errors", "xfail")):
            errors.append(f"unexpected nonexecution or failure in chunk {chunk.get('index', '<missing>')}")
    return errors


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
        if not all(isinstance(summary.get(k), int) for k in ("num_statements", "covered_lines", "num_branches", "covered_branches")):
            errors.append(f"invalid or missing counters for {path}")
            continue
        statements = int(summary["num_statements"])
        covered_lines = int(summary.get("covered_lines", 0))
        branches = int(summary.get("num_branches", 0))
        covered_branches = int(summary.get("covered_branches", 0))
        if statements <= 0 or branches < 0 or covered_lines < 0 or covered_branches < 0 or covered_lines > statements or covered_branches > branches:
            errors.append(f"impossible coverage counters for {path}")
            continue
        line_pct = _percent(covered_lines, statements)
        branch_pct = _percent(covered_branches, branches) if branches else None
        modules[path] = {
            "statements": statements,
            "covered_lines": covered_lines,
            "statement_pct": line_pct,
            "branches": branches,
            "covered_branches": covered_branches,
            "branch_pct": branch_pct,
            "branch_floor_applicable": bool(branches),
        }
        total_statements += statements
        total_covered_lines += covered_lines
        total_branches += branches
        total_covered_branches += covered_branches
        if covered_lines * 100 < statements * 95:
            errors.append(f"{path} statement coverage {line_pct:.1f}% is below {FLOOR_PCT:.1f}%")
        if branches and covered_branches * 100 < branches * 95:
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
        if total_covered_lines * 100 < total_statements * 95:
            errors.append(f"aggregate statement coverage {aggregate['statement_pct']:.1f}% is below {FLOOR_PCT:.1f}%")
        if total_branches == 0 or total_covered_branches * 100 < total_branches * 95:
            errors.append(f"aggregate branch coverage {aggregate['branch_pct']:.1f}% is below {FLOOR_PCT:.1f}%")
    return errors, {"modules": modules, "aggregate": aggregate}


def _docker_base(root: Path, image: str, *, environment: tuple[str, ...] = (), transferred_evidence_dir: Path = TRANSFERRED_EVIDENCE_DIR) -> list[str]:
    transferred_evidence_hashes(transferred_evidence_dir)
    return [
        "docker", "run", "--rm", "--network", "none", "-v", f"{root.resolve()}:/repo",
        "-v", f"{transferred_evidence_dir}:{transferred_evidence_dir}:ro",
        "-w", "/repo", "-e", "DJANGO_SETTINGS_MODULE=dmac.test_settings",
        "-e", "PYTHONPATH=/repo:/repo/dmac_assistant/src",
        *(entry for value in environment for entry in ("-e", value)), image,
        "uv", "run", "--project", "/app", "--no-sync", "python",
    ]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def _collect_nodes(root: Path, image: str) -> tuple[str, ...]:
    result = _run(_docker_base(root, image) + ["-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *TEST_TARGETS])
    if result.returncode:
        raise RuntimeError(f"Task 2 collection failed:\n{result.stdout}")
    nodes = tuple(line for line in result.stdout.splitlines() if "::" in line and not line.startswith("="))
    if not nodes:
        raise RuntimeError("Task 2 collection produced zero node IDs")
    return nodes


def _run_coverage_chunks(root: Path, image: str, chunks: tuple[tuple[str, ...], ...]) -> list[dict[str, Any]]:
    evidence_dir = root / "evidence"
    coverage_file = "/repo/evidence/.plan018-v4-9-task2.coverage"
    _run(_docker_base(root, image, environment=(f"COVERAGE_FILE={coverage_file}",)) + ["-m", "coverage", "erase"])
    results: list[dict[str, Any]] = []
    for index, nodes in enumerate(chunks):
        start = sum(len(previous) for previous in chunks[:index])
        junit_relative = f"evidence/plan018-v4-9-task2-chunk-{index:02d}.junit.xml"
        print(f"Task 2 chunk {index + 1}/{len(chunks)}: {len(nodes)} exact nodes", flush=True)
        command = _docker_base(root, image, environment=(f"COVERAGE_FILE={coverage_file}",)) + [
            "-m", "coverage", "run", "--branch", "--parallel-mode",
            "--source=nessie_tests,nextseek_api.eval", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--junitxml=/repo/{junit_relative}", *nodes,
        ]
        result = _run(command)
        junit = root / junit_relative
        if result.returncode or not junit.is_file():
            raise RuntimeError(f"Task 2 chunk {index} failed:\n{result.stdout}")
        counts = _junit_counts(junit)
        counts["deselected"] = _deselected(result.stdout)
        if counts["tests"] != len(nodes):
            raise RuntimeError(f"Task 2 chunk {index} JUnit selected {counts['tests']} but collected {len(nodes)}")
        results.append({
            "index": index, "predicate": f"exact collected node IDs {start}..{start + len(nodes) - 1}",
            "node_ids": list(nodes), "junit": junit_relative, "junit_sha256": sha256(junit), "counts": counts,
        })
    combine = _run(_docker_base(root, image, environment=(f"COVERAGE_FILE={coverage_file}",)) + ["-m", "coverage", "combine", "/repo/evidence"])
    if combine.returncode:
        raise RuntimeError(f"Task 2 coverage combine failed:\n{combine.stdout}")
    json_result = _run(_docker_base(root, image, environment=(f"COVERAGE_FILE={coverage_file}",)) + ["-m", "coverage", "json", "-o", f"/repo/{RAW_COVERAGE}"])
    if json_result.returncode:
        raise RuntimeError(f"Task 2 coverage JSON failed:\n{json_result.stdout}")
    return results


def run_coverage(root: Path, image: str, *, max_chunk_size: int) -> None:
    """Reproduce all Task-2 coverage in disconnected bounded Docker invocations."""
    pin_errors = validate_independent_pins(root)
    if pin_errors:
        raise RuntimeError("; ".join(pin_errors))
    clear_prior_generated_artifacts(root)
    print("Task 2: collecting exact intended nodes", flush=True)
    nodes = _collect_nodes(root, image)
    print(f"Task 2: collected {len(nodes)} exact nodes", flush=True)
    chunks = partition_node_ids(nodes, max_chunk_size=max_chunk_size)
    errors = validate_partition(nodes, chunks)
    if errors:
        raise RuntimeError("; ".join(errors))
    (root / FULL_COLLECTION).write_text("\n".join(nodes) + "\n", encoding="utf-8")
    chunk_records = _run_coverage_chunks(root, image, chunks)
    aggregate = collections.Counter()
    for record in chunk_records:
        aggregate.update(record["counts"])
    manifest = {
        "schema": "plan018-v4-9-task2-collection/v1", "full_collection": str(FULL_COLLECTION),
        "full_collection_sha256": sha256(root / FULL_COLLECTION), "test_targets": list(TEST_TARGETS),
        "chunk_max_node_ids": max_chunk_size, "chunks": chunk_records, "aggregate_counts": dict(aggregate),
        "partition_errors": validate_partition(nodes, tuple(tuple(record["node_ids"]) for record in chunk_records)),
    }
    (root / CHUNK_MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finalize_coverage(root)


def finalize_coverage(root: Path) -> None:
    """Finalize an already completed, authenticated bounded run without re-execution."""
    full_path = root / FULL_COLLECTION
    manifest_path = root / CHUNK_MANIFEST
    raw_path = root / RAW_COVERAGE
    if not full_path.is_file() or not manifest_path.is_file() or not raw_path.is_file():
        raise RuntimeError("Task 2 finalize requires full collection, chunk manifest, and raw coverage")
    full = tuple(full_path.read_text(encoding="utf-8").splitlines())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate_completed_collection(root, manifest, full)
    if errors:
        raise RuntimeError("; ".join(errors))
    coverage = json.loads(raw_path.read_text(encoding="utf-8"))
    coverage_errors, report = evaluate_coverage(coverage)
    summary = {
        "schema": "plan018-v4-9-task2-coverage/v2", "critical_modules": list(CRITICAL_MODULES),
        "ownership_map_sha256": sha256(root / OWNERSHIP_MAP), "raw_coverage_sha256": sha256(raw_path),
        "threshold_statement_pct": FLOOR_PCT, "threshold_branch_pct": FLOOR_PCT,
        "gate": "PASS" if not coverage_errors else "FAIL", "errors": coverage_errors, **report,
    }
    (root / COVERAGE_SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    aggregate = manifest["aggregate_counts"]
    evidence = {
        "schema": "plan018-v4-9-task2-evidence/v2", "gate": "PASS" if not coverage_errors else "FAIL", "coverage_errors": coverage_errors, "ownership_map_sha256": sha256(root / OWNERSHIP_MAP),
        "owned_surface_sha256": sha256(root / OWNED_SURFACE), "raw_coverage": str(RAW_COVERAGE),
        "raw_coverage_sha256": sha256(root / RAW_COVERAGE), "coverage_summary": str(COVERAGE_SUMMARY),
        "coverage_summary_sha256": sha256(root / COVERAGE_SUMMARY), "full_collection": str(FULL_COLLECTION),
        "full_collection_sha256": sha256(root / FULL_COLLECTION), "collection_manifest": str(CHUNK_MANIFEST),
        "collection_manifest_sha256": sha256(root / CHUNK_MANIFEST), "chunks": manifest["chunks"],
        "aggregate_counts": aggregate, "source_sha256": {path: sha256(root / path) for path in CRITICAL_MODULES},
        "network": "docker --network none", "producer_or_network_execution": False,
        "transferred_evidence_dir": str(TRANSFERRED_EVIDENCE_DIR), "transferred_evidence_sha256": transferred_evidence_hashes(),
    }
    (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if coverage_errors:
        raise RuntimeError("Task 2 coverage floor failed: " + "; ".join(coverage_errors))


def validate_current(root: Path) -> list[str]:
    """Fail closed if Task-2 evidence, scope, sources, or the Task-1 manifest drifted.

    The Task-1 ``--current`` mode is intentionally not used here.  Its immutable
    source SHA predates the later origin/dev integration, so it classifies every
    unrelated post-V4-9 repository addition as Task-2 drift.  Task 2 instead
    authenticates its exact current modules, exact collected nodes, ownership
    map, generated owned-surface bytes, JUnits, and raw coverage below.
    """
    errors: list[str] = validate_independent_pins(root)
    command = [sys.executable, str(root / "scripts/plan018_v4_9_owned_surface.py"), "validate"]
    if _run(command).returncode:
        errors.append("Task 1 owned-surface manifest validation failed")
    evidence_path = root / EVIDENCE
    if not evidence_path.is_file() or not (root / RAW_COVERAGE).is_file():
        return errors + ["Task 2 evidence or raw coverage is missing"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    errors.extend(validate_evidence_inputs(evidence, root=root, ownership_map=root / OWNERSHIP_MAP, raw_coverage=root / RAW_COVERAGE))
    if evidence.get("owned_surface_sha256") != sha256(root / OWNED_SURFACE):
        errors.append("owned-surface SHA-256 is stale")
    try:
        current_transferred = transferred_evidence_hashes()
    except RuntimeError as exc:
        errors.append(str(exc))
    else:
        if evidence.get("transferred_evidence_sha256") != current_transferred:
            errors.append("transferred evidence SHA-256 is stale")
    for path, expected in ((root / FULL_COLLECTION, evidence.get("full_collection_sha256")), (root / CHUNK_MANIFEST, evidence.get("collection_manifest_sha256"))):
        if not path.is_file() or expected != sha256(path):
            errors.append(f"collection artifact SHA-256 is stale for {path.relative_to(root)}")
    summary = json.loads((root / COVERAGE_SUMMARY).read_text(encoding="utf-8")) if (root / COVERAGE_SUMMARY).is_file() else {}
    if summary.get("ownership_map_sha256") != sha256(root / OWNERSHIP_MAP):
        errors.append("coverage summary ownership map SHA-256 is stale")
    if summary.get("raw_coverage_sha256") != sha256(root / RAW_COVERAGE):
        errors.append("coverage summary raw coverage SHA-256 is stale")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="run and authenticate V4-9 Task 2 coverage")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="collect then run bounded disconnected Docker chunks")
    run_parser.add_argument("--root", type=Path, default=Path("."))
    run_parser.add_argument("--image", default="nextseek-nextseek:latest")
    run_parser.add_argument("--chunk-size", type=int, default=50)
    validate_parser = subparsers.add_parser("validate", help="authenticate committed Task-2 evidence")
    validate_parser.add_argument("--root", type=Path, default=Path("."))
    finalize_parser = subparsers.add_parser("finalize", help="authenticate existing chunks and write Task-2 evidence")
    finalize_parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("coverage_json", type=Path, nargs="?")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run_coverage(args.root, args.image, max_chunk_size=args.chunk_size)
        print("Task 2 bounded coverage run PASS")
        return 0
    if args.command == "validate":
        errors = validate_current(args.root)
        print("Task 2 evidence " + ("PASS" if not errors else "FAIL"))
        if errors:
            print("\n".join(errors))
            return 1
        return 0
    if args.command == "finalize":
        finalize_coverage(args.root)
        print("Task 2 bounded coverage finalization PASS")
        return 0
    if args.coverage_json is None or args.report is None:
        parser.error("coverage_json and --report are required unless using run or validate")
    errors, report = evaluate_coverage(json.loads(args.coverage_json.read_text(encoding="utf-8")))
    result = {
        "schema": "plan018-v4-9-task2-coverage/v1",
        "critical_modules": list(CRITICAL_MODULES),
        "ownership_map_sha256": hashlib.sha256(OWNERSHIP_MAP.read_bytes()).hexdigest(),
        "raw_coverage_sha256": hashlib.sha256(args.coverage_json.read_bytes()).hexdigest(),
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
