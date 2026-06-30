# PLAN-7-compose-native-prod-deploy.md — Phase 2 iter-13 fresh review

**Target:** `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Reviewer:** Independent cold-context adversarial pre-execution review (iter 13)  
**Baseline verified (read-only):** tracker step 3 `not_started`; `docker-compose.yml` host-binds `/srv/dmac/users`; six external volumes only (no `dmac-cc-users`); `cc_engine._build_volumes` / `_run_kwargs` use host-bind `volumes=` dict; `cc_config.CCPaths` requires `host_user_root`; `cc_sweep` / `services/cc_assistant._session_metas` host-translate paths; `path_mappings` uses `host_root: dirs.output_src`; no `test_step7_*` / `validate_step7_*` yet.

---

## 2A — Vet

### Finding 2A-1 — HIGH — Task 6 checkbox order inverts the Step 2b gate
**Location:** Task 6 — *"**Step 3:** Refactor path builder + sibling spawn"* (before *"**Step 2b:** Refactor `cc_config.CCPaths`"*) plus *"**gate Task 6 Step 3** (path builder) on passing Step 2b config tests"*.  
**Why defect:** Subagent-driven implementers follow checkbox order. Step 3 refactors `_build_volumes`, memory mounts, and `path_mappings` against live `CCPaths` that still requires `host_user_root` (`cc_config.py:20–31`). Step 2b is listed afterward, so a careful implementer can complete spawn refactor on stale types, then discover the gate — or a lazy one skips 2b and ships hybrid host/volume semantics.  
**Fix:** Renumber/reorder Task 6 so **Step 2b (`cc_config.CCPaths`) is Step 3**, path-builder refactor is **Step 4**, dev migration remains Step 5. Add explicit success gate: *"Step 4 blocked until `test_cc_config_paths.py` passes with `{users_volume, user_root_mount}` only."*

### Finding 2A-2 — MEDIUM — Operator deploy docs omit mandatory Docker version floors
**Location:** Task 8 Step 2 — *"pull/clone → fill gitignored config → `./startup.sh install` → build → up → verifier"*; Task 7 Step 2 env keys; contrast Task 1 / Task 2 preflight floors (*Engine ≥26/API v1.45*, *Compose plugin ≥2.26*).  
**Why defect:** Task 6 sibling spawns require programmatic `docker.types.Mount` volume subpaths (Engine API v1.45+). Preflight/validator enforce floors on evidence bundles, but `DEPLOY.md`, `docker/nextseek.env.example`, and `startup/steps/prereqs.py` (currently only checks presence, not minimum versions) give operators no advance warning. Cold implementer passes doc guards; MBP Task 10 fails with opaque mount/API errors.  
**Fix:** Task 8 Step 2 + Task 7: add a numbered **Prerequisites** step documenting minimum Engine (≥26) and Compose plugin (≥2.26). Task 8 doc guard: fail if numbered procedure omits Docker version prerequisites when subpath mounts are required. Optionally extend `startup/steps/prereqs.py` with version parsing (out of plan scope note OK if DEPLOY is authoritative).

### Finding 2A-3 — MEDIUM — Locked SPEC-7 §8 preflight schema missing `docker_compose_meets_subpath_floor`
**Location:** PLAN Task 1 L89 / Task 2 L129 require `docker_compose_meets_subpath_floor`; SPEC-7 §8 `preflight.json` lists only `docker_engine_meets_subpath_floor` (SPEC L179).  
**Why defect:** Authority hierarchy is locked design > plan. Implementer building validator from SPEC §8 alone will omit a field the plan treats as mandatory, producing validator/preflight drift and ambiguous “source of truth” at Task 2.  
**Fix:** Amend SPEC-7 §8 to add `docker_compose_meets_subpath_floor` (bool, Compose plugin ≥2.26) with same semantics as Task 1, **or** explicitly drop compose floor from plan if only Engine is mandatory (not recommended given compose-file subpath syntax).

### Finding 2A-4 — MEDIUM — Task 3 port boundary vs in-tree `dmac_assistant/` Python package unstated
**Location:** Task 3 — *"Port broadly enough to build the full production-capable CC image into `docker/cc-runtime/`"*; File Structure lists `docker/cc-runtime/` only.  
**Why defect:** Live NExtSEEK already vendors `dmac_assistant/` (`pyproject.toml` path dep) for Django-side router, BAML, `run_tracker.diff_files` (`cc_engine.py:647`), etc. Task 3 scopes the **agent image** tree but never states that `dmac_assistant/` remains the Django Python dependency (distinct from external `dmac-assistant` repo checkout on deploy hosts per G7-2). Cold implementer may duplicate, delete, or break imports while “porting runtime.”  
**Fix:** Task 3 Step 2 add explicit boundary bullet: `docker/cc-runtime/` = CC **container image** assets only; in-tree `dmac_assistant/` package stays unless a named sub-step moves specific modules; list modules that must remain importable from nextseek (`run_tracker`, router BAML client, etc.).

### Finding 2A-5 — MEDIUM — Permissions table omits monorepo tracker write path clarity for Task 11
**Location:** Permissions table L551 — `INTEGRATION_PLAN_PATH` default `../state/integration-plan.json`; Task 11 updates tracker after MBP evidence.  
**Why defect:** Task 10 runs from NExtSEEK-only clone (may lack `../state/`). Task 11 says update tracker from monorepo checkout — correct in prose, but Permissions table does not list **write access to monorepo `work/state/integration-plan.json`** as distinct from NExtSEEK repo R/W. Implementer on Step 7 branch only could mutate wrong file or skip tracker update.  
**Fix:** Permissions table: add row for monorepo `work/state/integration-plan.json` write (Task 11 only, after MBP evidence committed); cross-reference Task 11 sequencing note.

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — `DMAC_PATH_MAPPINGS` / `path_mappings` post-cutover schema undefined
**Location:** Task 6 Step 3 item 7 — *"Refactor `path_mappings` / `DMAC_PATH_MAPPINGS` … to volume-relative logical roots (retire `output_src` / `scratch_src` host strings)"*; success L343 — *"mount-relative logical roots (retire `output_src` / `artifacts_published` host strings)"*.  
**Why defect:** Live code injects `{"output": {"container_root": ..., "host_root": dirs.output_src}, ...}` (`cc_engine.py:479–484`) and `_publish_artifacts` returns `Path(output_host_root) / rel` display paths (`cc_engine.py:671`). After G7-10, `output_src`/`host_user_root` retire with no replacement key names (`logical_root`? `volume_subpath`? in-container path only?). PLAN-3 will change `_publish_artifacts` return shape for turn-scoped artifacts — Step 7 cutover can break agent path translation and user-facing artifact listings without failing spawn-only tests.  
**Fix:** Lock a JSON schema in Task 6 (e.g. replace `host_root` with `logical_root` under `/dmac/users/...` or `{volume_subpath, mnt_path}`); add hermetic test asserting `build_agent_environment` + `_publish_artifacts` emit the new shape; update Task 5 Step 3 to migrate `validate_cc_acceptance.py` published-files check to nested `{project}/{user}/…` paths.

### Finding 2B-2 — MEDIUM — Volume subpath pre-existence requirement lacks negative hermetic test
**Location:** Task 6 Step 3 item 6 — *"Spawn only after provision mkdir for every subpath"*; permission model L329.  
**Why defect:** Docker Engine does not auto-create volume subpath directories before mount (subpath must exist inside the named volume). Plan relies on Django mkdir but has no failing test that spawn/mount errors when subpath is absent. Greenfield MBP with empty `dmac-cc-users` can fail at runtime with no hermetic guard — catastrophic for 7d gate debugging.  
**Fix:** Task 6 Step 1: hermetic test — mock `containers.run` or integration fixture asserting mount failure (or explicit preflight error) when subpath directory missing; Task 10 success bullet: first forced-CC turn proves provision created nested layout on empty volume.

### Finding 2B-3 — MEDIUM — `cc_sweep` / `_session_metas` cutover guarded by grep only
**Location:** Task 6 Step 1 L300 — *"grep guard forbids `host_user_root` replace in `cc_assistant.py`"*; atomic cutover tests `run_cc_turn` → `containers.run` only.  
**Why defect:** Live `cc_sweep.py:37–38` and `services/cc_assistant.py:98,329,335` reverse-translate via `host_user_root`. Implementer can pass spawn-path test while leaving sweep/memory backstop on host paths — 1c idle sweep silently no-ops after cutover (data loss / false “memory works”).  
**Fix:** Extend atomic cutover: hermetic tests that `_session_metas` stores mount-relative `transcript_path`, `cc_sweep` reads it without host translation, and memory staging copies CLAUDE.md bytes to `dirs.cc_state_mnt` (not host bind).

### Finding 2B-4 — MEDIUM — Dev migration policy capture missing from Task 9 smoke
**Location:** Task 6 Step 4 — validator requires `migration_policy` on dev-VM bundles; Task 9 Step 2 only optionally records it (*"If dev-VM and `/srv/dmac/users` had data…"*).  
**Why defect:** Task 9 is non-gating but its evidence is committed. Dev VM will have `/srv/dmac/users` data at cutover. Without required `migration_policy` + transcript in Task 9 bundle, implementer either fails validator on dev smoke or omits migration evidence needed to debug 1b/1c regression.  
**Fix:** Task 9 Step 2: **require** `meta.json.migration_policy` + command transcript when `host_label` is dev-VM and pre-cutover host data existed (detect via preflight hash or explicit flag).

### Finding 2B-5 — LOW — Rollback vs pause-and-ask for partial Task 6 cutover
**Location:** Risk register L569 — sibling subpath wrong → isolation guards; no explicit “revert compose bind + cc_config” rollback steps.  
**Why defect:** Atomic cutover across compose + five Python modules is one-way without documented rollback. Not blocking if sign-off gates hold, but careful implementer may hesitate.  
**Fix:** Task 6 Step 4 or Risk register: one-line rollback = restore host bind in compose + revert Task 6 commit; re-run Step 2 isolation tests.

---

## 2C — Validate External Dependencies

### Finding 2C-1 — MEDIUM — Step 7 evidence artifact rename / field mapping from legacy realstack unspecified
**Location:** Task 10 Step 4 — *"Adapt `tests/test_cc_realstack.py` … to emit SPEC-7 §8 artifacts"*; SPEC §8 names `forced_cc_result.json`, `proxy_log_window.txt`, `network_inspect.json`; live realstack writes `forced_result.json`, `proxy_log.txt`, `network.json`, `ledger.json` with `total_cost_usd`.  
**Why defect:** Task 2 validator checks `forced_cc_result.json.cost` but realstack records `total_cost_usd` in terminal frame + `ledger.json`. Without explicit rename/mapping table, implementer may emit wrong filenames (validator fail) or hardcode `cost: 0` (gameable).  
**Fix:** Task 10 Step 4 + Task 2: artifact mapping table (`forced_result.json` → `forced_cc_result.json`, map `total_cost_usd` → `cost`, `proxy_log.txt` → `proxy_log_window.txt`, etc.); validator negative control rejects legacy filenames under `acceptance_evidence/step7/`.

### Finding 2C-2 — MEDIUM — `host_label` enum / MBP pattern matching undefined in validator contract
**Location:** Task 2 L131 — *"`host_label` branching table: MBP → … dev-VM → require `migration_policy`"*; Task 1 L84 — *"when `meta.json.host_label` matches MBP pattern"*.  
**Why defect:** No normative regex or allowed enum (`mbp-local`, `macbook-pro`, case rules). Implementer can use loose substring match; operator can set `host_label: "mbp-greenfield"` to skip dev-only checks or mis-branch snapshot rules.  
**Fix:** Task 2 Step 2: document allowed `host_label` values or regex (`(?i)(mbp|macbook)` for MBP; `{dev-vm, nextseek-dev}` for dev); hermetic validator tests for each branch.

### Finding 2C-3 — MEDIUM — Greenfield volume list ignores startup `--instance` volume prefix
**Location:** Task 2 L129 — seven hardcoded volume names; `startup/steps/volumes.py` `volume_names_for_prefix("test-")` → `test-seek-filestore`, etc.  
**Why defect:** MBP greenfield check parses `docker volume ls` for exact names `seek-filestore`, … Default `./startup.sh install` uses empty prefix (OK). Named instance install creates prefixed volumes while compose still references unprefixed external names — pre-bootstrap check false-negatives or false-positives depending on host history.  
**Fix:** Task 10 Step 0: require default instance (no `--instance` prefix) for 7d gate, **or** validator reads `startup/.instance.json` prefix and adjusts expected volume names; document in Task 8 DEPLOY.

### Finding 2C-4 — LOW — Docker subpath API version claims verified OK
**Location:** Task 1 / Task 2 Engine ≥26 / API v1.45 and Compose ≥2.26.  
**Why note:** Confirmed against Docker Engine API v1.45 release notes (`VolumeOptions.Subpath` on `POST /containers/create`). Plan floors align with external reality.  
**Fix:** None required; cite in DEPLOY prerequisites (see 2A-2).

### Finding 2C-5 — LOW — Root `docker>=7.1.0` pin vs existing transitive dep
**Location:** Task 6 Step 2 — *"Add `docker>=7.1.0` to root `pyproject.toml`"*; `dmac_assistant/pyproject.toml` already pins `docker>=7.1.0`.  
**Why note:** Explicit root pin is good hardening; not a defect. Task 6 should note confirmation step if lock already satisfies.  
**Fix:** Optional clarifying sentence in Task 6 Step 2.

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — Task 10 forced-CC success gameable via hand-crafted `forced_cc_result.json`
**Location:** Task 10 success — *"Forced CC turn completes with sentinel in reply"* + *"Step 7 validator passes"*.  
**Cheapest fake:** Run `run_cc_turn` once (or skip), write `forced_cc_result.json` with `{"sentinel": "...", "is_error": false, "cost": 0.01}`, stub `proxy_log_window.txt` with a synthetic `POST /model/.../invoke -> 200` line, empty `agent_env_scan.txt`.  
**Oracle:** Mutation test — corrupt compose `bedrock-proxy` service; if validator still passes, gate is hollow.  
**Remedy:** Task 2 validator: cross-artifact correlation already listed (run_id in proxy log; agent container in `network_inspect.json`); **add** requirement that `forced_cc_result.json.run_id` matches `meta.json.run_id` and agent container label `nextseek.cc.run`; reject when proxy log window length is zero or agent env scan empty; require `cost` sourced from terminal `query_complete.total_cost_usd` with sanity check `cost > 0` for Opus turn (or document zero-cost exception).

### Finding 2D-2 — MEDIUM — Task 6 volume persistence success gameable without recreate proof
**Location:** Task 6 success — *"CC user data persists across `docker compose up -d --force-recreate nextseek`"* verified with sentinel under `scratch/`*.  
**Cheapest fake:** Write sentinel file manually on volume mount path without running recreate; or recreate only nginx.  
**Remedy:** Task 10 evidence must include `compose_services.txt` / timestamps showing recreate occurred after sentinel write; validator compares `meta.json` recreate command in transcript or `docker_ps.txt` generation times.

### Finding 2D-3 — MEDIUM — Coverage target scoped to validator module only
**Location:** Global constraints L29 — `--cov=nextseek_api.cc_assistant.tests.validate_step7_compose_deploy --cov-fail-under=95`.  
**Cheapest fake:** Minimal `cc_engine` / `cc_provision` refactor with weakened or deleted assertions in existing Step-2 tests while validator module stays well-tested.  
**Remedy:** Add explicit per-module floors for Task 6 touchpoints (`cc_engine`, `cc_provision`, `cc_config`) or require “no decrease in line coverage” on listed test files vs preflight baseline hash.

### Finding 2D-4 — MEDIUM — Task 5 legacy acceptance migration underspecified
**Location:** Task 5 Step 3 — update `validate_cc_acceptance.py` / `test_cc_realstack.py` for compose DNS `bedrock-proxy`.  
**Cheapest fake:** Global replace `dmac-bedrock-proxy` → `bedrock-proxy` in URLs only; leave `published_files.json` user-scoping check (`validate_cc_acceptance.py:129`) assuming flat `uid/` paths while nested `{project}/{user}/output/…` is live after Step 2/6.  
**Remedy:** Task 5 Step 3 bullet: update copier scope check for Step-2 nested layout + volume-relative display paths; hermetic test in `test_validate_cc_acceptance.py` with nested path fixture.

### Finding 2D-5 — LOW — Task 1 preflight `deploy_commit` gameable with amended commit
**Location:** Task 1 — `live_gate_transcript_committed` via `git cat-file` at `deploy_commit`.  
**Cheapest fake:** Commit empty placeholder transcript at Task 1, amend/replace before Task 10 without re-running preflight.  
**Remedy:** Validator re-reads `git cat-file` at validation time using `preflight.json.deploy_commit` **and** `meta.json.repo_commit` equality check.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log references prior `.vetting/` files; iter-13 reviewer did not read them (cold-context isolation).
- Task 6 uses duplicate step label “Step 2” (wire compose + Step 2b) — confusing but not blocking if reordered per 2A-1.
- `startup/steps/volumes.py` module docstring still says “six named volumes”; update when adding `dmac-cc-users`.
- `integration-plan.json` substep 7b description still lists `DMAC_USER_ROOT`; harmonize when Task 11 updates tracker (status-only mutation).
- SPEC-3 E8 neutral `/srv/dmac/users` default superseded by G7-10 at Step 7 — plan gate handles sequencing; not a PLAN-7 defect.
