"""Independent Plan 005 coverage / JUnit / pragma gate (real CI command)."""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from build_tools.plan005_closeout import (
    COVERAGE_MIN_TOTAL,
    PLAN005_BASE_COMMIT,
)

PRAGMA_RE = re.compile(r"pragma:\s*no\s+(cover|branch)", re.IGNORECASE)
COVERAGE_CONFIG_NAMES = (
    ".coveragerc",
    "setup.cfg",
    "tox.ini",
    ".coveragerc.ini",
)
LANE_FUTURE_OP = "nextseek_api/cc_assistant/tests/test_future_op_dropin.py"
LANE_AUDIT_A = "nextseek_api/cc_assistant/tests/test_op_registry_audit.py"
LANE_ROUTE = "nextseek_api/assistant/tests/test_route_capabilities.py"


class GateError(ValueError):
    """Raised when the Plan 005 coverage/JUnit gate fails."""


def enumerate_production_py(source_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_root.rglob("*.py"):
        rel = path.relative_to(source_root)
        if "tests" in rel.parts:
            continue
        files.append(path)
    return sorted(files)


def coverage_file_key(path: Path, source_root: Path) -> tuple[str, ...]:
    rel = path.resolve().as_posix()
    posix = path.as_posix()
    dotted = "nextseek_api/cc_assistant/" + path.relative_to(source_root).as_posix()
    return rel, posix, dotted, path.relative_to(source_root).as_posix()


def lookup_coverage_file(files: dict[str, Any], path: Path, source_root: Path) -> dict[str, Any]:
    keys = coverage_file_key(path, source_root)
    for key in files:
        normalized = key.replace("\\", "/")
        for candidate in keys:
            if normalized == candidate or normalized.endswith("/" + candidate) or normalized.endswith(candidate):
                return files[key]
    raise GateError(f"production file missing from coverage JSON: {path}")


def combined_branch_enabled_percent(
    coverage_json: dict[str, Any],
    *,
    source_root: Path,
) -> float:
    production = enumerate_production_py(source_root)
    files = coverage_json.get("files") or {}
    covered_lines = 0
    covered_branches = 0
    num_statements = 0
    num_branches = 0
    for path in production:
        entry = lookup_coverage_file(files, path, source_root)
        summary = entry.get("summary") or {}
        covered_lines += int(summary.get("covered_lines") or 0)
        covered_branches += int(summary.get("covered_branches") or 0)
        num_statements += int(summary.get("num_statements") or 0)
        num_branches += int(summary.get("num_branches") or 0)
    denom = num_statements + num_branches
    if denom <= 0:
        raise GateError("coverage denominator is zero")
    return 100.0 * (covered_lines + covered_branches) / denom


def construct_null_coverage_config() -> dict[str, Any]:
    return {
        "config_file": "/dev/null",
        "omit": [],
        "exclude_also": [],
        "source": [],
        "branch": True,
    }


def assert_clean_coverage_config(config: dict[str, Any]) -> None:
    if config.get("config_file") != "/dev/null":
        raise GateError("coverage config_file must be /dev/null")
    if config.get("omit"):
        raise GateError("coverage omit is forbidden")
    if config.get("exclude_also"):
        raise GateError("coverage exclude_also is forbidden")
    source = config.get("source") or []
    if source and source != ["nextseek_api.cc_assistant"] and source != []:
        raise GateError("coverage source narrowing is forbidden")
    if config.get("branch") is False:
        raise GateError("branch coverage must remain enabled")


def parse_junit_node_results(path: Path) -> dict[str, str]:
    tree = ET.parse(path)
    results: dict[str, str] = {}
    for case in tree.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        node_id = f"{classname}::{name}"
        status = "passed"
        if case.find("skipped") is not None:
            message = (case.find("skipped").get("message") or "") + (
                case.find("skipped").text or ""
            )
            status = "xfail" if "xfail" in message.lower() else "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        if node_id in results and results[node_id] != status:
            raise GateError(f"duplicate conflicting JUnit result: {node_id}")
        results[node_id] = status
    return results


def count_oracle_sites(source: str) -> int:
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "pytest" and func.attr in {"raises", "fail"}:
                    count += 1
            elif isinstance(func, ast.Name) and func.id in {"raises", "fail"}:
                count += 1
    return count


def git_show_text(repo_root: Path, rev: str, rel_path: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{rev}:{rel_path}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8")


def git_ls_files(repo_root: Path, rev: str, prefix: str) -> set[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "-r", "--name-only", rev, prefix],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise GateError(f"git ls-tree failed for {rev}")
    return {line for line in completed.stdout.splitlines() if line}


def reject_added_pragmas(
    *,
    repo_root: Path,
    source_root: Path,
    base: str,
) -> None:
    for path in enumerate_production_py(source_root):
        rel = path.relative_to(repo_root).as_posix()
        current = path.read_text(encoding="utf-8")
        base_text = git_show_text(repo_root, base, rel) or ""
        current_hits = set(PRAGMA_RE.findall(current.lower())) if False else set(
            m.group(0) for m in PRAGMA_RE.finditer(current)
        )
        base_hits = set(m.group(0) for m in PRAGMA_RE.finditer(base_text))
        added = []
        current_list = PRAGMA_RE.findall(current)
        base_list = PRAGMA_RE.findall(base_text)
        if len(current_list) > len(base_list):
            added = current_list[len(base_list) :]
        extra_directives = current.count("pragma: no cover") - base_text.count("pragma: no cover")
        extra_branch = current.count("pragma: no branch") - base_text.count("pragma: no branch")
        if extra_directives > 0 or extra_branch > 0 or added:
            raise GateError(f"Plan005-added coverage pragma in {rel}")
        if "coverage:" in current and "coverage:" not in base_text:
            raise GateError(f"Plan005-added coverage exclusion directive in {rel}")


def reject_new_coverage_config(*, repo_root: Path, base: str) -> None:
    for name in COVERAGE_CONFIG_NAMES:
        path = repo_root / name
        if path.is_file() and git_show_text(repo_root, base, name) is None:
            raise GateError(f"new coverage configuration file relative to base: {name}")
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        current = pyproject.read_text(encoding="utf-8")
        base_text = git_show_text(repo_root, base, "pyproject.toml") or ""
        if "[tool.coverage" in current and "[tool.coverage" not in base_text:
            raise GateError("new pyproject coverage configuration relative to base")


def classify_test_path(rel_posix: str) -> str | None:
    if rel_posix == LANE_FUTURE_OP:
        return "future-op"
    if rel_posix == LANE_AUDIT_A:
        return "audit-a"
    if rel_posix == LANE_ROUTE:
        return "assistant-route"
    if rel_posix.startswith("build_tools/tests/"):
        return "build_tools"
    if rel_posix.startswith("nextseek_api/cc_assistant/tests/"):
        return "cc_assistant"
    return None


def added_or_changed_tests(*, repo_root: Path, base: str) -> list[str]:
    prefixes = (
        "nextseek_api/cc_assistant/tests/",
        "nextseek_api/assistant/tests/",
        "build_tools/tests/",
    )
    current: set[str] = set()
    for prefix in prefixes:
        root = repo_root / prefix
        if not root.exists():
            continue
        for path in root.rglob("test_*.py"):
            current.add(path.relative_to(repo_root).as_posix())
        for path in root.rglob("test*.py"):
            if path.name.startswith("test"):
                current.add(path.relative_to(repo_root).as_posix())
    base_files: set[str] = set()
    for prefix in prefixes:
        base_files |= git_ls_files(repo_root, base, prefix)
        base_files = {p for p in base_files if Path(p).name.startswith("test")}
    changed: list[str] = []
    for rel in sorted(current):
        current_text = (repo_root / rel).read_text(encoding="utf-8")
        base_text = git_show_text(repo_root, base, rel)
        if base_text is None or base_text != current_text:
            changed.append(rel)
    return changed


def require_base_nodes_present(
    *,
    baseline_results: dict[str, str],
    final_results: dict[str, str],
) -> None:
    for node_id, status in baseline_results.items():
        if node_id not in final_results:
            raise GateError(f"base node ID deleted or renamed: {node_id}")
        if final_results[node_id] in {"skipped", "xfail"}:
            raise GateError(f"base node ID skipped in final lane: {node_id}")
        if final_results[node_id] == "failed":
            raise GateError(f"base node ID failed in final lane: {node_id}")


def require_new_tests_mapped_and_passed(
    *,
    repo_root: Path,
    base: str,
    junit_by_lane: dict[str, dict[str, str]],
    maintainer_signoff_diffs: set[str] | None = None,
) -> None:
    signoff = maintainer_signoff_diffs or set()
    changed = added_or_changed_tests(repo_root=repo_root, base=base)
    merged: dict[str, str] = {}
    for results in junit_by_lane.values():
        for node_id, status in results.items():
            if node_id in merged and merged[node_id] != status:
                raise GateError(f"duplicate conflicting JUnit result: {node_id}")
            merged[node_id] = status
    for rel in changed:
        lane = classify_test_path(rel)
        if lane is None:
            raise GateError(f"unclassified test path: {rel}")
        base_text = git_show_text(repo_root, base, rel)
        current_text = (repo_root / rel).read_text(encoding="utf-8")
        if base_text is not None:
            if count_oracle_sites(current_text) < count_oracle_sites(base_text):
                if rel not in signoff:
                    raise GateError(
                        f"reduced assert/pytest.raises/pytest.fail sites in {rel} "
                        "without structured maintainer sign-off"
                    )
        if lane not in junit_by_lane:
            raise GateError(f"missing JUnit for lane {lane} covering {rel}")
    for node_id, status in merged.items():
        if status in {"skipped", "xfail"}:
            raise GateError(f"new skip/xfail is not allowed: {node_id}")
        if status == "failed":
            raise GateError(f"failed test node: {node_id}")


def run_gate(
    *,
    coverage_json_path: Path,
    junit_paths: list[Path],
    baseline_junit: Path,
    source_root: Path,
    repo_root: Path,
    base: str,
    min_total: float,
    coverage_config: dict[str, Any] | None = None,
    maintainer_signoff_diffs: set[str] | None = None,
) -> dict[str, Any]:
    if min_total < COVERAGE_MIN_TOTAL:
        raise GateError("threshold reduction is forbidden")
    payload = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    percent = combined_branch_enabled_percent(payload, source_root=source_root)
    if percent < min_total:
        raise GateError(
            f"combined branch-enabled coverage {percent:.4f} < {min_total}"
        )
    config = coverage_config if coverage_config is not None else construct_null_coverage_config()
    assert_clean_coverage_config(config)
    reject_added_pragmas(repo_root=repo_root, source_root=source_root, base=base)
    reject_new_coverage_config(repo_root=repo_root, base=base)

    lane_for_path = {
        "cc-assistant.junit.xml": "cc_assistant",
        "assistant-route.junit.xml": "assistant-route",
        "build-tools.junit.xml": "build_tools",
        "audit-a.junit.xml": "audit-a",
        "future-op.junit.xml": "future-op",
    }
    junit_by_lane: dict[str, dict[str, str]] = {}
    for path in junit_paths:
        lane = lane_for_path.get(path.name)
        if lane is None:
            raise GateError(f"unrecognized JUnit filename: {path.name}")
        junit_by_lane[lane] = parse_junit_node_results(path)
    baseline_results = parse_junit_node_results(baseline_junit)
    cc_results = junit_by_lane.get("cc_assistant") or {}
    require_base_nodes_present(baseline_results=baseline_results, final_results=cc_results)
    require_new_tests_mapped_and_passed(
        repo_root=repo_root,
        base=base,
        junit_by_lane=junit_by_lane,
        maintainer_signoff_diffs=maintainer_signoff_diffs,
    )
    inventory = [p.relative_to(source_root).as_posix() for p in enumerate_production_py(source_root)]
    return {
        "combined_percent": percent,
        "production_files": inventory,
        "min_total": min_total,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan 005 coverage and JUnit gate.")
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--baseline-junit", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--base", default=PLAN005_BASE_COMMIT)
    parser.add_argument("--min-total", type=float, default=float(COVERAGE_MIN_TOTAL))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root or args.source_root.parent.parent
    try:
        outcome = run_gate(
            coverage_json_path=args.coverage_json,
            junit_paths=list(args.junit),
            baseline_junit=args.baseline_junit,
            source_root=args.source_root,
            repo_root=repo_root,
            base=args.base,
            min_total=args.min_total,
        )
    except GateError as exc:
        print(f"plan005_gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
