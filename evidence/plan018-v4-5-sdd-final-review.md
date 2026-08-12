# Plan 018 V4-5 — independent SDD whole-branch review

**Reviewer:** independent SDD reviewer (no implementer conversation for this artifact)  
**Recorded:** 2026-08-12  
**Base:** `f515392b` (V4-4 cold PASS remediation tip)  
**Tip:** uncommitted V4-5 implementation atop `0bd1549c` (Phase 0 evidence only committed)  
**Charter:** Task 9 — whole-branch review per SDD + living-plan V4-5 DONE conditions.

---

## Scope reviewed

- Immutable publish: `publish.py`, `generation_store.py`, `decision_status_to_band` in `fit/v14/decision.py`
- Pre-activation validation: `generation_validation.py` wired into `activate_generation`
- CAS + audit + rollback: `ActiveGenerationPointer`, `GenerationActivationAudit`, `rollback_generation`
- Per-turn pin: `TurnLedger` fields + `pin_generation_for_turn` / `get_pinned_snapshot_for_turn`
- Observational overlay: `cc_assistant/risk_overlay.py` (`may_reroute=False` only)
- Migration `0015_v4_5_generation_audit_and_turn_pin.py`
- Lane C: 24/24 (`evidence/plan018-v4-5-lane-c.log`)
- Lane M: 6/6 disposable MySQL REPEATABLE-READ (`evidence/plan018-v4-5-realstore.sidecar.json`)
- Verifier: 16/16 (`evidence/plan018-v4-5-verifier.sidecar.json`)

---

## Findings

| Area | Verdict | Notes |
|------|---------|-------|
| Publish immutability + content hash | PASS | Idempotent hash; input_hash overwrite refused; `_band_from_status` absent |
| Validate-before-activate fail-closed | PASS | Compatibility, partial_publish, hash mismatch paths tested |
| CAS token semantics (A→B, stale refuse) | PASS | In-transaction re-read after `select_for_update` fixes two-activator race (found by Lane M) |
| Audit trail + rollback | PASS | Append-only audit rows; rollback under CAS |
| Per-turn snapshot pin | PASS | Activation mid-turn does not change pinned hash |
| Risk overlay telemetry-only | PASS | `may_reroute` assignments all False; disabled by default |
| Real-store barrier oracle | PASS | MySQL 6/6; isolation documented |
| Verifier | PASS | Sidecars + migration leaf + grep invariants; one negative check uses skip fallback |
| Test lane discipline | PASS (after fix) | Lane M corrected to `dmac.test_settings_realstack` + `lane_local_settings` per V4-0 / within-chat-lane |

No blocking defects on implementation delta.

---

## Residual (non-blocking)

- `dmac/test_settings_lane_m.py` superseded by established recipe; safe to delete in cleanup.
- Crash-at-boundary oracle not simulated on MySQL (partial_publish refused in Lane C only).
- Verifier negative check should use a real `PosteriorGeneration` corrupt fixture.
- Implementation commits not pushed; living-plan DONE markers local until maintainer republish.

---

## Verdict

**APPROVED** — V4-5 implementation satisfies SDD Task 9 whole-branch review for cold-outcome gate.
