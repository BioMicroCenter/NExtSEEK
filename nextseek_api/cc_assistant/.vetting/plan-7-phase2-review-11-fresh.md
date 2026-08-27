# PLAN-7 Phase 2 Review — Iter 11 (Fresh, Cold Context)

**Date:** 2026-06-30  
**Reviewer:** Independent adversarial (iter-11)  
**Target:** `PLAN-7-compose-native-prod-deploy.md`  
**Locked spec:** `SPEC-7-compose-native-prod-deploy.md`  
**Guide:** `/home/taishajo/AGENTS.md`  
**Iter-10 hardening under test:** `cc_config.py` in Task 6, expanded test inventory, `cc_runner_available` message update, Task 6 cc-state copy substeps, atomic cutover gate.

**Live repo spot-checks (read-only, claim verification only):**
- `docker-compose.yml` host-binds `/srv/dmac/users`; six `external: true` volumes; no `dmac-cc-users`, `bedrock-proxy`, or `dmac-cc-net`.
- `startup/steps/volumes.py` lists six volumes only.
- Only lean `docker/cc-runner/Dockerfile` exists (no `docker/cc-runtime/`).
- `cc_config.CCPaths` still requires `host_user_root`; default is laptop path `/Users/taishajoseph/dmac-dev/users`.
- `cc_engine._build_volumes` / `_run_kwargs` use host bind dict + `volumes=` (not `mounts=`).
- `run_cc_turn` passes `path_mappings` with `dirs.output_src` / `dirs.scratch_src` host roots; `_publish_artifacts(..., output_host_root=dirs.output_src)`.
- `services/cc_assistant._session_metas` host-translates `transcript_path`; memory staging still builds `user_memory_file` / `transcripts_dir` via mount→host replace.
- `cc_sweep.py` reverse-translates `transcript_path` via `host_user_root`.
- `cc_runner_available()` cites `dmac's make image-build` and manual sidecar network bring-up.
- Root `pyproject.toml` has no explicit `docker` dep (transitive `docker==7.1.0` in `uv.lock`).
- `test_cc_engine_publish.py` asserts host-path display strings; not listed in Task 6 modify set.
- Tracker step 3 is `not_started` (expected pre-implementation).

---

## 2A — Vet (permissions & execution snags)

### Finding 2A-1 — MEDIUM — `cc_runner_available()` message update lacks file ownership + hermetic test

**Location:** Task 5 Step 3 (L243); Task 5 **Files** (L226–228) lists only `docker-compose.yml` and `test_step7_compose_deploy.py`. Live `cc_engine.cc_runner_available` (L121–146) still cites `make image-build` and manual sidecar network.

**Why defect:** Iter-10 hardening names the message update in Task 5 Step 3 but does not add `cc_engine.py` to Task 5 Files or require a hermetic assertion that error strings exclude standalone-repo / sidecar wording. A Task-5-only implementer can satisfy compose-config tests while leaving operator-facing errors stale until Task 6.

**Remedy:** Add `nextseek_api/cc_assistant/cc_engine.py` to Task 5 Files; add Step 3 substep + hermetic test asserting detail strings cite `docker compose build` + `NEXTSEEK_CC_IMAGE` and exclude `make image-build` / standalone sidecar bootstrap.

### Finding 2A-2 — MEDIUM — `cc_config.py` CCPaths end-state lacks an owned numbered substep

**Location:** Task 6 Files (L273, L276–277); Task 6 Step 2 (L305–308) covers compose/startup only; Step 3 (L310+) covers path builder / sibling spawn.

**Why defect:** Iter-10 added `cc_config.py` to Task 6 Files and documents end-state `{users_volume, user_root_mount}`, but no checkbox substep assigns the `CCPaths.from_env()` refactor (retire `host_user_root` / `DMAC_USER_ROOT` bind default). Live code still requires `host_user_root`. Cold implementer can complete Step 3 on `cc_provision`/`cc_engine` while `CCPaths.from_env()` remains host-bind shaped, breaking `Mount` source resolution.

**Remedy:** Add Task 6 Step 2b (or Step 3 Step 0): refactor `cc_config.CCPaths` + `test_cc_config_paths.py`; gate Step 3 on passing config tests.

---

## 2B — Stress test

### Finding 2B-1 — HIGH — `path_mappings` / `_publish_artifacts` cutover missing from Task 6 Step 3 substeps

**Location:** Task 6 Files block (L278); success conditions (L336); Task 6 Step 3 numbered items 1–6 (L316–322) cover 1c memory only. Live `run_cc_turn` (L479–484, L573–574) and `test_cc_engine_publish.py` (L15–23) use `output_src` / `output_host_root` host-path display.

**Why defect:** Success conditions require mount-relative logical roots and retiring `output_src` host strings, but the numbered implementation substeps an implementer follows do not include `path_mappings`, `_publish_artifacts` signature/display refactor, or `run_cc_turn` call-site update. After volume subpath cutover, artifact paths reported to users and injected into agent env can point at non-existent host bind paths — functional regression invisible to spawn-only tests.

**Remedy:** Add Task 6 Step 3 items: refactor `path_mappings` / `DMAC_PATH_MAPPINGS` to volume-relative logical roots; change `_publish_artifacts` to emit mount-relative display paths; add `test_cc_engine_publish.py` to Task 6 modify list with updated assertions.

### Finding 2B-2 — MEDIUM — Task 9 dev-VM smoke omits `host_label` / `migration_policy` evidence steps

**Location:** Task 2 validator + `host_label` branching table (L129–131); Task 6 Step 4 (L328–330); Task 9 Steps 1–3 (L423–434).

**Why defect:** Validator requires `migration_policy` on dev-VM `host_label` bundles, but Task 9 evidence generation never instructs setting `meta.json.host_label`, recording Task 6 Step 4 migration transcript, or populating `migration_policy`. Dev-VM smoke either fails validator for undocumented reasons or implementer avoids dev label — weakening non-gating smoke value.

**Remedy:** Task 9 Step 2: require `host_label`, and when dev-VM, capture `migration_policy` + migration/wipe transcript per Task 6 Step 4 before validator run.

### Finding 2B-3 — MEDIUM — Task 6 volume refactor lacks declared coverage exception

**Location:** Global coverage (L29); Task 6 touches `cc_config.py`, `cc_provision.py`, `cc_engine.py`, `cc_sweep.py`, `services/cc_assistant.py` (L61–62, L273–284).

**Why defect:** Tasks 3–4 and 9–10 have justified coverage exceptions; Task 6 is a large runtime refactor with no explicit exception or per-module coverage targets. Implementer may hit `--cov-fail-under=95` mid-cutover without guidance.

**Remedy:** Add Task 6 coverage note: list modules under refactor + interim threshold or staged coverage gates until cutover completes.

---

## 2C — Validate external dependencies

### Finding 2C-1 — MEDIUM — Compose plugin ≥2.26 recorded but not validator-enforced

**Location:** Task 1 Step 2 (L97) records `docker compose version` (≥2.26); Task 2 Step 2 (L129) enforces `docker_engine_meets_subpath_floor` (Engine ≥26 / API v1.45) only.

**Why defect:** Volume subpath mounts require both Engine API support and Compose file support. Preflight can record an insufficient Compose plugin while Engine floor passes, producing a late runtime failure on Task 10 MBP.

**Remedy:** Add `docker_compose_meets_subpath_floor` bool to `preflight.json`; validator fails when false (Compose ≥2.26).

### Finding 2C-2 — MEDIUM — Explicit `docker>=7.1.0` pin fragile after runtime port

**Location:** Task 6 Step 2 (L281); live root `pyproject.toml` has no explicit `docker` dep; `uv.lock` carries transitive `docker==7.1.0`.

**Why defect:** Task 6 requires `docker.types.Mount` with `volume_options={"subpath": ...}`. After Task 3 ports runtime in-tree, dependency graph may shift; without explicit root pin, hermetic Mount tests can pass in dev while production `nextseek` container lacks ≥7.1.0.

**Remedy:** Keep Task 6 Step 2 pin; add hermetic import/version floor test that fails if `docker` package <7.1.0 in the runtime environment.

### Finding 2C-3 — LOW — Standalone runtime port source commit is must-verify

**Location:** Dependency Validation (L582); Task 3 Step 2.

**Status:** Acknowledged. Preflight should record source commit at implementation time. Not a plan blocker.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Atomic cutover oracle is spawn-path-only

**Location:** Task 6 Step 1 (L301–302); success conditions (L338); grep guards for `cc_sweep` / `cc_assistant` (L299).

**Why defect:** "Atomic cutover gate" tests `run_cc_turn` → `containers.run` receives `mounts=` only. It does not integration-test `cc_sweep` transcript reads or `_session_metas` mount-relative paths after cutover. Implementer could pass spawn test while leaving sweep/backstop on `host_user_root` translation — 1c idle sweep silently no-ops until production.

**Remedy:** Extend atomic cutover to hermetic tests: `cc_sweep` reads mount-relative `transcript_path` without replace; `_session_metas` stores mount-relative paths; or single end-to-end hermetic fixture chaining provision → metas → sweep.

### Finding 2D-2 — LOW — Step 3 deploy gate is process-enforced (expected)

**Location:** Global Constraints (L27–28); tracker step 3 `not_started`.

**Status:** Plan correctly blocks Step 7 start until gate passes. Not a plan defect at planning time.

---

## Iter-10 hardening assessment

| Hardening claim | Present in plan? | Residual gap |
|-----------------|------------------|--------------|
| `cc_config.py` in Task 6 | Yes (Files L273) | No owned numbered substep (2A-2) |
| Expanded test inventory | Partial | `test_cc_engine_publish.py` omitted (2B-1) |
| `cc_runner_available` strings | Partial (Task 5 Step 3) | No Task 5 file ownership / hermetic test (2A-1) |
| Task 6 cc-state copy substeps | Yes (Step 3 items 1–6) | Adequate for 1c memory mount strategy |
| Atomic cutover gate | Yes (Step 1 + success) | Spawn-only; sweep/metas not gated (2D-1) |

---

## Severity counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 6 |
| LOW | 2 |

---

## Top findings (priority order)

1. **2B-1 (HIGH)** — `path_mappings` / `_publish_artifacts` cutover absent from Task 6 Step 3 numbered work; `test_cc_engine_publish.py` not in modify inventory.
2. **2A-2 (MEDIUM)** — `cc_config.py` CCPaths end-state documented in Files block but not assigned a numbered implementation substep.
3. **2B-2 (MEDIUM)** — Task 9 dev-VM smoke missing `host_label` / `migration_policy` capture required by Task 2 validator branching.
4. **2C-1 (MEDIUM)** — Compose ≥2.26 preflight-only; validator enforces Engine floor but not Compose plugin floor for subpath mounts.
5. **2D-1 (MEDIUM)** — Atomic cutover gate covers spawn path only; 1c sweep/backstop can remain on host translation while gate passes.

---

## Verdict

Iter-10 hardening materially improved Task 6 (cc-state copy substeps, `cc_config.py`/`cc_sweep`/`cc_assistant` in scope, expanded tests). One HIGH execution gap remains: artifact path / agent `path_mappings` refactor is in success conditions but not in numbered Step 3 substeps, with a live test file (`test_cc_engine_publish.py`) still asserting host-path display. Six MEDIUM findings are addressable without architectural change. No CRITICAL security or spec-contradiction defects found. Plan is implementable after targeted hardening.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
