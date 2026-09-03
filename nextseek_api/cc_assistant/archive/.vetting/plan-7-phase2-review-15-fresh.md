# TARGET: PLAN-7-compose-native-prod-deploy.md (Phase 2 iter-15 fresh cold-context review)

## 2A — Vet

### CRITICAL — `docker>=7.1.0` is insufficient for the locked `Mount(..., subpath=)` API
**Location:** Task 6 Step 2 / Step 1 hermetic import test — *"`Mount(type=\"volume\", source=volume_name, target=container_path, subpath=\"proj/user/scratch\")`; **not** `volume_options={\"subpath\": ...}`)"* and *"Add `docker>=7.1.0` to root `pyproject.toml`"*
**Why:** The repo lockfile pins `docker==7.1.0` (transitive via `dmac_assistant`). Verified against docker-py 7.1.0 source: `docker.types.Mount.__init__` has no `subpath` parameter and never sets `VolumeOptions.Subpath`. Subpath support was merged to docker-py main in PR #3270 (2025-06-11), after the 7.1.0 release; it is not documented in the 7.1.0 changelog. A cold implementer following the plan will fail Task 6 hermetic Mount construction and live sibling spawns on the pinned version.
**Fix:** Pin an explicit minimum docker-py version that ships subpath (must-verify at execution: first PyPI release containing PR #3270, likely `>=7.2.0` or document interim raw-dict mount with `VolumeOptions.Subpath` if no release exists). Add Task 6 Step 1 assertion that `inspect.signature(Mount.__init__).parameters` includes `subpath` (or equivalent) in the test venv. Bump root `pyproject.toml` and `dmac_assistant` dep together; regenerate lockfile.

### HIGH — Grep guards omit `services/cc_assistant.py` despite it being in the atomic cutover scope
**Location:** Task 6 Step 1 — *"grep guard forbids `host_user_root` replace in `cc_assistant.py`"*; Step 4 — *"Grep guards: no `host_user_root` in `cc_assistant.py` / `cc_sweep.py`"* vs File Structure / Step 2 — *"Modify: `nextseek_api/services/cc_assistant.py` … retire `host_user_root` translation"*
**Why:** Live code uses `host_user_root` heavily in `services/cc_assistant.py` (`_session_metas`, memory staging). Plan requires refactoring that file but only grep-guards `cc_assistant.py` (wrong module path) and `cc_sweep.py`. Implementer can pass spawn-path mocks while leaving Django-side 1c sync/sweep on host translation — silent post-cutover failure.
**Fix:** Extend grep guards and hermetic tests to `nextseek_api/services/cc_assistant.py`; add Task 6 success condition that `_session_metas` returns mount-relative `transcript_path` with no `host_user_root.replace` anywhere in services layer.

### HIGH — Preflight `deploy_commit` can go stale relative to the authoritative evidence bundle
**Location:** Task 1 (early preflight collection) vs Tasks 9–10 (evidence generation); Task 2 cross-artifact correlation — *"`run_id` in proxy log; agent container in network inspect"* (no commit correlation)
**Why:** `deploy_commit` is captured at Task 1 preflight time. Tasks 9/10 never require re-running the preflight collector in the same `<run_id>` session or asserting `preflight.json.deploy_commit == meta.json` repo commit. If the branch advances between Task 1 and Task 10, transcript `git cat-file` checks target a stale SHA while the MBP bundle reflects a newer commit — false pass or false fail; implementer may also collect preflight once and never refresh.
**Fix:** Task 9 Step 2 and Task 10 Step 5 must require fresh `preflight.json` generated in the same `<run_id>` directory immediately before other §8 artifacts. Task 2 validator must fail unless `preflight.deploy_commit == meta.json.repo_commit` (define field name — see 2A-MED-1).

### HIGH — Docker/Compose subpath floor is self-attested, not independently verified
**Location:** Task 2 — *"fail when `preflight.json.docker_engine_meets_subpath_floor` is not `true` **OR** `docker_compose_meets_subpath_floor` is not `true`"*
**Why:** Validator reads boolean flags only. Task 1 also records `docker version` / `docker compose version` strings, but Task 2 does not re-parse them to enforce Engine ≥26 (API v1.45) and Compose ≥2.26. Lazy implementer sets both booleans `true` on an non-compliant MBP and passes the gate while volume subpath mounts fail at runtime.
**Fix:** Task 2 validator must parse the version summary captured in `preflight.json` (or dedicated `docker_version.txt` artifact) and derive floor compliance independently; fail if parsed versions disagree with the booleans.

### MEDIUM — `meta.json` repo commit/branch field names are unspecified
**Location:** SPEC-7 §8 — *"`meta.json` - run id, host label, repo branch/commit, timestamp, verifier version, `budget_cap_usd`"*; Task 2 cross-artifact correlation references `meta.json.run_id` only
**Why:** No locked JSON keys for branch/commit (`commit`? `repo_commit`? `git_sha`?). Cross-artifact checks (`deploy_commit` equality, hash correlation) cannot be implemented deterministically; cold implementer and validator author will diverge.
**Fix:** Lock `meta.json` schema in SPEC-7 §8 and Task 2: e.g. `repo_commit`, `repo_branch`; validator asserts types and non-empty strings.

### MEDIUM — `pre_step3_snapshot_tag` is collected but never validated
**Location:** Task 1 / SPEC-7 §8 — *"`pre_step3_snapshot_tag` — git tag or annotated ref for `:pre-step3` image snapshot"*
**Why:** PLAN-3 Task 13 Step 3 requires `:pre-step3` snapshot before deploy. Field may be empty string with no validator consequence. Implementer can skip snapshot discipline while still passing Step 7 preflight gate.
**Fix:** Task 2 validator: when `live_gate_transcript_committed` and transcript text references `pre-step3`, require non-empty `pre_step3_snapshot_tag` plus `docker image inspect` line in evidence (dev bundles); MBP greenfield may record explicit `"N/A"` with reason enum.

### MEDIUM — Task 10 Step 0 instance-prefix requirement is an unresolved fork
**Location:** Task 2 / Task 10 Step 0 — *"require default instance (no `--instance` prefix) **or** document prefix-adjusted oracle in validator"*
**Why:** 7d gate does not pick one branch. Cold implementer on an MBP with `startup/.instance.json` prefix will not know whether to fail greenfield checks or adjust expected volume names. `startup/.instance.json` is not present in repo today; behavior is unspecified for creation path.
**Fix:** Lock 7d gate to default instance (no prefix) and fail validator otherwise, **or** require reading `startup/.instance.json` at evidence time with explicit expected volume name list in Task 2 (remove "or").

### MEDIUM — Step 3 live transcript gate checks existence only, not gate content
**Location:** Task 1 — *"Validator fails if path missing, file empty, or not in git at `deploy_commit`"*
**Why:** PLAN-3 Task 13 requires reload JSON excerpt, upload Celery completion, Playwright steps, etc. Step 7 only requires non-empty committed file + `git cat-file`. Cheapest fake: commit a one-line stub transcript after flipping tracker to `done`.
**Fix:** Task 2 validator must assert transcript contains required PLAN-3 markers (e.g. `migrate nextseek_api 0007`, `cc_traces` JSON excerpt substring, `celery inspect registered`, exit codes) via a small allowlist patterns file, not merely non-empty.

### MEDIUM — Task 3 success uses standalone `docker build`, not compose image target
**Location:** Task 3 Success conditions — *"`docker build` for the CC image succeeds from a clean NExtSEEK checkout"*
**Why:** Does not require `docker compose build <cc-service>` or that compose build context/path matches `docker/cc-runtime/`. Implementer can pass with direct Dockerfile build while compose still points at wrong context or external image tag.
**Fix:** Add success condition: `docker compose build <cc-image-service>` succeeds and resulting image tag matches `NEXTSEEK_CC_IMAGE` default from `docker compose config`.

### MEDIUM — Task 5 legacy validator publish scoping not migrated for G7-10 path semantics
**Location:** Task 5 Step 3 — updates `validate_cc_acceptance.py` for proxy DNS/container name only; Task 6 changes `_publish_artifacts` to `logical_root` display paths
**Why:** Live `validate_cc_acceptance.py` check 16 scopes `published_files.json` with flat `uid/` prefix heuristics. After volume cutover, published paths use `{user_root_mount}/{project}/{user}/output/...`. Partial realstack migration can leave legacy validator green while Step 7 paths diverge, or Task 10 adapter emits paths that fail legacy checks if still invoked.
**Fix:** Task 5 Step 3 must either migrate publish scoping to `logical_root` prefixes or deprecate `validate_cc_acceptance.py` for Step 7 sign-off with grep guard forbidding its use in Task 10 Step 4; Step 7 validator owns publish path checks.

### Permissions catalogue (2A completeness)

| Permission / resource | Tasks | Notes |
|----------------------|-------|-------|
| Read/write NExtSEEK repo | all | Port runtime + proxy |
| Docker Engine ≥26 + Compose plugin ≥2.26 | 6, 9–10 | Subpath mounts — **must be verified from evidence, not booleans alone** |
| docker-py with `Mount.subpath` | 6 | **Not satisfied by 7.1.0** — see CRITICAL |
| `uv` CLI | 10 Step 0 | `startup.sh` requires it |
| Docker socket | 9–10 | Stack + transient CC agents |
| Gitignored secrets (nextseek.env, db.env, local_settings.py, proxy token) | 7, 10 | Never committed |
| `INTEGRATION_PLAN_PATH` (default `../state/integration-plan.json`) | 1, 10, 11 | MBP snapshot exception bounded |
| Write monorepo `integration-plan.json` | 11 only | After MBP evidence committed |
| `./startup.sh install` (7 external volumes incl. `dmac-cc-users`) | 6, 8, 10 | Required before compose up |
| Bedrock API spend | 10 | Cap via validator |
| GitHub egress | 3 | Plugin context generation |
| Per-change sign-off | 9–10 | Global constraint |
| Step 3 deployed + committed `live_gate_transcript.txt` | 1 (gate) | Tracker step 3 currently `not_started` — expected pre-implementation |

---

## 2B — Stress Test

### Most likely failure mode
Task 6 sibling volume subpath spawns fail because docker-py 7.1.0 cannot emit `VolumeOptions.Subpath`, or subpath directories do not exist inside `dmac-cc-users` before mount (Docker requires pre-created subpaths). Matches Risk Register #2.

### Most catastrophic failure mode
Partial G7-10 cutover: compose uses named volume but `services/cc_assistant.py` / `cc_sweep.py` still translate via retired `host_user_root` — 1c memory and sweep silently stop working; forced-CC may still pass once while corrupting operator trust in persistence.

### Hidden dependencies
- docker-py release containing subpath (post-7.1.0) not named in plan.
- Root `pyproject.toml` has no direct `docker` dep; reliance on transitive pin from `dmac_assistant`.
- Subpath directories must exist in volume before sibling spawn (Engine does not auto-create).
- Step 3 not started (`integration-plan.json` step 3 `not_started`) — entire Step 7 blocked until PLAN-3 Task 13 completes (by design).

### Ambiguous success conditions
- Task 10 instance prefix fork (see 2A-MED-3).
- `meta.json` commit field naming (see 2A-MED-1).
- Task 6 live persistence sentinel listed in success conditions but only provable at Task 10 — Task 6 checkbox can close on hermetic tests alone.

### Coverage risk
Declared `--cov-fail-under=95` on `validate_step7_compose_deploy` is non-trivial and appropriate. Task 6 engine/volume modules are coverable via mocks; justified Docker/live exceptions for Tasks 3–4, 9–10 are legitimate **if** Step 7 validator is hardened (currently gaps above weaken it).

### Rollback
Pause-and-ask: MBP forced-CC failure, secret scan failure, `step3_deploy_gate` mismatch (aligned with Risk Register). Undo-and-revert: partial Task 6 cutover on dev VM — use `migration_policy` transcript + sign-off; atomic cutover tests should block merge before live gate.

---

## 2C — Validate External Dependencies

### docker-py `Mount.subpath` — **FAIL / must-verify**
Plan claims `docker>=7.1.0` with `subpath=` kwarg. Verified: **7.1.0 lacks subpath**; feature merged 2025-06-11 to main, not in 7.1.0 changelog. Lockfile pins 7.1.0. **Must bump to first release shipping subpath** or document raw API workaround.

### Docker Engine ≥26 / Compose ≥2.26 for volume subpaths — **OK (conceptually)**
Moby PR #45687 + Compose ≥2.26.0 support volume subpaths. Host has Compose v5.1.4 (exceeds floor). Subpath subdirectories must pre-exist — plan addresses via Django mkdir before spawn (Task 6 Step 4) but should note Engine will not auto-create subpath roots.

### Docker Compose dual-network / external volumes — **OK**
Standard compose-file semantics; current repo has six external volumes and host bind `/srv/dmac/users` — plan correctly targets delta.

### Standalone `dmac-assistant` port source — **must-verify at execution**
Task 1 preflight to record source commit; not verifiable in planning session.

### Step 3 gate alignment with PLAN-3 — **OK**
PLAN-3 Task 13 Step 9 requires committed `live_gate_transcript.txt`; aligns with SPEC-7 §8 / PLAN-7 Task 1. Tracker step 3 `not_started` is expected.

---

## 2D — Gameproof

### Task 1 — preflight / step3 gate
**Success condition (quoted):** *"It includes `step3_deploy_gate` with all required fields; validator fails if step 3 is not `done`."*
**Cheapest fake:** Hand-crafted `preflight.json` with `tracker_step3_status: "done"` and minimal non-empty transcript committed once; booleans for docker floor set true without version checks.
**No-op test:** Empty stub transcript + handoff JSON satisfies supplementary handoff parse if content patterns not enforced.
**Mutation test:** Removing real Step 3 deploy from history may not fail if transcript stub remains at `deploy_commit`.
**Remedy:** Content-pattern transcript validation; independent version parsing; `deploy_commit == meta.repo_commit` (HIGH).

### Task 2 — validator on synthetic bundles
**Success condition (quoted):** *"Hermetic tests pass with no Docker, DB, network, or spend."*
**Cheapest fake:** Synthetic JSON files matching artifact names with no cross-run integrity; self-attested floor booleans.
**Remedy:** See 2A HIGH/MED fixes; require agent container label `nextseek.cc.run == forced_cc_result.run_id` (partially listed — keep and enforce).

### Task 6 — atomic cutover
**Success condition (quoted):** *"**Atomic cutover gate:** hermetic spawn-path test passes (no `host_user_root` in `run_cc_turn` → `containers.run`)."*
**Cheapest fake:** Refactor engine only; leave `services/cc_assistant.py` on host paths; pass grep scoped to wrong files.
**Remedy:** Extend grep to services module; integration test for `_session_metas` + sweep read path (HIGH).

### Task 10 — MBP greenfield forced-CC
**Success condition (quoted):** *"Forced CC turn completes with sentinel in reply."* + validator pass
**Cheapest fake:** Reuse pre-existing `dmac-cc-users` volume with `greenfield_exception` + minimal handoff JSON; docker exec engine call that still hits proxy but skips full compose-native deploy path.
**Remedy:** Timestamp-order check: `pre_bootstrap_*` before `meta.timestamp`; handoff must cite user sign-off id; fail if seven volumes present without exception on MBP pattern host_label.

### Task 10 — cost cap
**Success condition (quoted):** *"`cost > 0` for Opus forced turn unless documented zero-cost exception"*
**Cheapest fake:** Record `cost: 0.0001` with vague exception string.
**Remedy:** Define allowed zero-cost exception enum in validator; default fail if `cost <= 0` for Opus model id in `forced_cc_result.json`.

### Ranked gameability (easiest / most intent lost)
1. Self-attested docker floor booleans (HIGH impact, trivial fake)
2. Stub `live_gate_transcript.txt` (HIGH — bypasses Step 3 live gate intent)
3. Partial Task 6 cutover via narrow grep guards (HIGH — 1c silently broken)
4. Stale or mismatched `deploy_commit` (MEDIUM)
5. Greenfield exception without volume pre-bootstrap ordering proof (MEDIUM)

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log table numbering skips rows 26/28 (formatting only).
- `integration-plan.json` step 7b description still mentions `DMAC_USER_ROOT`; plan correctly retires it in Tasks 6–7 — tracker text harmonization is Task 11/out-of-plan scope.
- Task 6 Step 2 duplicates `CCPaths` end-state prose in Files block and Step 3 (redundant, not blocking).
- SPEC-3 E8 `/srv/dmac/users` neutral default is superseded by G7-10 at Step 7 implementation — plan Global Constraints acknowledge re-grounding; no PLAN-3 change proposed here.
