#!/usr/bin/env python3
"""Plan 018 V4-7 verifier — experimental/observational separation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_REQUIRED_MODULES = (
    "nextseek_api/eval/evidence_kinds.py",
    "nextseek_api/eval/paired_run.py",
    "nextseek_api/eval/online_observation.py",
    "nextseek_api/eval/fit/fit_boundary.py",
    "nextseek_api/eval/paired_run_registry.py",
    "nextseek_api/cc_assistant/route_monitoring.py",
    "nextseek_api/migrations/0016_paired_run_registry.py",
)

_INVENTORY_SEAMS = (
    "build_fit_admission",
    "build_pair_rows",
    "run_v14_generation",
    "publish",
    "publish_generation",
    "activate_generation",
    "export_observational_rows",
    "build_route_monitoring_summary",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", default=str(_REPO / "evidence/plan018-v4-7-verifier.sidecar.json"))
    parser.add_argument("--log", default=str(_REPO / "evidence/plan018-v4-7-verifier.log"))
    args = parser.parse_args()

    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    for sidecar_name in (
        "plan018-v4-7-prereq.json",
        "plan018-v4-7-schemas.sidecar.json",
        "plan018-v4-7-fit-refuse.sidecar.json",
        "plan018-v4-7-registry.sidecar.json",
        "plan018-v4-7-monitoring.sidecar.json",
        "plan018-v4-7-lane-m.sidecar.json",
    ):
        path = _REPO / "evidence" / sidecar_name
        record(f"evidence_{sidecar_name}", path.is_file(), str(path))
        if path.is_file():
            data = json.loads(path.read_text())
            record(f"{sidecar_name}_gate_pass", data.get("gate") == "PASS", str(data.get("gate")))

    for rel in _REQUIRED_MODULES:
        record(f"module_{rel}", (_REPO / rel).is_file(), rel)

    inventory = _REPO / "evidence/plan018-v4-7-seam-inventory.json"
    record("inventory_exists", inventory.is_file(), str(inventory))
    if inventory.is_file():
        inv = json.loads(inventory.read_text())
        seams = {s.get("name") for s in inv.get("seams", [])}
        missing = sorted(set(_INVENTORY_SEAMS) - seams)
        record("inventory_complete", not missing, ",".join(missing) if missing else "all")
        record("inventory_status", inv.get("status") == "complete", inv.get("status", ""))

    ps = (_REPO / "nextseek_api/cc_assistant/posterior_selector.py").read_text()
    record(
        "posterior_flag_default_off",
        'getattr(settings, "NEXTSEEK_POSTERIOR_ROUTING_ENABLED", False)' in ps,
        "default False",
    )

    overlay = (_REPO / "nextseek_api/cc_assistant/risk_overlay.py").read_text()
    assignments = re.findall(r"may_reroute\s*=\s*(True|False)", overlay)
    record("may_reroute_only_false", assignments and all(v == "False" for v in assignments), ",".join(assignments))

    monitoring = (_REPO / "nextseek_api/cc_assistant/route_monitoring.py").read_text()
    record("monitoring_no_publish_import", "publish" not in monitoring and "activate_generation" not in monitoring, "grep")

    boundary = (_REPO / "nextseek_api/eval/fit/fit_boundary.py").read_text()
    record("boundary_validate_publish", "validate_publish_provenance" in boundary, "fn")
    record("boundary_require_approved", "require_approved_paired_run" in boundary, "fn")

    lane_log = _REPO / "evidence/plan018-v4-7-lane-c.log"
    record("lane_c_log_exists", lane_log.is_file(), str(lane_log))
    if lane_log.is_file():
        text = lane_log.read_text()
        record("lane_c_all_passed", " passed" in text and " failed" not in text.split("\n")[-3:], text.strip().split("\n")[-1])

    leaf = _REPO / "evidence/plan018-migration-leaf.json"
    if leaf.is_file():
        data = json.loads(leaf.read_text())
        record("migration_leaf_0016", "0016_paired_run_registry" in data.get("leaf", ""), data.get("leaf", ""))

    sidecar = {
        "schema": "plan018-v4-7-verifier/v1",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": "PASS" if not errors else "FAIL",
        "checks_passed": sum(1 for c in checks if c.get("pass")),
        "checks_total": len(checks),
        "errors": errors,
        "checks": checks,
        "hashes": {
            "evidence_kinds_sha256": _sha(_REPO / "nextseek_api/eval/evidence_kinds.py"),
            "fit_boundary_sha256": _sha(_REPO / "nextseek_api/eval/fit/fit_boundary.py"),
            "migration_0016_sha256": _sha(_REPO / "nextseek_api/migrations/0016_paired_run_registry.py"),
        },
        "paid_or_live_resources_used": False,
    }
    Path(args.sidecar).write_text(json.dumps(sidecar, indent=2) + "\n")
    Path(args.log).write_text(
        f"V4-7 verifier: {sidecar['checks_passed']}/{sidecar['checks_total']} PASS\n"
        + ("\n".join(errors) if errors else "all checks passed\n")
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
