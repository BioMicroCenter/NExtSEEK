#!/usr/bin/env python3
"""Run and authenticate the Plan 018 V4-9 clean-checkout final verifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan018_v4_9_task8_deploy as task8  # noqa: E402


SCHEMA = "plan018-v4-9-task9-verifier/v1"
RUN_EVIDENCE = "evidence/plan018-v4-9-task9-verifier.json"
COLD_REVIEW = "evidence/plan018-v4-9-cold-outcome-review.md"
PREREQUISITE = "evidence/plan018-v4-9-prereq.json"
GLOBAL_COVERAGE = "evidence/plan018-v4-9-global-coverage.json"
TASK5_EVIDENCE = "evidence/plan018-v4-9-task5-evidence.json"
TASK6_EVIDENCE = "evidence/plan018-v4-9-task6-evidence.json"
TASK7_EVIDENCE = "evidence/plan018-v4-9-task7-evidence.json"
SDD_DIR = Path(".superpowers/sdd/2026-08-13-plan018-v4-9")
MAX_WALL_S = 300.0

COLD_PROMPT = (
    "(Execution is complete. Evaluate the actual outcome against the original spec and each task's "
    "stated success conditions. For each task\u00a0: mark it pass, partial, or fail, and explain why. "
    "Identify any success conditions that were satisfied technically but not in spirit. Produce a "
    "final verdict on whether my original will was carried out, and flag any residual debt \u2014 things "
    "that technically work but shouldn't be left as-is.)"
)


@dataclass(frozen=True)
class ValidatorSpec:
    name: str
    argv: tuple[str, ...]
    success_marker: str


VALIDATORS = (
    ValidatorSpec("task1-owned-surface", ("scripts/plan018_v4_9_owned_surface.py", "validate", "--current"), "PASS: owned-surface manifest"),
    ValidatorSpec("task2-coverage", ("scripts/plan018_v4_9_task2_coverage.py", "validate"), "Task 2 evidence PASS"),
    ValidatorSpec("task3-coverage", ("scripts/plan018_v4_9_task3_coverage.py", "validate"), "Task 3 evidence PASS"),
    ValidatorSpec("task4-coverage", ("scripts/plan018_v4_9_task4_coverage.py", "validate"), "Task 4 evidence PASS"),
    ValidatorSpec("task5-mutation", ("scripts/plan018_v4_9_task5_mutation.py", "validate"), "Task 5 evidence PASS"),
    ValidatorSpec("task6-replay", ("scripts/plan018_v4_9_task6_replay.py", "validate"), "Task 6 evidence PASS"),
    ValidatorSpec("task7-deploy-recovery", ("scripts/plan018_v4_9_task7_recovery.py", "validate"), "PASS: Task 7 deploy-record, mixed-version, coverage, and mutation gate"),
    ValidatorSpec("task8-operational", ("scripts/plan018_v4_9_task8_deploy.py", "validate"), "Task 8 evidence PASS"),
    ValidatorSpec("global-coverage", ("scripts/plan018_v4_9_global_coverage.py", "validate"), "Plan 018 V4-9 global coverage PASS"),
)

CONTROL_FILES = (
    "docs/superpowers/plans/2026-08-13-plan018-v4-9.md",
    "scripts/plan018_v4_9_task9_verifier.py",
    "scripts/test_plan018_v4_9_task9_verifier.py",
    *(spec.argv[0] for spec in VALIDATORS),
    PREREQUISITE,
    "evidence/plan018-v4-9-owned-surface.json",
    "evidence/plan018-v4-9-task2-evidence.json",
    "evidence/plan018-v4-9-task3-evidence.json",
    "evidence/plan018-v4-9-task4-evidence.json",
    TASK5_EVIDENCE,
    TASK6_EVIDENCE,
    TASK7_EVIDENCE,
    task8.EVIDENCE,
    GLOBAL_COVERAGE,
)

EXPECTED_PREREQ = {
    "V4-2": "22/22",
    "V4-3": "14/14",
    "V4-4": "13/13",
    "V4-5": "22/22",
    "V4-6": "28/28",
    "V4-7": "33/33",
    "V4-8": "47/47",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _json(root: Path, relative: str, errors: list[str]) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        errors.append(f"missing evidence artifact: {relative}")
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"malformed evidence artifact {relative}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"evidence artifact is not an object: {relative}")
        return {}
    return value


def prerequisite_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    data = _json(root, PREREQUISITE, errors)
    if data.get("schema") != "plan018-v4-9-prereq/v1" or data.get("paid_or_live_resources_used") is not False:
        errors.append("Task 0 prerequisite envelope is not exact zero-effect v1 evidence")
    results = data.get("verifier_results")
    if not isinstance(results, dict) or set(results) != set(EXPECTED_PREREQ):
        errors.append("Task 0 prerequisite verifier inventory is not exact V4-2 through V4-8")
        results = {}
    for gate, expected in EXPECTED_PREREQ.items():
        result = results.get(gate)
        if not isinstance(result, dict) or result.get("gate") != "PASS" or result.get("checks") != expected:
            errors.append(f"Task 0 prerequisite {gate} is not {expected} PASS")
            continue
        path = result.get("path")
        sidecar = _json(root, path, errors) if isinstance(path, str) else {}
        if (
            sidecar.get("gate") != "PASS"
            or f"{sidecar.get('checks_passed')}/{sidecar.get('checks_total')}" != expected
        ):
            errors.append(f"Task 0 prerequisite sidecar {gate} disagrees with rollup")
    lineage = data.get("migration_lineage")
    if (
        not isinstance(lineage, dict)
        or not lineage.get("leaf")
        or not lineage.get("v4_7_membership")
        or not lineage.get("v4_8_membership")
        or lineage.get("policy") != "one current leaf with each prerequisite migration in its ancestry"
    ):
        errors.append("Task 0 forward migration lineage/membership is incomplete")
    if (data.get("v4_0") or {}).get("status") != "CLOSED" or (data.get("v4_1") or {}).get("gate") != "PASS":
        errors.append("Task 0 V4-0/V4-1 prerequisite closure is not PASS")
    return errors


def _coverage_errors(root: Path) -> list[str]:
    errors: list[str] = []
    data = _json(root, GLOBAL_COVERAGE, errors)
    aggregate = data.get("aggregate") or {}
    modules = data.get("modules")
    if data.get("gate") != "PASS" or len(data.get("critical_modules") or []) != 47:
        errors.append("global coverage inventory/gate is not exact 47-module PASS")
    if (
        aggregate.get("statements", 0) <= 0
        or aggregate.get("branches", 0) <= 0
        or aggregate.get("statement_pct", 0) < 95.0
        or aggregate.get("branch_pct", 0) < 95.0
    ):
        errors.append("global aggregate does not clear both 95% floors")
    if not isinstance(modules, dict) or set(modules) != set(data.get("critical_modules") or []):
        errors.append("global per-module coverage inventory is incomplete")
    else:
        for path, result in modules.items():
            if (
                result.get("statements", 0) <= 0
                or result.get("statement_pct", 0) < 95.0
                or result.get("branch_pct", 0) < 95.0
                or (
                    result.get("branches", 0) <= 0
                    and result.get("branch_floor_applicable") is not False
                )
                or (
                    result.get("branches", 0) > 0
                    and result.get("branch_floor_applicable") is not True
                )
            ):
                errors.append(f"critical module misses a positive 95% floor: {path}")
    return errors


def static_preflight_errors(root: Path = ROOT) -> list[str]:
    root = root.resolve()
    errors = prerequisite_errors(root)
    errors.extend(_coverage_errors(root))
    task5_data = _json(root, TASK5_EVIDENCE, errors)
    if task5_data.get("gate") != "PASS" or (task5_data.get("manifest_counts") or {}).get("mutants") != 76:
        errors.append("Task 5 evidence is not exact 76-mutant PASS")
    task6_data = _json(root, TASK6_EVIDENCE, errors)
    if task6_data.get("gate") != "PASS" or task6_data.get("provider_calls") != 0:
        errors.append("Task 6 replay evidence is not zero-provider PASS")
    task7_data = _json(root, TASK7_EVIDENCE, errors)
    if (
        task7_data.get("gate") != "PASS"
        or (task7_data.get("mutations") or {}).get("killed") != 3
        or (task7_data.get("coverage") or {}).get("aggregate", {}).get("statement_percent", 0) < 95
        or (task7_data.get("coverage") or {}).get("aggregate", {}).get("branch_percent", 0) < 95
    ):
        errors.append("Task 7 deploy/recovery evidence is not coverage+mutation PASS")
    task8_errors = task8.validation_errors(root, root / task8.EVIDENCE)
    errors.extend(f"Task 8 evidence: {error}" for error in task8_errors)
    missing_controls = [path for path in CONTROL_FILES if not (root / path).is_file()]
    if missing_controls:
        errors.append("final-verifier controls missing: " + ",".join(missing_controls))
    return errors


def _command_record(spec: ValidatorSpec, *, returncode: int = 0) -> dict[str, Any]:
    return {
        "name": spec.name,
        "argv": ["python", *spec.argv],
        "returncode": returncode,
        "success_marker": spec.success_marker,
        "success_marker_observed": returncode == 0,
        "stdout_sha256": hashlib.sha256(spec.success_marker.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        "duration_s": 0.01,
    }


def synthetic_run_evidence(*, head: str, tree: str, controls: dict[str, str]) -> dict[str, Any]:
    """Build a structurally valid unit-test fixture; never writes a gate artifact."""
    return {
        "schema": SCHEMA,
        "gate": "PASS",
        "subject": {"head": head, "tree": tree, "clean_at_start": True},
        "controls_sha256": controls,
        "commands": [_command_record(spec) for spec in VALIDATORS],
        "command_count": len(VALIDATORS),
        "static_preflight": "PASS",
        "wall_s": 1.0,
        "wall_cap_s": MAX_WALL_S,
        "external_effects": {
            "provider_calls": 0,
            "paid_resources": False,
            "deployment_mutations": False,
            "database_mutations": False,
            "registry_writes": False,
        },
    }


def run(subject: Path, output: Path) -> None:
    subject = subject.resolve()
    output = output.resolve()
    if output.is_relative_to(subject):
        raise RuntimeError("Task 9 output must be outside the clean subject checkout")
    status = _git(subject, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode or status.stdout:
        raise RuntimeError("Task 9 subject is not a clean checkout")
    head_result = _git(subject, "rev-parse", "HEAD")
    tree_result = _git(subject, "rev-parse", "HEAD^{tree}")
    if head_result.returncode or tree_result.returncode:
        raise RuntimeError("Task 9 subject git identity is unavailable")
    preflight = static_preflight_errors(subject)
    if preflight:
        raise RuntimeError("Task 9 static preflight failed: " + "; ".join(preflight))

    started = time.monotonic()
    commands: list[dict[str, Any]] = []
    for spec in VALIDATORS:
        argv = [sys.executable, *spec.argv]
        command_started = time.monotonic()
        result = subprocess.run(
            argv, cwd=subject, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        duration = time.monotonic() - command_started
        observed = spec.success_marker in result.stdout
        commands.append(
            {
                "name": spec.name,
                "argv": ["python", *spec.argv],
                "returncode": result.returncode,
                "success_marker": spec.success_marker,
                "success_marker_observed": observed,
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
                "duration_s": round(duration, 3),
            }
        )
        if result.returncode or not observed:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            raise RuntimeError(f"Task 9 validator failed: {spec.name}")
    elapsed = time.monotonic() - started
    if elapsed > MAX_WALL_S:
        raise RuntimeError(f"Task 9 exceeded {MAX_WALL_S:.0f}s wall cap")
    controls = {path: sha256(subject / path) for path in CONTROL_FILES}
    payload = synthetic_run_evidence(
        head=head_result.stdout.strip(), tree=tree_result.stdout.strip(), controls=controls,
    )
    payload.update(
        commands=commands,
        command_count=len(commands),
        wall_s=round(elapsed, 3),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_evidence_errors(root: Path = ROOT, *, check_static: bool = True) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    data = _json(root, RUN_EVIDENCE, errors)
    if not data:
        return [f"Task 9 verifier evidence missing or malformed: {RUN_EVIDENCE}", *errors]
    expected_keys = {
        "schema", "gate", "subject", "controls_sha256", "commands",
        "command_count", "static_preflight", "wall_s", "wall_cap_s",
        "external_effects",
    }
    if set(data) != expected_keys or data.get("schema") != SCHEMA or data.get("gate") != "PASS":
        errors.append("Task 9 verifier evidence envelope is not exact PASS")
    subject = data.get("subject") or {}
    if (
        set(subject) != {"head", "tree", "clean_at_start"}
        or not re.fullmatch(r"[0-9a-f]{40}", str(subject.get("head", "")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(subject.get("tree", "")))
        or subject.get("clean_at_start") is not True
    ):
        errors.append("Task 9 clean-checkout subject identity is malformed")
    elif check_static:
        commit = _git(root, "cat-file", "-e", f"{subject['head']}^{{commit}}")
        tree = _git(root, "rev-parse", f"{subject['head']}^{{tree}}")
        if commit.returncode or tree.returncode or tree.stdout.strip() != subject["tree"]:
            errors.append("Task 9 clean-checkout subject identity is not resolvable")
    controls = data.get("controls_sha256")
    if not isinstance(controls, dict) or (check_static and set(controls) != set(CONTROL_FILES)):
        errors.append("Task 9 control inventory is not exact")
        controls = controls if isinstance(controls, dict) else {}
    for relative, expected in controls.items():
        path = root / relative
        if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or sha256(path) != expected:
            errors.append(f"Task 9 control hash drift: {relative}")
    commands = data.get("commands")
    if not isinstance(commands, list):
        commands = []
    if data.get("command_count") != len(VALIDATORS) or len(commands) != len(VALIDATORS):
        errors.append("Task 9 validator command inventory/count is not exact")
    for index, spec in enumerate(VALIDATORS):
        command = commands[index] if index < len(commands) and isinstance(commands[index], dict) else {}
        if (
            command.get("name") != spec.name
            or command.get("argv") != ["python", *spec.argv]
            or command.get("returncode") != 0
            or command.get("success_marker") != spec.success_marker
            or command.get("success_marker_observed") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", str(command.get("stdout_sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(command.get("stderr_sha256", "")))
            or not isinstance(command.get("duration_s"), (int, float))
            or isinstance(command.get("duration_s"), bool)
            or command.get("duration_s", -1) < 0
        ):
            errors.append(f"Task 9 validator command is not exact PASS: {spec.name}")
    if data.get("static_preflight") != "PASS":
        errors.append("Task 9 static preflight attestation is not PASS")
    if check_static:
        errors.extend(static_preflight_errors(root))
    if (
        data.get("wall_cap_s") != MAX_WALL_S
        or not isinstance(data.get("wall_s"), (int, float))
        or isinstance(data.get("wall_s"), bool)
        or not 0 <= data.get("wall_s", -1) <= MAX_WALL_S
    ):
        errors.append("Task 9 verifier exceeded or omitted its wall cap")
    if data.get("external_effects") != {
        "provider_calls": 0,
        "paid_resources": False,
        "deployment_mutations": False,
        "database_mutations": False,
        "registry_writes": False,
    }:
        errors.append("Task 9 verifier is not exact read-only/zero-spend scope")
    return errors


def cold_review_errors(text: str) -> list[str]:
    errors: list[str] = []
    required_lines = {
        "reviewer_kind: cold_subagent": "reviewer_kind",
        "prompt_verbatim: true": "prompt_verbatim",
    }
    for line, label in required_lines.items():
        if line not in text.splitlines():
            errors.append(f"cold outcome review lacks exact {label} provenance")
    subagent = re.search(r"(?m)^subagent_id:\s*(\S+)\s*$", text)
    parent = re.search(r"(?m)^parent_transcript_id:\s*(\S+)\s*$", text)
    if not subagent or not subagent.group(1):
        errors.append("cold outcome review lacks subagent_id provenance")
    if not parent or not parent.group(1):
        errors.append("cold outcome review lacks parent_transcript_id provenance")
    if not re.search(r"(?is)prior implementer-written.*\bVOID\b", text):
        errors.append("cold outcome review lacks prior implementer-written VOID statement")
    if f"> {COLD_PROMPT}\n" not in text:
        errors.append("cold outcome review does not contain the exact verbatim prompt")
    for task in range(10):
        matches = re.findall(rf"(?im)^.*\bTask\s+{task}\b.*$", text)
        grade_lines = [line for line in matches if re.search(r"\b(PASS|PARTIAL|FAIL)\b", line, re.I)]
        if len(grade_lines) != 1 or not re.search(r"\bPASS\b", grade_lines[0], re.I):
            errors.append(f"cold outcome review does not grade Task {task} exactly PASS")
    verdicts = re.findall(r"(?im)^\s*Final verdict:\s*(PASS|PARTIAL|FAIL)\s*$", text)
    if verdicts != ["PASS"]:
        errors.append("cold outcome review final verdict is not exactly PASS")
    return errors


def status_surface_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    directory = root / SDD_DIR
    progress = directory / "progress.md"
    if not progress.is_file():
        return ["Task 9 progress status surface is missing"]
    text = progress.read_text()
    for task in range(10):
        if not re.search(rf"(?im)^.*Task\s+{task}:\s*(?:complete|PASS)\b", text):
            errors.append(f"Task 9 progress surface does not mark Task {task} complete")
    if not re.search(r"(?im)^.*Cold outcome review:\s*PASS\b", text):
        errors.append("Task 9 progress surface does not match cold PASS")
    for task in range(10):
        report = directory / f"task-{task}-report.md"
        if not report.is_file() or not re.search(r"(?im)^.*Status:\s*PASS\b", report.read_text()):
            errors.append(f"Task {task} report status is absent or not PASS")
    return errors


def final_errors(root: Path = ROOT) -> list[str]:
    root = root.resolve()
    errors = run_evidence_errors(root)
    review = root / COLD_REVIEW
    if not review.is_file():
        errors.append(f"cold outcome review is missing: {COLD_REVIEW}")
    else:
        errors.extend(cold_review_errors(review.read_text()))
    errors.extend(status_surface_errors(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--subject", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, default=ROOT / RUN_EVIDENCE)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--root", type=Path, default=ROOT)
    final_parser = sub.add_parser("validate-final")
    final_parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.command == "run":
        run(args.subject, args.output)
        print("Task 9 clean-checkout verifier PASS")
        return 0
    errors = run_evidence_errors(args.root) if args.command == "validate" else final_errors(args.root)
    print("Task 9 " + ("verifier" if args.command == "validate" else "final") + " " + ("PASS" if not errors else "FAIL"))
    for error in errors:
        print("- " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
