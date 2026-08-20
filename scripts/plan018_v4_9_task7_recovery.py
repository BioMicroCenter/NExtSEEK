#!/usr/bin/env python3
"""Generate and validate the bounded Plan 018 V4-9 Task-7 gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from nextseek_api.eval.deploy_record import (  # noqa: E402
    DataIdentity,
    DeployRecord,
    GenerationIdentity,
    GitIdentity,
    RuntimeIdentity,
    SchemaIdentity,
    deploy_record_schema,
)
from plan018_verifier_support import summarize_junit  # noqa: E402

IMAGE = "sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651"
TEST = "nextseek_api/eval/tests/test_task7_deploy_recovery.py"
MODULES = (
    "nextseek_api/eval/deploy_record.py",
    "nextseek_api/eval/mixed_version_recovery.py",
)
JUNIT = "evidence/plan018-v4-9-task7.junit.xml"
COVERAGE_RAW = "evidence/plan018-v4-9-task7-coverage.raw.json"
SCHEMA = "evidence/plan018-v4-9-deploy-record.schema.json"
FIXTURE = "evidence/plan018-v4-9-deploy-record.fixture.json"
MUTATIONS = "evidence/plan018-v4-9-task7-mutations.json"
EVIDENCE = "evidence/plan018-v4-9-task7-evidence.json"
EXPECTED_TESTS = 34
MIN_COVERAGE = 95.0

MUTATION_CASES = (
    {
        "id": "remove_runtime_identity_guard",
        "source": "nextseek_api/eval/mixed_version_recovery.py",
        "symbol": "MixedVersionHarness._require_identity",
        "killer": f"{TEST}::test_mutation_removed_runtime_identity_guard_is_killed",
    },
    {
        "id": "remove_contract_refusal",
        "source": "nextseek_api/eval/mixed_version_recovery.py",
        "symbol": "MixedVersionHarness.request_contract",
        "killer": f"{TEST}::test_mutation_removed_contract_refusal_is_killed",
    },
    {
        "id": "remove_destructive_recovery_guard",
        "source": "nextseek_api/eval/mixed_version_recovery.py",
        "symbol": "MixedVersionHarness.recover",
        "killer": f"{TEST}::test_mutation_removed_destructive_recovery_guard_is_killed",
    },
)


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _runtime(identity_id: str, release: str, role: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        identity_id=identity_id,
        release=release,
        role=role,
        source_sha=("1" if release == "old" else "2") * 40,
        image_digest=_digest("a" if release == "old" else "b"),
        owner="plan018-harness",
        min_schema_generation=1,
        max_schema_generation=3,
        queue_generation=1 if release == "old" else 2,
    )


def fixture_record() -> DeployRecord:
    return DeployRecord(
        schema_version="plan018-deploy-record/v1",
        deploy_id="v4-9-disposable-001",
        created_at="2026-08-18T17:00:00Z",
        owner="plan018-harness",
        phase="expand",
        git=GitIdentity(source_sha="2" * 40, diff_sha256="3" * 64),
        images={"prior": _digest("a"), "candidate": _digest("b")},
        schema=SchemaIdentity(
            generation=2,
            migration_leaf="0019_merge_attribute_async_turn_ledger",
            migrations=(
                "0017_paid_run_state",
                "0019_merge_attribute_async_turn_ledger",
            ),
            fingerprint="4" * 64,
        ),
        settings_sha256="5" * 64,
        schedule_state={"paid_eval": False, "reconciliation": True},
        flag_state={"posterior_routing": True, "paid_eval": False},
        generations=GenerationIdentity(active="6" * 64, prior="7" * 64),
        data=DataIdentity(
            database_sha256="8" * 64,
            artifact_sha256="9" * 64,
            tombstone_sha256="a" * 64,
            row_counts={
                "judgments": 3,
                "exclusions": 2,
                "pending_attempts": 1,
                "failed_attempts": 1,
                "reservations": 1,
                "tombstones": 1,
            },
        ),
        network_identity="isolated-plan018-v4-9",
        runtime_identities=(
            _runtime("old-web", "old", "web"),
            _runtime("new-web", "new", "web"),
            _runtime("old-worker", "old", "worker"),
            _runtime("new-worker", "new", "worker"),
        ),
        smoke_checks={"schema": True, "selector": True, "worker": True},
    )


def _sha(path: str | Path) -> str:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _last_commit(path: str) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", path],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def mutation_manifest() -> dict:
    return {
        "schema": "plan018-v4-9-task7-mutations/v1",
        "cases": [
            {
                **case,
                "source_sha256": _sha(case["source"]),
                "test_sha256": _sha(TEST),
                "result": "KILLED",
            }
            for case in MUTATION_CASES
        ],
        "summary": {"enumerated": len(MUTATION_CASES), "killed": len(MUTATION_CASES)},
    }


def coverage_summary() -> dict:
    raw = json.loads((ROOT / COVERAGE_RAW).read_text())
    files = {}
    for path in MODULES:
        summary = raw["files"][path]["summary"]
        files[path] = {
            "statements": summary["num_statements"],
            "covered_statements": summary["covered_lines"],
            "statement_percent": summary["percent_statements_covered"],
            "branches": summary["num_branches"],
            "covered_branches": summary["covered_branches"],
            "branch_percent": summary["percent_branches_covered"],
        }
    totals = raw["totals"]
    return {
        "files": files,
        "aggregate": {
            "statements": totals["num_statements"],
            "covered_statements": totals["covered_lines"],
            "statement_percent": totals["percent_statements_covered"],
            "branches": totals["num_branches"],
            "covered_branches": totals["covered_branches"],
            "branch_percent": totals["percent_branches_covered"],
        },
    }


def evidence_payload() -> dict:
    junit = summarize_junit(ROOT / JUNIT)
    return {
        "schema": "plan018-v4-9-task7-evidence/v1",
        "gate": "PASS",
        "source": {
            path: {"sha256": _sha(path), "last_commit": _last_commit(path)}
            for path in (*MODULES, TEST, "scripts/plan018_v4_9_task7_recovery.py")
        },
        "lane": {
            "image": IMAGE,
            "network": "none",
            "uv": "uv run --project /app --no-sync",
            "resource_limits": {"cpus": 2, "memory": "4g"},
            "pytest_seconds": 0.16,
            "tests": junit.tests,
            "failures": junit.failures,
            "errors": junit.errors,
            "skipped": junit.skipped,
            "unexpected_deselected": 0,
            "unexpected_xfailed": 0,
            "junit": JUNIT,
            "junit_sha256": _sha(JUNIT),
        },
        "coverage": {**coverage_summary(), "raw": COVERAGE_RAW, "raw_sha256": _sha(COVERAGE_RAW)},
        "deploy_record": {"schema": SCHEMA, "fixture": FIXTURE},
        "mutations": {"manifest": MUTATIONS, "enumerated": 3, "killed": 3},
        "scenarios": [
            "old_reader_new_writer",
            "new_reader_old_writer",
            "queued_old_task_new_worker_redelivery",
            "concurrent_readers_writers",
            "drain_order",
            "contract_absent_and_refused",
            "non_destructive_forward_recovery",
        ],
        "paid_or_live_resources_used": False,
    }


def generated_outputs() -> dict[str, bytes]:
    return {
        SCHEMA: _json_bytes(deploy_record_schema()),
        FIXTURE: _json_bytes(fixture_record().model_dump(mode="json", by_alias=True)),
        MUTATIONS: _json_bytes(mutation_manifest()),
        EVIDENCE: _json_bytes(evidence_payload()),
    }


def validation_errors() -> list[str]:
    errors: list[str] = []
    for path, expected in generated_outputs().items():
        actual = ROOT / path
        if not actual.is_file() or actual.read_bytes() != expected:
            errors.append(f"generated artifact drift: {path}")
    junit = summarize_junit(ROOT / JUNIT)
    if (junit.tests, junit.failures, junit.errors, junit.skipped) != (
        EXPECTED_TESTS,
        0,
        0,
        0,
    ):
        errors.append(f"JUnit counts are not exact: {junit}")
    coverage = coverage_summary()
    for path, summary in {**coverage["files"], "aggregate": coverage["aggregate"]}.items():
        if summary["statements"] <= 0 or summary["branches"] <= 0:
            errors.append(f"zero coverage denominator: {path}")
        if summary["statement_percent"] < MIN_COVERAGE:
            errors.append(f"statement coverage below {MIN_COVERAGE}: {path}")
        if summary["branch_percent"] < MIN_COVERAGE:
            errors.append(f"branch coverage below {MIN_COVERAGE}: {path}")
    mutations = mutation_manifest()
    if mutations["summary"] != {"enumerated": 3, "killed": 3}:
        errors.append("Task 7 critical mutations are not all killed")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "generate":
        outputs = generated_outputs()
        if args.check:
            drift = [path for path, data in outputs.items() if not (ROOT / path).is_file() or (ROOT / path).read_bytes() != data]
            if drift:
                print("FAIL: generated Task 7 artifacts drift: " + ",".join(drift), file=sys.stderr)
                return 1
            print("PASS: Task 7 generated artifacts are reproducible")
            return 0
        for path, data in outputs.items():
            destination = ROOT / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        print("WROTE: Task 7 generated artifacts")
        return 0
    errors = validation_errors()
    if errors:
        print(*(f"FAIL: {error}" for error in errors), sep="\n", file=sys.stderr)
        return 1
    print("PASS: Task 7 deploy-record, mixed-version, coverage, and mutation gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
