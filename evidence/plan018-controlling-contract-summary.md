# Plan 018 controlling contract — summary

**File:** `evidence/plan018-controlling-contract.json`  
**Status:** `v4-0-complete-for-plan-tasks`  
**Plan SHA-256:** `af99a24b765f04330cf25b09c005a80002d175890f66743d2b2115b9b2f74a6e` (verified)  
**Implementation base:** `6881b6a870d68a6efaeb483b111cb9244488c5f9`

## What this is

V4-0 / V10 require a machine-readable map from every executable task and V4 wave to its **controlling** clauses, plus explicit voids for superseded commands/oracles. The validator must fail on unmapped steps, multiple active authorities, or attempts to execute a void command.

## Coverage

| Surface | Count |
|---|---:|
| Tasks mapped (0–13, 7b, omitted 4, void 12b) | 16 |
| V4 waves (V4-0…V4-9) | 10 |
| Explicit voids | 23 |
| Clause index entries | 91 |
| Unchecked plan boxes | 171 (checklist only; not completion) |

## Precedence (short)

Highest active layer wins on conflict: **V14 → V13 → V12/V11 → V10 → V9 → V8 → V7 → V6 → V5 → V4 → V3 → V2 → original task body**. Superseded text is retained only as **void** markers.

## Task snapshot

| Task | Status | Primary controlling authorities |
|---|---|---|
| 0 | active | V5 Task-0 replacement, V2-T0, V4-0 |
| 1 | active | V2-T1, V10-B/H, V4-0 migration leaf, V13-G |
| 2 | active | V2-T2, V10-H promotion writer (with Task 5) |
| 3 | active | V2-T3, V3-B, V4-6, V6-A/B, V7-*, V10-F |
| 4 | omitted | V7-C / V2-T4 VOID — negative tests only |
| 5 | active | V2-T5, V5-3 terminal inventory, V10-H |
| 6 | active | V2-T6, V9-A carve-out (no artifact_validator port) |
| 7 | active | V2-T7, V8-C/D, V10-E |
| 7b | active | V9-A…G, V10-C/D/G, V13 stored-evidence pins |
| 8 | active | V2-T8, V10-A five-field reuse + judgment fingerprint |
| 9 | active | V2-T9, V4-8, V10-H (no premature beat) |
| 10 | active | V2-T10, V4-4/V14, V4-5, V4-7 |
| 11 | active | V2-T11, V4-7 observational only |
| 12 | active | V2-T12 overlay + V5-3/V10-H posterior selector |
| 12b | void | Removed by V6-E |
| 13 | active | V2-T13, V4-9, V5-4, V13-B/G |

## Standout voids (non-exhaustive)

- `docker exec nextseek` without worktree-mount provenance  
- Porting `artifact_validator.py`; editing `route_capabilities.json` for labels  
- `set3_final` / 127-pair replacement paired runs  
- Historical `0010_turn_ledger.py` filename assumption  
- Destructive reverse migrate / persistent rollback  
- `created_at__gt` watermark; V8 marginal aggregate as fit input  
- Max-of-three confidence; DD-44 reduced below three calls  
- Premature beat registration; skipped-live-test-as-acceptance  

## Ambiguities not resolved mechanically

1. **Task 12 dual role** — original body is telemetry-only; V10-H also assigns posterior selection to Task 12 / V4-6. Contract maps both under Task 12 and voids using the overlay body alone for selector DONE.  
2. **Plan V4 `dfbccaf` vs bindings `6881b6a8`** — contract binds the authorized bindings base; V4-0 still requires fetch/drift if tip moves.  
3. **V8-B vs V10-A cache key** — four-field key extended by five-field V10-A; V10-A controls DONE.  

## Related artifacts

- `evidence/plan018-controlling-bindings.json`  
- `evidence/plan018-v4-0-ownership-map.md`  
