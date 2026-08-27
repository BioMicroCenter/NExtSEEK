# Phase 2 Fresh Review (iter-22) — TARGET: PLAN-7-compose-native-prod-deploy.md

Cold-context adversarial review. Authority: SPEC-7 (locked design) > PLAN-7 > task specs. Verified
plan claims against live source (`cc_engine.py`, `cc_provision.py`, `cc_config.py`,
`test_cc_realstack.py`, `docker-compose.yml`, `startup/steps/volumes.py`), PLAN-3 Task 13,
docker-py 7.1.0 (live probe), and the CC image base. Did NOT read `.vetting/`.

## Verified-OK claims (no defect)
- `cc_engine.py` line cites accurate: `run_cc_turn` @398, `finally` stop/remove @598/601/605,
  `_run_kwargs` @325, label `nextseek.cc.run` @352, stale "joins … nextseek compose network"
  docstring @338, `DEFAULT_NETWORK` @51, `cc_runner_available` @121, `client.networks.get` @140.
- `build_user_dirs` @79 emits full `*_mnt` paths under `user_root_mount`; subpath-strip derivation
  is sound. `cc_config.CCPaths` currently carries `host_user_root` (retired by Task 6) — accurate.
- docker-py **7.1.0** is PyPI latest; `Mount` IS a `dict` subclass with PascalCase keys
  (`Type/Source/Target/ReadOnly`), is subscriptable, and has **no** `subpath` kwarg — the
  `_mount_volume_subpath` helper (`m["VolumeOptions"]={"Subpath":…}`) is correct (live-probed).
- Marker allowlist is **byte-identical** across PLAN-7:132 and PLAN-3 Task 13 Step 8 (lines
  2289-2294): `Applying nextseek_api.0007|[X] 0007_ccsessiontranscript`, `cc_assistant.upload`,
  `cc_traces`. Transcript path `…/evidence/3-ui-based-io-live/live_gate_transcript.txt` matches
  PLAN-3 Task 13 Step 8/9.
- `test_cc_config_paths.py` and `test_cc_memory_config.py` (cc_config coverage floor) both exist.
  `test_cc_provision_input_mnt/upload_validate/upload_list.py` are absent — correctly, they are
  PLAN-3 Step 3 deliverables created before the PLAN-7 hard gate.
- CC image base = `node:20-bookworm-slim` (Debian) ⇒ GNU `find -printf` is supported (the capture
  command is portable; not a busybox/alpine `-printf` hazard).
- Realstack forced-CC user is hard-set `user_id="ccacc"`, `project_dirname="personal-ccacc-ccacc"`
  (`test_cc_realstack.py:81,156`) — matches the plan's hard-coded seed path
  `personal-ccacc-ccacc/ccacc`. `host_label` enum, `migration_policy` conditional, and
  `budget_cap_usd` are internally consistent across Tasks 2/6/9/10 and SPEC §8.

---

## 2A — Vet (permissions / paths / endpoints)

**LOW — Task 10 Step 4 / Permissions table: `alpine` image availability for the seed helper not listed.**
Location: Task 10 Step 4 seed block (`docker run --rm -v "$vol":/v alpine sh -c …`). The
Permissions table enumerates Docker socket but not the registry pull of `alpine` needed by the
seed/own-marker helper containers on a greenfield MBP. A clean host mid-`docker compose build` has
egress, so impact is low. Fix: note `alpine` (or reuse an already-present image) under Task 10
permissions, or pre-pull during Step 0.

## 2B — Stress Test

**HIGH — The REQUIRED `subpath_isolation_scan.txt` leak gate is vacuous unless the foreign seed is
proven present; the validator never verifies it.** (See 2D for the full gameproof.) The single most
catastrophic-to-miss invariant (OI-3 cross-user leak, Risk Register rank 2) hangs on an
unverified manual seed step.

**MEDIUM — Coverage floor for the behavior-bearing module is re-measured only informationally.**
`cc_config` is gated at `--cov-fail-under=95` (good, commit-blocking), but the
behavior-bearing mount logic lives in `cc_engine._build_volumes/_mount_volume_subpath`, which is
gated only by the Task 6 Step 1/2 concrete `Subpath`-value assertions (no module floor). That is a
deliberate, justified split (mirrors PLAN-3 Task 5) and the per-user value assertions + anti-empty
negative control are the real guard — acceptable, but it means the leak invariant's *only*
commit-blocking hermetic proof is the Step 1/2 value assertions; if those are weakened the live
gate (2D HIGH) is the sole remaining net. Flagged so the implementer keeps both nets real.

## 2C — Validate External Dependencies

No external-dependency defect. docker-py 7.1.0 `VolumeOptions.Subpath` raw-dict path verified live.
Engine ≥26 / API v1.45 correctly identified as the **real** subpath floor (Engine applies the
runtime mount, not compose YAML); Compose ≥2.26 correctly made conditional on the compose file
actually using `volume.subpath:` syntax (it does not). The `do-not-pin docker>=7.2.0` note is
correct (unsatisfiable). One residual: the helper's reliance on `mounts=[Mount-with-custom-
VolumeOptions]` surviving docker-py's HostConfig serialization is the documented "must-verify at
execution" — Mount is the literal dict sent to the Engine, so this is sound, but keep the
execution-time live check the plan already requires.

## 2D — Gameproof

**HIGH — Foreign-absent oracle is satisfiable by simply not seeding; a real `Subpath=""` leak ships
green if the seed step is skipped.**
Location: Task 2 validator (`…validator runs grep -nE 'SENTINEL_FOREIGN|…otherproj…|…bob…' and fails
on any match`) and Task 10 Step 4 Oracle ("contains **none** of `meta.json.foreign_tokens`"); SPEC §8
`subpath_isolation_scan.txt`.
Success condition quoted: *"the bundle PASSES iff … contains the own marker … AND contains the live
sentinel … AND contains none of `meta.json.foreign_tokens`."*
- **Cheapest fake / no-op heuristic:** the only thing that makes "foreign absent" *meaningful* is
  that a foreign tree (`otherproj/bob/input/SENTINEL_FOREIGN`) actually exists at the volume root
  when the scan runs. Nothing in the validator confirms the seed was present — there is **no
  generated artifact capturing the seeded volume contents** (grep-confirmed: neither plan nor spec
  defines one; `pre_bootstrap_docker_volume_ls.txt` lists volume *names*, not contents). If the
  implementer omits the Task 10 Step 4 seed `docker run … mkdir … SENTINEL_FOREIGN`, then **even a
  genuine `Subpath=""` whole-volume leak** produces a scan with own-marker present, live-sentinel
  present, and zero foreign tokens (because none were seeded) ⇒ **PASS**. The gate that iter-18
  promoted to a CRITICAL-required §8 artifact then guards nothing.
- **Why the plan's own mutation proof doesn't save it:** the RED outcome in the "Mutation proof"
  paragraph is explicitly predicated on the foreign tree being on the volume. With the seed omitted
  the mutation stays GREEN.
- **Fix (spec-compatible, additive — same pattern as the iter-18 strengthening):** add a REQUIRED
  generated artifact, e.g. `pre_turn_seed_scan.txt`, written by the harness directly from the
  seed helper's `docker run --rm -v "$vol":/v alpine find /v -maxdepth 4` stdout **after seeding,
  before the turn**, and have the validator **assert it contains every `meta.json.foreign_token`**
  (`SENTINEL_FOREIGN`, `otherproj`, `bob`). Then: seed proven on the volume + foreign absent in the
  agent scan = real isolation proof, and a leak with the seed present turns the agent-scan grep RED.
  SPEC §8 already mandates the seed, so proving it does not contradict any locked decision.

**MEDIUM — The live-sentinel "mutation proof" rationale is technically wrong, which masks the HIGH
above.**
Location: Task 10 Step 4 "**Mutation proof:**" — *"because `/data/scratch` is now the volume root,
not the user's own scratch, **misses the agent-authored `LIVE_<sentinel>`**"* (and the parallel
Task 2 / success-condition / SPEC §8 wording "mutation … drops the live sentinel").
- **Why it is wrong:** the agent writes `LIVE_<sentinel>` to the **container path** `/data/scratch`,
  and the harness captures `docker exec <cid> find /data/scratch …` on that **same container path**.
  Where `/data/scratch` is *mounted* (own subpath vs. volume root) is irrelevant — under a
  `Subpath=""` leak the agent still writes `LIVE_<sentinel>` and `find /data/scratch` still lists it
  (at depth 1 of the volume root). The live sentinel is therefore present under **both** the correct
  and the leaking mount; it does **not** drop on a leak. Leak detection rests **solely** on the
  foreign-token check — i.e. entirely on the unverified seed (the HIGH).
- **Net effect:** the live sentinel is a legitimate *anti-stale / anti-substitution* binding (its
  SPEC §8 purpose — keep a clean scan from a *different* run out), but it is **not** a leak detector.
  Presenting it as one gives false confidence that the gate is doubly-protected and obscures the
  seed dependency. (Also note: the "harness-written, never hand-authored" provenance requirement is
  unenforceable by a text-file validator; the live sentinel only raises fabrication friction, it
  does not prove provenance — fine as defense-in-depth, but should not be billed as a hard catch.)
- **Fix:** correct the mutation/fabrication prose to state that the leak is caught by the
  foreign-token presence (which requires the seed-proof artifact from the HIGH), and re-label the
  live sentinel as an anti-stale binding, not a leak detector.

**LOW (non-blocking) — provenance claim unenforceable.** "written by the test harness directly from
the live `docker exec` stdout, never hand-authored" cannot be checked by the validator from the file
alone; treat it as a harness-implementation requirement, not a validator guarantee. Covered by the
MEDIUM fix.

---

## Non-blocking cosmetic notes
- Task 2 Step 2 is a single ~900-word run-on paragraph encoding the entire validator contract; it is
  technically complete but dense for a cold implementer — consider breaking into a checklist. Not a
  defect.
- Global Constraints coverage bullet cites `cc_engine.py:398–607 (~31% of the 672-line module)`;
  672 lines confirmed, run_cc_turn span confirmed — accurate, no action.
