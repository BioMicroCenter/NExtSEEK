# Plan 018 V4-2 — independent SDD final review (cold-review remediation)

```
reviewer_kind: cold_subagent
subagent_id: sdd-final-review-v4-2-2026-08-12
parent_transcript_id: f1ace383-f8c3-4bc1-8e31-71d5d8329da1
prompt_verbatim: false
prior_implementer_review: VOID
```

**Recorded:** 2026-08-12  
**Branch:** `ultraplan/hibayes-eval-routing` @ worktree tip `5bec28d9` (V4-2 closeout anchor `625b198e`; remediation **uncommitted**)  
**Charter:** Cold-debt remediation V42-T1…T4 per [`cold_debt_remediation_00d90d00.plan.md`](file:///home/taishajo/.cursor/plans/cold_debt_remediation_00d90d00.plan.md), evaluated against SDD plan [`plan018_v4-2_sdd_a6446251.plan.md`](file:///home/taishajo/.cursor/plans/plan018_v4-2_sdd_a6446251.plan.md) and cold review [`plan018-v4-2-cold-outcome-review.md`](plan018-v4-2-cold-outcome-review.md).

Any prior `plan018-v4-2-sdd-final-review.md` without cold-subagent provenance is **VOID**. This file replaces it.

---

## Inputs reviewed

| Artifact | Role |
|----------|------|
| `nextseek_api/cc_assistant/tests/test_v4_2_force_route_http.py` | V42-T1 authenticated HTTP cross (new) |
| `nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py` | V42-T1 product mutation killers (new) |
| `dmac/test_settings.py` | Lane C settings shim (modified, uncommitted) |
| `evidence/plan018-v4-2-lane-c.sidecar.json` | Lane C recipe + module list (untracked; `gate: PENDING_RERUN`) |
| `evidence/plan018-v4-2-set3-schema-version-note.json` | V42-T3 historical `schema_version` acceptance note |
| `work/OPS-TESTING-HARNESSES.md` §3.4a | V42-T2 canonical Lane C documentation |
| `evidence/plan018-v4-2-cold-outcome-review.md` | Pre-remediation cold verdict (Task 3 PARTIAL) |
| Original closeout evidence @ `625b198e` | Tasks 0–2, 4–6 baseline (unchanged by this remediation cluster) |

**Independent Lane C re-run (this review session):**

```bash
docker run --rm -v /home/taishajo/work/NExtSEEK-plan018:/repo -w /repo \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  -e PYTHONPATH=/repo:/repo/dmac_assistant/src \
  nextseek-nextseek:latest uv run --project /app --no-sync python -m pytest \
    nextseek_api/cc_assistant/tests/test_route_override.py \
    nextseek_api/cc_assistant/tests/test_ns_run_root_event.py \
    nextseek_api/cc_assistant/tests/test_decide_route_sticky_cc.py \
    nextseek_api/cc_assistant/tests/test_decide_route_pipeline_gate.py \
    nextseek_api/cc_assistant/tests/test_v4_2_force_route_http.py \
    nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py -q
```

**Result:** **5 failed, 43 passed** (exit 1). All five failures are in `test_v4_2_force_route_http.py`. Original 36 product-seam tests + 7 mutation tests pass.

---

## Remediation task verdicts (V42-T1…T4)

| Task | Cold debt | Verdict | Summary |
|------|-----------|---------|---------|
| **V42-T1** | V5-3 §1 HTTP cross + mutation killers | **PARTIAL** | HTTP module authored with correct intent (POST → viewset → `_decide_route` → sticky → dispatch observation) but **does not run green** on Lane C. Mutation module adds real `_decide_route` killers (non-admin drop, admin forced, sticky `attempted_*`, force beats sticky, schema reject) but **same-session / copied-arms / swapped-routes** cases are placeholder self-tests, not red-on-mutation against product code. |
| **V42-T2** | Product seam docker / §3.1 recipe gap | **PASS** | OPS §3.4a documents exact docker mount + `dmac.test_settings` command; sidecar records image, settings module, shim path, and full module list. Recipe is authoritative; execution proof still pending (see below). |
| **V42-T3** | set3 `schema_version` historical artifact | **PASS** | `plan018-v4-2-set3-schema-version-note.json` documents transferred set3 bytes, SHA binding, replay acceptance via optional default, and producer-write requirement — aligns with cold review residual and closeout wording. |
| **V42-T4** | SDD final-review provenance | **PASS** | This artifact (cold subagent, provenance block above). |

---

## Load-bearing findings (HTTP cross failures)

### 1. Settings shim incomplete for URLconf import chain

On HTTP POST, Django loads `dmac/urls.py` → `seek.urls` → `seek/dbtable_sample.py`, which reads `settings.PUBLISH_URL` at import time. The Lane C shim sets `NEO4J_DATABASE`, `SEEK_URL`, `NEXTSEEK_CHAT_CONFIG`, etc., but **not** `PUBLISH_URL`. Every HTTP test that reaches the view stack fails with:

`AttributeError: 'Settings' object has no attribute 'PUBLISH_URL'`

This blocks the V5-3 §1 acceptance path the remediation was meant to close.

### 2. Wrong router mock target in three HTTP tests

`test_http_nonadmin_force_route_ignored_at_controller`, `test_http_force_route_beats_sticky_cc`, and `test_http_sticky_cc_applies_without_force_route` patch `cc_router._baml_decision`, which **does not exist** on `nextseek_api/cc_assistant/router.py` (public entry is `decide`). Established pattern in `test_decide_route_sticky_cc.py` uses `monkeypatch.setattr(cc_router, "decide", ...)`. Even after fixing `PUBLISH_URL`, these three tests would still fail until retargeted.

### 3. Mutation killers — partial closure only

**Met at product seam (`_decide_route` / `QueryRequest`):**

- Non-admin `force_route` dropped (not `forced` source)
- Admin force labelled `forced` with expected route
- Sticky override records `attempted_route` / `attempted_source`
- Admin force beats sticky CC history
- Invalid `force_route` rejected at schema boundary

**Not met (spirit of V5-3 §1 / cold review):**

- `test_mutation_same_session_task_ids_must_differ_across_arms` and `test_mutation_swapped_routes_on_forced_arms` exercise local `_disjoint` / `_check_forced` helpers only — they do **not** mutate product code or assert red-on-break of a real invariant. Paired-producer rejection taxonomy for same-session / copied arms remains in harness + verifier (acceptable for V4-2 DONE, but **not** the cold-debt closure V42-T1 claimed).

---

## SDD plan tasks — post-remediation status

| SDD task | Pre-remediation (cold) | Post-remediation |
|----------|------------------------|------------------|
| 0 Prereq | PASS | PASS (unchanged @ closeout) |
| 1 Port tree | PASS | PASS |
| 2 `extra=forbid` | PASS | PASS; V42-T3 note documents historical set3 bytes |
| 3 Product seams | **PARTIAL** | **PARTIAL** — routing logic + `_decide_route` mutations improved; HTTP cross still red; weak same-session/copied-arm mutation placeholders |
| 4 Host lane | PASS | PASS (1218+28 @ closeout sidecar) |
| 5 set3 verifier | PASS | PASS (22/22 @ closeout; note does not weaken acceptance) |
| 6 Closeout | PASS | PASS @ `625b198e`; remediation not yet committed |
| 7 Cold review | PASS (with Task 3 partial) | Unchanged artifact; this SDD review covers remediation only |

---

## Success conditions satisfied technically but not in spirit

1. **HTTP cross module exists but is not executable evidence** — files and sidecar recipe present; Lane C gate not green (`PENDING_RERUN` honest; independent rerun confirms failure).
2. **Mutation killer count inflated** — two new tests pass trivially without touching product seams; cold debt item “same-session, copied arms at product seam” not honestly closed.
3. **Remediation uncommitted** — new tests, sidecar, schema note, and shim delta sit untracked/modified atop a branch whose later gates (V4-3…V4-8) already committed; V4-2 remediation cluster lacks its own commit per cold-debt plan.

---

## Residual debt (honest)

| Item | Severity | Notes |
|------|----------|-------|
| Fix `dmac/test_settings.py`: add `PUBLISH_URL` (and audit urlconf import chain for further missing attrs) | **Blocking for V42-T1 HTTP** | Required before HTTP tests can reach routing code |
| Retarget HTTP tests: `cc_router.decide` not `_baml_decision` | **Blocking for 3/5 HTTP tests** | Follow `test_decide_route_sticky_cc.py` pattern |
| Replace placeholder same-session / swapped-route mutation tests with real product or HTTP-level killers | Medium | Or explicitly defer to harness/verifier in closeout residual (cold review allowed that for V4-2 DONE, but V42-T1 claimed full closure) |
| Run Lane C to green; update sidecar `gate: PASS`, counts, log SHA | Medium | Sidecar currently `PENDING_RERUN` |
| Commit remediation cluster `fix(plan018-v4-2): close cold-review product seam and harness debt` | Medium | Per cold-debt plan; currently uncommitted |
| Live paid `--bayesian`, frontend UI, broader tip drift | Expected | Out of scope |
| SDD per-task implementer/reviewer loops (original session) | Low | Process debt; not reopened by this remediation |
| Living-plan local SHA ≠ published V14 | Expected | Await republish authorization |

---

## Verdict

**PARTIAL**

V4-2 **original gate work** (port, strict manifests, minimal seams, host lane, set3 replay verifier @ `625b198e`) remains **valid** per the cold review’s “V4-2 CLOSED with Task 3 partials.” The **cold-debt remediation cluster** materially advances harness documentation (V42-T2 PASS), set3 schema-version evidence (V42-T3 PASS), and `_decide_route`-level mutation coverage, but **does not** close V42-T1: authenticated HTTP cross tests fail on Lane C due to shim and mock defects, and two claimed mutation killers are placeholders. Remediation must not be treated as complete until Lane C runs **48/48** green and evidence sidecar records `gate: PASS`.

---

## Authorization menu (remediation closeout)

1. **Accept SDD final review** — Confirm this PARTIAL verdict; do not mark V42-T1 closed until HTTP Lane C is green.
2. **Fix + rerun** — Maintainer or next session: complete shim, fix router mocks, rerun §3.4a command, flip sidecar to PASS, commit remediation cluster.
3. **Next gate** — V4-2 retrospective only if later gates already ran; no V4-3 re-authorization needed from this review.
4. **Push** — Branch `ultraplan/hibayes-eval-routing`; push only with explicit maintainer approval.
5. **Registry / living-plan republish** — Not applicable to this remediation slice.
