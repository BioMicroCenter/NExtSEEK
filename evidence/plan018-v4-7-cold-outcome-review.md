# Plan 018 V4-7 — independent cold-context outcome review

**Reviewer:** independent cold agent (no implementer conversation history)  
**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing`  
**Base:** `b2cca5d7` (V4-6 vault-sync evidence tip)  
**HEAD:** `b2cca5d7` + uncommitted V4-7 implementation  
**deploydocs:** local V4-7 progress update (not pushed)

---

## Verbatim prompt

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Verification performed (independent)

| Check | Result |
|-------|--------|
| Phase 0 sidecar | gate PASS-WITH-LOCAL-DEPLOYDOCS; deploydocs banner edited locally; push/vault-sync deferred |
| Prereq | gate PASS; V4-0…V4-6 CLOSED; Lane C/A smoke green |
| Lane A schemas | **11 passed** (`evidence/plan018-v4-7-lane-a-schemas.log`) |
| Lane C log | **30 passed** (`evidence/plan018-v4-7-lane-c.log`) |
| Lane M | **15 passed** (`evidence/plan018-v4-7-lane-m.sidecar.json`) |
| Verifier | **30/30** PASS (`evidence/plan018-v4-7-verifier.sidecar.json`) |
| Migration leaf | `0016_paired_run_registry.py` |
| `fit_boundary.py` | refuse + validate_publish_provenance + require_approved_paired_run present |
| `route_monitoring.py` | no publish/activate substring imports; monitoring disclaimer present |
| Posterior flag | default off via getattr False |
| Push after Phase 0 | **not done** (hard refuse respected) |

---

## Per-task verdicts

| Task | Verdict | Rationale |
|------|---------|-----------|
| **Phase 0** | **partial** | Living-plan V4-7 start/close Progress banner edited locally; phase0 JSON written. deploydocs push + vault-sync not performed (authorization deferred). |
| **0 — Prereq** | **pass** | V4-0…V4-6 CLOSED; stale V4-6 closeout hygiene refreshed; ownership map amended; inventory scaffold → complete; prereq gate PASS. |
| **1 — schemas** | **pass** | EvidenceKind, PairedExperimentalBatch, OnlineObservationalRow with validators; Lane A 11/11; sidecar PASS. |
| **2 — fit refuse** | **pass** | fit_boundary wired at admission/pair_rows/v14/publish/generation_store; injection matrix + conservation test; sidecar lists cases PASS. |
| **3 — registry + Lane M** | **pass** | PairedRunRegistry ORM + 0016 migration; require_approved_paired_run; Lane C + Lane M negatives green. |
| **4 — monitoring** | **pass** | export emits OnlineObservationalRow; route_monitoring aggregates with caveats; import guard green; sidecar PASS. |
| **5 — inventory + verifier** | **pass** | Seam inventory complete; mutation killers; verifier 30/30 exit 0. |
| **6 — closeout** | **pass** | Preflight next_gate V4-8; closeout JSON; living-plan Progress honest (checkboxes open); OI refresh; no push. |
| **7 — SDD review** | **pass** | `plan018-v4-7-sdd-final-review.md` APPROVED. |
| **8 — cold review** | **pass** | This artifact. |

---

## Spirit gaps

1. **Phase 0 push/vault-sync:** Plan Phase 0 success condition called for matching deploydocs + vault SHA on remotes. Local-only edit satisfies hermetic start intent but not full publication oracle — correctly deferred, not silently claimed PASS.

2. **Thin monitoring vs Task 11 playbook:** Living plan mentions playbooks; V4-7 ships `route_monitoring.py` labels only. Ownership map correctly keeps full `playbook.py` as Task 11 future work — spirit aligned with locked SDD scope, but checkbox wording may read broader than delivered.

3. **Uncommitted implementation:** Substantive V4-7 code is WIP on disk atop evidence-only V4-6 tip commit. Push and vault-sync DONE markers correctly deferred.

4. **Scoped test oracle:** V4-7 suite (30 Lane C + 15 Lane M + 11 Lane A) is green; full §3.1 hermetic baseline not re-run after provenance gate — acceptable for gate scope, not a full regression proof.

---

## Final verdict on maintainer's will

**PASS — V4-7 hermetic implementation carries out the locked scope.**

Typed experimental/observational separation, approved paired-run registry, hard refuse at fit/publish/activate seams, observational export with selection caveats, route-conditional monitoring without publisher calls, seam inventory, mutation killers, and verifier binding are implemented and green on established lanes. Hard refuses respected (no push after Phase 0, no live DB, posterior flag default off).

Phase 0 remote publication remains partial by explicit authorization deferral, not by silent omission.

---

## Residual debt

1. Commit V4-7 implementation locally before push.
2. Maintainer authorization: push implementation branch, deploydocs Phase 0/V4-7 DONE republish + vault-sync.
3. Optional: scan remaining tests/fixtures for `publish_generation` without provenance beyond risk_overlay/eval_publish.
4. Task 11 full `playbook.py` / `ns_digest` — future gate, not V4-7 debt if scope lock holds.
5. Living-plan V4-7 checkboxes remain `[ ]` until maintainer accepts cold PASS and authorizes vault-sync.

---

## Authorization menu

1. **Proceed to V4-8 hermetic?** (still no paid/provider until separate ask)
2. **Push V4-7 implementation** on `ultraplan/hibayes-eval-routing`?
3. **Vault-sync / republish** living-plan V4-7 DONE Progress on deploydocs `dev`?
4. **Push deploydocs** Phase 0 + closeout Progress commit?
5. **Live DB activation / production routing enablement / paid** — remain separately gated (default **no**).
