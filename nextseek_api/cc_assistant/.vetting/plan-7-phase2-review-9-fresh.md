# PLAN-7 Phase 2 Review — Iter 9 (Fresh, Cold Context)

**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `nextseek_api/cc_assistant/SPEC-7-compose-native-prod-deploy.md`  
**Guide:** `/home/taishajo/AGENTS.md`  
**Reviewer:** Independent cold-context adversarial (iter 9)  
**Date:** 2026-06-30  
**Prior hardening acknowledged (not read):** iter-8 — 1c memory via cc-state copy; Engine subpath floor + pre-bootstrap volume/network validator; SPEC §8 pre-bootstrap + preflight fields.

**Repo spot-checks (read-only, claim verification only):** Root `docker-compose.yml` still host-binds `/srv/dmac/users`; six `external: true` volumes only; no root `bedrock-proxy` / `dmac-cc-net`; `startup/steps/volumes.py` lists six volumes; `cc_engine._build_volumes` / `_run_kwargs` use host bind dicts + `volumes=` (not `mounts=`); `services/cc_assistant._session_metas` stores `transcript_path` as host-translated path; memory staging still translates mount→`host_user_root` for `user_memory_file` / `transcripts_dir`; `test_cc_engine_memory_mounts.py` asserts nested file bind; root `pyproject.toml` has no explicit `docker` dep (transitive `docker==7.1.0` in `uv.lock`); `cc_runner_available()` cites standalone `make image-build`; tracker step 3 `not_started` (expected pre-implementation); `cc_config._DEFAULT_HOST_USER_ROOT` is still laptop path.

---

## 2A — Vet (permissions, execution snags)

### Finding 2A-1 — HIGH — Task 6 success condition contradicts Step 3 1c memory cutover

**Location:** Task 6 Step 3 vs Task 6 Success conditions  
**Quote (Step 3):** *"**drop** separate `user_memory_file` RO file bind in volume mode"* / *"update `test_cc_engine_memory_mounts.py` for `list[docker.types.Mount]` (no host file key)"*  
**Quote (Success):** *"1c memory mounts still populate after cutover (`user_memory_file`, `transcripts_dir` tests pass)."*

**Why:** A cold implementer can satisfy the success bullet by preserving the host file-bind API and tests while Step 3 explicitly retires it. That reintroduces the iter-8 defect family (file overlay on volume subpaths) or leaves contradictory acceptance criteria. The plan cannot be executed TDD-first without guessing which contract wins.

**Fix:** Replace success bullet with cc-state-copy assertions: merged CLAUDE.md bytes present under `cc-state/{session}/.claude/CLAUDE.md` before spawn; `_build_volumes` returns Mount list with cc-state `.claude` RW subpath + transcripts RO subpath; **no** `user_memory_file` bind key; update/remove `test_user_memory_mounted_ro_nested_over_session_claude`.

---

### Finding 2A-2 — HIGH — `cc_assistant.py` 1c staging refactor under-specified for cc-state copy + volume subpaths

**Location:** Task 6 Step 3 (1c memory block); File Structure (`services/cc_assistant.py`)  
**Quote:** *"(1) Django copies merged user-tier `CLAUDE.md` bytes into the cc-state volume path … **before** spawn (same sync step as today in `services/cc_assistant.py`)"*

**Why (spot-check):** Live `services/cc_assistant.py` writes memory to `dirs.memory_mnt / "CLAUDE.md"` (`_memory/{session}/`), then translates to `host_user_root` for `user_memory_file` bind — it does **not** copy into `cc-state/{session}/.claude/CLAUDE.md`. Task 6 lists `cc_assistant.py` for `host_user_root` translation retirement but gives no numbered substeps for: (a) retarget write/copy to cc-state `.claude`; (b) stop passing `user_memory_file`; (c) pass volume-relative subpaths (or internal Mount list) for `transcripts_dir`. A careful implementer stalls; a lazy one leaves 1c broken post-cutover.

**Fix:** Task 6 Step 3 add explicit substeps mirroring current L317–347 flow: write/render → copy/sync to `Path(dirs.cc_state_mnt)/".claude"/"CLAUDE.md"` → stage transcripts under volume-relative path → spawn with Mount API only. Add hermetic test that memory bytes land in cc-state path, not `_memory/.../CLAUDE.md` bind.

---

### Finding 2A-3 — HIGH — `_session_metas` / `cc_sweep` transcript path chain still host-centric; not in Task 6 test checklist

**Location:** Task 6 File Structure / Step 1 (`cc_sweep.py` grep guard only)  
**Quote:** *"refactor 1c memory path translation (`host_user_root` replace) to volume-subpath / mount-relative only"*

**Why (spot-check):** `_session_metas` reads jsonl via `cc_state_mnt` but stores `transcript_path=host_path` (mount→host replace). Sync summarize and sweep repeat host→mount replace. After G7-10 retires `host_user_root`, empty/wrong `DMAC_USER_ROOT` breaks 1c sync reads and sweep even if sibling mounts are correct. Plan names `cc_sweep.py` but not `_session_metas` refactor or tests.

**Fix:** Extend Task 6 modify list + Step 1 tests: `SessionMeta.transcript_path` mount-relative (or volume-subpath string); remove bidirectional host translation in `_session_metas`, memory staging, and `cc_sweep.py`; grep guard for `host_user_root` in `services/cc_assistant.py`.

---

### Finding 2A-4 — MEDIUM — `_publish_artifacts` display paths omitted from volume cutover

**Location:** Task 6 Step 2 / Success (*"`path_mappings` / `DMAC_PATH_MAPPINGS` use mount-relative paths"*)  
**Quote (live code):** `_publish_artifacts(..., output_host_root=dirs.output_src)` → `artifacts_published` host paths.

**Why:** Task 6 retires `*_src` / `host_user_root` but only names `path_mappings`. Copier still uses `output_src` for user-visible paths (Step 3 UI / realstack). Post-cutover, published artifact strings may be wrong or refer to deprecated host semantics.

**Fix:** Task 6 Step 2/3: refactor `_publish_artifacts` to emit mount-relative or logical paths consistent with updated `DMAC_PATH_MAPPINGS`; add hermetic test in `test_cc_engine_publish.py`.

---

### Finding 2A-5 — MEDIUM — `UserDirs` / `cc_provision.py` subpath migration absent from Task 6 Step 1

**Location:** Task 6 Step 2 end-state vs Step 1 tests  
**Quote:** *"`UserDirs` exposes `*_subpath` … Retire `host_user_root` / `*_src` host bind keys after cutover."*

**Why:** Live `build_user_dirs` returns only `*_src` + `*_mnt` derived from `host_user_root`. Step 1 lists compose/startup/Mount API tests but not failing tests for `input_subpath`, `cc_state_subpath`, etc., or `test_cc_provision_paths.py` updates. Implementer may bolt subpaths onto `_build_volumes` while provision layer still requires `host_user_root`.

**Fix:** Task 6 Step 1: failing tests for new `UserDirs` fields + `build_user_dirs` without `host_user_root`; Step 2 explicit `cc_provision.py` + `cc_config.py` cutover order before `_build_volumes`.

---

### Finding 2A-6 — MEDIUM — Permission model for uid 1001 on named volume deferred without choice

**Location:** Task 6 Step 2 — *"Permission model: document chosen uid-1001 scratch strategy (Django root mkdir + `chmod` on volume paths, or init)"*

**Why:** Phase 5.5 says permissions resolved before execution, but Task 6 success requires sentinel scratch write on volume (Tasks 9/10). Unchosen strategy blocks MBP gate or invites ad-hoc `chmod 777` without test contract.

**Fix:** Lock one strategy in Task 6 (recommend: retain Django mkdir+chmod via `*_mnt`, document in DEPLOY.md) and add hermetic/compose test asserting agent uid can write scratch subpath.

---

### Finding 2A-7 — LOW — `cc_config.py` laptop default persists; neutral default cutover not tied to Task 6/7

**Location:** live `cc_config._DEFAULT_HOST_USER_ROOT = "/Users/taishajoseph/dmac-dev/users"`  
**Why:** G7-10 retires host bind; default still implies laptop host root. Task 7 env template work may miss neutralizing `from_env` default when `DMAC_USER_ROOT` unset.

**Fix:** Task 6 or 7: set neutral default (empty or mount-only) and test `CCPaths.from_env()` without host bind implication.

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — Most likely Task 6 failure: partial Mount API migration

**Location:** Task 6; Risk Register rank 2  
**Why:** Live stack uses bind dict end-to-end (`build_user_dirs` → `_build_volumes` → `_run_kwargs(volumes=)`). Plan specifies Mount API but scattershot file list. Partial migration (compose volume OK, spawn still bind dict with empty `host_user_root`) fails every CC turn at MBP gate — matches Risk #2 but plan tasks don't enforce single atomic cutover checkpoint.

**Fix:** Task 6 add integration checkpoint: one hermetic test module asserts no `host_user_root` in spawn path from `run_cc_turn` through `containers.run` mock; block Task 9/10 until it passes.

---

### Finding 2B-2 — MEDIUM — Task 9 dev smoke vs Task 2 `migration_policy` validator gap

**Location:** Task 2 validator scope (*"`migration_policy` … when `meta.json.host_label` is in `{dev-vm, nextseek-dev}`"*); Task 9 (no migration evidence)  
**Why:** Dev VM smoke (Task 9) will hit existing `/srv/dmac/users` data. Validator requires `migration_policy` on dev-VM bundles, but Task 9 never captures Task 6 Step 4 migration transcript or `meta.json.migration_policy`. Dev smoke can fail validator for undocumented reason, or implementer skips dev label to game the gate.

**Fix:** Task 9 Step 2: when `host_label` is dev-class, require Task 6 Step 4 migration evidence fields; or document Task 9 uses non-dev label (then say dev migration is out-of-band until Task 10).

---

### Finding 2B-3 — MEDIUM — Coverage floor narrow; Task 6 engine refactor mostly live-gated

**Location:** Global Constraints — *"`--cov-fail-under=95`"* on validator module; Tasks 3–4, 9–10 justified exceptions  
**Why:** Largest behavioral change (volume subpaths, 1c memory, publish paths) lives in `cc_engine.py` / `cc_assistant.py` with justified live exceptions. Stress: hermetic suite green while Mount/subpath bugs surface only on MBP paid gate.

**Fix:** Justify explicitly in Task 6: list hermetic modules required at ≥95% (`cc_provision`, `_build_volumes`, memory staging helpers) or add DI mock test that mutates one subpath line and expects red.

---

### Finding 2B-4 — LOW — Rollback: dev migration wipe vs copy under-specified for data loss

**Location:** Task 6 Step 4  
**Why:** "copy … **or** wipe" without decision tree forces pause mid-execution on dev VM with live Step 2 data.

**Fix:** Add operator decision MCQ in Step 4 (default: copy with sign-off when `/srv/dmac/users` non-empty).

---

## 2C — Validate External Dependencies

### Finding 2C-1 — MEDIUM — Root `pyproject.toml` lacks explicit `docker>=7.1.0` pin

**Location:** Task 6 (`docker.types.Mount` + `volume_options={"subpath": ...}`); Dependency Validation table  
**Why (spot-check):** `docker` is transitive via `dmac_assistant` in `uv.lock`, not declared in root `pyproject.toml`. Subpath Mount API requires docker-py ≥7.1.0. Dependency drift could drop or downgrade docker-py without a failing install gate.

**Fix:** Task 6 or 7 Step 1: add `docker>=7.1.0` to root project dependencies; hermetic import test already planned — tie to install manifest.

---

### Finding 2C-2 — MEDIUM — Engine floor recorded but Compose v2 subpath support not gated

**Location:** Task 2 / preflight — *"`docker_engine_meets_subpath_floor` … Engine ≥26 / API v1.45"*  
**Why:** Docker docs: compose file `volume.subpath` requires Compose **≥ v2.26.0** (in addition to Engine 26). Plan uses docker-py `containers.run(mounts=)` for siblings (compose subpath less critical) but preflight omits compose version. MBP with old Compose plugin could pass Engine check yet fail if compose-level subpath ever added.

**Fix:** Extend preflight + validator: record `docker compose version` and fail if Compose < 2.26 when compose YAML uses subpath; or document sibling-only subpath via API and mark compose subpath N/A.

---

### Finding 2C-3 — LOW — Docker subpath pre-existence requirement implicit

**Location:** Task 6 Step 3 sibling subpath mounts  
**Why:** Engine API requires subpath directory exist inside volume before mount. Plan relies on Django mkdir via `*_mnt` but does not state spawn precondition explicitly — TOCTOU if CC spawn races before mkdir.

**Fix:** One sentence in Task 6 Step 3: `_build_volumes` / spawn MUST run only after provision mkdir for every subpath segment (including `.claude` under cc-state).

---

### Finding 2C-4 — OK — Engine ≥26 / API 1.45 claim

**Verified:** Docker Engine API v1.45 adds `VolumeOptions.Subpath` (moby PR #45687, milestone 26.0.0). Plan floor is consistent.

---

## 2D — Gameproof

### Finding 2D-1 — HIGH — Cheapest fake: keep `user_memory_file` tests green via stub binds

**Success condition (quoted):** *"1c memory mounts still populate after cutover (`user_memory_file`, `transcripts_dir` tests pass)."*  
**Cheapest fake:** Leave `_build_volumes` bind-dict + file overlay; rename tests minimally; cc-state copy step skipped.  
**Oracle:** Mutation — delete cc-state copy step; old tests still pass.  
**Remedy:** See 2A-1; assert cc-state file bytes + Mount list shape.

---

### Finding 2D-2 — MEDIUM — `forced_cc_result.json.cost` provenance unspecified

**Location:** Task 2 validator — *"`forced_cc_result.json.cost <= meta.json.budget_cap_usd`"*  
**Cheapest fake:** Hardcode `"cost": 0.01` in generated JSON.  
**Remedy:** Task 10 Step 4: specify cost field source (stream parse / claude JSON line / run_tracker) and validator cross-check against proxy log window or stream artifact.

---

### Finding 2D-3 — MEDIUM — `cc_runner_available()` still advertises standalone repo build

**Location:** live `cc_engine.cc_runner_available` — *"build it via dmac's `make image-build`"*  
**Cheapest fake:** Task 10 passes after manual standalone image build while compose CC image target untested.  
**Remedy:** Task 5 or 3: update message to compose build path; validator already checks image ids — tie `cc_runner_available.json` detail string to compose-built image inspect.

---

### Finding 2D-4 — MEDIUM — Preflight `deploy_commit` + transcript gate strong (positive)

**Location:** Task 1 / SPEC §8  
**Assessment:** `live_gate_transcript_committed` + `git cat-file` at `deploy_commit` closes handoff-only gaming. MBP snapshot exception rules are bounded. **Not a defect** — iter-8 hardening holds.

---

### Finding 2D-5 — LOW — Pre-bootstrap volume list gameable via partial naming

**Location:** Task 2 — seven-volume grep on `pre_bootstrap_docker_volume_ls.txt`  
**Cheapest fake:** Capture `docker volume ls` after creating volumes under different names.  
**Remedy:** Already mitigated by exact name list + greenfield_exception — acceptable residual.

---

## Non-blocking cosmetic notes

- `cc_engine.py` module docstring still describes bind sources as host paths (lines 16–18).
- Phase 2 Vetting Log table row numbering jumps (iter 8 user decision vs iter 9 orchestrator) — readable but confusing.
- Task 11 commit policy defers to "active branch policy" without example message.

---

## Severity summary

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 5 |
| MEDIUM | 10 |
| LOW | 4 |

---

## Top findings (priority order)

1. **2A-1 / 2D-1 (HIGH)** — Task 6 success still requires `user_memory_file` tests while Step 3 drops file bind; gameable and contradictory.
2. **2A-2 (HIGH)** — cc-state copy strategy not wired to actual `cc_assistant.py` staging flow (still `_memory/` + host bind translation).
3. **2A-3 (HIGH)** — `_session_metas` / sweep transcript paths remain host-centric; missing from Task 6 checklist.
4. **2B-1 (HIGH)** — Partial Mount API migration is the dominant execution failure mode; no atomic cutover gate.
5. **2A-4 (MEDIUM)** — `_publish_artifacts` / `artifacts_published` host paths omitted from volume cutover.

---

## SPEC / plan alignment

- SPEC §8 pre-bootstrap artifacts and preflight fields match PLAN Tasks 1–2, 10 (iter-8 sync **OK**).
- G7-10 volume persistence aligns across SPEC and PLAN Task 6 (**OK** at spec level).
- Internal PLAN Task 6 success vs Step 3 (**NOT OK** — see 2A-1).

---

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
