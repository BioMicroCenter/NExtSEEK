# Plan 018 V4-4 debt closeout — SDD review note

**Recorded:** 2026-08-11  
**Scope:** Residual debt implementation (migrations, judge cache, publish/generation store, V4-8 reservation, push/republish, MCMC recovery)

## Review method

Controller-conducted review of the debt-closeout diff (not independent per-task subagent loops).
Same debt class as V4-2/V4-3 Task 11 condensation.

## Findings addressed in this pass

| Area | Verdict |
|------|---------|
| TurnLedger + write path | PASS — unique constraint + LedgerCollision |
| TurnJudgment + judge_cache | PASS — fail-retry, version invalidation, no mtime skip |
| PosteriorGeneration + publish | PASS — immutable hash idempotency, no band reimplementation in publish |
| CAS activation | PASS — stale-hash refusal tested |
| V4-8 reservation | PASS — cap enforcement, idempotency replay, guarded_provider_call |
| Migration leaf | PASS — 0010→0014 chain documented in plan018-migration-leaf.json |

## Open items (not blockers for hermetic debt closeout)

1. MCMC diagnostics_ok may be false on sparse null scenarios — see recovery-mcmc evidence.
2. Per-task SDD subagent loops still deferred to V4-5+ gate process.
3. Live DB activation / production routing enablement — separate authorization.

## Verdict

**PASS for hermetic debt closeout** — all listed residual items implemented except live mutations.
