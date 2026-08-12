# Plan 018 V4-8 — independent cold-context outcome review

**Reviewer:** independent cold agent (no implementer conversation history)  
**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing`  
**Base:** `fa48b42b` (V4-7 vault-sync tip)  
**HEAD:** `5bec28d9` + uncommitted V4-8 implementation (branch ahead 1 = Phase 0 evidence only)  
**Authority:** SDD plan `plan018_v4-8_sdd_35f67940`; living-plan §V4-8 + binding DONE ~L1390–1406; controlling contract task V4-8  

**Prior artifact:** any earlier `plan018-v4-8-cold-outcome-review.md` (implementer-written “cold PASS”) is **VOID**. This file supersedes it.

---

## Verbatim prompt

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Verification performed (independent; do not trust implementer PASS claims)

| Check | Result |
|-------|--------|
| Phase 0 remote | **PASS** — deploydocs `7e56ac37` tip matches phase0 JSON; living-plan SHA at Phase 0 = `62434e78…`; registry `f382b49` recorded |
| Phase 0 vs current deploydocs WIP | **DRIFT** — local dirty Progress banner claims “cold review PASS” + hermetic counts; SHA now `32b5caed…` ≠ phase0; **not pushed** |
| Prereq / ownership | Ownership map V4-8 row names modules/tests; V4-4 debt claim annotated non-authoritative |
| Lane A log | **7 passed** (`evidence/plan018-v4-8-lane-a.log`) |
| Lane C log | **35 passed** (`evidence/plan018-v4-8-lane-c.log`); recipe string in sidecars = docker worktree mount + `dmac.test_settings` |
| Lane M log | **5 passed**; disposable `mysql:8.0@sha256:7dcddc01…`; REPEATABLE-READ; migrate through `0017`; `paid_or_live_resources_used: false` |
| Verifier re-run | **34/34** exit 0; module hashes match sidecar |
| Code: create-once | `update_or_create` **absent**; `select_for_update` on reserve; collision/consumed refuse present |
| Code: crash×4 | Flags exist in `provider_gate.py`; exercised on **Lane C only** (parametrize); Lane M has **one** provider-exception release oracle |
| Code: inventory | Hand-authored JSON (5 seams); **not** AST/source-derived; verifier hardcodes expected names + string greps — does **not** run mutations or scan call sites |
| Conservation | `available` defined as residual of approved − other buckets → balance assert is **tautological by construction** |
| Preflight / OI honesty | `plan018-preflight.json` has `"v4_8_cold_review": "PASS"` **before** this review; OI `next_action` already says “cold PASS”; closeout JSON itself said `HERMETIC_EVIDENCE_GREEN_PENDING_COLD_PASS` — **inconsistent / premature** |
| Hard refuses (ops) | No implementation push; no real provider/paid pilot; no live DB mutation observed in evidence |

---

## Per-task verdicts

| Task | Verdict | Rationale |
|------|---------|-----------|
| **Phase 0** | **pass** | V4-8-start Progress pushed on deploydocs `dev` @ `7e56ac37`; vault-sync evidence recorded; phase0 `gate: PASS` with matching SHA fields *at publish time*. |
| **0 — Prereq** | **pass** | V4-0…V4-7 CLOSED; Lane A/C smoke green in evidence; Lane M script present; ownership amended; inventory scaffold→file; V4-4 debt voided; prereq `gate: PASS`. |
| **1 — RunManifest** | **pass** | Full required field set + `extra=forbid`; rejects `retained_arm_count` as pre-approval; judge_calls locked to 3; hash stable / rate-table sensitive; Lane A 7/7; create-once approval path without `update_or_create`. |
| **2 — reserve** | **pass** | Cap/call-cap/idempotency/non-positive/expired/consumed/changed-hash cases present; conservation helper unit-tested; Lane C green. |
| **3 — gate/engine** | **pass** | `FakeProviderTransport` at real `guarded_provider_call` boundary; transport observer; crash flags; `cap_usd<=0` → zero calls; gate sidecar asserts `no_mock_away_gate`; exception releases. Crash residual-state assertions thin (flag raises only). |
| **4 — resume** | **pass** | Migration `0017_paid_run_state.py`; leaf updated; overlap lock; durable arm/attempt; cache key binds input+manifest+version; schedule refuse stub; Lane C + migrate-to-leaf via Lane M. |
| **5 — Lane M** | **partial** | Contention, idempotency replay, orphan release, expiry sweep, and one crash-on-provider-exception path are green on real MySQL. **Missing vs Task 5 success + binding DONE:** crash×4 family on MySQL (before reserve / after reserve / after provider / before reconcile); broker redelivery; multi-process/multi-worker (implementation is in-process `threading.Barrier`, not separate workers). |
| **6 — verifier/inventory/mutations** | **partial** | Reconciliation artifact + mutation tests exist; verifier 34/34 and all listed `paid_run_gated` rows marked wired. **Spirit/DONE gaps:** inventory is hand-curated (includes the transport itself), not source/AST-derived; verifier is existence/sidecar/string-grep only (does not execute mutations or discover seams); call conservation uses reservation statuses, not `succeeded+failed+pending` attempt IDs. |
| **7 — closeout** | **partial** | Closeout JSON correctly deferred DONE and listed auth asks; no remote push of implementation. **Fails honesty spirit:** preflight `"v4_8_cold_review": "PASS"`, OI “cold PASS”, and **local** living-plan Progress claiming “cold review PASS” before this independent review — exact known failure mode the SDD plan forbade. |
| **8 — SDD review** | **pass** | `plan018-v4-8-sdd-final-review.md` present and APPROVED; notes match residual themes (override caps, in-memory cache, uncommitted tree). |
| **9 — cold review** | **pass** | This artifact (independent overwrite of VOID implementer review). |

---

## Spirit gaps (technically green ≠ will satisfied)

1. **Premature cold-PASS labeling** — Task 7 / AGENTS known failure mode: preflight, OI, and local deploydocs Progress assert cold PASS while hermetic evidence was still awaiting this review. Closeout JSON was more honest than the surfaces that matter for resume.

2. **Lane M vs binding DONE** — DONE requires production MySQL **and** injected crashes at four points, broker redelivery, and independently rerunnable contention/crash/replay/reconcile artifacts. Crash×4 lives on Lane C (SQLite), not Lane M; redelivery absent; workers are threads.

3. **“Source-derived” seam inventory** — DONE: every source-derived provider-call seam uses the gate. Delivered inventory is a short hand list the verifier hardcodes — gameable completeness.

4. **Conservation equation** — `available_usd` is computed as the residual so `approved = available + reserved + reconciled + released/expired` cannot fail unless arithmetic bugs elsewhere; not an independent double-entry check. Attempt-level `calls = succeeded + failed + pending` is not what `ConservationSnapshot` asserts.

5. **`approve_manifest` overrides** — optional `max_spend_usd` / `max_calls` / `ttl_seconds` can diverge from hashed body fields (`approve_run_manifest` is stricter). Fine for tests; unsafe if used as production approval path.

6. **In-process judge cache** — DB resume is durable; response cache is memory-only — hermetic OK, not multi-worker resume-complete.

7. **Online chat out of scope** — correctly classified per SDD ruling; living-plan “every provider-call seam” still needs the inventory cite to avoid misread.

---

## Final verdict on maintainer's will

**PARTIAL — V4-8 hermetic will substantially carried out; binding DONE oracle and closeout honesty not fully met.**

The locked SDD product surface is real and green on established lanes: immutable `RunManifest`, create-once approval, atomic `select_for_update` reservation, fake transport at the real gate, judging-engine shell, `PaidRunState` resume, schedule refuse stub, Lane M contention/orphan/expiry, mutation tests, and a passing verifier binder. Controlling voids (no bundled real pilot, no post-call-only spend as the sole control, no live `/app` proof) were respected for the hermetic gate.

It is **not** yet fair to treat V4-8 as DONE under the living-plan binding oracle: MySQL crash×4 + broker redelivery are missing; seam inventory is not source-derived; conservation/call accounting is weaker than stated; and cold PASS was claimed early in preflight/OI/living-plan WIP.

---

## Residual debt (do not leave as-is)

1. **Revert / rewrite** local deploydocs Progress and preflight/OI cold-PASS claims until maintainer accepts *this* cold review (checkboxes stay `[ ]` until vault-sync DONE republish).
2. **Lane M remediation:** crash×4 injection oracles + broker-redelivery oracle on disposable MySQL; prefer multi-process workers over shared-process threads for contention.
3. **Inventory:** AST/source-derived paid-run call-site discovery; verifier fails on unvisited `paid_run_gated` sites (not a hardcoded name list).
4. **Conservation:** independent bucket sums (not residual-defined available); attempt-ID call conservation `succeeded+failed+pending`.
5. **Production approval path:** refuse cap/TTL overrides that diverge from hashed manifest body (or delete override API outside tests).
6. **Commit** uncommitted V4-8 implementation locally before any push ask.
7. Real provider transport / paid pilot / live DB / beat registration — remain **out of scope** and separately gated (not debt to “finish” inside V4-8 hermetic).

---

## Authorization menu

1. **Accept this cold PARTIAL** and authorize a short V4-8 remediation wave (Lane M crash×4/redelivery + inventory/conservation honesty + strip premature cold-PASS labels) before V4-9?
2. **Or accept PARTIAL as CLOSED-for-hermetic** and proceed to **V4-9** anyway (residual debt tracked)?
3. **Push** `ultraplan/hibayes-eval-routing` after local commit of V4-8 implementation? (currently: tip `5bec28d9` + large uncommitted WIP; ahead of origin by Phase 0 evidence commit only)
4. **Vault-sync / republish** living-plan V4-8 Progress on deploydocs? (**no** until Progress text matches cold verdict; do **not** publish the dirty “cold review PASS” WIP as-is)
5. **First real paid pilot** (immutable manifest + spend cap) — separate ask, default **no**
6. **Live DB activation / production routing enablement** — separately gated, default **no**
