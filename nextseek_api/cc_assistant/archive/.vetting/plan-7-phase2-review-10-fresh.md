# PLAN-7-compose-native-prod-deploy.md — Independent adversarial Phase 2 review (iter 10, cold context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `nextseek_api/cc_assistant/archive/SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-10)  
**Iter-9 hardening acknowledged:** Task 6 cc-state copy substeps (1–6), dropped `user_memory_file` success bullet, atomic cutover hermetic test, `_session_metas` mount-relative, `docker>=7.1.0` pin in Task 6 Step 2.  
**Live repo spot-checks (read-only):** `docker-compose.yml` still host-binds `/srv/dmac/users`; six external volumes only; `startup/steps/volumes.py` six names; `cc_engine._build_volumes` / `_run_kwargs` use host bind dict + `volumes=`; `cc_config.CCPaths` still requires `host_user_root`; `services/cc_assistant._session_metas` host-translates `transcript_path`; `cc_sweep.py` host↔mount reverse-translate; `_publish_artifacts` emits `output_host_root`-based display paths; root `pyproject.toml` has no explicit `docker` dep (transitive `docker==7.1.0` in `uv.lock`); `cc_runner_available()` cites standalone `make image-build` + manual sidecar network; `test_cc_provision_isolation.py` / `test_cc_engine_env.py` not listed in Task 6 modify set.

---

## 2A — Vet

### Finding 2A-1 — HIGH — Task 6 omits `cc_config.py` despite CCPaths end-state contract

**Location:** File Structure (Modify) vs Task 6 **Files** block; Task 6 Step 2 — *"`CCPaths` end state (Task 6 Step 2): `{users_volume: str, user_root_mount: str}`; retire `host_user_root`"*

**Why defect:** Global File Structure lists `cc_config.py` as modified for volume-backed trees, but Task 6’s explicit **Files** section never names `cc_config.py`. Live `cc_config.CCPaths` still exposes only `host_user_root` + `user_root_mount` and reads `DMAC_USER_ROOT`. Sibling `Mount` objects need a named volume source (`DMAC_CC_USERS_VOLUME` / `dmac-cc-users`) that cannot be inferred from mount path alone. A cold implementer can complete Task 6 Steps 2–3 on `cc_provision`/`cc_engine` while leaving `CCPaths.from_env()` unchanged, breaking spawn mounts or silently retaining host-bind semantics.

**Fix:** Add `cc_config.py` and `tests/test_cc_config_paths.py` to Task 6 **Files**; add Step 1 failing tests for `{users_volume, user_root_mount}` and absence of required `host_user_root`; add Step 2 substep to read `DMAC_CC_USERS_VOLUME` and retire `DMAC_USER_ROOT` from the deploy contract.

---

### Finding 2A-2 — HIGH — Task 6 test inventory incomplete for Mount API cutover

**Location:** Task 6 Step 1 — listed tests vs live dependents of `_build_volumes` / `_run_kwargs`

**Quote (Task 6 Step 1):** *"Modify/extend: … `test_cc_engine_volumes.py`, `test_cc_engine_memory_mounts.py`, `test_cc_provision_paths.py`, `test_step7_compose_deploy.py`."*

**Why defect:** Live code also has `tests/test_cc_provision_isolation.py` (calls `_build_volumes` with dict keys), `tests/test_cc_engine_env.py` (`test_run_kwargs_attaches_default_network` passes `volumes={}`), and `tests/test_cc_config_paths.py` (asserts `host_user_root`). Plan’s atomic cutover test targets `run_cc_turn` → `containers.run` only; it does not require updating `_run_kwargs` signature tests. Subagent per task can land `mounts=` in engine while leaving `_run_kwargs`/`test_cc_engine_env.py` on `volumes=`, producing a red suite or a partial revert.

**Fix:** Extend Task 6 **Files** and Step 1 to include `test_cc_provision_isolation.py`, `test_cc_engine_env.py`, and `test_cc_config_paths.py`; add explicit Step 2 substep to refactor `_run_kwargs` to accept `mounts: list[docker.types.Mount] | None` and update `test_run_kwargs_attaches_default_network` accordingly.

---

### Finding 2A-3 — MEDIUM — `cc_sweep.py` cutover underspecified vs `_session_metas`

**Location:** Task 6 Step 1 — *"`cc_sweep.py` + `services/cc_assistant._session_metas`: mount-relative `transcript_path` only; grep guard forbids `host_user_root` replace in `cc_assistant.py`."*

**Why defect:** File Structure lists `cc_sweep.py` as modified, and live `_run_sweep` still does `tgt.transcript_path.replace(host_user_root, user_root_mount, 1)` before reading bytes. After `_session_metas` stores mount-relative paths, that replace is dead or wrong if any host-path leak remains. Grep guard scopes only `cc_assistant.py`, not `cc_sweep.py`, so the sweep backstop can ship with stale translation logic.

**Fix:** Task 6 Step 3 substep: read `transcript_path` directly from `SessionMeta` (mount-relative); delete host↔mount replace in `cc_sweep.py`; extend grep guard to forbid `host_user_root` translation in both `cc_assistant.py` and `cc_sweep.py`; add hermetic sweep fixture with mount-relative `transcript_path`.

---

### Finding 2A-4 — MEDIUM — `_publish_artifacts` display paths lack implementation step

**Location:** Task 6 Success conditions — *"`path_mappings` / `DMAC_PATH_MAPPINGS` and **`_publish_artifacts` display paths** use mount-relative logical roots"*

**Why defect:** Live `run_cc_turn` calls `_publish_artifacts(..., output_host_root=dirs.output_src)` and `_publish_artifacts` builds display paths from `output_host_root` (`cc_engine.py` ~571–672). Success condition names the requirement but Task 6 Steps 1–3 only explicitly refactor `path_mappings`, `_session_metas`, and spawn mounts—not `_publish_artifacts` signature/call site. Implementer can pass atomic cutover + `path_mappings` tests while still emitting host-style `artifacts_published` strings.

**Fix:** Task 6 Step 3 bullet: retarget `_publish_artifacts` to accept logical/mount-relative root (or derive from `UserDirs.output_mnt` / `*_subpath`); add hermetic test in `test_cc_engine_publish.py` asserting display paths do not contain retired host bind roots.

---

### Finding 2A-5 — MEDIUM — `cc_runner_available()` operator messaging still pre-compose

**Location:** Live `cc_engine.cc_runner_available` — *"build it via dmac's \`make image-build\`"* and *"bring up the segmented network + bedrock-proxy sidecar first"*

**Why defect:** After Tasks 3–5, the authoritative path is root `docker compose build && docker compose up -d`. Misleading `(False, detail)` strings on MBP greenfield (Task 10) send operators to standalone repo / manual sidecar steps the plan retires (Task 8, SPEC §7). No Task step owns updating these strings or asserting absence of standalone-repo references in hermetic tests.

**Fix:** Add Task 5 Step 3 or Task 8 Step 2 substep: rewrite `cc_runner_available()` detail strings to cite compose build + `NEXTSEEK_CC_IMAGE`; hermetic test asserts detail contains no `make image-build` / standalone-repo wording.

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — Partial Task 6 cutover leaves 1c sweep/backstop silently broken

**Location:** Task 6 atomic cutover gate vs `cc_sweep.py` / `_session_metas` scope

**Quote (Task 6 Success conditions):** *"**Atomic cutover gate:** hermetic spawn-path test passes (no `host_user_root` in `run_cc_turn` → `containers.run`)."*

**Likely failure:** Engine spawn uses `mounts=` while `_session_metas` or `cc_sweep` still host-translate paths → Celery sweep cannot read transcripts (wrong path) or summarizer never fires.

**Catastrophic failure:** Dev VM cutover (Task 6 Step 4) without migration transcript + sweep silently stale → cross-session memory appears deployed but idle sessions never summarize.

**Rollback:** Pause-and-ask on failed sentinel scratch write (Task 10) or sweep hermetic regression; dev migration transcript required before cutover.

**Fix:** Widen atomic cutover gate to include `_session_metas` output shape + `cc_sweep` read path hermetic test; tie Task 9 dev smoke to one sweep-target fixture or post-cutover `cc-state` sentinel, not only `cc_runner_available()`.

---

### Finding 2B-2 — MEDIUM — Task 6 refactors lack declared coverage exception

**Location:** Global Constraints — *"`--cov-fail-under=95`"* on validator module; Tasks 3–4, 9–10 justified exceptions

**Why defect:** Task 6 rewrites `_build_volumes`, `run_cc_turn`, `cc_provision.build_user_dirs`, and service-layer memory staging—far more lines than the validator module. No per-module coverage target or justified exception is declared for `cc_engine` / `cc_provision` / `cc_assistant` changes. Implementer may satisfy global validator coverage while shipping untested Mount edge cases (missing subpath mkdir, wrong volume name).

**Fix:** Add Task 6 coverage note: `--cov=nextseek_api.cc_assistant.cc_engine` (and `cc_provision`) with `--cov-fail-under=95` on hermetic spawn/volume tests, or document a justified exception listing DI-only paths covered by Task 10 live gate.

---

### Finding 2B-3 — MEDIUM — Step 3 deploy gate is process-only for downstream artifact shapes

**Location:** Global Constraints Step 3 HARD GATE; Task 10 Step 4

**Why defect:** Gate correctly blocks stale planning state via committed `live_gate_transcript.txt`, but Task 10 Step 4 only says *"Adapt `tests/test_cc_realstack.py` … to emit SPEC-7 §8 artifacts"* without binding `forced_cc_result.json` / proxy correlation to Step 3’s post-deploy `query_complete` shape (PLAN-3 removes `artifacts_published`, adds structured `artifacts`). If Step 3 lands first, Step 7 forced-CC generator can assert obsolete fields and still produce pretty JSON.

**Fix:** Task 10 Step 4: require re-read of PLAN-3 Task 13 committed transcript + post-Step-3 `test_cc_realstack.py` assertions; Task 2 validator adds check that `forced_cc_result.json` keys match the Step-3-deployed schema (grep or schema version field in `meta.json`).

---

## 2C — Validate External Dependencies

### Finding 2C-1 — MEDIUM — `docker>=7.1.0` pin location vs runtime import site

**Location:** Task 6 Step 2 — *"Add `docker>=7.1.0` to root `pyproject.toml`"*

**Why defect:** Live root `pyproject.toml` has no `docker` entry; `cc_engine` imports `docker` inside the **nextseek** container. Transitive lock (`uv.lock` → `docker==7.1.0`) exists via `dmac_assistant/pyproject.toml`, not the Django app’s direct dependency. Relying on transitive resolution is fragile if packaging changes during Task 3 port. Plan states the pin but does not require hermetic test that `docker.types.Mount` accepts `volume_options={"subpath": ...}` on the **same** env used by `uv run pytest` for cc_assistant tests.

**Fix:** Keep root `pyproject.toml` pin; add Task 6 Step 1 assertion that import-time `docker.__version__ >= 7.1.0` in the hermetic test env (or document lockfile-only exception with grep guard on root pyproject).

**Verified OK:** Docker Engine ≥26 / API v1.45 for programmatic volume subpaths (Moby v1.45 `VolumeOptions.Subpath`); plan’s `docker_engine_meets_subpath_floor` aligns with upstream docs.

---

### Finding 2C-2 — MEDIUM — Compose plugin ≥2.26 recorded but not validator-enforced

**Location:** Task 1 Step 2 — *"Compose ≥2.26 if compose YAML uses volume subpaths"* vs Task 2 validator — only `docker_engine_meets_subpath_floor`

**Why defect:** CC sibling mounts are docker-py `Mount` from Django (Engine floor suffices), but if implementer uses compose long-form `volume.subpath` anywhere in root YAML, Compose 2.26+ is required. Preflight records compose version; validator never fails stale Compose on MBP. False-negative greenfield failure is likely on older Docker Desktop bundles.

**Fix:** Task 2 validator: when `compose_config.json` contains any `volume.subpath`, require `preflight.json.compose_meets_subpath_floor == true` (Compose ≥2.26); else document in Task 6 that nextseek service uses full-volume mount only and subpaths are API-only (drop compose version gate from Task 1 prose to avoid ambiguity).

---

### Finding 2C-3 — LOW — Standalone runtime port source commit is still must-verify

**Location:** Dependency Validation — *"Standalone `dmac-assistant` repo (port source) | Must-verify at execution"*

**Why note:** NExtSEEK already vendors `dmac_assistant/` Python package and `docker/cc-runner/` lean image; Task 3 targets `docker/cc-runtime/`. Not blocking if Task 1 preflight records source commit, but implementer must not conflate in-tree Python package with CC **image** runtime port scope.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Atomic cutover oracle is narrow (spawn path only)

**Success condition (quoted):** *"hermetic spawn-path test passes (no `host_user_root` in `run_cc_turn` → `containers.run`)."*

**Cheapest fake:** Refactor `run_cc_turn` spawn to `mounts=` while leaving `build_user_dirs` emitting `*_src` host strings, `_session_metas` host-translating, and `_publish_artifacts` using `output_src`; grep only inspects `run_cc_turn` body.

**No-op test:** Stub `containers.run` and delete `CCPaths` refactor → spawn test still green if grep scope is too narrow.

**Mutation test:** Break `users_volume` env read in `cc_config` → spawn test may stay green.

**Remedy:** Expand oracle to fail if `build_user_dirs` returns any `*_src` host bind keys; assert `Mount.source == paths.users_volume` in hermetic test.

---

### Finding 2D-2 — MEDIUM — Task 6 1c memory success relies on copy substep without byte-equality oracle vs prior MERGE probe

**Success condition (quoted):** *"merged CLAUDE.md present under cc-state `.claude/` before spawn; cc-state + transcripts `Mount` list tests pass — **no** `user_memory_file` bind."*

**Cheapest fake:** Write empty file to cc-state `.claude/CLAUDE.md`; remove RO nested bind; tests pass on existence only.

**Remedy:** Hermetic test compares copied bytes to rendered merged-tier output from `cc_memory` render path; negative test fails spawn if copy step skipped (mock render produces sentinel content).

---

### Finding 2D-3 — MEDIUM — MBP greenfield volume pre-bootstrap gameable via `greenfield_exception`

**Location:** Task 2 — *"fail if any of the seven external volumes … exist unless `meta.json.greenfield_exception` + handoff ref"*

**Cheapest fake:** Reuse dev VM volumes, set `greenfield_exception: true`, attach handoff JSON without proving clean MBP.

**Remedy:** Validator requires `pre_bootstrap_docker_volume_ls.txt` timestamp before `meta.json.timestamp` and SHA256 of handoff; fail if `dmac-cc-users` contains sentinel paths from dev migration unless `migration_policy` + dev `host_label` (not MBP pattern).

---

### Finding 2D-4 — LOW — Task 9 labeled non-gating but still commits evidence

**Success condition:** Task 9 commit of local verification evidence.

**Cheapest fake:** Commit bundle failing secret scan on branch; Task 11 blocked later but Task 9 marked done in agent notes.

**Remedy:** Already mitigated by Task 11 requiring Task 10 MBP pass; retain as LOW reminder—Task 9 commit message should not imply step 7 done (plan already states non-gating).

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log row numbering (iter 5–9 vs review file iter-N) is confusing for cold reviewers; does not block execution.
- Task 8 referenced in DEPLOY merge-order constraint while Task 7 owns env template—internally consistent but easy to misread.
- `test_cc_engine_memory_mounts.py::test_existing_volume_shape_unchanged` name will be misleading after Mount API cutover; rename during Task 6 is nice-to-have.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 3 |
| MEDIUM | 10 |
| LOW | 3 |

**Top findings:** (1) Task 6 omits explicit `cc_config.py` / `DMAC_CC_USERS_VOLUME` wiring despite CCPaths end state. (2) Incomplete Task 6 test file list leaves `_run_kwargs` and isolation tests unowned. (3) Atomic cutover gate too narrow—sweep/backstop and `_publish_artifacts` can regress while spawn test passes. (4) `cc_runner_available()` still advertises standalone build path. (5) Step 3 gate does not bind Task 10 forced-CC artifact schema to post-Step-3 reality.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
