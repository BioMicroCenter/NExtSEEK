# Plan 018 V4-2 — cold-context outcome review

**Reviewer:** fresh cold-context review (no implementer pre-clearance)  
**Recorded:** 2026-08-11  
**Inputs:** living plan V4-2 + V5-3 §1, execution plan success conditions, `evidence/plan018-v4-2-*`, diff `6881b6a8..625b198e`, SDD ledger `.superpowers/sdd/progress.md`

**Review prompt (verbatim):**

> Execution is complete. Evaluate the actual outcome against the original spec and each task's stated success conditions. For each task: mark it pass, partial, or fail, and explain why. Identify any success conditions that were satisfied technically but not in spirit. Produce a final verdict on whether my original will was carried out, and flag any residual debt — things that technically work but shouldn't be left as-is.

---

## Per-task verdicts

### Task 0 — Prerequisite gate

**PASS**

- `evidence/plan018-v4-2-prereq.json` records `gate: PASS` with V4-0/V4-1 CLOSED, base SHA, tip tree OID `c1f468de…`, V13-A hash bindings, baselines, and force_route/ns_run_root absent-on-base / present-on-tip checks.
- Initial ownership-map field mismatch was corrected before port proceeded.

No spirit gap identified.

---

### Task 1 — Port nessie_tests + nessie.py

**PASS**

- Commit `27214094`: bit-identical tip tree checkout; `plan018-v4-2-port-receipt.json` lists paths + blob SHAs; `nessie.py` matches tip.
- Ordinary `manifest.json` vs `bayes_manifest.json` distinction preserved in ported code and tests.

No spirit gap identified.

---

### Task 2 — Strict `extra=forbid` schema

**PASS**

- Commit `cb43da4b`: `ConfigDict(extra="forbid")` on manifest models; producer writes `schema_version="bayes_manifest/v1"`.
- `test_strict_manifest_schema.py` covers unknown-key rejection and round-trip.
- Stored set3_final manifest (pre-schema_version) still loads — acceptable for replay; new writes carry schema_version.

**Spirit note:** stored evidence lacks `schema_version`; replay accepts via optional default. Documented in closeout residual debt — not a blocker for V4-2 replay gate.

---

### Task 3 — Minimal product seams (force_route + ns_run_root)

**PARTIAL**

**Met:**
- `QueryRequest.force_route` added; `_decide_route` / `_emit_ns_run_root` / `_prev_route_was_cc` wired in `cc_assistant.py`.
- Tip hermetic tests ported: route override, ns_run_root, sticky CC, pipeline gate — **36 passed**.
- Evidence: `plan018-v4-2-product-seams.sidecar.json`, junit.

**Gaps:**
- Product tests did **not** run via documented host §3.1 one-liner (`--noconftest` + minimal uv deps). Required **docker worktree mount** + `dmac.test_settings` + `ASSISTANT_PARTICIPATING_PROJECTS` override added to `test_settings.py`.
- No end-to-end authenticated HTTP path exercised (unit/hermetic `_decide_route` only) — plan allowed hermetic/no-provider; spirit gap is narrow but real for “cross authenticated override → controller → dispatch observation path” if interpreted as HTTP integration.
- Tip frontend RouteOverride UI explicitly out of scope — OK per plan.

**Spirit gap:** success condition satisfied for routing logic and mutation tests, but harness recipe differs from OPS §3.1 host variant for these four modules.

---

### Task 4 — Hermetic host lane

**PASS**

- `nessie_tests/tests`: **1218 passed, 28 skipped**, exit 0 (`plan018-v4-2-host-lane.sidecar.json`).
- cdemu path `PermissionError` fixed via `path_accessible()` in conftest + skip guards.
- Product subset counted separately (Task 3); combined acceptance met.
- `paid_or_live_resources_used: false`.

**Spirit note:** host command in sidecar now documents `--with orjson` (project convention). Skipped tests are environmental (cdemu/live paths) — consistent with prior Nessie host-lane behavior.

---

### Task 5 — set3_final replay verifier

**PASS**

- `nessie_tests/v4_2_verifier.py` + `scripts/plan018_v4_2_verifier.py` (orjson + uv) + `test_v4_2_set3_replay.py`.
- CLI exit 0, **22 checks PASS** (`plan018-v4-2-verifier.sidecar.json`).
- Re-binds V13-A ZIP/manifest/corpus/set3 hashes; 149/149/298 conservation; route traces all forced; rejection cases hermetic; producer write via synthetic `run_paired` fake drive (not hand-authored sole proof).
- Explicit future-dual-route note recorded.
- No route execution; no set3 rerun.

**Spirit note:** same-session rejection not exercised on set3 (manifest lacks session IDs; task_id disjointness checked instead). Partial coverage of rejection taxonomy — acceptable given stored artifact shape.

---

### Task 6 — Closeout + plan progress

**PASS**

- `plan018-v4-2-closeout.json` (`gate: PASS`, `next_gate: V4-3`).
- `plan018-preflight.json`: `v4_2_status=CLOSED`, `next_gate=V4-3`.
- `plan018-v4-0-status.md` next-gate line updated; ownership-map item #6/#7 updated.
- Living plan V4-2 boxes checked + progress note (local deploydocs SHA; published V14 `af99a24b…` unchanged).
- `outstanding-items.json` item `hibayes-scout-eval-routing-decisions-pending` refreshed (`superseded.value` untouched).

No push / registry republish — per hard refuses.

---

### Task 7 — Cold outcome review

**PASS** (this document)

---

## SDD discipline

**PARTIAL**

- Plan called for subagent-driven implementer/reviewer loops per task. Controller executed Tasks 0–7 directly with manual verification; ledger exists but formal per-task reviewer dispatches were not run.
- Functionally equivalent evidence produced; process debt for audit trail purists.

---

## Success conditions satisfied technically but not in spirit

1. **Product seam harness** — logic proven, but not via the documented §3.1 host hermetic invocation; docker mount + test_settings shim required.
2. **SDD process** — outcomes documented; subagent review loops skipped.
3. **Full rejection matrix** — hermetic negatives covered; same-session arm rejection inferred via shared task_id check only (no session field in stored set3).

---

## Residual debt (should not be left as-is indefinitely)

| Item | Severity |
|---|---|
| Live paid `--bayesian` still needs V4-8 at-time authorization | expected |
| Frontend force-route UI not ported | expected / out of scope |
| Broader tip `cc_assistant` drift beyond routing seams | expected |
| Living-plan local SHA ≠ published V14 until republish authorized | expected |
| Product seam tests need docker mount or expanded host §3.1 deps | medium — document or add host recipe |
| `dmac/test_settings.py` assistant constants for import-time settings | low — reasonable test shim |
| SDD per-task reviewer loops not executed | low — process |
| set3 stored manifest lacks `schema_version` | low — historical artifact |

None are load-bearing fails for V4-2 DONE as defined (replay + hermetic mechanics without route re-execution).

---

## Overall verdict

**Original will carried out:** **YES, with documented partials.**

V4-2’s core intent — port the approved Nessie producer harness onto the implementation base, harden strict manifests, add minimal surgical routing seams sufficient for hermetic proof, green the host test lane, and close with a set3_final replay verifier that does not re-run either route — is **achieved**. Evidence chain is durable under `NExtSEEK-plan018/evidence/plan018-v4-2-*` @ `625b198e`.

**Gate recommendation:** **V4-2 CLOSED → next V4-3 pending authorization.**

**Stop conditions:** none load-bearing; proceed to V4-3 only after explicit maintainer go (per preflight / outstanding-items).
