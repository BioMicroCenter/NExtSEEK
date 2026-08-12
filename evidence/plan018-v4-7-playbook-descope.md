# Plan 018 V4-7 — Task 11 playbook de-scope

**Recorded:** 2026-08-12  
**Gate:** V4-7 remediation (cold-review residual debt item 2)  
**Authority:** locked SDD ruling — V4-7 ships thin `route_monitoring.py`; full playbook remains Task 11.

---

## What V4-7 delivers

| Deliverable | Scope |
|-------------|--------|
| `nextseek_api/cc_assistant/route_monitoring.py` | Route-conditional observational summaries + operational alerts (policy drift, family mix, missingness, route outcome shifts) |
| `nextseek_api/eval/export.py` | Typed `OnlineObservationalRow` export with selection caveats and propensity-unavailable disclosure |
| `nextseek_api/eval/online_observation.py` | Observational schema boundary (not fitter-admissible) |

These modules are **monitoring and export only**. They do not call `publish`, `activate_generation`, or `run_v14_generation`.

---

## What remains Task 11 (out of V4-7 scope)

| Artifact | Status | Notes |
|----------|--------|-------|
| `nextseek_api/cc_assistant/playbook.py` | **Not created** | Consumer (a) in living-plan Phase 3 Task 11 |
| `nextseek_api/cc_assistant/ns_digest.py` playbook injection | **Not wired** | Requires `build_playbook(user, project_ids)` |
| `nextseek_api/cc_assistant/tests/test_playbook.py` | **Not created** | Living-plan sketches `docker exec /app` — void for worktree proof; use Lane C mount |

Full playbook guidance (`FamilyPosterior` aggregate lines, project-scoped examples, counterfactual-safe wording) is **future work** under Task 11, not V4-7.

---

## Living-plan checkbox alignment

The V4-7 checkbox *"Online observations may update route-conditional quality monitoring and playbook guidance"* is interpreted as:

1. **Monitoring** — satisfied by `route_monitoring.py` + observational export (V4-7).
2. **Playbook guidance** — **deferred** to Task 11; `route_monitoring` text is not a substitute for `build_playbook`.

Checkbox language in deploydocs living-plan was narrowed so `[x]` reflects monitoring delivery and explicit Task 11 deferral, not a claim that `playbook.py` exists.

---

## Do not conflate

- `route_monitoring.build_route_monitoring_summary` ≠ `build_playbook`
- Observational rows may inform human review of route quality; they must not update the comparative posterior (V4-7 hard boundary)
- Enabling posterior routing or production playbook injection remains separately gated
