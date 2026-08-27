# PLAN-7 Phase 2 — Hardener fix-log (iter-21 review)

Review: `.vetting/plan-7-phase2-review-21-fresh.md` — CONDITIONAL_ACCEPTANCE (0 CRITICAL, 1 HIGH, 0 MEDIUM, 3 LOW).
Scope edited: `PLAN-7-compose-native-prod-deploy.md` only. SPEC-7 §8 **not** touched (no fix required it).
Prohibited regions (PLAN-3, Vetting Log table, Phase 2 status line, `.vetting/defect-lineage.md`) untouched.

---

## Finding 1 — HIGH (thread D): coverage floor unproducible + false pragma claim

**Reality verified before editing:**
- `cc_engine.py` is **672 lines**; `run_cc_turn` spans **398–607** (~31%), called **only** by the live
  `test_cc_realstack.py` (`grep run_cc_turn(` across `tests/` → realstack alone). Never hermetically exercised.
- `cc_engine.py` currently carries **0** `pragma: no cover` lines (`grep -n "pragma"` → none). The old plan
  claim that "the single spawn-invocation block carries `# pragma: no cover`" was therefore **factually false**.
- `.coveragerc` (`source = nextseek_api`) does **not** omit `cc_engine`; whole-module `--cov=cc_engine
  --cov-fail-under=95` is unreachable while `run_cc_turn` + the `cc_runner_available` live `networks.get`
  branch stay live-only.

**Throwaway probe (measured, not asserted from memory):**
- `pytest test_cc_config_paths.py --cov=...cc_config` alone → **72%** (misses `_env_int`/`CCMemoryConfig.from_env`).
- `pytest test_cc_config_paths.py test_cc_memory_config.py --cov=...cc_config --cov-report=term-missing`
  → **`cc_config.py  32 stmts  0 miss  100%`**. The rescoped pure-module floor is genuinely producible.

**Fix applied (Global Constraints, line 30 — mirrors the legitimate PLAN-3 Task 5 `translate` pattern):**
Replaced the single contradictory paragraph with three honest pieces:
1. **Reachable hermetic floor (gated, commit-blocking):** `--cov=nextseek_api.cc_assistant.cc_config
   --cov-fail-under=95` over `test_cc_config_paths.py` **+** `test_cc_memory_config.py` together — measured 100%.
   `cc_config.py` is the pure module Task 6 Step 3 refactors. **Floor rescoped to real pure code, not deleted.**
2. **`cc_engine` pure mount helpers (surfaced, not whole-module gated):** `--cov=...cc_engine
   --cov-report=term-missing` with **no `--cov-fail-under`** (informational, like PLAN-3 Task 5).
   `_mount_volume_subpath` / `_build_volumes` / `_run_kwargs` (incl. per-user `Subpath`-value branches) are
   proven by the Task 6 Step 1/2 concrete `Subpath`-value hermetic assertions + anti-empty negative control.
3. **`run_cc_turn` runtime mount/spawn (justified, non-deferrable live-gate exception):** covered by the live
   realstack gate — the REQUIRED `subpath_isolation_scan.txt` §8 cross-user-isolation gate + forced-CC turn
   (Tasks 9/10), which is mandatory/not-skippable. Procedural seam covered by a real live gate per discipline
   rule #6.

**False claim removed; explicitly negated:** the new text states "**no blanket `# pragma: no cover` is applied
to `run_cc_turn`** (cc_engine carries no coverage pragmas)" — matching verified reality. The related LOW (the
`--cov=<whole module>` command could not express "these functions") folds into this fix: helpers are now
surfaced via term-missing + concrete value assertions, not a whole-module percentage.

**Consistency check:** the coverage floor appears substantively only at line 30 (`grep "cov-fail-under"` across
PLAN-7 → line 30 only; Task 6 / Task 2 validator restate no `--cov-fail-under` command). The iter-18 Vetting-Log
row mentioning "`--cov=cc_engine --cov=cc_config` floor" is a historical log entry in the prohibited table and is
left untouched (it records what was done then, not a live contract).

## Finding 2 — LOW 2A: Subpath derivation (Task 6 Step 1 + Step 2)

**Reality verified:** `build_user_dirs` (`cc_provision.py:79–109`) builds paths as
`{mount_root}/{project}/{user}/...` where `mount_root = paths.user_root_mount` (default `/dmac/users`), e.g.
`scratch_mnt = /dmac/users/proj/alice/scratch`. The Engine `VolumeOptions.Subpath` must be the **tail after
stripping `user_root_mount`** (`proj/alice/scratch`); a full/absolute `*_mnt` string is rejected by the Engine.

**Fix applied:**
- Step 1 (line 304): restated the assertion as "the per-user `*_mnt` path … **with the `user_root_mount`
  (volume-root) prefix stripped** … not the full `*_mnt` path," with the concrete `/dmac/users/proj/alice/...`
  → `proj/alice/...` example. Pinned assertion strings unchanged.
- Step 2 (new sentence after the `_mount_volume_subpath` code block): "**Subpath derivation (explicit):** the
  caller (`_build_volumes`) derives each `subpath` … relative to `user_root_mount` … strip the `user_root_mount`
  prefix … Never pass the full `*_mnt` string as `Subpath`." (the one sentence the reviewer requested in Step 2).

**Cross-check (all agree):** input→`proj/alice/input`, scratch→`proj/alice/scratch`,
cc-state→`proj/alice/cc-state/sess`, transcripts→`proj/alice/_memory/sess/transcripts` — each equals the
`build_user_dirs` `*_mnt` output minus the `/dmac/users` prefix.

## Finding 3 — remaining LOW/cosmetic

- "Phase 2 Vetting Log jumps 25→27→29" — in the prohibited Vetting Log table; left untouched by scope rule.
- "Global Constraints cites `cc_engine.py:598-607` for the finally block; `remove(force=True)` at 605 is within
  range — fine." Reviewer marked OK; no change.
- The line-30 "related LOW (coverage measurement command)" is subsumed by the Finding 1 fix.

---

## SPEC-7 §8

Not edited — the HIGH was a plan-side coverage-gate defect; no spec contract needed strengthening to fix it.

## Unresolved

None.
