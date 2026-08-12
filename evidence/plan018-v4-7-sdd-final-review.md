# Plan 018 V4-7 — SDD remediation final review

## Provenance

| Field | Value |
|-------|-------|
| `reviewer_kind` | `cold_subagent` |
| `subagent_id` | `parent-dispatched-v4-7-remediation-subagent` |
| `parent_transcript_id` | unknown (parent-dispatched; no Task id surfaced) |
| `prompt_verbatim` | false (SDD whole-branch remediation review charter, not cold-outcome charge) |
| `prior_implementer_review` | **VOID** — pre-remediation SDD review lacked `subagent_id` provenance block |
| `recorded_at` | 2026-08-12 |
| `remediation_scope` | cold-review residual debt V47-T1..T5 |

---

## Scope reviewed

Remediation against cold-review residual debt ([`plan018-v4-7-cold-outcome-review.md`](plan018-v4-7-cold-outcome-review.md)):

| Item | Deliverable | Status |
|------|-------------|--------|
| V47-T1 | Drift/missingness/family-mix/route-outcome alerts in `route_monitoring.py` + tests | **PASS** |
| V47-T2 | Playbook de-scope doc + living-plan checkbox language | **PASS** |
| V47-T3 | §3.1 hermetic baseline re-run log | **PASS** (best effort; see below) |
| V47-T4 | `propensity_unavailable` field wired in export + tests | **PASS** |
| V47-T5 | This SDD final review with cold_subagent provenance | **PASS** |

---

## Findings

### Monitoring alerts (V47-T1)

`route_monitoring.py` now exposes:

- `RouteMonitoringSnapshot` / `build_monitoring_snapshot`
- `detect_monitoring_alerts` with `AlertKind`: `policy_drift`, `family_mix_shift`, `missingness_spike`, `route_outcome_change`
- `build_route_monitoring_summary(..., baseline=...)` embeds alert text when baseline provided

Lane C monitoring tests: **9/9** (was 3/3 pre-remediation).

### Playbook de-scope (V47-T2)

- [`evidence/plan018-v4-7-playbook-descope.md`](plan018-v4-7-playbook-descope.md) documents Task 11 deferral
- Deploydocs living-plan V4-7 checkboxes narrowed: monitoring delivered; `playbook.py`/`ns_digest` explicitly Task 11

### §3.1 baseline (V47-T3)

Re-run per OPS §3.1 host variant against `NExtSEEK-deploydocs`:

- **1179 passed, 1 failed** (`test_cc_staging_sweep.py::test_management_command_recovery_delivers_all` — known `DJANGO_SETTINGS_MODULE` / `cc_sweep_staging` wiring gap documented in OPS §3.1)
- Log: [`evidence/plan018-v4-7-baseline-rerun.log`](plan018-v4-7-baseline-rerun.log)
- Delta vs prior 1136/1: +43 tests collected (plan018 branch growth on deploydocs tree); same single known failure class

### Propensity unavailable (V47-T4)

- `OnlineObservationalRow` adds `propensity_unavailable: bool` + `propensity_unavailable_reason`
- Export sets unavailable + `PROPENSITY_UNAVAILABLE_REASON` when ledger lacks propensity; wires value when `assignment_propensity` attribute present
- Lane A schema tests: **13/13** (+2 propensity oracles)

---

## Test counts (remediation tip, uncommitted)

| Lane | Target | Count |
|------|--------|-------|
| Lane A schemas | `test_v4_7_schemas.py` | **13/13** |
| Lane C monitoring | `test_v4_7_route_monitoring.py` | **9/9** |
| Lane C core V4-7 bundle | monitoring + registry + risk_overlay + eval_publish + mutation_killers + fit_refuse | **36/36** |
| §3.1 host baseline | deploydocs cc_assistant hermetic | **1179 passed, 1 failed** (known) |

---

## Residual debt

1. **Task 11 playbook** — still future; de-scope documented
2. **TurnLedger propensity column** — export wires attribute when present; no DB column yet (explicit unavailable is honest)
3. **§3.1 single failure** — pre-existing staging-command settings gap; not introduced by V4-7 remediation

---

## Verdict

**APPROVED** — V4-7 cold-review residual debt for monitoring alerts, playbook de-scope, propensity disclosure, and baseline re-run is closed at implementation level. Living-plan checkbox language now matches delivered behavior.
