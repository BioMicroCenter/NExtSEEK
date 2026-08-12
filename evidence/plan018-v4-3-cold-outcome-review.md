# Plan 018 V4-3 — cold-context outcome review

reviewer_kind: cold_subagent  
subagent_id: cold-reviewer-v4-3-2026-08-12  
parent_transcript_id: unknown  
prompt_verbatim: true  
prior_implementer_review: VOID  

**Recorded:** 2026-08-12 (independent cold review; prior parent-written artifact at this path is VOID)  
**Worktree:** `/home/taishajo/work/NExtSEEK-plan018`  
**Plan:** `/home/taishajo/.cursor/plans/plan018_v4-3_sdd_9ae91f7b.plan.md`  
**Living plan §V4-3:** `/home/taishajo/work/NExtSEEK-deploydocs/docs/superpowers/plans/2026-07-31-hibayes-eval-routing.md`  
**Evidence glob:** `evidence/plan018-v4-3-*`  
**Closeout SHA:** `4e204341ae82e7cd8c99ca0c3811d7bff78f262e` (per `plan018-v4-3-closeout.json`)

## Verbatim charge

> Execution is complete. Ultra think, then evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

## Method (no implementer history)

- Read living-plan V4-3 DONE predicate, V5-3 §2 DD-44 oracle language, and V8-D disposition requirements.
- Read SDD plan task success conditions and all `plan018-v4-3-*` evidence sidecars/closeout.
- Read product modules under `nextseek_api/eval/` (judge, attempt_store, stage_c_runner, disposition, human_annotations, conservation, v4_3_verifier).
- **Independently re-ran** Lane A acceptance:
  - `uv run --no-project --with pydantic --with orjson python scripts/plan018_v4_3_verifier.py` → **PASS (12/12)**
  - `pytest` on seven V4-3 eval test modules → **33 passed**
- Did not trust the void prior review or closeout labels alone.

---

## Per-task verdicts

| Task | Verdict | Summary |
|------|---------|---------|
| 0 — Prerequisite gate | **partial** | Preflight V4-0/1/2 CLOSED and vendor pin verified; V13-A corpus hash re-bind not recorded in prereq JSON (deferred to verifier). |
| 1 — DD-44 aggregation port | **pass** | Six operators ported; 9 tests green; no confidence field; port receipt cites `dcca50c`. |
| 2 — Content-addressed attempt store | **pass** | Round-trip hash-verify; hash-without-bytes rejected; duplicate/invalid call_index fail closed. |
| 3 — Exactly-three Stage C runner | **partial** | Exactly-three + replay proven; oracle/mutation coverage thinner than V5-3 §2 asks. |
| 4 — V8-D disposition | **partial** | Core mapping + fail-closed present; not every V8-D table row has a golden test. |
| 5 — Human annotations | **partial** | Schema + vocab map + override-silence guard; orphan/dup/stale/auth/conflict rejection missing. |
| 6 — Conservation + fit admission | **partial** | Identity + fit-admission gate work; differential attrition/sensitivity bounds not implemented. |
| 7 — V4-3 DONE verifier | **pass** | 12/12 checks; no provider spend; V4-8 note recorded; independently reproduced PASS. |
| 8 — Closeout + progress | **pass** | Closeout JSON, preflight `v4_3_status=CLOSED`, living-plan local progress, OI refresh. |
| 9 — Cold outcome review | **pass** | This artifact (provenance-bearing cold subagent review). |

### Task 0 — Prerequisite gate (**partial**)

`evidence/plan018-v4-3-prereq.json` records `gate: PASS` with V4-0/1/2 CLOSED, `next_gate: V4-3`, vendor `dcca50c`, judge.py absent-on-entry, Lane A/C protocol citations, hard refuses in force. SDD Task 0 also requires V13-A ZIP/MANIFEST/corpus/set3 hashes match V4-1 closeout — that check is **not** enumerated in the prereq JSON (only `v4_1_gate_pass: true`). V13-A zip existence/sha is re-bound later in the verifier, so the binding exists but **not at the prereq gate** as specified.

### Task 1 — Port DD-44 aggregation (**pass**)

`judge_models.py` + `judge.py` implement all six DD-44 operators with failure-partition outcome tie-break and primary-issue severity tie-break. `test_judge_aggregation.py`: 9 passed; unknown outcome enums fail closed in stage_c tests. `rg`/module introspection: no `confidence` field on judge surface. `plan018-v4-3-port-receipt.json` cites source `dcca50c…` and destination SHAs.

### Task 2 — Content-addressed attempt store (**pass**)

`attempt_store.py`: write → retrieve → hash-verify; `HashWithoutBytesError` on hash-without-bytes; duplicate attempt IDs and invalid call_index fail closed. `test_attempt_store.py`: 4 passed. Sidecar exit 0.

### Task 3 — Exactly-three Stage C runner (**partial**)

`stage_c_runner.py` enforces exactly three sequential calls, persists every attempt (including failures), aggregates via Task 1, and never silently completes partial/failed runs. Golden + replay tests pass (4 + 2 mutation = 6).

**Gaps vs SDD / V5-3 §2:**

- Mutation list is **two mutants** (`test_judge_mutations.py`); plan requires killing each aggregation operator/tie-break mutation with an **explicit mutant list in evidence** — no such list in `plan018-v4-3-stage-c.sidecar.json`.
- Golden coverage exercises representative permutations, not exhaustive three-vote enum combinations for every operator/tie-break.
- Partial/failed-attempt runner paths are implemented in code but **not asserted** by dedicated tests (only happy-path + unknown-enum aggregate test).

Acceptable for hermetic V4-3 DONE, but not full oracle spirit.

### Task 4 — V8-D disposition (**partial**)

`disposition.py` implements combined_success, should_call_judge, classify_arm with provider_outage→excluded, unjudged/zero-criteria/unevaluable/missing-arm paths, and exclusion_census. `test_disposition.py`: 7 passed including unrecognized fail-closed and deterministic-gate never-calls-judge.

**Gaps:** SDD asks golden tests for **every** V8-D table row including usage_policy/code_error/timeout/no_answer→scored 0. Those paths rely on `EvalRow.outcome()` in `router_models_proposal.py` (disposition logic is split across modules) and lack dedicated golden tests in `test_disposition.py`. Unevaluable pre-score path exists in code but is untested.

### Task 5 — Human annotations (**partial**)

`human_annotations.py`: versioned pydantic schema with `extra=forbid`, observed vocabulary mapping, override-silence guard (`apply_human_annotation` raises on silent override). `test_human_annotations.py`: 3 passed.

**Gaps vs living plan + SDD:** No implementation or tests for rejecting **orphan, duplicate, stale, unauthorized, or conflicting** annotations — only schema fields and label validation exist. Living plan explicitly requires those rejections. Wiring to nightly export correctly deferred, but ingestion validation is incomplete.

### Task 6 — Conservation + fit admission (**partial**)

`conservation.py`: conservation identity, pair retention (two arms required), `SupportGateConfig` defaults 5/2, fit-admission excludes pending/excluded, basic attrition counts in support gate. `test_conservation.py`: 4 passed; verifier uses reduced 1/1 fixture thresholds for hermetic proof while module defaults remain 5/2.

**Gaps:** Living plan requires reporting **differential attrition and sensitivity bounds**; implementation only emits simple excluded/pending pair counts — no route-level differential attrition or sensitivity bounds. Single-arm pair rejection logic exists (`ns`/`cc` None → pending) but lacks an explicit unit test.

### Task 7 — V4-3 DONE verifier (**pass**)

`scripts/plan018_v4_3_verifier.py` + `v4_3_verifier.py`: V13-A hash re-bind, DD-44 aggregate oracle, attempt-store replay, provider_outage excluded, conservation + fit-admission + support gate fixture, V4-8 authorization note. Independently reproduced **exit 0, 12/12 checks**; `paid_or_live_resources_used: false`.

### Task 8 — Closeout + local plan progress (**pass**)

`plan018-v4-3-closeout.json` (`gate: PASS`, `next_gate: V4-4`). `plan018-preflight.json`: `v4_3_status=CLOSED`. Living plan V4-3 checkboxes marked with local progress note; published V14 SHA unchanged (intentional).

### Task 9 — Cold outcome review (**pass**)

Delivered by this provenance-bearing cold subagent artifact; prior parent-written file VOID.

---

## Spirit gaps (technically OK but not in spirit)

1. **Thin DD-44 oracle** — Two mutation killers and spot golden tests prove core correctness but fall short of V5-3 §2's exhaustive permutation + per-operator mutation mandate. Fine for hermetic gate; insufficient before live V4-8 spend.
2. **Split disposition ownership** — V8-D scoring paths for timeout/code_error/usage_policy live primarily in `router_models_proposal.EvalRow`, while `disposition.classify_arm` is the promoted product seam. Works, but obscures the "one total mapping" story and leaves table-row coverage uneven.
3. **Human-annotation validation stub** — Schema exists; enforcement layer for orphan/dup/stale/auth/conflict does not. Labels cannot silently override judge output (good), but untrusted annotations are not yet rejected at ingest.
4. **Attrition reporting minimal** — Support gate counts pairs; no differential route attrition or sensitivity bounds per living plan.
5. **SDD process condensed** — `.superpowers/sdd/plan018_v4-3_sdd/progress.md` shows controller-driven completion without fresh per-task implementer/reviewer subagent loops (V4-2 process debt at reduced severity).
6. **Coverage ≥95%** — Not measured with coverage.py; 33 focused unit tests cover owned modules but no formal coverage report in closeout.

---

## Residual debt (should not be left as-is indefinitely)

| Item | Severity | Notes |
|------|----------|-------|
| Live three-call provider judgment | Expected | Requires separate **V4-8** authorization; noted in verifier |
| DB `judge_cache` / `TurnJudgment` migration | Expected | Deferred past V4-3 |
| Full `hibayes_*` Bayesian fitter | Expected | V4-4 scope |
| Human-annotation ingest validators (orphan/dup/stale/auth/conflict) | **Should fix** | Living-plan requirement; schema-only today |
| Exhaustive DD-44 oracle + mutation evidence | **Should fix before V4-8** | Expand mutants; document list in evidence |
| V8-D golden row coverage (timeout/code_error/usage_policy/unevaluable) | **Should fix** | Add disposition golden tests |
| Differential attrition + sensitivity bounds | **Should fix** | Conservation module incomplete vs living plan |
| Formal pytest-cov ≥95% on new modules | Nice-to-have | Plan asks for it; not recorded |
| Living-plan local SHA ≠ published V14 | Expected | Republish needs authorization |

---

## Living-plan V4-3 DONE predicate

| Predicate | Met? | Evidence |
|-----------|------|----------|
| Raw-attempt replay reproduces aggregates/totals deterministically | **Yes** | Verifier replay check; stage_c replay tests |
| Deliberate failure/outage/unknown-status fail safely | **Yes** | Provider outage excluded; unknown enum fail-closed |
| No excluded/pending case reaches fit-admission | **Yes** | `build_fit_admission` + conservation tests + verifier |

The **V4-3 DONE** bar (hermetic replay, fail-safe exclusions, fit-admission hygiene) is satisfied. Several **SDD task success conditions** are only partial (Tasks 0, 3, 4, 5, 6).

---

## Final verdict

**Original will carried out: YES — PASS with documented partials and residual debt.**

V4-3 delivered the intended hermetic judgment stack (DD-44 port, content-addressed attempt store, exactly-three runner, V8-D disposition seam, human-annotation schema, conservation/fit-admission, DONE verifier) without provider spend. The maintainer's core intent — replayable three-call judgment with fail-closed disposition and conservation before any live judge spend — is met. Task-level gaps (human-annotation ingest validation, full DD-44 oracle, complete V8-D golden matrix, attrition/sensitivity reporting) are real and should be closed before treating the gate as audit-complete or authorizing V4-8 live calls.

**Authorization menu (maintainer):**

1. **Next gate** — Accept V4-3 as CLOSED on documented partials and proceed to V4-4 follow-on / later gates?
2. **Push** — Not evaluated this review (hermetic only); confirm branch/commit push if desired.
3. **Paid / provider spend** — Not applicable to V4-3 acceptance; V4-8 remains separate.
4. **Deploy / production** — Not applicable.
5. **Registry / living-plan republish** — Local progress differs from published V14 SHA; republish only with explicit authorization.
