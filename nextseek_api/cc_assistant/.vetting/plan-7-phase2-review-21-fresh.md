# Phase 2 fresh review (iter 21) — TARGET: PLAN-7-compose-native-prod-deploy.md

Cold-context independent review. Authority hierarchy: SPEC-7 (locked design) > PLAN-7 > task specs.
Verified live against source under `/home/taishajo/work/NExtSEEK`, docker-py 7.1.0 in the dmac venv,
and the sibling PLAN-3 Task 13 Step 8 handshake.

## Verified-GOOD (no finding — recorded so the next reviewer need not re-derive)

- **docker-py `VolumeOptions.Subpath` raw-dict primary (Task 6).** Probed docker-py **7.1.0** in
  `/home/taishajo/work/dmac-assistant/.venv`: `docker.types.Mount` **is a `dict` subclass**;
  `m["VolumeOptions"] = {"Subpath": "p/u/input"}` serializes to
  `{'Target','Source','Type','ReadOnly','VolumeOptions':{'Subpath':...}}` — exactly the PascalCase
  Engine-API payload the `_mount_volume_subpath` helper (PLAN-7 Task 6 Step 2) claims. The "no
  `Mount.subpath` kwarg; patch the dict" approach is **correct** on the pinned version.
- **Cross-plan marker handshake (PLAN-7:132 ↔ PLAN-3 Task 13 Step 8).** Byte-identical: both name
  `Applying nextseek_api.0007` **OR** `[X] 0007_ccsessiontranscript`, `cc_assistant.upload`,
  `cc_traces`. PLAN-3 Task 13 Step 8 explicitly adds the `showmigrations nextseek_api` capture for
  the idempotent `[X] 0007_…` form, so an already-deployed instance's committed transcript still
  passes PLAN-7's start-gate. Confirmed at PLAN-3 lines 2256–2261. No drift.
- **Live-evidence path** `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt`
  matches what PLAN-3 Task 13 Step 8/9 commits. `git cat-file -e ${deploy_commit}:<path>` gate is producible.
- **Force-removal lifecycle & during-turn capture.** `cc_engine.py` `finally` (598–607: `stop`+`remove(force=True)`)
  and the `nextseek.cc.run=<run_id>` label (line 352) confirm the agent is gone post-turn, so the
  poll-by-label during-turn capture is the only viable mechanism — and it mirrors the *existing working*
  `_capture_agent_env` (`test_cc_realstack.py:89–104`) + background-thread (`:154–166`) pattern. Sound.
- **Stale `_run_kwargs` docstring** the plan says to fix (Task 6 Step 4) genuinely exists
  (`cc_engine.py:338` "joins … the nextseek compose network"); DEFAULT_NETWORK is `dmac-cc-net` (line 51). Accurate.
- **Isolation-scan oracle** is mutation-robust via the foreign-token grep: a `Subpath=""` leak mounts the
  volume root at the find roots, surfacing `otherproj/bob/input/SENTINEL_FOREIGN` at depth 4 → grep matches → RED.

---

## 2A — Vet (permissions / paths / dependencies for clean execution)

- **LOW** — *Task 6 Step 1/2, "the exact per-user path that `build_user_dirs` produces … `Subpath == "proj/alice/input"`".*
  `build_user_dirs` (cc_provision.py:79–109) produces **full mount paths** (`{user_root_mount}/{project}/{user}/input`,
  e.g. `/dmac/users/proj/alice/input`), **not** the volume-root-relative subpath. The `VolumeOptions.Subpath`
  must be the tail **after** stripping `user_root_mount`. The plan pins the correct concrete target strings
  (`proj/alice/input`, …) so the hermetic assertion is unambiguous and would catch a wrong derivation, but the
  plan never states the derivation step ("strip `user_root_mount` prefix to get the subpath"). Concrete values
  + the asserting test de-risk this; noting so the implementer does not pass the full `*_mnt` string as `Subpath`
  (an absolute/over-long Subpath is rejected by the Engine). **Fix:** one sentence in Step 2 — "Subpath is the
  `*_mnt` path relative to `user_root_mount` (volume root)."

- **OK** — All declared permissions (Docker socket, `docker exec` into live sibling, `uv`/`startup.sh install`,
  `INTEGRATION_PLAN_PATH`, Bedrock spend cap, gitignored secrets) are enumerated in the Permissions table and
  match the tasks that need them. docker-py `>=7.1.0` is already present in `dmac_assistant/pyproject.toml:20`
  (verified) — no version bump required, consistent with the plan's "no version change" note.

## 2B — Stress Test

- **HIGH** — *Global Constraints, "Coverage targets (Phase 2 hardened)": "run `--cov=nextseek_api.cc_assistant.cc_engine
  --cov=nextseek_api.cc_assistant.cc_config --cov-fail-under=95` over the Task 6 hermetic mount/path tests. Because
  `cc_engine.py` also holds the live `containers.run` spawn block that cannot run hermetically, that **single
  spawn-invocation block** carries `# pragma: no cover` … all pure mount-assembly logic … meet the ≥95% floor."*
  **This coverage gate is not producible as specified and the "single block" claim is factually wrong.**
  Verified: `run_cc_turn` spans `cc_engine.py:398–607` (~210 lines of a 672-line module ≈ 31%) and is called
  **only** by the live `test_cc_realstack.py` — `grep run_cc_turn(` across `tests/` returns realstack alone.
  No hermetic test exercises it; the hermetic `test_cc_engine_*.py` files only call the **pure** helpers
  (`build_agent_environment`, `_build_command`, `_build_volumes`, `_run_kwargs`, `_publish_artifacts`,
  `_redact_env`, `_rewrite_loopback_url`, `snapshot_before`, `_validate_*`). `.coveragerc` does **not** omit
  cc_engine, and there are currently **0** `pragma: no cover` lines in it. Pragma-ing only the one
  `client.containers.run(...)` call (504–519) leaves the rest of `run_cc_turn` — the event-stream `while True`
  loop (540–588), the `except` handlers (591–597), the `finally` cleanup (598–607) — **and** the live
  `cc_runner_available` network branch (140 `client.networks.get`) **uncovered and un-pragma'd**. Whole-module
  `--cov=cc_engine` therefore caps near ~65–70% even with every pure function at 100%; combined with the 63-line
  cc_config it cannot reach 95%. A careful implementer following the literal "single block" instruction is
  **stalled** (gate red); a lazy one **games it** by blanket-pragma'ing all of `run_cc_turn` (contradicting the
  plan and hiding the very spawn/mount-assembly wiring the gate exists to guard) or by writing unsanctioned
  mocked `run_cc_turn` tests the plan never specifies. **Fix:** pick one and make it consistent —
  (a) scope the floor to the pure functions only (e.g. a dedicated `--cov` target or a coverage `omit`/section
  that excludes the entire live `run_cc_turn` body + `cc_runner_available` live branch, explicitly enumerated,
  not "single block"); or (b) add hermetic mocked-docker-client tests for `run_cc_turn` to the Task 6 inventory
  so the body is genuinely covered; then restate the floor honestly. As written it is internally contradictory.

- **LOW (coverage risk, related)** — the same "Phase 2 hardened" paragraph asserts "all pure mount-assembly logic
  (`_mount_volume_subpath`, `_build_volumes`, `_run_kwargs`) … meet the ≥95% floor including the per-user
  `Subpath`-value branches." Those functions are genuinely hermetic and coverable; the problem is **only** that
  the chosen measurement command (`--cov=<whole module>`) cannot express "these functions" without dragging in
  the live `run_cc_turn`. Folds into the HIGH fix.

- **OK** — Failure/rollback conditions (Risk Register rank 1–10), pause-and-ask triggers, and the
  greenfield/migration branching (`had_host_bind_data` gating `migration_policy` across Tasks 2/6/9) are
  internally consistent. Catastrophic cross-user-leak (rank 2) is closed by the required live
  `subpath_isolation_scan.txt` + foreign-token grep + live-sentinel cross-check.

## 2C — Validate External Dependencies

- **OK / verified** — docker-py 7.1.0 `Mount` raw-dict `VolumeOptions.Subpath` (see Verified-GOOD). The plan's
  "do not pin `docker>=7.2.0` (unsatisfiable); record must-verify release tag at execution" is correct — PyPI
  latest is 7.1.0 and PR #3270's `subpath=` kwarg has not shipped. Engine ≥26 / API v1.45 is the real
  isolation floor (correctly identified as the gate, with Compose ≥2.26 correctly downgraded to CONDITIONAL
  because this plan mounts the whole volume with no YAML `subpath:` syntax). No dependency risk that derails.

- **OK** — Compose multi-network dual-homing, seven external volumes via `./startup.sh install`, and the
  `{prefix}`-aware volume-name resolution (`startup/steps/volumes.py:18` confirms the `{prefix}{name}` mechanism)
  are all real. `REQUIRED_VOLUMES` exists and is the correct place to add `dmac-cc-users`.

## 2D — Gameproof

- **Primary success oracle (`subpath_isolation_scan.txt`)** is well-hardened: harness writes the file straight
  from live `docker exec … find -maxdepth 4` stdout; positive allowlist (own marker `OWN_<run_id>` + agent-authored
  `LIVE_<sentinel>`) + foreign-absent grep; `meta.json.{foreign_tokens,own_marker,live_sentinel}` required pairwise
  disjoint. No-op test: a stub/empty scan fails (own marker absent). Mutation test: `Subpath=""` → foreign grep
  RED. Fabrication: clean substitute lacks this run's unpredictable live sentinel. **Not gameable cheaply.**

- **HIGH (carryover from 2B) — the cc_engine coverage gate is itself gameable/illegitimate as written.** The
  `--cov-fail-under=95` "guard" on the mount refactor cannot pass honestly, so the only ways to get it green are
  to blanket-pragma the live body (no-op the guard for the spawn/mount wiring) or delete the floor — both lose
  the intent. Remedy is the 2B fix (rescope to pure functions or add mocked hermetic `run_cc_turn` coverage).

- **OK** — Other gameability rows (Task 1 step3_deploy_gate, Task 2 markdown-only reject, Task 5 live
  `docker compose config` subprocess vs golden fixture, Task 8 numbered-procedure + whole-file `/srv/dmac` scan,
  Task 6 volume-persistence `docker volume ls` pre-bootstrap proof) each close their cheapest fake.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log jumps iteration **25 → 27 → 29** (missing 26/28 rows as numbered entries); harmless
  bookkeeping artifact of the alternating reviewer/hardener cadence.
- Global Constraints reference `cc_engine.py:598-607` for the finally block; actual `finally` is line 598,
  `remove(force=True)` at 605 — within the cited range, fine.

---

## Summary

One substantive defect: the **cc_engine whole-module `--cov-fail-under=95` gate is unachievable hermetically**
(run_cc_turn is live-only, never hermetically called) and the plan's "single spawn-invocation block carries
pragma" statement is factually wrong — it would stall a careful implementer or invite blanket-pragma gaming.
Everything else load-bearing (docker-py Subpath API, cross-plan marker handshake, isolation-scan oracle,
lifecycle/capture mechanism, volume/prefix wiring) verifies as correct. Fixable without redesign.
