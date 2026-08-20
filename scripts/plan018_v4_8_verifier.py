#!/usr/bin/env python3
"""Plan 018 V4-8 verifier — reservation gate + paid-run manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from plan018_verifier_support import (
    derive_migration_graph,
    migration_lineage_status,
    summarize_junit,
)
from nextseek_api.eval.seam_inventory import (  # noqa: E402
    build_inventory,
    find_unvisited_paid_run_gated,
)

_REQUIRED_MODULES = (
    "nextseek_api/eval/run_manifest.py",
    "nextseek_api/eval/run_authorization.py",
    "nextseek_api/eval/provider_gate.py",
    "nextseek_api/eval/fake_provider.py",
    "nextseek_api/eval/judging_engine.py",
    "nextseek_api/eval/spend_conservation.py",
    "nextseek_api/eval/reconciliation.py",
    "nextseek_api/eval/paid_run_state.py",
    "nextseek_api/eval/paid_run_schedule.py",
    "nextseek_api/eval/seam_inventory.py",
    "nextseek_api/migrations/0017_paid_run_state.py",
)

_LANE_C_SOURCE_FILES = (
    "nextseek_api/cc_assistant/tests/test_v4_8_gate.py",
    "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py",
    "nextseek_api/cc_assistant/tests/test_v4_8_reconciliation.py",
    "nextseek_api/cc_assistant/tests/test_v4_8_reserve.py",
    "nextseek_api/cc_assistant/tests/test_v4_8_resume.py",
    "nextseek_api/cc_assistant/tests/test_run_authorization.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sidecar", default=str(_REPO / "evidence/plan018-v4-8-verifier.sidecar.json")
    )
    parser.add_argument("--log", default=str(_REPO / "evidence/plan018-v4-8-verifier.log"))
    parser.add_argument(
        "--refresh-inventory",
        action="store_true",
        help="Rewrite provider seam inventory from AST scan before checks",
    )
    args = parser.parse_args()

    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    inventory_path = _REPO / "evidence/plan018-v4-8-provider-seam-inventory.json"
    if args.refresh_inventory:
        inventory_path.write_text(json.dumps(build_inventory(repo_root=_REPO), indent=2) + "\n")

    for sidecar_name in (
        "plan018-v4-8-phase0-publish.json",
        "plan018-v4-8-prereq.json",
        "plan018-v4-8-manifest.sidecar.json",
        "plan018-v4-8-reserve.sidecar.json",
        "plan018-v4-8-gate.sidecar.json",
        "plan018-v4-8-resume.sidecar.json",
        "plan018-v4-8-lane-m.sidecar.json",
    ):
        path = _REPO / "evidence" / sidecar_name
        record(f"evidence_{sidecar_name}", path.is_file(), str(path))
        if path.is_file():
            data = json.loads(path.read_text())
            record(f"{sidecar_name}_gate_pass", data.get("gate") == "PASS", str(data.get("gate")))

    for rel in _REQUIRED_MODULES:
        record(f"module_{rel}", (_REPO / rel).is_file(), rel)

    scanned = build_inventory(repo_root=_REPO)
    record("inventory_ast_derivation", scanned.get("derivation") == "ast_source_scan", scanned.get("derivation", ""))
    record("inventory_exists", inventory_path.is_file(), str(inventory_path))

    on_disk = json.loads(inventory_path.read_text()) if inventory_path.is_file() else {}
    on_disk_names = {s.get("name") for s in on_disk.get("seams", [])}
    scanned_names = {s.get("name") for s in scanned.get("seams", [])}
    missing_from_disk = sorted(scanned_names - on_disk_names)
    record(
        "inventory_covers_ast_sites",
        not missing_from_disk,
        ",".join(missing_from_disk) if missing_from_disk else "all",
    )

    unwired_scanned = find_unvisited_paid_run_gated(scanned)
    record(
        "inventory_no_unvisited_paid_run_gated",
        not unwired_scanned,
        ",".join(unwired_scanned) if unwired_scanned else "all wired",
    )

    unwired_disk = find_unvisited_paid_run_gated(on_disk)
    record(
        "inventory_disk_no_unvisited",
        not unwired_disk,
        ",".join(unwired_disk) if unwired_disk else "all wired",
    )
    record("inventory_status", on_disk.get("status") == "complete", on_disk.get("status", ""))

    gate_src = (_REPO / "nextseek_api/eval/provider_gate.py").read_text()
    record("gate_has_crash_flags", "CRASH_BEFORE_RESERVE" in gate_src, "crash hooks")
    record("gate_calls_reserve", "reserve_budget" in gate_src, "reserve")

    auth_src = (_REPO / "nextseek_api/eval/run_authorization.py").read_text()
    record(
        "approve_manifest_refuses_overrides",
        "override diverges from manifest body" in auth_src,
        "override refusal",
    )

    conservation_src = (_REPO / "nextseek_api/eval/spend_conservation.py").read_text()
    record(
        "conservation_attempt_id_accounting",
        "succeeded_calls" in conservation_src and "failed_calls" in conservation_src,
        "attempt buckets",
    )

    schedule = (_REPO / "nextseek_api/eval/paid_run_schedule.py").read_text()
    record("schedule_refuses_default", "ScheduleRefused" in schedule, "refuse")

    lane_junit = _REPO / "evidence/plan018-v4-8-lane-c.junit.xml"
    record("lane_c_junit_exists", lane_junit.is_file(), str(lane_junit))
    lane_sidecar = _REPO / "evidence/plan018-v4-8-lane-c.sidecar.json"
    record("lane_c_sidecar_exists", lane_sidecar.is_file(), str(lane_sidecar))
    lane_data = json.loads(lane_sidecar.read_text()) if lane_sidecar.is_file() else {}
    if lane_sidecar.is_file():
        record("lane_c_sidecar_gate_pass", lane_data.get("gate") == "PASS", str(lane_data.get("gate")))
        source_hashes = lane_data.get("source_files") or {}
        source_mismatches = [
            rel
            for rel in _LANE_C_SOURCE_FILES
            if source_hashes.get(rel) != _sha(_REPO / rel)
        ]
        record(
            "lane_c_sources_match_evidence",
            set(source_hashes) == set(_LANE_C_SOURCE_FILES) and not source_mismatches,
            ",".join(source_mismatches) if source_mismatches else "all",
        )
        counts = lane_data.get("counts") or {}
        record(
            "lane_c_sidecar_counts_exact",
            counts
            == {
                "tests": 35,
                "passed": 35,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "expected_deselected": 4,
                "unexpected_deselected": 0,
            },
            json.dumps(counts, sort_keys=True),
        )
        record(
            "lane_c_junit_hash_matches_sidecar",
            lane_junit.is_file() and lane_data.get("junit_sha256") == _sha(lane_junit),
            str(lane_data.get("junit_sha256")),
        )
    if lane_junit.is_file():
        try:
            summary = summarize_junit(lane_junit)
            record(
                "lane_c_junit_all_passed",
                summary.tests == 35
                and summary.failures == 0
                and summary.errors == 0
                and summary.skipped == 0,
                (
                    f"tests={summary.tests},failures={summary.failures},"
                    f"errors={summary.errors},skipped={summary.skipped},suites={summary.suites}"
                ),
            )
        except ElementTree.ParseError as exc:
            record("lane_c_junit_all_passed", False, f"invalid junit: {exc}")

    graph = derive_migration_graph(_REPO / "nextseek_api/migrations")
    lineage = migration_lineage_status(graph, "0017_paid_run_state")
    current_leaf = lineage.leaf or ""
    record(
        "migration_graph_unique_leaf",
        lineage.error is None,
        current_leaf if lineage.error is None else lineage.error,
    )
    record(
        "migration_0017_in_on_disk_lineage",
        lineage.required_is_ancestor,
        ",".join(sorted(graph.ancestors_of(current_leaf))) if current_leaf else "",
    )

    leaf = _REPO / "evidence/plan018-migration-leaf.json"
    record("migration_leaf_evidence_exists", leaf.is_file(), str(leaf))
    if leaf.is_file():
        data = json.loads(leaf.read_text())
        files = set(data.get("files") or [])
        record(
            "migration_leaf_evidence_matches_on_disk",
            data.get("leaf") == f"{current_leaf}.py" and "0017_paid_run_state.py" in files,
            data.get("leaf", ""),
        )

    sidecar = {
        "schema": "plan018-v4-8-verifier/v1",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": "PASS" if not errors else "FAIL",
        "checks_passed": sum(1 for c in checks if c.get("pass")),
        "checks_total": len(checks),
        "errors": errors,
        "checks": checks,
        "hashes": {
            "run_manifest_sha256": _sha(_REPO / "nextseek_api/eval/run_manifest.py"),
            "provider_gate_sha256": _sha(_REPO / "nextseek_api/eval/provider_gate.py"),
            "migration_0017_sha256": _sha(_REPO / "nextseek_api/migrations/0017_paid_run_state.py"),
            "seam_inventory_sha256": _sha(_REPO / "nextseek_api/eval/seam_inventory.py"),
        },
        "paid_or_live_resources_used": False,
    }
    Path(args.sidecar).write_text(json.dumps(sidecar, indent=2) + "\n")
    Path(args.log).write_text(
        f"V4-8 verifier: {sidecar['checks_passed']}/{sidecar['checks_total']} PASS\n"
        + ("\n".join(errors) if errors else "all checks passed\n")
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
