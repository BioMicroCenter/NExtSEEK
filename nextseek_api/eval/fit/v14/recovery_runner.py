"""Run V4-4 recovery matrix (8 scenarios × 5 seeds = 40 fits)."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from nextseek_api.eval.fit.v14.combined import run_v14_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.recovery_acceptance import (
    FEASIBILITY_SLOT_INDICES,
    evaluate_recovery_results,
    slot_winner,
)
from nextseek_api.eval.fit.v14.recovery_matrix import (
    RECOVERY_SCENARIOS,
    RecoverySlot,
    build_scenario_rows,
    ground_truth,
    matrix_fingerprint,
    recovery_seeds,
)


def all_slots() -> list[RecoverySlot]:
    slots: list[RecoverySlot] = []
    idx = 0
    for scenario in RECOVERY_SCENARIOS:
        for seed in recovery_seeds():
            slots.append(RecoverySlot(scenario=scenario, seed=seed, slot_index=idx))
            idx += 1
    return slots


def run_recovery(
    *,
    slot_indices: list[int] | None = None,
    use_mcmc: bool = True,
    wall_limit_s: float = 3600.0,
) -> dict:
    cfg = V14FitConfig()
    all_s = all_slots()
    if slot_indices is None:
        chosen = all_s
    else:
        chosen = [all_s[i] for i in slot_indices if 0 <= i < len(all_s)]
    t0 = time.monotonic()
    results = []
    for slot in chosen:
        if time.monotonic() - t0 > wall_limit_s:
            report = evaluate_recovery_results(results, use_mcmc=use_mcmc, wall_s=time.monotonic() - t0, serial_s=sum(r["duration_s"] for r in results))
            return {
                "gate": "INCONCLUSIVE",
                "reason": "wall_clock_cap",
                "completed": len(results),
                "matrix_fingerprint": matrix_fingerprint(),
                "acceptance": report.__dict__,
                "results": results,
            }
        rows = build_scenario_rows(slot.scenario)
        t1 = time.monotonic()
        fit = run_v14_generation(rows, cfg, seed=slot.seed, use_mcmc=use_mcmc)
        duration = time.monotonic() - t1
        gt = ground_truth(slot.scenario)
        winner = slot_winner(fit.decision)
        results.append(
            {
                "slot_index": slot.slot_index,
                "scenario": slot.scenario.value,
                "seed": slot.seed,
                "duration_s": round(duration, 3),
                "ground_truth": gt,
                "winner": winner,
                "activated": list(fit.decision.activated_families),
                "generation_status": fit.decision.generation_status,
                "diagnostics_ok": fit.diagnostics_ok,
            }
        )
    wall_s = round(time.monotonic() - t0, 3)
    serial_s = sum(r["duration_s"] for r in results)
    report = evaluate_recovery_results(results, use_mcmc=use_mcmc, wall_s=wall_s, serial_s=serial_s, wall_limit_s=wall_limit_s)
    return {
        "gate": report.gate,
        "matrix_fingerprint": matrix_fingerprint(),
        "completed": len(results),
        "serial_s": serial_s,
        "wall_s": wall_s,
        "acceptance": {
            "strong_effect": report.strong_effect,
            "indecisive": report.indecisive,
            "wrong_direction": report.wrong_direction,
            "diagnostics_failures": report.diagnostics_failures,
            "reasons": list(report.reasons),
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feasibility", action="store_true", help="Run five representative slots only")
    parser.add_argument("--slot", action="append", type=int, default=None, dest="slots")
    parser.add_argument("--no-mcmc", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    indices = args.slots
    if args.feasibility:
        indices = list(FEASIBILITY_SLOT_INDICES)
    payload = run_recovery(slot_indices=indices, use_mcmc=not args.no_mcmc)
    args.out.write_text(json.dumps(payload, indent=2))
    print(json.dumps({"gate": payload["gate"], "completed": payload.get("completed")}))
    return 0 if payload.get("gate") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
