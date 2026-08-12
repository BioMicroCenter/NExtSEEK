# Plan 018 — cold-review remediation rollup (V4-2…V4-8)

```
reviewer_kind: cold_subagent
subagent_id: a858c2ba-be49-45a0-a7df-68f4c0c8b250
parent_transcript_id: f1ace383-f8c3-4bc1-8e31-71d5d8329da1
prompt_verbatim: true
prior_implementer_review: VOID
```

**Recorded:** 2026-08-12  
**Evaluation SHA:** `8e31d26f49c2f83acb1824ce6b9299dcab0c5aa9`  
**Branch:** `ultraplan/hibayes-eval-routing`  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018`  
**Authority:** living plan `2026-07-31-hibayes-eval-routing.md`; remediation plan `cold_debt_remediation_00d90d00.plan.md`; prior cold reviews `evidence/plan018-v4-*-cold-outcome-review.md`; redispatch rollup `evidence/plan018-cold-review-redispatch-2026-08-12.json`; per-gate SDD final reviews in `evidence/plan018-v4-*-sdd-final-review.md`.

Any prior implementer-written rollup (including `subagent_id: cold-rollup-remediation-subagent-2026-08-12` @ `b9112234`) is **VOID**. This file is the sole post-remediation cold rollup artifact.

**Adjacent tip note:** Branch HEAD at review time was `100b4941` (`docs(plan018-v4-8): SDD final review with cold subagent provenance`), one commit after the evaluation SHA. Where noted below, that commit closes V48-T6 only; it does not retroactively fix other SHA-bound debt at `8e31d26f`.

---

## Verbatim charge

> Execution is complete. Ultra think, then evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Remediation commit chain (@ evaluation SHA)

| # | SHA (short) | Message |
|---|-------------|---------|
| 1 | `3c6a17e2` | `fix(plan018-v4-2): close cold-review product seam and harness debt` |
| 2 | `f8b79155` | `fix(plan018-v4-3): close cold-review judgment-stack debt` |
| 3 | `04baa2f2` | `fix(plan018-v4-4): evidence hygiene and SDD provenance` |
| 4 | `2d12a426` | `fix(plan018-v4-5): closeout refresh and MySQL payload tamper oracle` |
| 5 | `1118bb6a` | `fix(plan018-v4-6): router integration and ledger provenance debt` |
| 6 | `fdfd6eb2` | `fix(plan018-v4-7): monitoring alerts and baseline rerun` |
| 7 | `b9112234` | `feat(plan018-v4-8): close cold-review Lane M, inventory, and conservation debt` |
| 8 | `ef600db4` | `docs(plan018): honesty surfaces pre-rollup cold review` |
| 9 | `8e31d26f` | `fix(plan018): close rollup PARTIAL gaps for V4-2 mutations and closeouts` |

---

## Independent verification (this review session)

| Check | Method | Result |
|-------|--------|--------|
| V4-2 Lane C full §3.4a recipe (6 modules) | Independent docker re-run @ evaluation tree | **48 passed** in 17.26s |
| V4-2 mutation oracle wiring | Read `test_v4_2_product_mutations.py` @ `8e31d26f` | **PASS** — `test_mutation_same_session_*` and `test_mutation_swapped_routes_*` call `v4_2_verifier.validate_manifest_route_policy` |
| V4-2 HTTP cross | Read `test_v4_2_force_route_http.py`; uses `cc_router.decide`; shim has `PUBLISH_URL` | **PASS** — 5 HTTP oracles green in full Lane C run |
| V4-3 verifier | `uv run … scripts/plan018_v4_3_verifier.py` | **14/14 PASS** |
| V4-8 verifier sidecar @ `8e31d26f` | `git show 8e31d26f:evidence/plan018-v4-8-verifier.sidecar.json` | **39/39 PASS** |
| V4-8 Lane M sidecar | Read `plan018-v4-8-lane-m.sidecar.json` | **PASS** — 10 oracles incl. crash×4 + broker redelivery |
| V48-T6 SDD @ `8e31d26f` | `git show 8e31d26f:…/plan018-v4-8-sdd-final-review.md` | **FAIL provenance** — `subagent_id: v4-8-remediation-implementer` |
| V48-T6 SDD @ branch tip `100b4941` | Read working-tree file | **PASS** — `subagent_id: bff4d450-e4cc-4a8b-bd66-e6df23a1ac9a` |
| V4-5 closeout residual @ `8e31d26f` | Read JSON | Stale `"Uncommitted remediation delta"` line remains |
| V4-2 SDD final review | Read committed file | Still **PARTIAL** / pre-commit narrative contradicts 48/48 Lane C |

---

## Per-gate remediation task tables

### V4-2

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V42-T1** HTTP cross + mutation killers | **pass** | Authenticated HTTP cross is real (`test_v4_2_force_route_http.py`, 5 oracles) and **green** on Lane C. Five `_decide_route` killers cover requested/actual mismatch at `services/cc_assistant.py`. Copied-arms / swapped-route killers now bind to **`nessie_tests.v4_2_verifier.validate_manifest_route_policy`** (gap fix @ `8e31d26f`), not placeholder local helpers. Independent docker run: **48/48 PASS**. |
| **V42-T2** Lane C recipe in OPS | **pass** | `work/OPS-TESTING-HARNESSES.md` §3.4a + `evidence/plan018-v4-2-lane-c.sidecar.json` document docker mount, `dmac.test_settings`, module list, `gate: PASS`. |
| **V42-T3** set3 `schema_version` note | **pass** | `evidence/plan018-v4-2-set3-schema-version-note.json` documents historical bytes + verifier acceptance. |
| **V42-T4** SDD provenance redispatch | **partial** | Header block exists, but committed `plan018-v4-2-sdd-final-review.md` still records **PARTIAL**, HTTP red failures, and "remediation uncommitted" — contradicts independent **48/48** Lane C @ same tree. `subagent_id` is descriptive string, not Task UUID. Not an honest post-remediation SDD verdict. |

**Gate V4-2 remediation:** **partial** (implementation closed; SDD artifact stale)

---

### V4-3

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V43-T1** Human-annotation validators | **pass** | Rejection-class tests in `test_human_annotations.py`; sidecar + verifier bind oracles. |
| **V43-T2** DD-44 mutant matrix | **pass** | 14 mutants in `test_judge_mutations.py`; manifest `plan018-v4-3-dd44-mutation-manifest.json` aligns. |
| **V43-T3** V8-D golden rows | **pass** | Golden tests for `timeout`, `code_error`, `usage_policy`, `unevaluable` in `test_disposition.py`. |
| **V43-T4** Attrition + sensitivity | **pass** | `conservation.py` + tests; verifier 14/14 independent re-run. |
| **V43-T5** pytest-cov ≥95% | **pass** | `plan018-v4-3-coverage.sidecar.json`: all seven owned modules ≥95%, total 97%. |
| **V43-T6** V13-A hash re-bind @ tip | **partial** | V13-A block present with `rebind_at_prereq: true`, but `worktree_sha` frozen at **`3c6a17e`**, not evaluation tip `8e31d26f`. Success condition requires tip SHA. |
| **V43-T7** SDD provenance | **partial** | Header present; `subagent_id: v43-remediation-subagent-2026-08-12` is not a Task UUID; `parent_transcript_id: unknown`. Locked plan required redispatch provenance-bearing Task subagents. |

**Gate V4-3 remediation:** **pass** (implementation); **partial** on SHA/provenance hygiene

---

### V4-4

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V44-T1** Recovery-feasibility PASS rewrite | **pass** | `plan018-v4-4-recovery-feasibility.json` gate PASS; cites full 40-run MCMC (633s wall). |
| **V44-T2** Lane A count fix (30) | **pass** | `remediation-closeout.json` records **30** eval unit tests. |
| **V44-T3** Stale artifact banners | **pass** | `plan018-v4-4-recovery.json` carries `superseded: true` + pointer. |
| **V44-T4** Preflight pointers @ tip | **partial** | Preflight `cold_review_remediation.worktree_tip` is **`b9112234`**, not `8e31d26f`; `v4_4_worktree_sha` same lag. Honesty commit `ef600db4` predates gap-fix tip. |
| **V44-T5** SDD provenance | **partial** | Provenance header present; `subagent_id` not a Task UUID. |

**Gate V4-4 remediation:** **pass** (substance); **partial** on cross-SHA honesty

---

### V4-5

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V45-T1** Closeout refresh @ tip | **partial** | Counts (32/12/22) and `payload_canonical_tamper` oracle correct. Gap fix bumped `worktree_sha` to **`ef600db4`**, but evaluation tip is **`8e31d26f`** and `residual_debt` still lists **"Uncommitted remediation delta"** — false @ evaluation SHA. Success condition: "Closeout matches disk @ commit tip." |
| **V45-T2** Payload-canonical tamper | **pass** | `test_mysql_payload_canonical_tamper_refused_on_activate`; realstore sidecar lists oracle; Lane M 12/12. |
| **V45-T3** SDD provenance | **partial** | Header present; non-UUID `subagent_id`; written @ pre-later-gates anchor. |

**Gate V4-5 remediation:** **partial**

---

### V4-6

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V46-T1** Call-table integration | **pass** | `test_router_v46_calltable.py` exercises real classify/route path; transport_trace oracles in sidecar. |
| **V46-T2** Flag-off byte-equivalence | **pass** | Frozen baseline fixture + byte test for destination/model. |
| **V46-T3** Ledger `attempted_*` migration | **pass** | Migration `0018_turn_ledger_attempted_provenance.py`; `test_router_ledger_v46.py` reads back fields. |
| **V46-T4** Selector edge cases | **pass** | Stale/malformed/incompatible cases tested; sidecar aligned. |
| **V46-T5** Variant / zero-variant coverage | **pass** | Sidecar lists zero-variant + decisive variant transport oracles. |
| **V46-T6** SDD provenance | **partial** | Header present; non-UUID `subagent_id`. Closeout refreshed @ `ef600db4` in gap fix (attempted_* residual removed) but still one commit behind evaluation tip. |

**Gate V4-6 remediation:** **pass** (implementation); **partial** on closeout SHA + SDD provenance

---

### V4-7

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V47-T1** Monitoring alerts + tests | **pass** | `route_monitoring.py` implements policy drift, family mix, missingness, route-outcome alerts; Lane C monitoring tests green. |
| **V47-T2** Playbook de-scope doc | **pass** | `plan018-v4-7-playbook-descope.md` + honest living-plan checkbox narrowing. |
| **V47-T3** §3.1 baseline re-run | **pass** | `plan018-v4-7-baseline-rerun.log`: **1179 passed, 1 failed** (same `cc_sweep_staging` class as prior baseline). Success condition allows documented delta with fix — met. Baseline is not fully green; documented honestly. |
| **V47-T4** Propensity unavailable wire | **pass** | Export schema + tests for propensity-unavailable disclosure. |
| **V47-T5** SDD provenance | **partial** | `subagent_id: parent-dispatched-v4-7-remediation-subagent` — not a Task UUID. |

**Gate V4-7 remediation:** **pass** (monitoring debt closed); **partial** on SDD provenance

---

### V4-8

| Task | Verdict | Rationale |
|------|---------|-----------|
| **V48-T1** Commit WIP | **pass** | V4-8 product modules, tests, scripts committed in `b9112234`; clean for V4-8 paths @ evaluation SHA. |
| **V48-T2** Multi-process Lane M crash×4 + redelivery | **pass** | `test_v4_8_mysql.py` multiprocessing workers; parametric crash×4; broker redelivery; Lane M sidecar 10 oracles; log green. |
| **V48-T3** AST seam inventory + verifier | **pass** | `seam_inventory.py` AST scan; verifier 39/39 @ evaluation SHA. |
| **V48-T4** Independent conservation + attempt-ID accounting | **pass** | `spend_conservation.py` independent bucket sums; attempt-ID partition checks. |
| **V48-T5** Refuse manifest cap/TTL overrides | **pass** | `approve_manifest` refuses diverging overrides; mutation tests prove. |
| **V48-T6** SDD provenance | **partial** @ `8e31d26f` / **pass** @ `100b4941` | At evaluation SHA, SDD file still has **`v4-8-remediation-implementer`** and "uncommitted WIP" prose — suspect for locked redispatch requirement. **Immediate follow-on commit `100b4941`** overwrites with Task UUID **`bff4d450-e4cc-4a8b-bd66-e6df23a1ac9a`** and independent disk verification (Lane M 10/10, verifier 39/39). Rollup bound to `8e31d26f` marks **partial**; maintainer may treat V48-T6 closed after cherry-picking or advancing tip to `100b4941`. |

**Gate V4-8 remediation:** **pass** (hermetic oracles); **partial** on SDD @ evaluation SHA only

---

### Cross-gate honesty (HON-T1)

| Task | Verdict | Rationale |
|------|---------|-----------|
| **HON-T1** Align preflight/OI/Progress pre-rollup | **partial** | `ef600db4` honesty commit exists, but preflight `cold_review_remediation.worktree_tip` remains **`b9112234`** (not `8e31d26f`); rollup deliverable pointer correct; premature cold-PASS removed. Local living-plan Progress not vault-synced (expected until rollup PASS). |

---

## Success conditions satisfied technically but not in spirit

1. **V42-T1 copied-arms binding** — Killers invoke `v4_2_verifier` (authoritative set3 replay oracle) rather than red-on-mutation at `models_api.py` / paired-producer write path. Acceptable closure given gap-fix commit intent and cold-review allowance that harness/verifier owns manifest policy; not identical to literal "product seam" wording in remediation plan table.
2. **SDD redispatch theater (V4-2…V4-7)** — Headers claim `cold_subagent` but six of seven SDD files use descriptive `subagent_id` strings, not Task-tool UUIDs like original cold reviews. Only V4-8 receives proper UUID — and only in commit **`100b4941`**, not evaluation SHA.
3. **V4-2 SDD vs Lane C contradiction** — Sidecar and independent rerun **48/48 PASS**; committed SDD final review still says PARTIAL / HTTP red / uncommitted.
4. **SHA-bound evidence drift** — Closeouts/prereqs/preflight anchor `ef600db4`, `b9112234`, or `3c6a17e` while evaluation tip is `8e31d26f`; V4-5 closeout retains obsolete uncommitted note.

---

## Original will assessment

**Maintainer will (remediation plan):** Close every actionable cold-review residual debt V4-2…V4-8 with per-gate commits, provenance-bearing SDD redispatch, honest surfaces, then one rollup cold review; on rollup **PASS**, push + vault-sync.

**Outcome:** **Substantially progressed, not fully carried out.**

- **Closed in substance:** V4-2 product HTTP + mutation oracles (incl. `v4_2_verifier` wiring @ `8e31d26f`), V4-3 judgment stack, V4-4 evidence hygiene, V4-5 payload-canonical tamper, V4-6 router integration + ledger, V4-7 monitoring alerts, V4-8 Lane M multiprocess/crash×4/AST inventory/conservation/override refusal.
- **Not closed:** Stale V4-2 SDD artifact; SDD UUID provenance discipline for V4-2…V4-7 (V4-8 fixed one commit after evaluation SHA); tip-SHA honesty across closeouts/preflight/prereqs; V4-5 stale residual_debt line.
- **Pre-remediation cold reviews** remain valid historical record; this rollup evaluates **remediation tasks**, not re-litigating original gate DONE predicates except where remediation claimed closure.

---

## Final verdict

### **PARTIAL**

Hermetic oracle and product-seam debt is **materially closed** relative to pre-remediation cold reviews — including V42-T1 gap fix and V4-8 substantive delivery. **Actionable debt remains** on SDD artifact honesty (especially V4-2), cross-gate SHA alignment, non-UUID SDD provenance for six gates, and V48-T6 @ evaluation SHA (closed on adjacent `100b4941` only).

Do **not** authorize push or vault-sync on this rollup bound to `8e31d26f` without resolving listed debt or explicitly advancing evaluation tip to include `100b4941` **and** refreshing honesty surfaces.

---

## Residual debt (honest; non-empty)

| # | Item | Severity | Gate |
|---|------|----------|------|
| 1 | Rewrite `plan018-v4-2-sdd-final-review.md` via **fresh Task subagent** to match **48/48** Lane C @ `8e31d26f`; mark current file misleading sections VOID | **High** | V4-2 |
| 2 | Redispatch SDD reviews for V4-3…V4-7 with real Task UUID `subagent_id` (V4-8 done @ `100b4941` / `bff4d450-…`) | **Medium** | cross-gate |
| 3 | Refresh closeouts/prereqs/preflight to **`8e31d26f`** (or current tip); remove V4-5 `"Uncommitted remediation delta"` residual_debt | **Medium** | cross-gate |
| 4 | Advance branch tip to **`100b4941`** (or merge V48 SDD redispatch) before claiming V48-T6 closed @ rollup SHA | **Medium** | V4-8 |
| 5 | §3.1 baseline single failure (`cc_sweep_staging`) — documented, pre-existing, not introduced by remediation | **Low** | V4-7 |
| 6 | Living-plan vault-sync / published Progress SHA lag | **Expected** | cross-gate |
| 7 | Paid provider, live DB activation, deploy | **Expected** | out of scope |

---

## Authorization menu (maintainer)

1. **Accept PARTIAL rollup** — Do not push `ultraplan/hibayes-eval-routing` or vault-sync deploydocs Progress on this verdict @ `8e31d26f`.
2. **V4-2 SDD delta** — Redispatch SDD final review with Task provenance; align verdict with 48/48 Lane C sidecar.
3. **Evidence hygiene pass** — Bump closeouts/prereqs/preflight/OI to `8e31d26f` or `100b4941`; strip V4-5 stale residual_debt.
4. **V48-T6 tip alignment** — Treat `100b4941` as closing V48-T6; include in next rollup evaluation SHA or cherry-pick onto remediation branch before PASS claim.
5. **SDD reprovenance V4-3…V4-7** — Optional process waiver vs fresh Task redispatch per locked plan.
6. **Re-run rollup cold subagent** — After items 1–4, dispatch fresh rollup with same verbatim charge @ new tip.
7. **Push** — Only after subsequent rollup verdict **PASS** with empty actionable residual debt; branch is 11+ commits ahead of last known push anchor.
8. **Vault-sync deploydocs** — Only after rollup **PASS**.
9. **Paid / live DB / deploy** — Not part of this remediation; remain separately gated.

---

*Review method: read living plan, remediation plan, prior cold reviews, redispatch JSON, all seven SDD final reviews, evidence sidecars/logs @ `8e31d26f`; independent Lane C docker re-run (48/48); independent V4-3 verifier run; git object inspection for V48 SDD @ evaluation SHA vs `100b4941`; source inspection of mutation oracle wiring, closeout SHA fields, SDD provenance headers. No implementer conversation history used.*
