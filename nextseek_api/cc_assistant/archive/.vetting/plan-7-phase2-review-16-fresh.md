# PLAN-7 Phase 2 Review — iter 16 (fresh, canonical prompt)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md`  
**Reviewer:** Independent cold-context adversarial (2026-06-30)  
**Sibling context consulted:** PLAN-3 Task 13 gate + shared-file overlap only; SPEC-3 E8/`input_mnt`/`DEPLOY.md` — PLAN-3 not re-vetted.

---

## 2A — Vet

### CRITICAL — `docker>=7.2.0` pin is unsatisfiable on PyPI
**Location:** Task 6 Step 2 — *"Pin **`docker>=7.2.0`** (or verified minimum with subpath); regenerate lockfile"*
**Why:** PyPI latest release is **7.1.0** (verified 2026-06-30). Subpath support merged to docker-py `main` in PR #3270 (2025-06-11) but **not shipped** in any public release. `uv lock` / `pip install` with `docker>=7.2.0` fails before Task 6 can start. The plan's "fallback: raw mount dict" is secondary prose; the primary pin blocks execution.
**Fix:** Make the **primary** path explicit: pin `docker>=7.1.0` (or unpinned) and require `_build_volumes` to emit subpath via raw `Mount`/`volume_options` dict until a release containing #3270 exists; add a hermetic test that asserts the serialized mount payload includes `Subpath`/`subpath` without importing a nonexistent 7.2 API. Record must-verify release tag at execution; do not pin a fictional version.

### HIGH — 1c merged `CLAUDE.md` staging path contradicts cc-state → `.claude` mount mapping
**Location:** Task 6 Step 4 (1c memory) — *"**Copy bytes** to `Path(dirs.cc_state_mnt) / ".claude" / "CLAUDE.md"`"* and *"cc-state (RW → `/home/user/.claude`)"*
**Why:** When `{project}/{user}/cc-state/{session}` subpath mounts to `/home/user/.claude`, the in-container file is `{cc_state_mnt}/CLAUDE.md`, **not** `{cc_state_mnt}/.claude/CLAUDE.md`. Staging to the nested `.claude/` directory places bytes at `/home/user/.claude/.claude/CLAUDE.md` in-container — breaking the MERGE semantics the nested RO file bind preserved today (`test_user_memory_mounted_ro_nested_over_session_claude`). A careful implementer following the plan verbatim ships broken 1c memory after cutover while hermetic mount-list tests can still pass.
**Fix:** Stage merged memory to `Path(dirs.cc_state_mnt) / "CLAUDE.md"` (session store root = container `.claude` root). Add hermetic assertion that staged bytes appear at the mount target path equivalent to `/home/user/.claude/CLAUDE.md`, not a nested `.claude/.claude` path.

### HIGH — Task 6 test/modify inventory omits Step 3 upload-path regressions
**Location:** Task 6 Files / Step 1 — lists `test_cc_provision_paths.py`, `test_cc_engine_*` but not PLAN-3 artifacts `test_cc_provision_input_mnt.py`, `cc_upload_tasks.py`, `test_cc_upload_validate.py`, `test_cc_upload_list.py`
**Why:** Step 3 (gate) adds `UserDirs.input_mnt` and upload tasks writing via `dirs.input_mnt` under `cc_config.host_user_root`/`user_root_mount`. Task 6 retires `host_user_root` and refactors `build_user_dirs`/`CCPaths`. Without explicit failing tests + updates for upload list/task modules, implementer can pass spawn-path mocks while uploads land on wrong paths or break after cutover — Step 7 forced-CC gate does not exercise UI upload.
**Fix:** Extend Task 6 Step 1/4: hermetic tests that `cc_upload_tasks` / `list_input_files` resolve paths via volume-mount `*_mnt` only; update `test_cc_provision_input_mnt.py` for post-G7-10 `CCPaths`; grep-guard `host_user_root` in `cc_upload_tasks.py` and upload list module.

### HIGH — Task 5 legacy acceptance migration silent on `validate_cc_acceptance` publish scope
**Location:** Task 5 Step 3 — *"Update or deprecate … `validate_cc_acceptance.py`"* (proxy DNS + container name only)
**Why:** Live `validate_cc_acceptance.py:121-130` scopes `published_files.json` to flat `{user_id}/` prefixes. Task 6 locks `path_mappings` / `_publish_artifacts` to **`logical_root`** under `{user_root_mount}/{project}/{user}/output`. Task 5 Step 3 does not require updating the copier-scope check or `test_cc_realstack.py`'s `artifacts_published` field (Step 3 hybrid split replaces this with structured `artifacts` anyway). Post-Step-3/7, realstack/validator can fail or pass vacuously on wrong path shape while compose-native gate claims green.
**Fix:** Task 5 Step 3: explicitly migrate `validate_cc_acceptance` published-scope oracle to nested `{project}/{user}/` or `logical_root` prefix; align `test_cc_realstack` artifact capture with SPEC-7 §8 names where reused; add hermetic negative control for pre-nested path patterns.

### MEDIUM — Task 9 smoke success conditions omit forced-CC artifacts the validator requires
**Location:** Task 9 Success conditions vs Task 2 validator scope — Task 9 lists `cc_runner_available`, network evidence, validator pass; Task 2 requires `forced_cc_result.json`, `proxy_log_window.txt`, `agent_env_scan.txt`, etc.
**Why:** No `host_label` branch exempts dev-vm bundles from forced-CC artifacts. Implementer can interpret Task 9 as compose-up-only smoke, run validator, fail, or skip validator and commit incomplete bundle. Ambiguous contract for non-gating smoke vs validator completeness.
**Fix:** Either (a) state Task 9 must run the same forced-CC + §8 artifact set as Task 10 (with `host_label=dev-vm` + `migration_policy`), or (b) document explicit validator branch for dev-vm smoke bundles that omits paid artifacts — not both implicitly.

### MEDIUM — `deploy_commit` required by plan but absent from locked SPEC-8 preflight schema
**Location:** Task 1/2 — *"`preflight.deploy_commit == meta.json.repo_commit`"*; SPEC-7 §8 preflight bullets list gate fields but **no `deploy_commit`**
**Why:** Authority drift — implementer following SPEC alone omits `deploy_commit`; validator/plan disagree with locked design. Cross-artifact correlation gate is unenforceable from spec of record.
**Fix:** Amend SPEC-7 §8 preflight schema with `deploy_commit` (full SHA; validator re-checks live transcript at this SHA). Mirror in Task 1 collector field list.

### MEDIUM — `_run_kwargs` still emits `volumes=` dict; refactor not gated as its own substep
**Location:** Task 6 Step 4 item 9 — *"`_build_volumes` returns `list[docker.types.Mount]` … `_run_kwargs` passes `mounts=`"* vs current `cc_engine._run_kwargs` (`:342-346`) returning `"volumes": volumes`
**Why:** Atomic-cutover test targets `containers.run` kwargs but plan does not require a failing test on `_run_kwargs` itself. Implementer could patch `containers.run` call site while leaving `_run_kwargs` on legacy `volumes` key — partial migration that passes a narrow mock spy.
**Fix:** Add explicit Task 6 Step 1 failing test: `_run_kwargs(...)["mounts"]` present and `"volumes"` absent; grep guard forbids `"volumes": volumes` in spawn path after cutover.

### LOW — Risk Register row 3 cites "Step 4 migration" but migration is Task 6 Step 5
**Location:** Risk Register rank 3 — *"Step 4 migration transcript"*
**Why:** Off-by-one task reference may send implementer to wrong step during dev cutover.
**Fix:** Retarget to Task 6 Step 5.

---

## 2B — Stress Test

| Lens | Assessment |
|------|------------|
| **Most likely failure** | Task 6 blocked or broken at dependency install because `docker>=7.2.0` does not exist; fallback raw-dict path not wired as primary. |
| **Most catastrophic** | Volume subpath mis-spec (wrong subpath segment or CLAUDE.md staging path) → cross-session clobber or silent 1c memory loss on shared `dmac-cc-users` volume; hard to detect without two-user live gate. |
| **Hidden dependencies** | Unreleased docker-py subpath API; Step 3 `input_mnt`/upload modules on shared `cc_provision.py`; `./startup.sh install` requires `uv` on MBP (Permissions table — OK). |
| **Ambiguous success** | Task 9 vs Task 10 artifact completeness; dev-vm `migration_policy` trigger (`had_host_bind_data` vs preflight hash) underspecified for clean dev VM that never had host bind. |
| **Coverage risk** | Validator under `tests/` with `--cov=…tests.validate_step7…` is workable but unconventional; justified exceptions for Tasks 3–4, 9–10 OK **if** MBP forced-CC gate remains non-deferrable (G7-8). |
| **Rollback** | `step3_deploy_gate` + pause-and-ask on MBP failure — adequate. Partial Task 6 cutover without upload-path tests is the gap. |

---

## 2C — Validate External Dependencies

| Dependency | Verdict | Notes |
|------------|---------|-------|
| **docker-py subpath (`Mount.subpath`)** | **FAIL — must-verify** | Merged to main 2025-06-11; **no PyPI release >7.1.0**. Plan pin `>=7.2.0` is invalid. Raw API + Engine ≥26 / API v1.45 is the viable path today. |
| **Docker Engine ≥26 + Compose ≥2.26** | OK (must-verify at execution) | Plan + validator independent version parse — sound if implementer records real `docker version` strings. |
| **Compose external volumes (7)** | OK | `startup/steps/volumes.py` currently six volumes; plan adds `dmac-cc-users` + test update — aligned with repo. |
| **Root compose `dmac-cc-net`** | OK | `cc_engine.DEFAULT_NETWORK` already defaults to `dmac-cc-net`; compose does not own it yet — Task 5 delta correct. |
| **`dmac_assistant` path dep `docker>=7.1.0`** | OK / bump needed | Lockfile pins 7.1.0; plan requires coordinated bump — blocked until release strategy fixed (see CRITICAL). |
| **Standalone `dmac-assistant` port source** | Must-verify at execution | Task 1 preflight records source commit — OK. |
| **PLAN-3 gate (`live_gate_transcript.txt`)** | OK | PLAN-3 Task 13 Step 9 commits transcript; SPEC-8/plan aligned; handoff-only rejected. |

---

## 2D — Gameproof

### Task 6 — Atomic cutover / 1c memory
**Success condition (quoted):** *"**Atomic cutover gate:** hermetic spawn-path test passes (no `host_user_root` in `run_cc_turn` → `containers.run`)."* and *"**1c memory after cutover:** merged CLAUDE.md present under cc-state `.claude/` before spawn"*

**Cheapest fake:** Mock `containers.run` to assert `mounts=` list; stage CLAUDE.md to wrong nested path; skip `services/cc_assistant.py` host→mount translation removal.

**No-op oracle:** Empty `_build_volumes` returning `[]` with mock still called — passes if spy only checks call, not mount count/subpaths.

**Mutation oracle:** Corrupt subpath segment (`project/user` swapped) — no test proves isolation across users on shared volume without two-user hermetic or live gate.

**Remedy:** Fix staging path (2A HIGH); add subpath-segment assertions per mount; extend upload-path tests; optional two-user hermetic with distinct subpaths.

### Task 2 / Task 10 — Generated evidence validator
**Success condition (quoted):** *"Validator passes on the MBP bundle"*

**Cheapest fake:** Hand-craft JSON artifacts with matching `run_id`; boolean `docker_engine_meets_subpath_floor: true` with forged version strings — **mitigated** by independent version-string parse (iter-15 hardening).

**No-op oracle:** Stub `forced_cc_result.json` with `cost: 0.01`, empty `proxy_log_window.txt` — **partially mitigated** by `cost > 0` rule and proxy invoke regex; still gameable if log window copied from prior run.

**Mutation oracle:** Remove `bedrock-proxy` from compose — `cc_runner_available()` may still pass; validator must fail on proxy invoke / network inspect — covered in Task 2 scope.

### Task 1 — Step 3 deploy gate
**Success condition (quoted):** *"`live_gate_transcript_committed == true`"* at `deploy_commit`

**Cheapest fake:** Commit stub transcript with allowlist substrings but no real migrate/Playwright — **mitigated** by allowlist markers + `git cat-file` at SHA.

**No-op oracle:** Transcript file with markers pasted, no exit-code lines — plan requires exit-code lines; adequate if validator enforces.

### Task 9 — Dev smoke labeled non-gating
**Success condition (quoted):** *"Validator passes"* without forced-CC steps listed

**Cheapest fake:** Skip paid turn; never run validator; mark Task 9 checkbox done.

**Remedy:** Clarify artifact set (2A MEDIUM).

### Task 5 — Compose config
**Success condition (quoted):** subprocess `docker compose config` parse

**Cheapest fake:** N/A — real subprocess oracle is sound.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log table references prior `.vetting/` filenames — informational for orchestrator only.
- Task 6 Step 5 vs Risk Register "Step 4" wording mismatch (2A LOW).
- SPEC-3 E8 `/srv/dmac/users` neutral default superseded by G7-10 at Step 7 — plan Global Constraints acknowledge re-grounding; no PLAN-3 change required in this review.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 3 |
| MEDIUM | 3 |
| LOW | 1 |

**Top findings:** (1) `docker>=7.2.0` does not exist on PyPI — unsatisfiable pin; (2) 1c CLAUDE.md copy target path wrong for cc-state→`.claude` mount; (3) Step 3 upload modules/tests missing from Task 6 cutover inventory; (4) legacy `validate_cc_acceptance` publish scope not migrated in Task 5; (5) Task 9 vs forced-CC artifact ambiguity.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
