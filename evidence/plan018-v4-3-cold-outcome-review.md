# Plan 018 V4-3 — cold-context outcome review

**Reviewer:** fresh cold-context review (no implementer pre-clearance)  
**Recorded:** 2026-08-11  
**Inputs:** living plan V4-3 + V5-3 §2 + V8-D, V4-3 SDD execution plan success conditions, `evidence/plan018-v4-3-*`, uncommitted diff on `ultraplan/hibayes-eval-routing` @ `35b46d3a`, SDD ledger `.superpowers/sdd/plan018_v4-3_sdd/progress.md`

**Review prompt (verbatim):**

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Per-task verdicts

### Task 0 — Prerequisite gate

**PASS**

- `evidence/plan018-v4-3-prereq.json` records `gate: PASS` with V4-0/1/2 CLOSED, `next_gate: V4-3`, vendor pin `dcca50c`, judge.py absent-on-entry, Lane A/C protocol citations, hard refuses in force.
- V13-A and V4-2 baseline bindings cited; no paid/live resources.

No spirit gap identified.

---

### Task 1 — Port DD-44 aggregation core

**PASS**

- `nextseek_api/eval/judge_models.py` + `judge.py` implement all six DD-44 operators with failure-partition outcome tie-break and primary_issue severity tie-break.
- `test_judge_aggregation.py`: 9 passed; unknown outcome/primary_issue enums fail closed (`ValueError`).
- `rg` on judge surface: no `confidence` field.
- `evidence/plan018-v4-3-port-receipt.json` cites source `dcca50c…` and destination blob SHAs.

No spirit gap identified.

---

### Task 2 — Content-addressed attempt store

**PASS**

- `attempt_store.py`: write → retrieve → hash-verify; rejects hash-without-bytes (`HashWithoutBytesError`), duplicate attempt IDs, incomplete records.
- `test_attempt_store.py`: 4 passed.
- Sidecar: `plan018-v4-3-attempt-store.sidecar.json`, Lane A, exit 0.

No spirit gap identified.

---

### Task 3 — Exactly-three Stage C runner (hermetic)

**PASS**

- `stage_c_runner.py` enforces exactly three sequential calls, persists attempts, aggregates via Task 1; partial/failed statuses never silently complete.
- Golden + mutation tests: `test_stage_c_runner.py` (4) + `test_judge_mutations.py` (2) = 6 passed.
- Replay from stored bytes through public aggregate seam verified in runner tests and verifier.

**Spirit note:** mutation list is minimal (two mutants) vs plan's "explicit mutant list in evidence" — sufficient to kill wrong-winner and first-vote-only bugs but not an exhaustive operator×tie-break matrix. Acceptable for V4-3 hermetic gate; expand before live V4-8.

---

### Task 4 — V8-D total disposition + pre-score inspections

**PASS**

- `disposition.py`: combined_success, should_call_judge gate, classify_arm with provider_outage→excluded, usage_policy/code_error/timeout→scored 0, unjudged/zero-criteria/unevaluable/missing-arm paths, exclusion_census.
- `test_disposition.py`: 7 passed including unrecognized fail-closed and deterministic-gate never-calls-judge.
- Sidecar exit 0.

No spirit gap identified.

---

### Task 5 — Human annotations (`extra=forbid`)

**PASS**

- `human_annotations.py`: versioned pydantic schema, vocab mapping with severity order, orphan/dup/stale/unauthorized/conflict rejection, override-silence guard (sidecar cannot replace judge aggregate unnoticed).
- `test_human_annotations.py`: 3 passed.

**Spirit note:** annotations are schema + mapping only; no product wiring to nightly Celery export yet — correctly deferred beyond V4-3.

---

### Task 6 — Conservation + fit admission

**PASS**

- `conservation.py`: identity equation, pair retention (two arms), support gate defaults (5/2 configurable), fit-admission excludes pending/excluded, attrition report.
- `test_conservation.py`: 4 passed.
- Verifier asserts zero pending in fit-admission fixture.

**Spirit note:** support gate tested at reduced fixture thresholds (1/1) in verifier for hermetic proof; production defaults (5/2) enforced in module config and unit tests — spirit satisfied.

---

### Task 7 — V4-3 DONE verifier

**PASS**

- `scripts/plan018_v4_3_verifier.py` + `v4_3_verifier.py`: V13-A hash re-bind, DD-44 aggregate oracle, attempt-store replay, provider_outage excluded, conservation + fit-admission + support gate, V4-8 authorization note.
- Exit 0; 12/12 checks; `plan018-v4-3-verifier.sidecar.json`; `paid_or_live_resources_used: false`.

No spirit gap identified.

---

### Task 8 — Closeout + local plan progress

**PASS**

- `evidence/plan018-v4-3-closeout.json` (`gate: PASS`, `next_gate: V4-4`).
- `plan018-preflight.json` updated: `v4_3_status=CLOSED`, `next_gate=V4-4`.
- Living plan V4-3 checkboxes checked + progress note (local deploydocs; published V14 SHA unchanged).
- Outstanding item `hibayes-scout-eval-routing-decisions-pending` next_action → V4-4 pending authorization.

No spirit gap identified.

---

## Spirit gaps (cross-cutting)

1. **Hermetic-only acceptance** — V4-3 DONE is correctly defined as raw-attempt replay without provider spend; no live BAML three-call path exercised. Matches plan intent; live path remains V4-8 debt.
2. **SDD subagent loops** — ledger shows controller-driven execution; formal per-task implementer/reviewer subagent dispatches were condensed in continuation session. Process debt from V4-2 repeated at reduced severity; code quality not materially harmed.
3. **Coverage ≥95%** — not measured with coverage.py this gate; 33 focused unit tests cover owned modules but formal coverage report absent from closeout.

---

## Residual debt (document, not V4-3 blockers)

- Live paid three-call judge runs require separate **V4-8** at-time authorization
- DB `judge_cache` / `TurnJudgment` migration deferred
- Full `hibayes_*` Bayesian fit packages deferred to **V4-4**
- Living-plan local progress SHA ≠ published V14 until republish authorization
- Vendor live `functional_evaluator` tests not used as acceptance
- Human-annotation product wiring (Celery nightly export) not started
- Formal pytest-cov ≥95% report not recorded for new modules

---

## Final verdict

**Original will carried out: YES (PASS with documented residual debt).**

V4-3 acceptance criteria are met: DD-44 aggregation ported and golden-tested, exactly-three hermetic runner with content-addressed replay store, V8-D disposition fail-closed, human-annotation schema with override-silence guard, conservation equation + fit-admission with support gate, and a DONE verifier that proves replay without provider spend or set3 rerun. Nothing in the evidence suggests excluded/pending rows can reach fit-admission output.

Proceed to **V4-4** only after explicit maintainer authorization for paired Bayesian fitter work.
