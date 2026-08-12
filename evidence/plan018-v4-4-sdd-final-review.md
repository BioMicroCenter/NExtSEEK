# Plan 018 V4-4 — independent SDD whole-branch review (remediation tip)

**Reviewer:** independent cold SDD reviewer (no implementer conversation)  
**Recorded:** 2026-08-12  
**Branch tip:** `ultraplan/hibayes-eval-routing` (uncommitted remediation delta on `80c726f3` base)  
**Charter:** Task 11 — whole-branch review of V4-4 remediation per SDD + living-plan V4-4 DONE.

---

## Scope reviewed

- V14 ruling **B** contract: `decision.py`, living-plan L899/L975/L1071, `evidence/plan018-v4-4-v14b-amendment.json`
- Recovery honesty: `recovery_matrix.py`, `recovery_runner.py`, `recovery_acceptance.py`, `combined.py`
- Deterministic tests: 63 Lane A (`test_v14_*` including censoring, mutations, matrix support)
- MCMC Lane F: `plan018-v4-4-recovery-mcmc.json` (40/40 PASS, diagnostics 0 failures)
- Verifier: `scripts/plan018_v4_4_verifier.py` (13/13, recomputes MCMC acceptance)
- Product seams: 28/28 Lane C (`plan018-v4-4-remediation-product-tests.log`)

---

## Findings

| Area | Verdict | Notes |
|------|---------|-------|
| Ruling B split (retained vs discordance) | PASS | Code matches amended living-plan; quality_eq both_succeed valid |
| Fail-closed recovery runner | PASS | No hardcoded PASS; `--no-mcmc` → PROFILE_ONLY |
| MCMC acceptance predicates | PASS | 20/20 strong slots ≥4/5 each; 20/20 indecisive 5/5; wrong-direction 0 |
| Diagnostics gates | PASS | 40/40 `diagnostics_ok`; latency skipped below retained minimum |
| Censoring unit tests | PASS | 4 kinds + extract path |
| Mutation tests | PASS | pair-reversal, 25% slowdown, FDR cherry-pick, discordance blocks quality only |
| Verifier | PASS | Tautologies removed; MCMC gate recomputed from artifact |
| Product tests | PASS | Honest 28/28 log |
| MCMC sample budget bump | PASS (documented) | 300/500 → 600/2000 within 60m wall (633s) |

No blocking defects found on remediation tip.

---

## Residual (non-blocking)

- Formal ≥95% line coverage on MCMC modules not recorded (living-plan allows Lane F exercise).
- Registry vault may lag deploydocs progress SHA until maintainer vault-sync.
- Live DB activation / V4-8 paid judge remain separately gated.

---

## Verdict

**PASS** — remediation tip satisfies SDD Task 11 whole-branch review for V4-4 cold-PASS gate.
