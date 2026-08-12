# Plan 018 V4-6 — independent SDD whole-branch review

**Reviewer:** independent SDD reviewer (no implementer conversation for this artifact)  
**Recorded:** 2026-08-12  
**Base:** `0a5b052a` (V4-5 vault-sync DONE tip)  
**Tip:** uncommitted V4-6 implementation atop `85d2660a` (Phase 0 evidence only committed)  
**Charter:** Task 8 — whole-branch review per SDD + living-plan V4-6 DONE conditions.

---

## Scope reviewed

- BAML split: `classifier.baml` (ClassifiedFamily @@dynamic, ClassifyQuery) + restored pin-compatible `router.baml`
- Generated client regen recipe (`baml-cli generate --from dmac_assistant/baml_src`)
- Python seams: `family_labels.py`, `baml_introspect.py`, `router.py`, `posterior_selector.py`, `transport_trace.py`
- Product path: `services/cc_assistant.py` `_decide_route` sticky attempted/actual + `_record_ledger_row`
- `RouteSource.posterior` in `router_models_proposal.py`
- Tests: family labels, BAML pins, call-table, mutations, selector, ledger, risk overlay
- Lane C: **42/42** (`evidence/plan018-v4-6-lane-c.log`)
- Verifier: **28/28** (`evidence/plan018-v4-6-verifier.sidecar.json`)

---

## Findings

| Area | Verdict | Notes |
|------|---------|-------|
| Structural classifier ≠ router | PASS | Classifier schema has no route/model; ClassifyQuery in separate `classifier.baml`; RouteQuery unchanged in `router.baml` |
| Dual BAML identity (router) | PASS | dmac vs docker/cc-runtime router.baml byte-identical; classifier copies identical |
| Corpus-owned dynamic enum | PASS | `family_labels` reads `nessie_tests/corpus.json` families only; bidirectional equality test |
| Call-count table rows | PASS | All 7 modes (6 + sticky) covered in `test_router_v46_calltable.py` |
| Posterior selector + flag default off | PASS | `getattr(..., False)`; overlay `may_reroute` still False-only |
| Ledger generation provenance | PASS (partial spirit) | `_record_ledger_row` persists generation id/hash; attempted_route not in DB columns |
| Transport observers | PASS (partial spirit) | Hooks installed on generated client; call-table tests mock inner calls so transport counts are 0 under mock |
| Mutation killers | PASS | Dedicated mutation module + existing BAML pin suite |
| Lane discipline | PASS | Lane C docker worktree mount + dmac.test_settings; no host Django burn |
| Evidence binding | PASS | Sidecars + inventory hashes for baml/corpus |

No blocking defects on implementation delta.

---

## Residual (non-blocking)

- Implementation uncommitted atop Phase 0; push awaits maintainer ask.
- `baml_client/` gitignored — evidence binds `baml_src` hashes, not generated tree SHA.
- Call-table tests assert call graph via mocks, not live provider transport (acceptable for hermetic unit oracle; integration transport proof is thin).
- Sticky attempted/actual on `RouteDecision` only; TurnLedger schema unchanged.

---

## Verdict

**APPROVED** — V4-6 implementation satisfies SDD Task 8 whole-branch review for cold-outcome gate.
