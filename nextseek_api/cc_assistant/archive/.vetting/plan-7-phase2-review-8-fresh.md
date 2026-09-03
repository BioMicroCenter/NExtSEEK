# PLAN-7 Phase 2 Review — iter 8 (fresh, cold context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `nextseek_api/cc_assistant/archive/SPEC-7-compose-native-prod-deploy.md` (§8 preflight amended 2026-06-30)  
**Reviewer:** Independent adversarial pre-execution (lenses 2A–2D)  
**Date:** 2026-06-30  
**Repo spot-checks (read-only):** Root `docker-compose.yml` still host-binds `/srv/dmac/users`; six `external: true` volumes only; no root `bedrock-proxy` / `dmac-cc-net`; `startup/steps/volumes.py` lists six volumes; `cc_engine._build_volumes` / `_run_kwargs` use host-path bind dicts; `cc_assistant.py` and `cc_sweep.py` translate via `host_user_root`; `test_cc_engine_memory_mounts.py` asserts single-file bind to `/home/user/.claude/CLAUDE.md`; root `pyproject.toml` has no explicit `docker` pin (transitive `docker==7.1.0` in `uv.lock`); `cc_runner_available()` still cites standalone `make image-build`; tracker step 3 `not_started` (expected pre-implementation).

---

## 2A — Vet (correctness, completeness, spec alignment, permissions)

### CRITICAL — Task 6 volume-subpath plan conflicts with 1c nested single-file memory mount

**Location:** Task 6 Step 3 — *"Sibling CC containers mount **subpaths of `dmac-cc-users`** to: … **`/home/user/.claude/CLAUDE.md`** (`user_memory_file`)"*

**Why:** Live `test_cc_engine_memory_mounts.py` and `_build_volumes` rely on a **host file bind** of one file onto a nested path inside an existing RW session mount (`cc_state` → `/home/user/.claude`, then `user_memory_file` → `/home/user/.claude/CLAUDE.md`). Docker’s volume-subpath feature mounts a **subdirectory** of a named volume (Engine ≥26 / API 1.45); it does not document file-target overlays equivalent to the current MERGE pattern (`evidence/1c-claude-md-merge-probe.md`). Task 6 instructs converting all mounts to `list[docker.types.Mount]` with `volume_options={"subpath": ...}` but gives no alternate design for simultaneous session-store RW + RO memory-file overlay. A literal implementer breaks every non-fresh 1c turn at sibling spawn or silently drops memory.

**Fix:** Task 6 Step 3 must specify the preserved mount topology explicitly (e.g., subpath directory mounts + documented file-overlay strategy, or an amended mount target layout with hermetic tests mirroring `test_cc_engine_memory_mounts.py`). Do not leave `user_memory_file` as an implicit port of host file binds.

---

### HIGH — `docker_engine_meets_subpath_floor` recorded but never enforced

**Location:** Task 1 Step 1 — *"`docker_engine_meets_subpath_floor` (bool)"*; Task 2 Step 2 — validator checklist (no mention)

**Why:** Volume subpaths require Docker Engine ≥26.0 / API v1.45 (docker-py 7.1.0 release notes). Task 1 collects the bool from `docker version` / `docker info`, but Task 2 never requires the validator to **fail** when it is `false`. MBP greenfield (G7-7) can pass hermetic tests yet fail at runtime on older Docker Desktop with API errors unrelated to compose wiring — false gate failure with no deterministic preflight block.

**Fix:** Task 2 Step 2 must fail bundles when `preflight.json.docker_engine_meets_subpath_floor` is not `true` (or equivalent Engine/API semver check). Task 6 / Task 8 must pin the minimum Engine/API version in DEPLOY.md.

---

### HIGH — Greenfield network anti-cheat captured but not validated

**Location:** Task 10 Step 0 — *"capture `pre_bootstrap_docker_network_ls.txt`"*; Task 2 Step 2 — only `pre_bootstrap_docker_volume_ls.txt` listed

**Why:** Task 10 records network state before bootstrap, but Task 2 validator never parses `pre_bootstrap_docker_network_ls.txt`. A host with pre-existing manually created `dmac-cc-net` (today’s DEPLOY.md Phase A pattern) satisfies volume-absence checks while violating G7-7 greenfield intent. Gameability row Task 6 covers volumes only.

**Fix:** Task 2 Step 2 must parse `pre_bootstrap_docker_network_ls.txt` and fail when `dmac-cc-net` (or compose-managed CC network name) exists unless `meta.json.greenfield_exception` + signed handoff ref — symmetric with volume check.

---

### HIGH — Locked SPEC §8 schema lags plan-mandatory preflight / artifact fields

**Location:** SPEC §8 `step3_deploy_gate` vs Task 1 L84–89; SPEC §8 required artifacts vs Task 10 L446–447

**Why:** Plan requires `canonical_integration_plan_sha256`, `docker_engine_meets_subpath_floor`, MBP snapshot exception rules, `pre_bootstrap_docker_volume_ls.txt`, and `pre_bootstrap_docker_network_ls.txt`. Locked SPEC §8 lists neither the extra preflight fields nor the pre-bootstrap artifacts. Authority is locked design > plan — a cold implementer tracing only SPEC §8 under-generates the bundle; a validator built from the plan rejects SPEC-conformant bundles.

**Fix:** `/ultraplan amend` SPEC §8 to add the iter-7/8 fields and artifacts, or narrow the plan validator to SPEC §8 only until amended. Do not leave divergent contracts.

---

### MEDIUM — Greenfield volume check lacks canonical six-volume enumeration

**Location:** Task 2 Step 2 — *"fail if `dmac-cc-users` or required SEEK volumes exist"*

**Why:** The six SEEK external volumes are fixed in `startup/steps/volumes.py` (`seek-filestore`, `seek-mysql-db`, `seek-solr-data`, `seek-cache`, `nextseek-static-files`, `neo4j-data`) but Task 2 never lists them. Validator implementers may check only `dmac-cc-users`, leaving partial pre-seeded stack state undetected.

**Fix:** Task 2 must enumerate the seven external volume names (six SEEK + `dmac-cc-users`) in validator scope and Gameability Audit Task 10 row.

---

### MEDIUM — `cc_runner_available()` error text still references standalone repo build

**Location:** `cc_engine.py` L132–134; no plan task updates strings after compose-native cutover

**Quote (code):** *"build it via dmac's \`make image-build\`, or set NEXTSEEK_CC_IMAGE"*

**Why:** After Task 3/5, the authoritative path is `docker compose build`. Stale messages send operators and Task 9/10 debuggers back to the retired standalone workflow — execution snag and false-negative triage on G7-7.

**Fix:** Add Task 5 or Task 8 sub-step: update `cc_runner_available()` detail strings to cite compose build + `NEXTSEEK_CC_IMAGE`; hermetic test asserts no standalone-repo reference.

---

### MEDIUM — `pre_step3_snapshot_tag` required but undefined

**Location:** Task 1 L89; SPEC §8 `step3_deploy_gate`

**Why:** Field is mandatory in preflight and SPEC §8 but neither document defines collection semantics (git tag name? commit of pre-Step-3 tree? annotated object?). Collector and validator cannot agree on pass/fail.

**Fix:** Define `pre_step3_snapshot_tag` in Task 1 (e.g., annotated tag recorded in handoff, or `null` when absent with validator rule) and mirror in SPEC §8.

---

### MEDIUM — Preflight `deploy_commit` freshness at live gates unspecified

**Location:** Task 1 L86 — *"`deploy_commit`: full SHA of `HEAD` at preflight collection"*; Task 9/10 evidence generation

**Why:** Task 1 is an early implementation task; Task 10 generates the authoritative bundle later. Plan does not require **re-running** the preflight collector immediately before Task 9/10 evidence capture. If the branch advances between collection and MBP gate, `deploy_commit` can disagree with `meta.json` commit while transcript checks target a stale SHA — false pass or false fail.

**Fix:** Task 9 Step 2 and Task 10 Step 5 must require preflight re-collection in the same run as other §8 artifacts, with `deploy_commit` == `meta.json.commit`.

---

### MEDIUM — `docker>=7.1.0` not pinned in NExtSEEK deploy dependencies

**Location:** Task 6 Step 1 — *"docker-py ≥7.1.0"*; root `pyproject.toml` (no `docker` entry)

**Why:** `Mount` + `volume_options.subpath` is a docker-py 7.1 feature. Dependency exists transitively in `uv.lock` today but Task 3 runtime port and Task 6 refactor can change the dependency graph. Without an explicit pin in the image/deploy deps Task 3 ports, a resolver downgrade breaks sibling spawn after hermetic import tests pass.

**Fix:** Task 3 or Task 6 must add `docker>=7.1.0` to the NExtSEEK deploy dependency set (root `pyproject.toml` or ported runtime lockfile) with hermetic import test.

---

## 2B — Stress Test

### HIGH — (Same as 2A CRITICAL) 1c memory mount cutover is the highest catastrophic regression risk

**Likely failure:** Task 6 refactors input/scratch/cc-state subpaths only; memory file overlay dropped → non-fresh turns lose 1c guidance.  
**Catastrophic failure:** Wrong subpath interpolation leaks another user’s volume subtree (called out in Risk Register rank 2; still depends on unspecified `UserDirs.*_subpath` derivation).  
**Rollback:** Pause-and-ask on failed memory-mount hermetic tests before MBP spend gate.

---

### MEDIUM — MBP snapshot canonical SHA sourcing underspecified

**Location:** Task 10 Step 1 — *"`canonical_integration_plan_sha256` from monorepo handoff at gate time"*

**Why:** MBP clone may not have `../state/integration-plan.json`. Plan relies on handoff JSON but does not name the SRS field path or a validator rule when handoff is missing/stale relative to dev tracker update in Task 11. Stress: Task 10 passes with self-consistent snapshot; Task 11 updates real tracker later — no cross-check.

**Fix:** Task 2 validator must require `canonical_integration_plan_sha256` on all MBP bundles and fail when snapshot SHA ≠ canonical; document handoff field name in Task 1.

---

### MEDIUM — `chmod 0777` permission model unproven for named-volume uid-1001 agent

**Location:** Task 6 Step 3 — *"Django (root in nextseek) mkdir + `chmod 0777`"*

**Why:** Risk Register rank 2 covers subpath wrong; permission failures on named volumes (root-squash, different volume driver behavior on Docker Desktop) cause agent scratch write failures after compose-up succeeds. Task 6 mentions sentinel write in success conditions but Task 10 is the first live proof — late discovery on paid gate.

**Fix:** Task 9 dev smoke must include sentinel scratch write (not only `cc_runner_available()`), or Task 6 adds hermetic permission-probe fixture documenting expected uid/gid behavior.

---

## 2C — Validate External Dependencies

### MEDIUM — Docker Engine/API floor for volume subpath not in Dependency Validation table

**Location:** `## Dependency Validation` vs Task 6 subpath mounts

**Why:** Table marks compose networks and external volumes OK but omits Engine ≥26 / API v1.45 for `volume-subpath`. Web-verified via docker-py PR #3270 and Docker Engine 26.0 release notes. MBP Docker Desktop version drift is a must-verify external dependency.

**Fix:** Add Dependency Validation row: volume subpath → Engine ≥26.0 / API v1.45; tie to `docker_engine_meets_subpath_floor` enforcement.

---

### LOW — `cc_runner_available()` does not check proxy health (acknowledged)

**Location:** Dependency Validation — *"Checks Docker + image + network only (not proxy health)"*

**Why:** Acceptable given Task 2 proxy invoke checks; retained as LOW reminder that `cc_runner_available()==(True,"ok")` alone is insufficient for forced-CC — plan already compensates in validator scope.

---

## 2D — Gameproof

### MEDIUM — Pre-bootstrap network capture is a dead artifact without validator oracle

**Success condition (Task 10):** *"MBP evidence proves the host had no prior required Container-CC state."*

**Cheapest fake:** Run on dev VM with existing `dmac-cc-net`, wipe only volumes, capture `pre_bootstrap_docker_volume_ls.txt` post-wipe, omit network file from validator.  
**No-op test:** Yes — network file presence alone satisfies Task 10 Step 0 checkbox without Task 2 parse.  
**Remedy:** Wire Task 2 network parse (see 2A HIGH).

---

### MEDIUM — `docker_engine_meets_subpath_floor` gameable as cosmetic bool

**Success condition (Task 1):** preflight records bool from docker info.

**Cheapest fake:** Collector sets `true` without semver check; validator never reads field.  
**Mutation test:** Lowering Engine version would not fail validator.  
**Remedy:** Task 2 enforced semver gate (see 2A HIGH).

---

### MEDIUM — `deploy_commit` / bundle commit divergence

**Success condition (Task 1 / SPEC §8):** transcript exists at `deploy_commit`.

**Cheapest fake:** Collect preflight on commit A; generate remaining evidence after commit B adds unrelated files; transcript still at A while bundle claims B.  
**Remedy:** Require `deploy_commit == meta.json.commit` in Task 2 (see 2A MEDIUM).

---

### LOW — Doc grep guard vs numbered-procedure parse

**Location:** Task 8 Step 1 — validator parses numbered procedure

**Why:** Gameability row Task 7 covers grep bypass via "Historical" section; Task 8 already requires numbered-procedure parse — adequate if implemented. LOW residual: validator must use same section boundaries as grep tests.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log row 16 still says "pending fresh re-vet" after iter-7 hardening — update after this review.
- Global Constraints coverage command targets `tests.validate_step7_compose_deploy` module path under `tests/` — unconventional but consistent with file placement.
- `cc_config.py` laptop default (`/Users/taishajoseph/...`) remains pre-Step-3/7; PLAN-3 Task 8 and Step 7 Task 7 cover harmonization at execution time.

---

## Severity summary

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 4 |
| MEDIUM | 10 |
| LOW | 2 |

---

## Top findings (one line each)

1. **CRITICAL:** Task 6 mandates volume subpaths to `/home/user/.claude/CLAUDE.md` without a design that preserves 1c’s nested single-file RO overlay over session `.claude` RW — likely unsatisfiable as written.
2. **HIGH:** `docker_engine_meets_subpath_floor` is collected in preflight but not enforced by Task 2 validator (Engine ≥26 / API v1.45 required for subpaths).
3. **HIGH:** `pre_bootstrap_docker_network_ls.txt` is captured in Task 10 but not validated in Task 2 — greenfield G7-7 gameable via pre-existing `dmac-cc-net`.
4. **HIGH:** Plan-mandatory preflight fields and pre-bootstrap artifacts exceed locked SPEC §8 — contract drift blocks traceable implementation.
5. **MEDIUM:** `deploy_commit` vs bundle commit freshness, undefined `pre_step3_snapshot_tag`, and stale `cc_runner_available()` messages remain execution snags.

---

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
