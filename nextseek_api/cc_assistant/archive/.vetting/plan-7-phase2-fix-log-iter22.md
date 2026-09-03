# PLAN-7 Phase 2 — Hardener fix-log (iter-22, thread A reopen)

Target: `PLAN-7-compose-native-prod-deploy.md` (+ strengthening-only edits to `SPEC-7 §8`).
Source review: `.vetting/plan-7-phase2-review-22-fresh.md`. Authority: SPEC-7 (locked) > PLAN-7.
Scope honored: did **not** touch PLAN-3, the Vetting Log table, the Phase 2 status line, or
`.vetting/defect-lineage.md`.

This iteration re-closes **thread A** (the OI-3 cross-user isolation gate). The prior hardening was
necessary but not sufficient: the foreign-absent oracle was **vacuous** because nothing proved the
foreign seed tree was ever planted, and the live-sentinel "mutation proof" rationale was **factually
wrong** (it claimed the sentinel drops on a leak; it does not). Both are fixed below.

---

## Finding 1 — HIGH (thread A reopen, VACUOUS ORACLE): foreign-absent proves nothing unless the seed is proven present — FIXED

**Defect:** `subpath_isolation_scan.txt` asserts "foreign tokens ABSENT" in the agent view, but no
artifact proved a foreign tree existed at the volume root. If the implementer skips the Task 10
Step 4 seed step, a genuine `Subpath=""` whole-volume leak yields a scan with own-marker present,
live-sentinel present, and **zero foreign tokens (because none were seeded)** ⇒ GREEN. The most
catastrophic-to-miss invariant (OI-3, Risk-Register rank 2) hung on an unverified manual step.

**Fix (spec-compatible, additive — new harness-captured artifact `pre_turn_seed_scan.txt`):**
- **SPEC-7 §8** (strengthening): added a new REQUIRED artifact `pre_turn_seed_scan.txt` — a
  root-mounted recursive volume listing (`docker run --rm -v <vol>:/v alpine find /v -maxdepth 4`)
  written by the harness **immediately after seeding, before the turn**. Validator **fails** unless
  it is non-empty and contains **every** `meta.json.foreign_token` (`SENTINEL_FOREIGN`,
  `otherproj`, `bob`).
- **PLAN-7 Task 10 Step 4:** added a "Pre-turn seed scan (REQUIRED — `pre_turn_seed_scan.txt`)"
  block with the exact capture command, placed after the seed block and before the during-turn
  Capture block. Rewrote the Oracle to require **BOTH** scans: (0) pre-turn seed scan contains all
  foreign tokens; (1) in-turn scan has own marker + live sentinel and **none** of the foreign
  tokens. Stated explicitly: "The leak detector is (0) ∧ (1) together."
- **PLAN-7 Task 2 Step 2 (validator):** added `pre_turn_seed_scan.txt` as a required, non-empty,
  every-foreign-token-present check; renamed the in-turn oracle "seed-gated foreign-absent oracle";
  stated "The actual leak detector is the pair (pre-turn foreign-present) ∧ (in-turn
  foreign-absent) — neither alone suffices."
- **PLAN-7 Task 10 Step 4 success conditions** and **Gameability Audit row 6** updated to the
  paired gate.

**3-scenario walkthrough (concrete, required):**
- **(a) Honest run:** seed present ⇒ `pre_turn_seed_scan.txt` lists `SENTINEL_FOREIGN/otherproj/bob`
  ⇒ gate-0 GREEN. Correct per-user `Subpath` ⇒ `/data/input` = `proj/alice/input`; the foreign tree
  (`otherproj/bob/...` at the volume **root**) is **not** under `proj/alice`, so the in-turn
  `find -maxdepth 4` never reaches it ⇒ foreign grep empty; `OWN_<run_id>` (under `/data/input`) and
  `LIVE_<sentinel>` (under `/data/scratch`) present ⇒ gate-1 GREEN ⇒ **GREEN**. ✓
- **(b) Skipped seed:** no foreign tree planted ⇒ the root-mounted `pre_turn_seed_scan.txt` lacks
  `SENTINEL_FOREIGN/otherproj/bob` ⇒ gate-0 **RED before the turn runs** — even if a real
  `Subpath=""` leak exists, the bundle cannot pass. ✓ (This is the previously-open hole.)
- **(c) Real `Subpath=""`/`"/"` leak (seed present):** gate-0 GREEN (seed present), but the whole
  `dmac-cc-users` root mounts at `/data/input`, so the in-turn `find /data/input -maxdepth 4` lists
  `otherproj/bob/input/SENTINEL_FOREIGN` (depth 4) ⇒ foreign grep matches ⇒ gate-1 **RED**. ✓

All three confirmed: skipped seed → gate-0 RED; real leak → gate-1 RED; honest → GREEN.

---

## Finding 2 — MEDIUM (thread A, WRONG RATIONALE): "leak misses the live sentinel" is false — FIXED

**Defect:** the old "Mutation proof" claimed a `Subpath=""` leak makes the scan "miss the
agent-authored `LIVE_<sentinel>`". FALSE: the agent writes `LIVE_<sentinel>` to the **container
path** `/data/scratch`, and the harness `find`s that **same container path**; under a leak
`/data/scratch` is the volume root but the agent still writes there and `find /data/scratch` still
lists the sentinel (at depth 1). The sentinel is present under **both** mounts → it is **not** a
leak detector, only an anti-stale binding. The false claim masked the HIGH.

**Fix (corrected rationale, no false leak-detection claim):** replaced the single "Mutation proof"
paragraph with: a three-scenario "Mutation proof (three scenarios)" block (above); a new **"Why the
live sentinel is NOT the leak detector (corrected — prior rationale was wrong)"** paragraph; and a
re-scoped "Fabrication friction" paragraph. The corrected live-sentinel rationale, quoted verbatim:

> "under a `Subpath=""` leak the agent still writes `LIVE_<sentinel>` to the **container path**
> `/data/scratch`, and the harness `find`s that **same container path** — so `find /data/scratch`
> lists `LIVE_<sentinel>` (now at depth 1 of the volume root) under **both** the leaking and the
> correct mount. The live sentinel therefore does **not** drop on a leak; it is purely an
> **anti-stale / anti-substitution binding** ... Leak detection rests entirely on **foreign-token
> absence in-turn, gated by foreign-token presence pre-turn**."

This makes **no** false leak-detection claim about the live sentinel. The same correction was
applied to: Task 2 Step 2 oracle clause (b); Task 10 Step 4 success-condition bullet (removed
"drops the live sentinel"); Gameability Audit row 6; and SPEC-7 §8 (additive clarifier — see below).

---

## Finding 3 — MEDIUM (2B): keep both hermetic nets explicitly real — FIXED

**Fix:** extended the Global Constraints `cc_engine` coverage sub-bullet to state the split
explicitly and confirm both nets are commit-blocking and real (neither assumed):
- Net 1: `cc_config` floor at `--cov-fail-under=95` (commit blocked below it).
- Net 2: the Task 6 Step 1/2 hermetic mount tests are **ordinary failing-then-passing tests whose
  RED blocks the commit** (not informational). Quoted the concrete per-user `Subpath` value
  assertions they make (verbatim from Task 6 Step 1 "Per-user Subpath VALUE isolation", lines
  ~305–309):
  - `input → "proj/alice/input"`
  - `scratch → "proj/alice/scratch"`
  - `cc-state → "proj/alice/cc-state/sess"`
  - `transcripts → "proj/alice/_memory/sess/transcripts"`
  - anti-empty/anti-constant negative control fails any `Subpath` of `""`, `"/"`, a constant, or
    one omitting the `{project_dirname}/{user_id}/` prefix.
- Clarified that `--cov-report=term-missing` is the **only** informational piece (line surfacing),
  and it is **not** the isolation net — the value assertions are. The live §8 paired gate covers the
  `run_cc_turn` spawn seam. The two hermetic nets plus the live gate are independent and each
  enforced.

---

## Finding 4 — LOW ×2: permissions table + provenance reconciliation — FIXED

- **`alpine` image permission:** added a Permissions-table row for the seed + pre-turn-seed-scan
  helper containers (`docker run --rm -v "$vol":/v alpine …`, Task 10 Step 4), noting pre-pull
  during Step 0 or reuse of the already-permitted CC/Debian runtime image on egress-restricted
  hosts.
- **Unenforceable "harness-written" provenance:** reconciled in Task 2 Step 2 and Task 10 Step 4 —
  "written by the harness from `docker exec … find` stdout" is now stated as a **harness-
  implementation requirement**, and the text spells out what the **validator actually enforces**:
  the seed-scan/in-turn-scan **pair** plus the live-sentinel and own-marker cross-checks and the
  pairwise-disjoint `meta.json` token sets. No "the validator proves the harness wrote this" claim
  remains.

---

## SPEC-7 §8 edits (strengthening only — before/after)

Two additive changes, no requirement weakened or removed:

1. **New required artifact bullet** inserted between `agent_env_scan.txt` and
   `subpath_isolation_scan.txt`:
   > `pre_turn_seed_scan.txt` — REQUIRED seed-presence proof ... validator fails the bundle unless
   > this scan is non-empty and contains **every** `meta.json.foreign_token` ... so the foreign-
   > absent oracle on `subpath_isolation_scan.txt` is **not vacuous**.

2. **Additive clarifier** appended to the existing `subpath_isolation_scan.txt` bullet ("Seed-
   presence pairing + leak-detector clarification"): the foreign-absent check is meaningful only
   because `pre_turn_seed_scan.txt` proves the seed; the leak detector is foreign-token absence
   in-turn gated by presence pre-turn — **not** the live sentinel, which is present under both
   mounts and serves only as an anti-stale binding.

Both are additive/strengthening: they add a required check and correct a framing that could be
read as weaker than reality. Nothing in §8 was relaxed; the existing capture mechanism, live
sentinel, and foreign-token fail conditions are all retained.

---

## Self-verification

- Re-read each edited section after editing: Global Constraints coverage bullet (Net-split), Task 2
  Step 2 validator clause, Task 10 Step 4 (Pre-turn seed scan, Oracle, three-scenario Mutation
  proof, live-sentinel correction, Fabrication friction, success conditions), Gameability Audit
  row 6, and both SPEC-7 §8 bullets.
- Finding 1 three-scenario walkthrough done concretely (a GREEN / b gate-0 RED / c gate-1 RED) —
  see above; each confirmed against the real seed path (`otherproj/bob/input/SENTINEL_FOREIGN` at
  the volume root, `-maxdepth 4` reaching depth-4) and the real container paths.
- Finding 2 corrected rationale quoted above; it makes no false leak-detection claim about the live
  sentinel (the sentinel is present under both leaking and correct mounts).
- Finding 3 Task 6 value assertions quoted verbatim from Task 6 Step 1.
- SPEC-7 §8 edits quoted before/after; strengthening only.
- Cross-checked verbatim against real sources: docker-py force-remove `finally` (`cc_engine.py:
  598-607`), the during-turn poll-by-label capture, the seed mechanism in SPEC-7 §8 / Task 10
  Step 4, and the Task 6 Step 1/2 Subpath-value assertions. No invented contracts.
- Defects unresolved: **none**.

Locked-design alignment: verified (SPEC-7 §8 edits additive/strengthening, consistent with the
plan's validator). Thread A re-closed soundly.
