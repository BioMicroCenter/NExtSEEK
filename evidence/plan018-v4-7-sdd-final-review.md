# Plan 018 V4-7 — independent SDD whole-branch review

**Reviewer:** independent SDD reviewer (no implementer conversation for this artifact)  
**Recorded:** 2026-08-12  
**Base:** `b2cca5d7` (V4-6 vault-sync evidence tip)  
**Tip:** uncommitted V4-7 implementation atop `b2cca5d7`  
**Charter:** Task 7 — whole-branch review per SDD + living-plan V4-7 DONE conditions.

---

## Scope reviewed

- Typed evidence boundary: `evidence_kinds.py`, `paired_run.py`, `online_observation.py`
- Fit/publish refuse: `fit/fit_boundary.py` wired into `conservation.py`, `pair_rows.py`, `combined.py`, `publish.py`, `generation_store.py`
- Approved paired-run lineage: `PairedRunRegistry` ORM + `0016_paired_run_registry.py` + `paired_run_registry.py`
- Observational export + monitoring: `export.py` (`OnlineObservationalRow`), `route_monitoring.py` (no publisher imports)
- Tests: `test_v4_7_schemas`, `test_v4_7_fit_refuse`, `test_v4_7_paired_run_registry`, `test_v4_7_route_monitoring`, `test_v4_7_mutation_killers`; updated `test_eval_publish`, `test_generation_store_mysql`, `test_risk_overlay`
- Lane A schemas: **11/11** (`evidence/plan018-v4-7-lane-a-schemas.log`)
- Lane C: **30/30** (`evidence/plan018-v4-7-lane-c.log`)
- Lane M: **15/15** (`evidence/plan018-v4-7-lane-m.sidecar.json`)
- Verifier: **30/30** (`evidence/plan018-v4-7-verifier.sidecar.json`)

---

## Findings

| Area | Verdict | Notes |
|------|---------|-------|
| Disjoint schemas + EvidenceKind enum | PASS | Closed enum; schema version constants; extra=forbid on pydantic models |
| Online row counterfactual ban | PASS | Validator rejects banned phrases; DEFAULT_SELECTION_CAVEAT avoids self-trigger |
| Fit boundary refuse (typed + dict) | PASS | OnlineObservationalRow, forged kind/version, online schema_version rejected |
| Publish/activate provenance gate | PASS | `validate_publish_provenance` requires paired_run_id + forced route_source + registry row |
| PairedRunRegistry migration | PASS | Leaf advanced to 0016; Lane M negatives for unapproved/forged provenance |
| Conservation / zero online IDs in hash | PASS | Dedicated test in fit_refuse suite |
| Export observational rows | PASS | `export_observational_rows` / `ledger_row_to_observational`; forced rows skipped |
| route_monitoring isolation | PASS | No publish/activate/run_v14 imports; AST guard test green |
| Posterior flag default off | PASS | Verifier confirms getattr False in posterior_selector |
| Overlay may_reroute False-only | PASS | Verifier confirms all assignments False |
| Seam inventory complete | PASS | All named fit/publish/activate/monitoring seams listed status complete |
| Mutation killers | PASS | Boundary removal / forged discriminator / mixed batch / monitoring import tests |
| Lane discipline | PASS | Lane C docker worktree + dmac.test_settings; Lane A --noconftest; Lane M disposable MySQL |
| Phase 0 deploydocs | PASS (partial) | Living-plan banner edited locally; push/vault-sync deferred per hard refuse |

No blocking defects on implementation delta.

---

## Residual (non-blocking)

- Implementation uncommitted atop `b2cca5d7`; push awaits maintainer ask.
- Phase 0 deploydocs commit not pushed; vault-sync for V4-7 DONE markers pending authorization.
- Full Task 11 `playbook.py` / `ns_digest` intentionally out of scope — thin `route_monitoring.py` only.
- Broader §3.1 baseline (1179+ tests) not re-run; V4-7 scoped suite green only.
- Legacy tests updated for provenance gate (`test_risk_overlay`, `test_eval_publish`); other publish callers may need same pattern if encountered later.

---

## Verdict

**APPROVED** — V4-7 implementation satisfies SDD Task 7 whole-branch review for cold-outcome gate.
