#!/usr/bin/env python3
"""Plan 018 V4-8 verifier — reservation gate + paid-run manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

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

    lane_log = _REPO / "evidence/plan018-v4-8-lane-c.log"
    record("lane_c_log_exists", lane_log.is_file(), str(lane_log))
    if lane_log.is_file():
        text = lane_log.read_text()
        record(
            "lane_c_all_passed",
            " passed" in text and " failed" not in text,
            text.strip().split("\n")[-1],
        )

    leaf = _REPO / "evidence/plan018-migration-leaf.json"
    if leaf.is_file():
        data = json.loads(leaf.read_text())
        record("migration_leaf_0017", "0017_paid_run_state" in data.get("leaf", ""), data.get("leaf", ""))

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
