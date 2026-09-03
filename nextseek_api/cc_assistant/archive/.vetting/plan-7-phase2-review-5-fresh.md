# PLAN-7 Phase 2 Adversarial Review (iter 5 — fresh, cold context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md` (incl. G7-10 amend 2026-06-30)  
**Reviewer:** Independent cold-context reviewer (iter 5)  
**Repo spot-checks (read-only, no `.vetting/` reads):** Root `docker-compose.yml` still host-binds `/srv/dmac/users`, six `external: true` volumes only, no `bedrock-proxy`/`dmac-cc-net`; `startup/steps/volumes.py` lists six volumes; `startup.sh` requires `uv`; `DEPLOY.md` still Phase A/B; only `docker/cc-runner/Dockerfile` (lean proof image); `cc_engine._build_volumes` / `_run_kwargs` use host-path bind dicts; tracker step 3 is `not_started`; `docker/nextseek.env.example` has no CC topology keys.

---

## 2A — Vet

### CRITICAL — Hardcoded absolute tracker path breaks portable validation

**Location:** Task 1 Step 1 / Step 2 (L81, L87)  
**Quote:** *"validator **re-reads** `/home/taishajo/work/state/integration-plan.json` at validation time"*  
**Why:** The tracker lives outside the NExtSEEK repo on a user-specific absolute path. Task 10 (MBP greenfield) and any cold implementer on another machine will not have this path. The Step 7 validator therefore fails every evidence bundle on the authoritative 7d gate host even when Step 7 work is correct — or forces a non-portable machine layout not documented in SPEC §7 or Permissions.  
**Fix:** Parameterize via env (e.g. `INTEGRATION_PLAN_PATH`, default relative to repo or documented monorepo root). Record the resolved path in `preflight.json`; validator reads path from bundle metadata, not a baked-in home directory. Document in Permissions Required.

### HIGH — `step3_deploy_gate` live-evidence oracle assumes repo-local artifacts PLAN-3 does not commit

**Location:** Task 1 Step 1 (L81); Global Constraints (L27–28)  
**Quote:** *"`live_evidence_path` must be exactly `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/` with non-empty `live_gate_transcript.txt` containing Task 13 markers"*  
**Why:** PLAN-3 Task 13 Step 8 writes this evidence; Step 9 commit adds `DEPLOY.md` and frontend bundles only — not the `evidence/3-ui-based-io-live/` tree. Prior steps committed sanitized markdown under `evidence/`, but Step 3’s gate transcript is operational. A fresh MBP clone at Task 10 cannot satisfy “path exists on disk” unless Step 3 evidence is committed (secret-scanned) or copied — neither is required in PLAN-7 or PLAN-3 Task 13.  
**Fix:** Add an explicit Step 3→7 contract: Step 3 Task 13 must commit secret-scanned `live_gate_transcript.txt` (+ optional index) on the integration branch, **or** Task 1 gate must validate Step 3 completion via tracker + handoff JSON only (drop on-disk re-read for MBP). PLAN-7 should state which.

### HIGH — G7-10 Task 6 scope omits `cc_sweep.py` host-path coupling

**Location:** Task 6 Files (L256–273); live `cc_sweep.py` L37–39  
**Quote:** Task 6 modifies `cc_config.py`, `cc_provision.py`, `cc_engine.py` only.  
**Why:** `cc_sweep.py` maps transcript paths via `paths.host_user_root` → `paths.user_root_mount`. G7-10 retires host bind sources and deprecates `DMAC_USER_ROOT` as bind source. After Task 6, sweep may read wrong paths or fail silently if `host_user_root` is empty/legacy. Catastrophic: memory sweep stops updating user-tier CLAUDE.md post-cutover (1c regression).  
**Fix:** Add `cc_sweep.py` (and any other `host_user_root` consumers found by grep) to Task 6 modify list with tests; or document that transcript paths remain mount-relative and sweep uses `user_root_mount` only.

### MEDIUM — Locked SPEC §8 omits mandatory `preflight.json`

**Location:** Task 1 Note (L83); SPEC-7 §8 (L168–182)  
**Quote:** *"preflight.json extends SPEC-7 §8 evidence contract (plan superset)"*  
**Why:** Authority hierarchy: locked design > plan. SPEC §8 lists 11 artifacts; plan adds mandatory `preflight.json` + `step3_deploy_gate`. A implementer reconciling docs may ship MBP bundles satisfying SPEC §8 but failing plan Task 2, or omit gate fields believing SPEC is complete.  
**Fix:** `/ultraplan amend` SPEC-7 §8 to add `preflight.json` schema (incl. `step3_deploy_gate`), or elevate the plan note to a Global Constraint: “validator joint contract = §8 + preflight; SPEC amend pending.”

### MEDIUM — `CCPaths.host_user_root` end state ambiguous after G7-10

**Location:** Task 6 (L261–264); live `cc_config.py` L23–30, `cc_provision.build_user_dirs` L87–90  
**Quote:** *"deprecate host-path `DMAC_USER_ROOT` as bind source (may remain as legacy alias during migration only)"*  
**Why:** `build_user_dirs` still requires non-empty `host_user_root` for `*_src` paths. Task 6 says sibling mounts use volume subpaths but does not define whether `CCPaths` keeps `host_user_root`, renames it, or derives subpaths from `DMAC_CC_USERS_VOLUME`. Cold implementer cannot finalize `cc_config` / `UserDirs` without guessing.  
**Fix:** Task 6 Step 2 must specify target types: e.g. `CCPaths` fields `{users_volume, user_root_mount}`, `UserDirs.input_subpath`, and `_build_volumes` return type (`list[docker.types.Mount]`).

---

## 2B — Stress Test

### HIGH — Task 6 Mount API not wired through `_run_kwargs` / `containers.run`

**Location:** Task 6 Step 3 (L268–269); live `cc_engine.py` `_run_kwargs` L325–346, `run_cc_turn` L504–508  
**Quote:** *"`_build_volumes` / `run_cc_turn` use `docker.types.Mount(type=\"volume\", ...)`"*  
**Why:** `_run_kwargs` accepts only `volumes: dict[str, dict[str, str]]` and passes `volumes=` to `containers.run`. Volume+subpath mounts require `mounts=[Mount(...)]`, not the bind dict. Likely failure: implementer changes `_build_volumes` return shape but `_run_kwargs` ignores it → CC turns fail at spawn. Catastrophic if tests mock docker and miss the API mismatch.  
**Fix:** Task 6 must explicitly refactor `_run_kwargs` to accept `mounts: list[Mount] | None`, update `run_cc_turn`, and extend hermetic tests to assert `containers.run` receives Mount kwargs (via spy/fixture), not only dict keys.

### HIGH — `path_mappings` / `DMAC_PATH_MAPPINGS` not addressed in volume cutover

**Location:** Task 6; live `run_cc_turn` L477–484  
**Quote:** *(Task 6 silent on path_mappings)*  
**Why:** Agent env encodes `host_root` from `dirs.output_src` / `dirs.scratch_src` (today host bind paths). After G7-10, `*_src` semantics change to volume subpaths or mount-relative paths. Without an explicit update, forced-CC turns may succeed at infra level but report wrong artifact paths to the user (Step 3/7 acceptance regressions).  
**Fix:** Task 6 Step 3 add: update `path_mappings` to use operator-meaningful paths (mount-relative or documented logical roots); extend `test_cc_engine_env.py` or volume tests.

### MEDIUM — Task 5 does not assert `nextseek` is excluded from `dmac-cc-net`

**Location:** Task 5 Success conditions (L237–238); SPEC-7 §3 (L61–69)  
**Quote:** *"Backend services (`db`, `seek`, …) are not attached to `dmac-cc-net`."*  
**Why:** Topology lists only `bedrock-proxy`, `nextseek_nginx`, and transient agents on `dmac-cc-net`. If `nextseek` is accidentally dual-homed, the de-credentialed agent gains L3 reach to Django/daphne bypassing nginx Host normalization — weakens OI-3 segmentation. Success conditions name backends but not `nextseek`.  
**Fix:** Compose-config test: `nextseek` networks must not include `dmac-cc-net`; only `nextseek_nginx` is dual-homed.

### MEDIUM — chmod 777 replacement underspecified for named volumes

**Location:** Task 6 Step 3 (L300–301); live `run_cc_turn` L449–470  
**Quote:** *"Set uid-1001-writable scratch via in-volume permissions (replace dev-only host `chmod 777` pattern)"*  
**Why:** `run_cc_turn` still chmods 777 on scratch/cc-state via mount paths. Named-volume ownership defaults may differ from host bind; uid 1001 agent may fail to write scratch without an explicit strategy (init container, root-owned mkdir in Django, or documented volume `chown`).  
**Fix:** Task 6 Step 3 name the chosen permission model and add a live/Task 9 sentinel write check under `scratch/`.

### MEDIUM — Dev migration (Task 6 Step 4) optional without default

**Location:** Task 6 Step 4 (L303–307); Risk Register #3 (L532)  
**Quote:** *"copy … **or** wipe and recreate"*  
**Why:** Silent default-to-wipe loses 1b/1c transcripts on dev cutover. Risk register notes it but no “pause-and-ask” trigger in Task 6 success conditions.  
**Fix:** Require explicit `migration_policy: copy|wipe` in evidence `meta.json` with sign-off reference; validator fails if absent on dev-VM bundles.

---

## 2C — Validate External Dependencies

### MEDIUM — Docker Engine volume-subpath version floor not pinned

**Location:** Task 6 Step 3 (L268–269); Permissions (L510)  
**Quote:** *"`docker.types.Mount(type=\"volume\", …, volume_options={\"subpath\": ...})`"*  
**Why:** Volume subpath mounts require sufficiently new Docker Engine/API support. Plan cites docker-py API but not minimum Engine version for MBP/dev VM. Older Docker Desktop could fail at runtime after hermetic tests pass.  
**Fix:** Task 1 preflight record `docker version` / `docker info`; Task 6 document minimum Engine version; Task 10 success include version in `meta.json`.

### MEDIUM — `docker-py` Mount surface must match pinned version

**Location:** Task 6; repo `dmac_assistant/pyproject.toml` (`docker>=7.1.0`), `cc_engine` runtime import  
**Why:** 2C requires verifying API claims. Root image dependency path for `docker` is indirect; Task 6 assumes `Mount` + `volume_options['subpath']` without a hermetic import/contract test or pin in NExtSEEK’s deploy deps.  
**Fix:** Task 6 Step 1 add hermetic test importing `docker.types.Mount` with subpath kwargs; pin `docker>=7.1.0` in the image deps Task 3 ports if not already.

### OK — Compose v2 multi-network, `./startup.sh install` + seven external volumes, `uv` gate

Verified: `startup.sh` exits without `uv`; `volumes.ensure_volumes` creates prefixed names idempotently; plan Tasks 8/10/Permissions document bootstrap. Dependency table (L547–554) accurate for these items.

### OK — Standalone `dmac-assistant` port source

Correctly marked must-verify at Task 1 preflight / Task 3 port time.

---

## 2D — Gameproof

### MEDIUM — Task 10 “greenfield” gameable without pre-bootstrap volume/network snapshots

**Location:** Gameability Audit (L568); Task 10 Success conditions (L461–470)  
**Quote:** Gameability: *"Record `docker volume ls` before bootstrap in evidence"* — not in Task 10 success conditions.  
**Why:** Cheapest fake: MBP with pre-existing `dmac-cc-users` / SEEK volumes from an earlier `./startup.sh install`; skip true greenfield; still pass compose up + forced-CC. Violates G7-7 intent.  
**Fix:** Task 10 Step 0 require `pre_bootstrap_docker_volume_ls.txt` and `pre_bootstrap_docker_network_ls.txt` in bundle; validator asserts `dmac-cc-users` absent (or documents signed reuse).

### MEDIUM — `user_signoff_handoff_path` content not constrained

**Location:** Task 1 Step 1 (L81)  
**Quote:** *"`user_signoff_handoff_path` pointing at a `/handoff` report JSON"*  
**Why:** Cheapest fake: any JSON file on disk with valid syntax. No required fields (step id, verification status, evidence pointers). Mutation: empty handoff passes if path exists.  
**Fix:** Validator parses handoff JSON for required SRS fields referencing step 3 + live evidence paths + `verification_status`; reject if step ≠ 3 or status dishonest.

### MEDIUM — `step3_deploy_gate` transcript markers checkable but PLAN-3 commit gap (see 2A)

Cross-reference 2A HIGH: even valid marker checks fail on fresh clone if evidence never landed in repo.

### OK — Strong oracles present

Subprocess `docker compose config` (Task 5); Markdown-only rejection (Task 2); secret-scan negative controls (Task 2 Step 3); `bedrock-proxy` `container_name` pin (Task 5 Step 2); DEPLOY numbered-procedure parser forbidding `/srv/dmac` host prep (Task 8); Task 9 labeled non-gating vs Task 10 authoritative (Tasks 9–11).

---

## Non-blocking cosmetic notes

- `startup/steps/volumes.py` module docstring still says “six named volumes”; Task 6 should update to seven.
- `cc_runner_available()` error text still cites `dmac's make image-build` — update during Task 3/5 doc pass.
- `integration-plan.json` step 7b description still lists `DMAC_USER_ROOT`; tracker mutation is Task 11 only — harmless drift until then.
- Step 7 validator does not replicate legacy `validate_cc_acceptance` BAML router check — acceptable infra-scope narrowing (SPEC §8 does not require it).
- Phase 2 Vetting Log table references prior `.vetting/` files — meta; not execution-blocking.

---

## Summary counts

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 4 |
| MEDIUM | 11 |
| LOW | 0 (cosmetic listed separately) |

**Top findings (one line each):**

1. **CRITICAL:** Validator hardcodes `/home/taishajo/work/state/integration-plan.json` — breaks MBP/portable 7d gate.
2. **HIGH:** Step 3 live evidence path required on disk but PLAN-3 does not commit it — fresh clone fails gate.
3. **HIGH:** Task 6 volume Mount API incompatible with current `_run_kwargs` bind-dict contract.
4. **HIGH:** `path_mappings` / agent artifact path reporting not specified for G7-10 cutover.
5. **HIGH:** `cc_sweep.py` host-path logic omitted from Task 6 scope — 1c memory sweep risk.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
