# Phase 2 Defect-Lineage Ledger (PLAN-3 + PLAN-7)

Purpose: make loop convergence **auditable**, not asserted. Every Phase-2 finding is tagged
**NEW** (independent latent defect, first caught now), **PARTIAL(parent)** (prior fix closed only
one layer; same defect re-surfaced), or **REGRESSION(parent)** (a hardener fix introduced it).

**Rule:** a thread stays **OPEN** until a *fresh* reviewer clears the whole thread (zero MEDIUM+).
A finding may not be recorded "resolved" on hardener say-so — only a subsequent fresh re-vet that
does not re-raise it (or any descendant) closes it. Orchestrator may never self-assign UA (ANN-2).

Convergence target: per round, **NEW→0** and **all descendant threads CLOSED**. If new independent
defects keep appearing, the artifact is immature (keep vetting). If only descendants appear, the
hardeners are under-thorough (apply full-thread hardening) — either way, do **not** advance.

---

## Thread A — PLAN-7 cross-user subpath isolation gate  [STATUS: CLOSED @ iter-23 — fresh reviewer confirmed the paired live seed/in-turn gate backstops any leak; 0 HIGH. (Provisional per rule 1a, but the live gate is sound.)]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 17 | CRITICAL | Mount dict lowercase `volume_options` silently drops subpath | NEW | — |
| 18 | CRITICAL | tests assert Subpath **key exists**, not its per-user **value**; scan only "(recommended)" | PARTIAL | 17 |
| 19 | HIGH | required scan captured **post-turn**, but agent force-removed in `finally` → ungenerable | REGRESSION | 18 |
| 19 | HIGH | isolation oracle non-recursive `ls` → cannot see the empty-Subpath leak | REGRESSION | 18 |
| 20 | MEDIUM | foreign-sentinel **seeding mechanism unspecified** (oracle needs it) | PARTIAL | 19 |
| 20 | MEDIUM | scan file is operator-placed text — **authenticity not bound to live capture** (hand-editable) | PARTIAL | 18/19 |
| 21 | — | iter-21 reviewer verified oracle "mutation-robust", did not re-raise → **CLOSED (premature)** | — | — |
| 22 | HIGH | "foreign tokens absent" oracle is **vacuous unless the seed is proven present**; nothing gates seed planting → a real `Subpath=""` leak passes green if the seed step is skipped | PARTIAL | 20 |
| 22 | MEDIUM | iter-20 "mutation proof" rationale **factually wrong**: live sentinel at container path `/data/scratch` is present under BOTH leak and correct mount → it is anti-stale binding, NOT a leak detector | REGRESSION | 20 |

**Lesson (feeds the method doc):** a thread marked CLOSED can **REOPEN** when a later, sharper fresh
reviewer finds a descendant the earlier one missed — and a hardener's own "mutation-RED proof" can be
wrong if its reasoning is wrong. CLOSED is provisional until the artifact stops changing.
**To CLOSE (revised):** the leak detector is **foreign-token-absence**, which is only meaningful if
the foreign seed is **proven planted** — add a harness-captured `pre_turn_seed_scan.txt` asserting every
foreign token is PRESENT before the turn; the in-turn agent scan must then show them ABSENT. Skipped
seed → pre-turn scan RED; real leak → in-turn scan RED. Correct the mutation-proof rationale.
**iter-20 hardener:** prefix-aware foreign-tree seeding step specified; `subpath_isolation_scan.txt`
now harness-written from live `docker exec … find` stdout + agent-authored `LIVE_<sentinel>` in
`/data/scratch` cross-checked via `meta.json.live_sentinel`; SPEC-7 §8 "Authenticity binding" clause
added. (iter-22 showed this was necessary but **not sufficient** — seed presence ungated.)
**iter-22 hardener:** added REQUIRED `pre_turn_seed_scan.txt` (root-mounted volume listing after
seeding, before the turn) to SPEC-7 §8 + Task 10 Step 4 + Task 2 validator; gate now requires BOTH
gate-0 (pre-turn scan contains every foreign token → proves seed; skipped seed = RED) AND gate-1
(in-turn scan: own marker + live sentinel present, foreign tokens absent; real leak = RED). Wrong
"live sentinel = leak detector" rationale replaced with a 3-scenario block everywhere. Pending iter-23.

## Thread B — transcript marker handshake (PLAN-7 validator ↔ PLAN-3 Task 13 producer)  [STATUS: CLOSED @ iter-21 — BOTH fresh reviewers confirmed byte-identical + idempotency-robust allowlist]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 18 | HIGH | PLAN-7 validator greps `celery inspect registered` — absent from real command | NEW | — |
| 19 | (hardened) | PLAN-7 markers → `inspect registered`, then → guaranteed-stdout `Applying nextseek_api.0007`/`cc_assistant.upload`/`cc_traces` | — | — |
| 20 | HIGH | only PLAN-7 side updated; PLAN-3 Task 13 Step 8 still names old command strings; `Applying …0007` only on a **fresh** migration (re-run → "No migrations to apply.") | REGRESSION | 19 |

**To CLOSE:** both plans name the **same** marker allowlist verbatim, with an idempotent-migration
fallback, and one fresh reviewer confirms the handshake on both sides.
**iter-20 hardener (single owner, both files):** byte-identical 4-string allowlist — `Applying
nextseek_api.0007` OR `[X] 0007_ccsessiontranscript` (idempotency-robust via added
`showmigrations nextseek_api`); `cc_assistant.upload`; `cc_traces` — in PLAN-7 Task 2 validator AND
PLAN-3 Task 13 Step 8. Pending iter-21 fresh re-vet.

## Thread C — PLAN-3 cc_traces store vs locked E5  [STATUS: CLOSED @ iter-21 — mirror RESTORED per user; fresh reviewer did not re-raise]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 17 | (hardened) | "drop cc_traces mirror" | — | — |
| 20 | HIGH | drop was inconsistent (Task 11/11a still say "mandatory") **and** violates locked SPEC-3 E5/§6.5 | PARTIAL/REGRESSION | 17 |

**User decision (2026-06-30):** RESTORE the `extra_state["cc_traces"]` mirror to honor locked E5;
reconcile all three sites (Task 11 Step 5, Task 11a line 1897, Task 11a interface line 1905 + the
`_append_cc_turn_complete` paste). No amend. **To CLOSE:** fresh reviewer confirms one consistent
"mirror mandatory" statement across the plan and the paste.
**iter-20 hardener:** all 4 sites reconciled to "mirror mandatory"; dual-store write extracted to pure
`cc_turn_complete.apply_turn_to_extra_state` (writes `chat_log[]` + `es["cc_traces"]` in one RMW save,
shape copied from `services/cc_assistant.py:65-72`); new mutation-RED guard test
`test_apply_turn_writes_chat_log_and_cc_traces_mirror`. Pending iter-21 fresh re-vet.

---

## Thread D — PLAN-7 cc_engine whole-module ≥95% coverage floor  [STATUS: CLOSED @ iter-23 — fresh reviewer (plan-7-phase2-review-23-fresh) adjudicated the rescope LEGITIMATE: hermetic ≥95% floor on pure cc_config + RED-blocking Subpath-value tests + the live realstack isolation gate = three independent enforced nets; run_cc_turn is live-only. Reconfirmed iter-24 (review-24 item 7). Stale OPEN header corrected 2026-07-01.]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 18 | (hardened) | extended coverage floor to `--cov=cc_engine --cov=cc_config --cov-fail-under=95` + claimed a `# pragma: no cover` on the live spawn block | — | — |
| 21 | HIGH | floor is **unproducible**: `run_cc_turn` (cc_engine.py:398–607) is only exercised by the live `test_cc_realstack.py`; cc_engine has **0** pragmas (the iter-18 claim is factually wrong); whole-module caps ~65–70% | PARTIAL/incorrect | 18 |

**To CLOSE:** rescope the floor to the **pure** module(s) the Task 6 mount refactor adds; cover
`run_cc_turn` via the live realstack gate + a **justified, non-deferrable** exception (same legitimate
pattern as PLAN-3 Task 5); restate the floor consistently; remove the false pragma claim.
**iter-21 hardener:** floor rescoped to pure `cc_config` (probe-measured **100%** hermetic); cc_engine
mount helpers surfaced via `--cov-report=term-missing` (no fail-under); `run_cc_turn` on the live
realstack gate as a justified exception; false pragma claim removed. Pending iter-22 fresh re-vet.

## Thread E — PLAN-3 missing-jsonl persistence policy consistency  [STATUS: CLOSED @ iter-22 — fresh reviewer (plan-3-phase2-review-22-fresh) confirmed persistence is best-effort on success behind CC_PERSIST_STRICT (a paid reply is never converted to query_error); no unconditional-raise wording remains. Reconfirmed iter-23. Stale OPEN header corrected 2026-07-01.]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 19 | (hardened) | made persistence **best-effort on success** (raise only under `CC_PERSIST_STRICT`) in the Task 11 Step 2 paste | — | — |
| 21 | MEDIUM | Task 11 **Step 1** still says "on missing jsonl after 3× retry, **raise RuntimeError**" unconditionally — contradicts the locked best-effort Step 2 paste; re-introduces the paid-reply→`query_error` regression iter-19 removed | PARTIAL | 19 |

**To CLOSE:** reconcile ALL sites describing the missing-jsonl policy to "raise only under
`CC_PERSIST_STRICT`"; fresh reviewer confirms no unconditional-raise statement remains.
**iter-21 hardener:** Task 11 Step 1 unconditional `raise` reconciled to the locked best-effort rule;
coverage-exceptions row 11 tightened; post-edit grep confirms no unconditional-raise wording remains.
Pending iter-22 fresh re-vet. (Also iter-21: NEW persist→reload wiring-guard MEDIUM closed with two
mutation-RED source guards; Task 6 Step 5b coverage run fixed; inventory paths corrected.)

## Thread F — PLAN-7 Task 6 hermetic Subpath-value net completeness  [STATUS: CLOSED @ iter-24 — fresh reviewer confirmed all 5 Subpath values match `build_user_dirs`; per-mount control correct]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 21 | (hardened) | Subpath derivation stated as "strip `user_root_mount` from the `*_mnt` path" | — | — |
| 23 | MEDIUM | `shared` mount in the spawn set but **absent** from the Task 6 Step 1 Subpath-value enumeration; the `{project}/{user}/`-prefix negative control would falsely reject the legit project-scoped `proj/shared` value → `shared` Subpath hermetically unguarded (`Subpath=""` passes the suite) | PARTIAL | 21/iter-22 "both nets" |
| 23 | MEDIUM | derivation shorthand wrong vs real source: `input` is `input_src` (no `input_mnt`); transcripts = `memory_mnt` + `/transcripts`; mixes `*_mnt`/`*_subpath`/strip-prefix vocab → stall risk | PARTIAL | 21 |
| 23 | MEDIUM (2D) | cheapest hermetic fake = mutate the un-enumerated `shared` Subpath | PARTIAL | 21 |

**Note:** MEDIUM not HIGH because the **live** seed/in-turn gate (thread A) backstops it — no leak ships
green; the hole is in the *hermetic* net only. **To CLOSE:** enumerate EVERY spawn-set mount's concrete
per-user Subpath value (incl. `shared`'s project-scoped value with a correct negative control), replace
the strip-prefix shorthand with the real `*_src`/`*_mnt` + `/transcripts` values; fresh reviewer confirms
no mount's Subpath is un-enumerated.

## Independent NEW defects (no open lineage — close on next clean fresh re-vet)

PLAN-3: iter-18 Task 9 celery import breaks hermetic validator; iter-18 Task 11 `PosixPath.stat`
monkeypatch illegal on 3.12; iter-18 `settings` NameError in `recover_transcript`; iter-18 coverage
floor only wired into Task 6; iter-19 Task 9 cross-device `os.replace`; iter-19 `run_cc_turn` paste
order; iter-19 success-path re-raise discards paid reply; iter-20 persist-block missing imports
(borderline PARTIAL of iter-19 best-effort edit); iter-20 Turn-projection passthrough unguarded;
iter-20 fixture `# <path>` header inflates `line_count`.
PLAN-7: iter-18 `migration_policy` self-contradiction; iter-18 Task 3 runtime-port source unnamed;
iter-20 Compose ≥2.26 vs docker-py Engine-API floor inconsistency.

## FINAL — Phase 2 COMPLETE on both plans (as of iter-24)

- **PLAN-3:** UNCONDITIONAL_ACCEPTANCE @ iter-23 (fresh, un-steered).
- **PLAN-7:** UNCONDITIONAL_ACCEPTANCE @ iter-24 (fresh, un-steered).
- **All threads A–F CLOSED.** Hard-gate sections (Permissions / Risk Register / Dependency Validation /
  Gameability Audit) present in both plans.
- Thread tally over the run: A (isolation gate, closed→reopened iter-22→re-closed iter-23/24, 8 findings),
  B (marker handshake), C (cc_traces mirror), D (cc_engine coverage floor), E (persist policy),
  F (hermetic Subpath-value net). Of ~30 findings across iters 18–24, roughly half were descendants of
  prior fixes — the lineage ledger is what kept that visible and prevented a partial fix from shipping
  as an "accepted residual." Every closure was confirmed by an independent fresh reviewer; the one
  premature closure (A @ iter-21) was caught at iter-22 and corrected.
- **Next:** Phase 2 → Phase 3 (task-spec writing) is a USER checkpoint. Do NOT auto-advance.

## Convergence read (as of iter-23)

- **PLAN-3 — PHASE 2 COMPLETE.** iter-23 independent fresh reviewer returned **UNCONDITIONAL_ACCEPTANCE**
  (0C/0H/0M/0L, cosmetic-only) after empirically verifying every load-bearing claim. Per loop rule, a
  target that returns UA from a fresh reviewer is **not reopened**. Threads B, C, E all CLOSED en route.
- **PLAN-7 — 0 CRITICAL, 0 HIGH, 3 MEDIUM (one root cause = thread F), 3 LOW.** Thread A **re-CLOSED @
  iter-23** (paired live seed/in-turn gate accepted; OI-3 leak cannot ship green). Threads B, D CLOSED.
  Only thread F (hermetic Subpath-value net completeness) remains — MEDIUM, live-gate-backstopped.
- Convergence is essentially reached: PLAN-7 needs one hardening pass on thread F + an iter-24 fresh
  re-vet to be UA-eligible. The reinforced discipline did its job — every closure was independently
  confirmed, and the one premature closure (thread A @ iter-21) was caught and corrected by iter-22.
- **Do NOT advance PLAN-3 to Phase 3 yet:** ultraplan governance blocks Phase 3 until Phase 2 completes
  on BOTH plans, AND Phase 2→3 is a user checkpoint. Park PLAN-3 at Phase-2-complete; finish PLAN-7.

---

# G7-11 sidecar-wave threads (opened 2026-07-02, iter-1 fresh review)

## Thread G — same-turn staging sweep / capability surfacing  [STATUS: OPEN]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 1 | HIGH | Task 14 invariants omit same-turn sweep + artifact surfacing; janitor sweep passes; exit-0 matrix hides dead staged artifacts (H-1) | NEW | — |
| 1 | MEDIUM | Task 13 whole-volume placeholder-behind-failing-test contradicts green/zero-skips discipline; mid-wave whole-volume sidecar exposure (M-2) | NEW | — |
| 1 (hardener) | — | Fifth locked invariant (in-turn sweep, published_path cross-check in matrix); "or the sidecar itself" struck; placeholder deleted → no-mount two-state | pending fresh re-vet | — |

## Thread H — capability-matrix / network-membership enforceability  [STATUS: OPEN]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 1 | HIGH | Closed-set peer rule unimplementable: inspect has names only, agents unnamed, labels invisible (H-2) | NEW | — |
| 1 | MEDIUM | Matrix provenance unpinned (no container/image binding); Layer-2 write alternative not machine-checkable (M-4) | NEW | — |
| 1 (hardener) | — | Deterministic agent names dmac-cc-agent-<run_id> (Task 13 Step 2b) + name-based closed set replacing run_id-substring seam; matrix rows carry container_id/image/wall_secs + images.json/network join; Layer-2 pinned exit-5/WRITE_BLOCKED | pending fresh re-vet | — |

## Thread I — greenfield achievability of the 9/9 gate  [STATUS: OPEN]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 1 | HIGH | 9/9-exit-0 unachievable on greenfield MBP: no data seeding; server-side LLM prereqs + spend unaddressed (H-3) | NEW | — |
| 1 | MEDIUM | `_staging` greenfield provisioning unowned → compose up fails sidecar create on fresh volume; dedicated-volume fallback ripple unenumerated (M-1) | NEW | — |
| 1 (hardener) | — | Step 3b seeded fixture (seeded_fixture.json) + per-op empty-data semantics + GCP/Bedrock prereqs into Tasks 7/8 + spend estimate; Step 2c startup `_staging` bootstrap + fallback blast radius pre-enumerated | pending fresh re-vet | — |

## Thread J — locked-text authority drift  [STATUS: OPEN]

| Iter | Severity | Finding | Class | Parent |
|------|----------|---------|-------|--------|
| 1 | MEDIUM | Task 7 env keys, Task 8 doc-guard "sidecar" token ambiguity, Task 2 no-YAML-subpath prose all contradicted by the wave and unamended (M-3) | NEW | — |
| 1 (hardener) | — | All three locked texts amended in place with (G7-11, iter-1 M-3) markers | pending fresh re-vet | — |
