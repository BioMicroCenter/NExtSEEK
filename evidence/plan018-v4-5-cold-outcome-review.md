# Plan 018 V4-5 — independent cold-context outcome review

**Reviewer:** independent cold agent (no implementer conversation history)  
**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing`  
**Base:** `f515392b` (V4-4 cold PASS remediation)  
**HEAD:** `0bd1549c` (Phase 0 evidence only committed; V4-5 implementation uncommitted on disk)

---

## Verbatim prompt

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Verification performed (independent)

| Check | Result |
|-------|--------|
| `rg '_band_from_status'` in `publish.py` | **absent** (confirmed) |
| Lane C log (`plan018-v4-5-lane-c.log`) | **24 passed** |
| Lane M log tail (`plan018-v4-5-realstore.log`) | **6 passed** on disposable MySQL |
| Verifier sidecar | **16/16** checks PASS |
| Lane M settings | `dmac.test_settings_realstack` + `lane_local_settings.py` overlay (established recipe, not ad-hoc) |
| MySQL image digest | `mysql:8.0@sha256:7dcddc01…` per V4-0 sidecar |
| Isolation level | **REPEATABLE-READ** documented in realstore sidecar |
| Crash-at-boundary oracle on MySQL | **not present** in `test_generation_store_mysql.py` |
| Corruption oracle on MySQL | **not present** |
| `plan018-v4-5-debt-product-tests.log` | **not used** as PASS oracle (log shows failures) |
| Git state | 1 commit ahead of `f515392b` (Phase 0 only); ~450 LOC implementation + tests + migration **uncommitted** |
| deploydocs remote | Phase 0 pushed `2ec98a63` (“starting”); living-plan **CLOSED** edit is **local uncommitted** in deploydocs worktree |

---

## Per-task verdicts

| Task | Verdict | Rationale |
|------|---------|-----------|
| Phase 0 republish | **pass** | `plan018-v4-5-phase0-publish.json` gate PASS; deploydocs `2ec98a63` and vault SHA `c23579cf…` match phase0 record. Remotes record V4-5 **starting**, not DONE — correct for Phase 0 scope. |
| Task 0 prereq | **pass** | `plan018-v4-5-prereq.json` gate PASS; V4-0…4 CLOSED; Lane C/M recipes cited; hard refuses listed; `paid_or_live_resources_used: false`. |
| Task 1 publish contract | **partial** | `_band_from_status` removed; bands flow from fit decision via `decision_status_to_band`; content-hash idempotency and overwrite refusal implemented and Lane-C-tested. **Gaps:** no test for `CombinedFitResult` publish path (`_groups_from_combined` in `publish.py` is untested); create vs activate permission seam exists (`require_publish_permission` / `require_activate_permission`) but has no dedicated test. |
| Task 2 validate before activation | **partial** | `validate_generation_for_activation` + `require_valid_for_activation` wired into `activate_generation`; fail-closed paths tested for compatibility, partial_publish, hash mismatch, parent (MySQL). **Gaps vs plan checklist:** no tests for `filename_only_validation`, staleness (`stale: true`), invalid `decision_status`, or precision/sample floor refusals despite code paths existing in `generation_validation.py`. Sidecar lists only three refusal classes. |
| Task 3 CAS + audit + rollback | **pass** | `expected_hash` = currently active token; A→B activation, stale refuse, rollback under CAS, append-only `GenerationActivationAudit` — all exercised in Lane C; two-activator race on MySQL. |
| Task 4 per-turn snapshot | **pass** | `TurnLedger` pin fields (migration 0015); `pin_generation_for_turn` / `get_pinned_snapshot_for_turn`; test proves mid-turn activation does not change pinned hash. |
| Task 5 risk overlay | **pass** | `risk_overlay.assess(..., may_reroute=False)` only; grep shows six `False` assignments, zero `True`; `test_overlay_can_never_authorise_a_reroute` present; 5/5 Lane C. |
| Task 6 MySQL real-store barrier (V5-3) | **partial** | Six named oracles PASS on disposable MySQL REPEATABLE-READ using corrected established recipe (`scripts/plan018_lane_m_mysql.sh` → `test_settings_realstack` + V4-0 migrate overlay). **Missing vs living-plan V5-3 / V4-5 DONE quote:** corruption oracle, crash-at-publish/activation-boundary simulation, and “incompatibility” as a real-store barrier case. Partial-publish refusal is Lane C (SQLite) only. Controlling contract voids SQLite as DONE oracle — Lane C cannot substitute. |
| Task 7 verifier | **partial** | `scripts/plan018_v4_5_verifier.py` exits 0 with 16/16 sidecar checks including migration leaf, realstore gate, grep invariants. **Gap:** `negative_validation_fails` passes via **skip fallback** when `_FakeGen` triggers ORM type error — does not prove a corrupt real row fails (spirit of “deliberately broken fixture would fail”). Several checks are presence/grep-only. |
| Task 8 closeout | **partial** | `plan018-v4-5-closeout.json` gate PASS; preflight `next_gate: V4-6`; residual debt honestly listed; no push/live mutation. **Gap:** living-plan in deploydocs worktree locally marks V4-5 CLOSED with checkboxes `[x]` before maintainer vault-sync authorization — honest in banner (“local only”) but premature if read without that caveat. |
| Task 9 SDD whole-branch review | **pass** | `plan018-v4-5-sdd-final-review.md` APPROVED; load-bearing findings (Lane M recipe correction) addressed; no blocking defects recorded. |
| Task 10 cold review | **pass** | This artifact (fresh independent review, 2026-08-12). |

---

## Spirit gaps

1. **V5-3 real-store completeness:** Living-plan V5-3 (~L1330–1335) and V4-5 DONE quote require corruption and crash at every publish/activation boundary on the **production MySQL ORM harness**. Six passing MySQL tests cover concurrency/CAS/immutability well but do not satisfy the full DONE oracle wording. Lane C hash-mismatch and partial_publish tests are necessary but, per controlling contract, **cannot** close this gap alone.

2. **Checkbox / Progress honesty:** deploydocs living plan locally shows V4-5 **hermetic CLOSED** with all checkboxes ticked while remote remains at Phase 0 “starting” and implementation is uncommitted. The banner disclaims “local only,” but the visual `[x]` state could mislead skimmers.

3. **CombinedFitResult path:** Plan Task 1 explicitly requires covering `FitResult` **and** `CombinedFitResult`. Code implements `_groups_from_combined`; zero tests invoke it — technically present, not proven.

4. **Verifier negative check:** Passing a check via exception skip is “technically 16/16” but not adversarial proof that validation rejects corrupt stored generations.

5. **Validation breadth:** Refusal logic for filename-only, staleness, and decision-status gates exists in code but lacks refusal-class tests the plan asked for (“at least one failing case per checklist refusal class”).

---

## Final verdict on maintainer's will

**Partial carry — hermetic core delivered, DONE oracle incomplete, delivery hygiene unfinished.**

The maintainer's central intent — immutable generation publish/activate with content hashing, fail-closed pre-activation validation, correct CAS semantics (A→B, stale refuse), audit + rollback, per-turn reader snapshot pin, and telemetry-only risk overlay — is **implemented in code and substantially evidenced** on the correct lanes (Lane C iterative, Lane M disposable MySQL). The session did not touch live DB or production routing, consistent with hard refuses.

However, the **binding V5-3 / V4-5 DONE oracle** (“corruption, mixed generations, incompatibility, failed validation, concurrent activation, and rollback are exercised against the **real store** … crash at every publish/activation boundary”) is **not fully satisfied**: MySQL barriers omit corruption and crash-boundary cases; several validation refusal classes lack tests; and the implementation remains **uncommitted** with living-plan DONE markers only local. I would **not** treat V4-5 as fully closed for vault republish or V4-6 scope authorization without maintainer ruling on the oracle gaps and a commit/push pass.

---

## Residual debt (actionable)

1. **Commit V4-5 implementation** — one or more logical commits atop `0bd1549c` (store, validation, overlay, migration 0015, MySQL tests, lane script, verifier); currently ~450 LOC modified + new files untracked.
2. **MySQL barrier oracles** — add real-store tests for corruption (tampered hash/payload) and crash/partial-boundary at publish/activate (or obtain explicit maintainer deferral ruling documented in evidence).
3. **Validation test matrix** — add Lane C refusal tests for `filename_only_validation`, staleness, invalid `decision_status`, precision/sample floors.
4. **CombinedFitResult publish test** — exercise `_groups_from_combined` end-to-end.
5. **Verifier hardening** — replace `_FakeGen` skip with corrupt `PosteriorGeneration` fixture; fail verifier if negative check skips.
6. **Living-plan republish discipline** — push/vault-sync CLOSED Progress only after maintainer authorizes; keep remote at “starting” until then.
7. **Permission seam tests** — assert `live:` actor raises on publish/activate.
8. **Cleanup** — confirm no stale `test_settings_lane_m.py` left behind (SDD review noted supersession; glob shows none present).

---

## Authorization menu

| Item | Recommendation |
|------|----------------|
| **Proceed to V4-6 hermetic?** | **Ask maintainer.** Core V4-5 mechanics are usable for V4-6 selector work, but oracle gaps and uncommitted state should be resolved or explicitly waived first. |
| **Push V4-5 implementation commits?** | **Recommended after commit.** Branch is 1 commit ahead of `f515392b` (Phase 0 evidence only); implementation not on remote. Push when maintainer approves. |
| **Vault-sync / republish living-plan V4-5 DONE?** | **Not yet.** Remote/vault still at Phase 0 “starting” SHA; local CLOSED edit uncommitted in deploydocs. Republish after cold review acceptance + implementation commit. |
| **Live DB activation / production enablement?** | **No** — remain separately gated; no evidence of live mutation this session. |

---

*Review method: read plan SDD, controlling contract V4-5 voids, living-plan V4-5/V5-3 quotes, all `evidence/plan018-v4-5-*` sidecars/logs, implementation modules, migration 0015, lane scripts, git status, and deploydocs remote vs local state. Prior cold-review and SDD artifacts treated as pointers only; all verdicts re-derived from disk.*
