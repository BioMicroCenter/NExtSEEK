# Plan 018 V4-6 — independent cold-context outcome review

**Reviewer:** independent cold agent (no implementer conversation history)  
**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing`  
**Base:** `0a5b052a` (V4-5 vault-sync DONE)  
**HEAD:** `85d2660a` + uncommitted V4-6 WIP (`dirty_diff_sha256` 47f6de0d…)  
**deploydocs:** local V4-6 progress update (not pushed)

---

## Verbatim prompt

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Verification performed (independent)

| Check | Result |
|-------|--------|
| Phase 0 sidecar | gate PASS; deploydocs pushed @ 3c5f531c; vault-sync @ a48e4a24 |
| Lane C log | **42 passed** (`evidence/plan018-v4-6-lane-c.log`) |
| Verifier | **28/28** PASS (`evidence/plan018-v4-6-verifier.sidecar.json`) |
| `classifier.baml` | ClassifiedFamily @@dynamic; ClassificationDecision without route/model |
| `router.baml` | Restored pre-split RouteQuery; dual copy byte-identical |
| `posterior_selector.py` | exists; flag default off via getattr False |
| `risk_overlay.py` | may_reroute assignments all False |
| `route_capabilities` in family seam | absent from module bodies |
| Ownership map | names `posterior_selector.py` + tests |
| Push after Phase 0 | **not done** (hard refuse respected) |

---

## Per-task verdicts

| Task | Verdict | Rationale |
|------|---------|-----------|
| **Phase 0** | **pass** | Living-plan V4-6 start banner; stale V4-5 vault prose fixed; deploydocs pushed; registry synced; phase0 JSON gate PASS. |
| **0 — Prereq** | **pass** | V4-0…V4-5 CLOSED in preflight; Lane C smoke green; ownership map amended; inventory scaffold → complete; `plan018-v4-6-prereq.json` gate PASS. |
| **1 — family_labels** | **pass** | Corpus-only read seam; bidirectional enum test; no route_capabilities reads; sidecar PASS. |
| **2 — BAML split** | **pass** | Classifier in separate file preserves router prompt pins; dual router.baml identity; schema oracle proves no destination/model on classifier; baml_client regenerated via established recipe. |
| **3 — call-table + transport** | **partial** | All call-table rows + sticky attempted/actual covered with PASS. Transport hooks exist on generated client, but tests mock `_classify_query`/`_route_query` so provider-transport tracing is not exercised end-to-end on real asyncio calls — row logic is proven, live transport counting is not. |
| **4 — posterior selector** | **pass** | New module (not overlay); flag default off; RouteSource.posterior; differential fallback cases; sidecar PASS. |
| **5 — ledger provenance** | **partial** | Generation id/hash written on posterior rows; ledger collision non-blocking. **Gap:** attempted_route/attempted_source not persisted to TurnLedger columns — only on RouteDecision for sticky override. |
| **6 — inventory + verifier** | **pass** | Inventory complete with hashes; mutation killers; verifier 28/28 exit 0. |
| **7 — closeout** | **pass** | Closeout/preflight updated; living-plan Progress honest (pending cold PASS, checkboxes open); no remote push. |
| **8 — SDD review** | **pass** | `plan018-v4-6-sdd-final-review.md` APPROVED. |
| **9 — cold review** | **pass** | This artifact. |

---

## Spirit gaps

1. **Mock-heavy call-table proof:** Satisfies the call-count *graph* but not literal provider-transport byte observation under flag-on paths without mocks. Plan wording emphasized "real generated clients"; hooks are wired but oracle is primarily mock-driven.

2. **Ledger attempted vs actual:** Sticky records attempted on RouteDecision; ledger row stores final route only. A maintainer auditing overrides from DB alone cannot see attempted route without expanding schema or reasoning text parsing.

3. **Uncommitted implementation:** Substantive V4-6 code is WIP on disk; only Phase 0 evidence is committed. Push and vault-sync DONE markers correctly deferred.

4. **baml_client gitignored:** Regeneration is recipe-bound via sidecar hashes on `baml_src`; generated tree not in git diff review.

---

## Final verdict on maintainer's will

**PASS — V4-6 hermetic implementation carries out the locked scope.**

Structural classifier/router split, corpus-dynamic family labels, flag-gated comparative posterior selector, call-count table coverage, sticky attempted/actual on the decide path, and evidence/verifier binding are implemented and green on Lane C. Hard refuses respected (no push after Phase 0, no live DB, flag default off).

Partial items (transport integration depth, ledger attempted columns) are residual debt, not blockers for hermetic gate closeout pending maintainer authorization on push/vault-sync.

---

## Residual debt

1. Commit V4-6 implementation locally (single or logical commits) before push.
2. Optional: integration test one call-table row without mocking `_classify_query`/`_route_query` to assert transport_trace counts on real baml client dispatch.
3. Optional: TurnLedger columns or JSON provenance field for attempted_route/attempted_source.
4. Living-plan V4-6 checkboxes remain `[ ]` until maintainer accepts cold PASS and authorizes vault-sync DONE republish.

---

## Authorization menu

1. **Proceed to V4-7 hermetic?** V4-6 implementation complete locally; next gate is experimental/observational separation per living plan.
2. **Push V4-6 implementation commits on `ultraplan/hibayes-eval-routing`?** Branch is 1 commit ahead (Phase 0 only) + uncommitted WIP; recommend commit then push on approval.
3. **Vault-sync / republish living-plan V4-6 DONE Progress?** Local deploydocs has honest "pending cold PASS" banner; republish after maintainer accepts this review.
4. **Live DB activation / production routing enablement / paid runs** — remain separately gated (default **no**).
