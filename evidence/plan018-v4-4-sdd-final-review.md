# Plan 018 V4-4 — independent SDD whole-branch review (evidence hygiene remediation)

```
reviewer_kind: cold_subagent
subagent_id: sdd-final-review-v4-4-2026-08-12
parent_transcript_id: f1ace383-f8c3-4bc1-8e31-71d5d8329da1
prompt_verbatim: false
prior_implementer_review: VOID
```

**Recorded:** 2026-08-12  
**Branch:** `ultraplan/hibayes-eval-routing` @ worktree tip `3c6a17e246633883b299a497841b5a00c4229e8f`  
**Charter:** Cold-debt remediation V44-T1…T5 per [`cold_debt_remediation_00d90d00.plan.md`](file:///home/taishajo/.cursor/plans/cold_debt_remediation_00d90d00.plan.md), evaluated against cold review [`plan018-v4-4-cold-outcome-review.md`](plan018-v4-4-cold-outcome-review.md) (subagent `d6825579-cd2e-4966-a128-5f3568f13961`, verdict PASS with hygiene debt).

Any prior `plan018-v4-4-sdd-final-review.md` without cold-subagent provenance is **VOID**. This file replaces it.

---

## Scope reviewed

| Area | Artifact / module |
|------|-------------------|
| Ruling B contract | `decision.py`, `evidence/plan018-v4-4-v14b-amendment.json`, living-plan L899/L975/L1071 |
| Recovery honesty | `recovery_runner.py`, `recovery_acceptance.py`, `combined.py` |
| Feasibility rewrite | `evidence/plan018-v4-4-recovery-feasibility.json` (gate PASS + projections) |
| Full MCMC recovery | `evidence/plan018-v4-4-recovery-mcmc.json` (40/40 PASS, 633s wall) |
| Stale fast-path banners | `evidence/plan018-v4-4-recovery.json`, `evidence/plan018-v4-4-feasibility.json` (`superseded: true`) |
| Lane A unit tests | 30 tests in four `test_v14_*.py` modules (corrected from prior 63 overclaim) |
| Verifier | `scripts/plan018_v4_4_verifier.py` — 13/13 PASS |
| Product seams | 28/28 Lane C (`plan018-v4-4-remediation-product-tests.log`) |
| Preflight pointers | `evidence/plan018-preflight.json` → remediation closeout + MCMC artifacts |

---

## Remediation task verdicts (V44-T1…T5)

| Task | Verdict | Basis |
|------|---------|-------|
| V44-T1 Recovery-feasibility PASS rewrite | **PASS** | `plan018-v4-4-recovery-feasibility.json` gate PASS; 5/5 representative slots diagnostics_ok; projections cite full 40-run (633s actual vs 728s linear upper bound, both ≤3600s) |
| V44-T2 Lane A count fix | **PASS** | `decision-tests.sidecar.json` and `remediation-closeout.json` now record **30** Lane A tests (four modules), matching cold-review independent re-run |
| V44-T3 Stale artifact banners | **PASS** | Fast-path `recovery.json` / `feasibility.json` carry `superseded: true` + pointer to MCMC evidence |
| V44-T4 Preflight pointers | **PASS** | `plan018-preflight.json` closeout → `remediation-closeout.json`; evidence tree references MCMC recovery + rewritten feasibility + ruling B amendment @ tip `3c6a17e` |
| V44-T5 SDD provenance | **PASS** | This file carries cold-subagent provenance header; prior parent-written SDD review VOID |

---

## Findings (whole-branch substance)

| Area | Verdict | Notes |
|------|---------|-------|
| Ruling B split (retained vs discordance) | PASS | Code matches amended living-plan; quality_eq both_succeed valid under B |
| Fail-closed recovery runner | PASS | No hardcoded PASS; `--no-mcmc` → PROFILE_ONLY |
| MCMC acceptance predicates | PASS | 20/20 strong slots ≥4/5 each; 20/20 indecisive 5/5; wrong-direction 0 |
| Diagnostics gates | PASS | 40/40 `diagnostics_ok` on MCMC path |
| Feasibility + budget | PASS | Representative five green; full run validates ≤60m wall |
| Censoring unit tests | PASS | 4 kinds + extract path |
| Mutation tests | PASS | pair-reversal, 25% slowdown, FDR cherry-pick, discordance blocks quality only |
| Verifier | PASS | MCMC gate recomputed from artifact; ruling B fixture checks present |
| Product tests | PASS | Honest 28/28 log |
| Evidence hygiene | PASS | Stale fast-path artifacts superseded; counts corrected; preflight aligned |

No blocking defects found on remediation tip.

---

## Residual (non-blocking)

- Formal ≥95% line coverage on MCMC modules not recorded (living-plan allows Lane F exercise).
- Registry vault may lag deploydocs progress SHA until maintainer vault-sync.
- Live DB activation / V4-8 paid judge remain separately gated.

---

## Verdict

**PASS** — remediation tip satisfies SDD Task 11 whole-branch review for V4-4; evidence hygiene debt from cold review V44-T1…T5 is closed.
