#!/usr/bin/env python3
"""Plan 018 V4-4 verifier — hermetic replay and decision contract checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

V13A_ZIP = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07/testquestions.zip")
V13A_SHA = "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814"

from nextseek_api.eval.fit.v14.combined import run_v14_generation
from nextseek_api.eval.fit.v14.decision import DecisionStatus, retained_support_ok, quality_discordance_ok
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, config_fingerprint
from nextseek_api.eval.fit.v14.recovery_acceptance import evaluate_recovery_results
from nextseek_api.eval.fit.v14.recovery_matrix import (
    RECOVERY_SCENARIOS,
    RecoveryScenario,
    build_scenario_rows,
    ground_truth,
    matrix_fingerprint,
)


def main() -> int:
    checks: list[dict] = []
    errors: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}")

    record("v13a_zip_exists", V13A_ZIP.is_file(), str(V13A_ZIP))
    if V13A_ZIP.is_file():
        sha = hashlib.sha256(V13A_ZIP.read_bytes()).hexdigest()
        record("v13a_zip_sha", sha == V13A_SHA, sha)

    cfg = V14FitConfig()
    fp = config_fingerprint(cfg)
    record("config_fingerprint", len(fp) == 64, fp[:16])

    rows = build_scenario_rows(RecoveryScenario.ns_strong_quality)
    fit = run_v14_generation(rows, cfg, seed=11, use_mcmc=False)
    record(
        "strong_quality_decision",
        any(c.status == DecisionStatus.quality_ns for c in fit.decision.candidates),
        fit.decision.generation_status,
    )
    record("cost_excluded_from_fit", all(r.cost_usd is None for r in rows), "nullable cost only in fixtures")
    record("matrix_fingerprint", len(matrix_fingerprint()) == 64, matrix_fingerprint()[:16])
    record(
        "fdr_complete_set",
        fit.decision.generation_status in {"activated_all", "empty_candidate_set", "multiplicity_indecisive"},
        fit.decision.generation_status,
    )
    record("independent_recompute", fit.decision.config_fingerprint == fp, fp[:16])

    # Ruling B: quality_eq fixtures retain support without discordance.
    eq_rows = build_scenario_rows(RecoveryScenario.quality_eq_ns_faster)
    record("quality_eq_retained_support", retained_support_ok(eq_rows, "fam_a", cfg), "retained>=5")
    record(
        "quality_eq_no_discordance",
        not quality_discordance_ok(eq_rows, "fam_a", cfg),
        "zero discordant under both_succeed",
    )

    mcmc_path = _REPO / "evidence" / "plan018-v4-4-recovery-mcmc.json"
    if mcmc_path.is_file():
        mcmc = json.loads(mcmc_path.read_text())
        report = evaluate_recovery_results(
            mcmc.get("results", []),
            use_mcmc=True,
            wall_s=float(mcmc.get("wall_s", 0)),
            serial_s=float(mcmc.get("serial_s", 0)),
        )
        record("mcmc_recovery_gate", report.gate == "PASS", report.gate)
        record("mcmc_diagnostics_ok", report.diagnostics_failures == 0, str(report.diagnostics_failures))
        record("mcmc_wrong_direction", report.wrong_direction == 0, str(report.wrong_direction))
    else:
        record("mcmc_recovery_artifact", False, "missing plan018-v4-4-recovery-mcmc.json")

    sidecar = {
        "schema": "plan018-v4-4-verifier/v1",
        "gate": "PASS" if not errors else "FAIL",
        "checks_passed": sum(1 for c in checks if c["pass"]),
        "checks_total": len(checks),
        "errors": errors,
        "checks": checks,
        "route_execution": False,
        "set3_rerun": False,
        "paid_or_live_resources_used": False,
        "matrix_fingerprint": matrix_fingerprint(),
    }
    out = _REPO / "evidence" / "plan018-v4-4-verifier.sidecar.json"
    out.write_text(json.dumps(sidecar, indent=2))
    print(json.dumps({"gate": sidecar["gate"], "checks": f"{sidecar['checks_passed']}/{sidecar['checks_total']}"}))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
