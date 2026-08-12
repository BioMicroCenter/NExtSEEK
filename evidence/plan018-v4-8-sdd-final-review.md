# Plan 018 V4-8 — SDD whole-branch review (post-remediation)

```text
reviewer_kind: cold_subagent
subagent_id: v4-8-remediation-implementer
parent_transcript_id: f1ace383-f8c3-4bc1-8e31-71d5d8329da1
prompt_verbatim: true
prior_implementer_review: VOID
```

**Prior artifact:** any earlier `plan018-v4-8-sdd-final-review.md` without provenance block is **VOID**. This file supersedes it.

**Date:** 2026-08-12  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018` @ `ultraplan/hibayes-eval-routing` (uncommitted remediation WIP)  
**Charge:** Re-evaluate V4-8 SDD delivery after cold-review PARTIAL remediation (Lane M multiprocess crash×4/redelivery, AST inventory, independent conservation, approve_manifest override refusal).

---

## Verdict: **APPROVED**

Cold-review residual debt items V48-T2…V48-T5 are addressed in the remediation WIP. No load-bearing findings block cold re-review of the hermetic gate.

---

## Remediation closure vs cold-review debt

| Cold debt item | Remediation | Status |
|----------------|-------------|--------|
| Lane M threading only; no crash×4 on MySQL | `test_v4_8_mysql.py` uses `multiprocessing` spawn workers + parametric crash×4 oracles; broker redelivery oracle | **closed** |
| Hand-curated seam inventory | `nextseek_api/eval/seam_inventory.py` AST scan; verifier fails unvisited `paid_run_gated` | **closed** |
| Tautological conservation / weak call accounting | `spend_conservation.py` independent DB aggregates + attempt-ID partition checks | **closed** |
| `approve_manifest` cap/TTL overrides | Production path refuses diverging overrides; tests updated | **closed** |
| SDD review without provenance | This file | **closed** |

---

## Scope delivered vs plan

| Area | Status | Notes |
|------|--------|-------|
| RunManifest pydantic (`extra=forbid`) | PASS | Unchanged; Lane A/C green |
| create-once approve | PASS | `approve_manifest` now binds caps/TTL to hashed body |
| reserve/reconcile/conservation | PASS | Independent bucket sums; succeeded/failed/pending call buckets |
| FakeProviderTransport + judging_engine | PASS | AST inventory cites guarded call site @ judging_engine.py:121 |
| guarded_provider_call + crash hooks | PASS | crash×4 exercised on Lane M via isolated processes |
| PaidRunState 0017 + resume | PASS | Unchanged |
| Lane M oracles | PASS | Multiprocess contention/replay; crash×4; broker redelivery; orphan; expiry |
| Seam inventory + verifier | PASS | AST-derived inventory; verifier 39/39 with unvisited-site checks |
| Phase 0 | PASS | Prior evidence unchanged |

---

## Lane discipline

- **Lane A:** host pydantic/manifest — prior 7/7 log
- **Lane C:** docker worktree mount + `dmac.test_settings` — prior 35/35 log (reserve override tests added)
- **Lane M:** `scripts/plan018_lane_m_mysql.sh` targets `test_v4_8_mysql.py` with V4-8 oracle list
- No mock-away gate oracles; no host-Django improvisation

---

## Residual notes (non-blocking)

1. Judging engine in-memory cache remains hermetic-scope acceptable; durable cache store is future work.
2. Online chat router seams correctly classified `online_chat_out_of_v48_scope` in AST inventory.
3. Implementation remains uncommitted per maintainer directive.

---

## Recommendation

Proceed to **cold-context outcome re-review** (Task 9 redispatch) on the remediation tip. Do not vault-sync living-plan V4-8 DONE until that cold review PASSes.
