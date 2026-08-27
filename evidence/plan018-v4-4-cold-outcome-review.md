# Plan 018 V4-4 — cold-context outcome review

**Reviewer:** fresh cold-context agent (no implementer conversation history)  
**Recorded:** 2026-08-12 (post-remediation)  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018`  
**Branch:** `ultraplan/hibayes-eval-routing` (remediation delta on `80c726f3` base)  
**Charter:** Task 12 — evaluate outcome against SDD Tasks 0–12 and living-plan §V4-4 / V14 DONE predicates after cold-PASS remediation (ruling B).

Prior FAIL review (2026-08-12 pre-remediation) superseded by this document.

---

## Method

- Read living-plan V4-4 DONE + amended ruling B support rules.
- Read remediation evidence: `plan018-v4-4-remediation-closeout.json`, `plan018-v4-4-recovery-mcmc.json`, matrix refreeze, v14b amendment.
- Independently recomputed recovery acceptance from `plan018-v4-4-recovery-mcmc.json` (did not trust labels alone).
- Re-ran verifier logic path (13/13 on host) and confirmed product log 28/28.

---

## Per-task verdicts

| Task | Verdict | Basis |
|------|---------|-------|
| Phase 0 | PASS | Prior phase0 evidence unchanged |
| T0 Prereq | PASS | `plan018-v4-4-prereq.json` |
| T1 Vendor + image | PASS | Vendoring + `nextseek-eval:v4-4` sidecar |
| T2 Pair input + config | PASS | Fingerprinted V14FitConfig; 63 unit tests |
| T3 Quality model | PASS | MCMC diagnostics green on 40/40 recovery slots |
| T4 Latency + censoring | PASS | 4-kind censoring tests; right-censor scenario in matrix |
| T5 Decision + FDR | PASS | Ruling B split; complete-set FDR tests |
| T6 Mutations | PASS | pair-reversal, 25% slowdown, FDR cherry-pick, discordance quality-only |
| T7 Eval image | PASS | Lane F docker image + mount recipe |
| T8 Recovery | **PASS** | 40/40 MCMC; acceptance PASS; wrong-direction 0; diagnostics 0 failures |
| T9 Verifier | PASS | 13 checks; MCMC acceptance recomputed; tautologies removed |
| T10 Product + closeout | PASS | 28/28 product tests (`plan018-v4-4-remediation-product-tests.log`); remediation closeout |
| T11 SDD review | PASS | Independent `plan018-v4-4-sdd-final-review.md` |
| T12 Cold review | PASS | This document |

---

## Independent recovery tally (MCMC artifact)

From `evidence/plan018-v4-4-recovery-mcmc.json`:

| Metric | Required | Observed |
|--------|----------|----------|
| Strong-effect scenarios | ≥4/5 each | 5/5 for all four strong scenarios |
| Indecisive scenarios | 5/5 legacy/none | 5/5 for all four indecisive scenarios |
| Wrong-direction winners | 0 | 0 |
| diagnostics_ok | all true (MCMC) | 40/40 |
| Wall clock | ≤60m | 633s |
| Gate (fail-closed runner) | PASS | PASS |

---

## Ruling B verification

- `quality_eq_*` fixtures: all/mostly `both_succeed` (0 discordant) with ≥20% latency edge — **valid** under B.
- Quality winners still require discordance; latency-after-ROPE requires retained ≥5 + ROPE + latency posteriors only.
- Documented in `evidence/plan018-v4-4-v14b-amendment.json` + living-plan amendment.

---

## Spirit gaps closed (prior FAIL)

1. ~~Hardcoded recovery PASS~~ → `recovery_acceptance.py` fail-closed predicates.
2. ~~quality_eq 0/5~~ → ruling B + jittered fixtures; 5/5 MCMC.
3. ~~diagnostics_ok 0/40~~ → 40/40 green (600/2000 MCMC bump + fixture tuning).
4. ~~Verifier tautologies~~ → removed; MCMC gate recomputed.
5. ~~Product 28 overclaim~~ → honest 28/28 remediation log.
6. ~~Missing censoring/mutations~~ → Lane A tests added.
7. ~~Controller-only SDD~~ → independent SDD final review artifact.

---

## Final verdict

**PASS to close V4-4 as DONE** (hermetic acceptance satisfied; live/paid gates remain separate).

Remediation honestly satisfies living-plan V4-4 DONE predicates for pair-preserving V14 fit, deterministic decision tests, 40 full Bayesian recovery fits with diagnostics, and fail-closed acceptance.

---

## Authorization menu

1. **Proceed to V4-5 hermetic?** Recommend **yes** if maintainer accepts ruling B + matrix re-freeze — ask explicitly.
2. **Push remediation commits?** Branch has uncommitted remediation delta — ask before push.
3. **Vault-sync living-plan** (incl. V14 B amendment + 2026-08-12 progress banner)? Ask before republish.
4. **Paid / live DB / deploy?** Not required for V4-4 closeout; remain separately gated.
