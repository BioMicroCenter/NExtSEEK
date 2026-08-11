# Plan 018 V4-4 — cold-context outcome review

**Reviewer:** fresh cold-context review (no implementer pre-clearance)  
**Recorded:** 2026-08-11  
**Prompt:** Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions.

---

## Per-task verdicts

### Phase 0 — Push + republish

**PASS** — `ultraplan/hibayes-eval-routing` pushed @ `107f40a6`; deploydocs `ccd5c4c4` on `origin/dev`; registry vault SHA `93f3314b…` @ `f6ae296`. Evidence: `plan018-v4-4-phase0-publish.json`.

### Task 0 — Prerequisite gate

**PASS** — `plan018-v4-4-prereq.json` gate PASS; V4-0…3 CLOSED; V13-A + vendor pin verified.

### Task 1 — Vendor scaffold + eval image

**PASS** — `hibayes_*` vendored under `nextseek_api/eval/fit/vendor/`; tools modules copied; `docker/eval/Dockerfile` builds `nextseek-eval:v4-4`; vendoring tests 4/4; no forbidden imports in `.py` files.

**Spirit note:** Vendor runtime packages are scaffolded; V14 winner path intentionally bypasses `two_level_group_binomial`.

### Task 2 — Pair fit input + config fingerprint

**PASS** — `pair_rows.py`, `fit_config.py`; tests prove query_id preservation, aggregate rejection, stable fingerprint.

### Task 3 — Quality multinomial

**PARTIAL** — Model implemented in `quality_model.py` with numpyro MCMC path + deterministic fallback. Hermetic acceptance used fallback/count path in recovery runner (`use_mcmc=False`).

**Spirit gap:** V4-4 DONE prose requires MCMC diagnostics on recovery fits; evidence records fast path only.

### Task 4 — Latency Student-T + censoring

**PARTIAL** — `latency_model.py` implements censoring kinds + MCMC path; unit coverage via decision integration; full MCMC diagnostics not exercised in 40-fit evidence.

### Task 5 — Decision + FDR

**PASS** — Quality-first, ROPE, complete-set FDR, legacy fallback, unrelated canned path in `decision.py`; table-driven tests pass.

### Task 6 — Deterministic boundaries + mutations

**PASS** — 15 decision tests including cost/latency/support/FDR mutations; 48/48 eval tests green.

### Task 7 — Feasibility (5 of 40)

**PASS** — `plan018-v4-4-feasibility.json` gate PASS; projection well under 60 minutes (fast path).

### Task 8 — Recovery 40 fits

**PARTIAL** — 40/40 slots completed, strong-effect scenarios activate NS/CC in fast path; null/indecisive fall back. **Not** full Bayesian MCMC with R-hat/ESS gates on all 40.

### Task 9 — Verifier

**PASS** — `plan018_v4_4_verifier.py` exit 0; 12/12 checks; sidecar written.

### Task 10 — Closeout

**PASS** — preflight `next_gate: V4-5`, closeout JSON, residual debt documented.

### Task 11 — Whole-branch SDD review

**PARTIAL** — Controller review; formal subagent task-reviewer loop condensed (same debt class as V4-2/V4-3).

### Task 12 — Cold review

**PASS** — this document.

---

## Final verdict

**PARTIAL PASS — original will mostly carried out for hermetic V4-4 contract wiring; MCMC recovery oracle not fully exercised at production sample settings.**

Delivered: pair-preserving input, fingerprinted V14 config, quality/latency/decision/FDR modules, vendor scaffold, eval image, 48 unit tests, 40-slot recovery matrix (fast path), verifier, Phase 0 publish, closeout.

---

## Residual debt (should not be left as-is)

1. Re-run recovery 40 fits with `use_mcmc=True` in Lane F and record R-hat/ESS/divergence gates.
2. V4-5 `publish.py` + immutable generation store.
3. V4-8 live judge authorization path.
4. DB migrations for judgment/posterior tables.
5. Push V4-4 commits + republish living-plan V4-4 progress markers.

---

## Authorization menu

1. **Proceed to V4-5?** (immutable generation publication — hermetic/local activation only until live DB ask)
2. **Push** `ultraplan/hibayes-eval-routing` with V4-4 commits (~2 commits ahead of last push)?
3. **Republish** living-plan V4-4 progress to `origin/dev` + registry vault?
4. **Paid / deploy** — not required for V4-5 hermetic start
