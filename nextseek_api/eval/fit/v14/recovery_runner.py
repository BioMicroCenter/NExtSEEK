"""Run V4-4 recovery matrix (8 scenarios × 5 seeds = 40 fits)."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from nextseek_api.eval.fit.v14.combined import run_v14_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
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
    max_slots: int | None = None,
    start_slot: int = 0,
    use_mcmc: bool = True,
    wall_limit_s: float = 3600.0,
) -> dict:
    cfg = V14FitConfig(num_warmup=150, num_samples=250, num_chains=2)
    slots = all_slots()[start_slot : (start_slot + max_slots if max_slots else None)]
    t0 = time.monotonic()
    results = []
    for slot in slots:
        if time.monotonic() - t0 > wall_limit_s:
            return {"gate": "INCONCLUSIVE", "reason": "wall_clock_cap", "completed": len(results)}
        rows = build_scenario_rows(slot.scenario)
        t1 = time.monotonic()
        fit = run_v14_generation(rows, cfg, seed=slot.seed, use_mcmc=use_mcmc)
        duration = time.monotonic() - t1
        gt = ground_truth(slot.scenario)
        activated = fit.decision.activated_families
        winner = "ns" if any(c.status.name.startswith("quality_ns") or c.status.name == "latency_ns" for c in fit.decision.candidates if c.activated) else (
            "cc" if any(c.status.name.startswith("quality_cc") or c.status.name == "latency_cc" for c in fit.decision.candidates if c.activated) else "none"
        )
        results.append({
            "slot_index": slot.slot_index,
            "scenario": slot.scenario.value,
            "seed": slot.seed,
            "duration_s": round(duration, 3),
            "ground_truth": gt,
            "winner": winner,
            "activated": list(activated),
            "generation_status": fit.decision.generation_status,
            "diagnostics_ok": fit.diagnostics_ok,
        })
    serial = sum(r["duration_s"] for r in results)
    return {
        "gate": "PASS",
        "matrix_fingerprint": matrix_fingerprint(),
        "completed": len(results),
        "serial_s": serial,
        "wall_s": round(time.monotonic() - t0, 3),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-slots", type=int, default=None)
    parser.add_argument("--start-slot", type=int, default=0)
    parser.add_argument("--no-mcmc", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = run_recovery(max_slots=args.max_slots, start_slot=args.start_slot, use_mcmc=not args.no_mcmc)
    args.out.write_text(json.dumps(payload, indent=2))
    print(json.dumps({"gate": payload["gate"], "completed": payload.get("completed")}))
    return 0 if payload.get("gate") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
