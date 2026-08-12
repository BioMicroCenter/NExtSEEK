# Plan 018 V4-5 — independent cold-context outcome review (post-remediation)

**Reviewer:** independent cold agent (no implementer conversation history)  
**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing`  
**Base:** `f515392b` (V4-4 cold PASS remediation)  
**HEAD:** `a89fb3dc` (remediation commit atop `90383381` WIP)  
**deploydocs:** `/home/taishajo/work/NExtSEEK-deploydocs` @ `60d661c0` (local, not pushed)

Prior PARTIAL review (same date, pre-remediation @ `0bd1549c`) superseded by this document.

---

## Verbatim prompt

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Verification performed (independent)

| Check | Result |
|-------|--------|
| Commits | `90383381` (feat) + `a89fb3dc` (remediation); git clean except untracked `.superpowers/sdd/` noise |
| `rg '_band_from_status'` | **absent** |
| Lane C remediation log | **32 passed** (`plan018-v4-5-remediation-lane-c.log`) |
| Lane M log | **11 passed** on disposable MySQL (`plan018-v4-5-realstore.log`) |
| Verifier sidecar | **22/22** checks PASS |
| MySQL image digest | `mysql:8.0@sha256:7dcddc01…` per realstore sidecar |
| Isolation level | **REPEATABLE-READ** documented |
| `test_settings_lane_m.py` | **absent** |
| Abort hooks | `PublishAbort` / `ActivationAbort` in `generation_store.py`; test-only flags default off |
| Verifier negative check | Real `PosteriorGeneration` row, hash tampered, `validate_generation_for_activation` → `ok=False`; exception → FAIL (no skip-as-pass) |
| deploydocs living-plan | **Remediation in progress / not DONE**; unchecked `[ ]`; banner cites prior PARTIAL + remediation scope |
| closeout.json | **Stale** — still references `0bd1549c`, 24/6/16 counts, pre-remediation residual_debt |
| preflight.json / OI | **Premature** `v4_5_status: CLOSED` / “cold PASS 2026-08-12” before this review |

---

## Per-task verdicts (remediation plan tasks 0–7)

| Task | Verdict | Rationale |
|------|---------|-----------|
| **0** Commit V4-5 WIP locally | **pass** | Two logical commits (`90383381`, `a89fb3dc`) atop Phase 0 evidence; no push; V4-5 paths committed; only excluded SDD noise untracked. |
| **1** Living-plan honesty | **pass** | deploydocs `60d661c0` reverts premature CLOSED markers; Progress banner reads “remediation in progress — not DONE”; checkboxes unchecked; remote/vault remain Phase 0 “starting”; local-only commit, no push. |
| **2** Lane C validation + CombinedFitResult + permissions | **pass** | Sidecar gate PASS, 32/32; refusal classes include compatibility, partial_publish, filename_only_validation, stale_generation, invalid_decision_status, precision floors, live publish/activate; `test_publish_combined_fit_result_uses_decision_bands` exercises `CombinedFitResult` → `publish()`; `live:` actor tests on create/activate. |
| **3** MySQL oracles + abort hooks | **pass** | Sidecar lists 11 named oracles (incl. corruption, taxonomy_corpus_incompat, partial_publish_refused, crash_publish_boundary, crash_activation_boundary); log 11/11; abort hooks mid-create/mid-activate with transactional rollback asserted; `paid_or_live_resources_used: false`. |
| **4** Verifier hardening | **pass** | 22/22 checks; `negative_validation_fails` uses real ORM row + migrate, not `_FakeGen` skip; requires remediation Lane C + realstore sidecars gate PASS; deliberately broken path fails closed on exception. |
| **5** Re-run lanes + refresh evidence | **pass** | Lane C 32, Lane M 11, verifier 22/22 all PASS; sidecars refreshed; no `_band_from_status`; no stale lane_m settings. |
| **6** Closeout / preflight / OI hygiene | **partial** | Living-plan correctly **not** marked CLOSED (ruling 2C). **Gaps:** `plan018-v4-5-closeout.json` still records pre-remediation SHA/counts/residual_debt and claims gate PASS; `plan018-preflight.json` sets `v4_5_status: CLOSED` prematurely; OI `next_action` falsely claims “cold PASS 2026-08-12” when prior review was PARTIAL. Bookkeeping not refreshed post-`a89fb3dc`. |
| **7** Cold review (this artifact) | **pass** | Fresh independent re-derivation from disk; prior PARTIAL findings re-checked. |

Tasks 8 (push) and 9 (auth menu) are out of scope for this reviewer.

---

## Original V4-5 spec / V5-3 DONE oracle (post-remediation)

| Original concern (prior PARTIAL) | Status |
|----------------------------------|--------|
| Uncommitted implementation | **Closed** — committed @ `90383381`/`a89fb3dc` |
| MySQL corruption oracle | **Closed** — `test_mysql_corruption_refused_on_activate` |
| Crash at publish/activation boundary | **Closed** — abort-flag oracles with rollback assertions |
| taxonomy/corpus incompat + partial_publish on MySQL | **Closed** — dedicated Lane M tests |
| Validation refusal matrix (filename/stale/decision/precision) | **Closed** — Lane C tests + sidecar refusal_classes |
| CombinedFitResult publish path | **Closed** — `test_publish_combined_fit_result_uses_decision_bands` |
| Verifier skip-as-pass | **Closed** — real ORM negative check |
| Permission seams (`live:`) | **Closed** — dedicated Lane C tests |
| V5-3 real-store barrier set | **Closed** — 11/11 oracles on disposable MySQL REPEATABLE-READ |

**V4-5 DONE quote** (“corruption, mixed generations, incompatibility, failed validation, concurrent activation, and rollback against the real store”): satisfied via Lane M oracles — `reader_single_hash` covers mixed-generation reader view; two_activators covers concurrent activation; rollback/stale_cas/immutable_overwrite/parent_mismatch round out the set. Failed validation classes exercised on Lane C with MySQL barriers for store-specific refusals.

---

## Spirit gaps

1. **Closeout / preflight / OI ahead of cold PASS:** Evidence files and OI text declare V4-5 CLOSED/cold-PASS before this independent review completed. Living-plan honesty (Task 1) is correct; cross-session bookkeeping is not yet aligned — skimmers could misread preflight/OI as authoritative over the living-plan banner.

2. **Corruption oracle scope:** Tests tamper `generation_hash` only, not canonical payload fields. Plan wording allowed “and/or”; hash mismatch is the primary integrity gate and is proven on MySQL + verifier, but payload tampering is untested.

3. **Abort hooks are test-only module flags:** Acceptable per maintainer ruling 1A; production default never sets them. Spirit OK for hermetic barrier proof; not a production crash-recovery mechanism.

4. **`publish_generation(actor="live:…")` not named separately:** Covered by delegation to `create_generation`; functionally equivalent but plan listed all three entry points explicitly.

---

## Final verdict on maintainer's will

**PASS — substantive V4-5 remediation complete; hermetic DONE oracle satisfied.**

The maintainer's central intent — immutable generation publish/activate with content hashing, fail-closed pre-activation validation, CAS semantics, audit + rollback, per-turn reader snapshot pin, telemetry-only risk overlay, and full V5-3 real-store barrier coverage on disposable MySQL — is **implemented, committed, and evidenced** on the established lanes. Prior PARTIAL findings are closed on disk. Hard refuses respected (no live DB activation, no push, no vault-sync DONE).

Task 6 bookkeeping remains incomplete: closeout/preflight/OI must be refreshed to `a89fb3dc` counts (32/11/22) before push or vault-sync; living-plan CLOSED markers should wait for maintainer authorization after this PASS (ruling 2C).

---

## Residual debt

1. **Refresh `plan018-v4-5-closeout.json`** — update `worktree_sha` to `a89fb3dc`, counts 32/11/22, oracle list, empty residual_debt except auth-gated items (push, vault-sync, V4-6, live activation).
2. **Correct `plan018-preflight.json` and OI `next_action`** — remove premature “cold PASS / CLOSED” until maintainer accepts this review; point `next_gate: V4-6` with explicit auth asks.
3. **Post-PASS living-plan CLOSED** — local deploydocs `[x]` + vault-sync only after maintainer authorization (Task 9).
4. **Optional hardening:** payload-canonical corruption tamper test on MySQL (low priority; hash path proven).

---

## Authorization menu (informational — Task 9 is implementer scope)

| Item | Status |
|------|--------|
| Push `ultraplan/hibayes-eval-routing` | Ready after closeout refresh; ruling 3B allows push post cold PASS without further ask |
| Vault-sync living-plan V4-5 DONE | **Ask maintainer** (ruling 2C) |
| Proceed to V4-6 hermetic | **Ask maintainer** |
| Live DB activation | **No** — separately gated |

---

*Review method: read remediation plan, living-plan V4-5/V5-3 quotes, all post-remediation sidecars/logs, implementation modules, migration 0015, lane scripts, verifier source, git log @ `a89fb3dc`, deploydocs @ `60d661c0`, and prior PARTIAL review. All verdicts re-derived from disk; no implementer history used.*
