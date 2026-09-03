# PLAN-7 Phase 2 Review — Iteration 14 (Fresh, Cold Context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md`  
**Reviewer:** Independent adversarial pre-execution reviewer  
**Date:** 2026-06-30  
**Baseline repo spot-checks:** `docker-compose.yml` still host-binds `/srv/dmac/users`; six external volumes only (no `dmac-cc-users`); no root `bedrock-proxy` / `dmac-cc-net`; `startup/steps/volumes.py` lists six volumes; `cc_engine._build_volumes` / `_run_kwargs` use host-bind `volumes=` dict; `cc_config._DEFAULT_HOST_USER_ROOT` is laptop path; `cc_runner_available()` cites standalone `make image-build`; tracker step 3 `not_started` (expected pre-gate); no `test_step7_*` / `validate_step7_*` yet.

---

## Verdict Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 7 |
| LOW | 3 |

**Verdict:** **CONDITIONAL_ACCEPTANCE**

Plan is executable as a cold-start TDD contract after iter-13 hardening (1c memory copy-before-spawn, Engine/Compose floors in DEPLOY, `preflight.json` locked in SPEC §8, MBP tracker snapshot, `dmac_assistant/` boundary). Remaining defects are concrete API/oracle gaps, not architectural blockers.

---

## 2A — Vet (Execution Readiness & Permissions)

### Permissions catalogue (complete per plan § Permissions Required)

| Permission | Tasks | Status |
|------------|-------|--------|
| Read/write NExtSEEK Step 7 branch | all | Required |
| Docker Compose v2 + BuildKit | 3–6, 9–10 | Required |
| `uv` CLI (`./startup.sh install`) | 10 Step 0 | Required |
| Docker socket (dev VM / MBP) | 9–10 | Required |
| Gitignored secrets (`nextseek.env`, `db.env`, `local_settings.py`, proxy token) | 7, 10 | Required |
| `INTEGRATION_PLAN_PATH` (default `../state/integration-plan.json`) | 1, 10 | Required |
| Write monorepo `work/state/integration-plan.json` | 11 only | Required |
| `./startup.sh install` (7 external volumes incl. `dmac-cc-users`) | 6, 10 | Required |
| MBP local Docker + network | 10 | Required |
| Bedrock API spend (forced-CC, ≤$2 cap) | 10 | Required |
| GitHub egress (plugin context generation) | 3 | Optional fallback |
| Service-account `docker:cli` (dev VM smoke) | 9 | Optional |
| Per-change sign-off | 9–10 | Required |
| Dev VM `/srv/dmac/users` → volume migration | 6 Step 5 | Optional with sign-off |

### Execution snags (non-blocking)

1. **Hard Step 3 gate is correctly specified** and aligned with PLAN-3 Task 13 (`live_gate_transcript.txt` committed under `evidence/3-ui-based-io-live/`). Tracker step 3 is `not_started` today — Step 7 must not start; this is expected, not a plan defect.
2. **DEPLOY.md merge order** (PLAN-3 append → Step 7 rewrite on recorded `deploy_commit`) is explicit and matches sibling PLAN-3 Task 13 Step 3b.
3. **Task 6 is the critical path** — atomic cutover across `cc_config`, `cc_provision`, `cc_engine`, `cc_sweep`, `services/cc_assistant.py`, compose, and startup volumes. Hermetic gates are specified; execution order (Step 3 before Step 4) is correct.
4. **`dmac_assistant/` vs `docker/cc-runtime/` boundary** is now explicit in Task 3 (iter-13 fix) — reduces port-scope confusion.

---

## 2B — Stress Test

| Dimension | Assessment |
|-----------|------------|
| **Most likely failure** | Task 6 volume-subpath refactor: wrong `docker.types.Mount` construction or missing pre-spawn mkdir on volume subpaths → sibling spawn API error or uid-1001 scratch write failure at Task 10. |
| **Most catastrophic failure** | Incorrect volume subpath in `_build_volumes` → cross-user data exposure on shared `dmac-cc-users` volume. Mitigated by Step 2 isolation tests + hermetic mount-list assertions, but not formally proven without live two-user gate in Step 7. |
| **Hidden dependencies** | Standalone `dmac-assistant` checkout as port source (must-verify at Task 3); GitHub egress for plugin context (G7-6 fallback); Bedrock spend on MBP; `uv` on MBP for `startup.sh install`; Step 3 live deploy completing before any Step 7 edit. |
| **Ambiguous success** | MBP greenfield volume-absence oracle vs `startup/.instance.json` volume prefix (see HIGH-2). Legacy `validate_cc_acceptance.py` vs Step 7 validator artifact contracts after Task 6 `logical_root` cutover (see MED-3). |
| **Coverage risk** | Task 2 validator is a monolith (15+ check categories in one task). Docker/live Tasks 3–4, 9–10 rely on justified exceptions — acceptable per G7-8 if validator is complete. |
| **Rollback** | Risk register is adequate. Pause-and-ask on MBP forced-CC failure, secret scan failure, or `step3_deploy_gate` mismatch. Dev migration `copy|wipe` transcript required on dev-VM bundles. |

---

## 2C — Validate External Dependencies

| Claim | Verification | Status |
|-------|--------------|--------|
| Docker Compose multi-network dual-homed nginx | Docker compose networks reference docs | OK |
| Engine ≥26 / API v1.45 for volume subpaths | docker-py PR #3270; Engine 26 release notes | OK — plan enforces via preflight + validator |
| Compose plugin ≥2.26 | Docker compose `volume.subpath` docs | OK — conservative floor (sibling mounts use docker-py, not compose subpath YAML) |
| docker-py ≥7.1.0 subpath via `Mount` | `dmac_assistant/pyproject.toml` pins `docker>=7.1.0`; `uv.lock` has `docker==7.1.0` | OK version; **API shape wrong in plan** (see HIGH-1) |
| `./startup.sh install` creates external volumes | `startup/steps/volumes.py` today lists 6 volumes; Task 6 adds 7th | Must-implement |
| Standalone `dmac-assistant` port source | Not in NExtSEEK repo | Must-verify at Task 3 execution |
| `cc_runner_available()` | Live code checks image + network only, not proxy | OK — Step 7 validator adds proxy/segmentation |
| PLAN-3 Step 3 gate / transcript path | PLAN-3 Task 13 Steps 8–9 match SPEC-7 §8 | OK |

---

## 2D — Gameproof Audit

| Success condition (quoted) | Cheapest fake | Ease | Remedy in plan | Gap |
|----------------------------|---------------|------|----------------|-----|
| "`preflight.json` + `step3_deploy_gate`" | Hand-crafted JSON | Medium | Re-read tracker at path; committed `live_gate_transcript.txt` at `deploy_commit` | Strong |
| "Validator passes MBP bundle" | Markdown index only | Easy | Reject Markdown-only; require artifact set | Strong |
| "`pre_bootstrap_docker_volume_ls.txt` — seven volumes absent" | Unprefixed names absent while prefixed copies exist | **Easy** | `greenfield_exception` + handoff | **Weak — see HIGH-2** |
| "`forced_cc_result.json.cost <= budget_cap_usd`" | `$0` stub JSON | Medium | `cost > 0` unless documented exception | Adequate |
| "`cc_runner_available()==(True,'ok')`" | Image present, proxy down | Medium | Validator proxy invoke + log window | Adequate |
| "Network excludes backend services" | Stale inspect snapshot | Medium | Cross-artifact `run_id` correlation | Adequate |
| "No `host_user_root` in spawn path" | Hermetic mock only, live still uses binds | Hard | Atomic cutover grep + mock tests | Adequate post iter-9/13 |
| "DEPLOY numbered procedure has no `/srv/dmac` prep" | Move forbidden steps to unnumbered appendix | Medium | Parse numbered procedure only | Adequate |

**Ranked by ease of fake pass:** (1) prefixed-volume greenfield oracle, (2) Markdown-only bundle, (3) zero-cost forced-CC stub, (4) stale network inspect.

---

## Findings

### HIGH-1 — docker-py `Mount` API mis-specified (`volume_options` vs `subpath`)

**Location:** Task 6 Steps 1–2, 4 — `_build_volumes` returns `list[docker.types.Mount]` with `volume_options={"subpath": ...}`; hermetic test specifies same shape.

**Quote:** *"`docker.types.Mount` with `volume_options={"subpath": "proj/user/scratch"}` (docker-py ≥7.1.0)"*

**Why defect:** docker-py 7.1.0 `docker.types.Mount` accepts `subpath=` as a direct constructor keyword (mapped to `VolumeOptions.Subpath`), not a `volume_options` dict. Implementer or hermetic test written literally from the plan will not match the real API.

**Fix:** Replace all `volume_options={"subpath": ...}` with `Mount(target=..., source=<volume_name>, type="volume", subpath="proj/user/scratch", read_only=...)`. Hermetic test should assert serialized mount dict contains `VolumeOptions.Subpath`.

---

### HIGH-2 — MBP greenfield volume oracle ignores instance prefix

**Location:** Task 2 validator — hardcoded seven volume names; Task 8 Step 2 — *"unless validator reads `startup/.instance.json` and adjusts expected volume names"*; Task 10 Step 0 — capture `pre_bootstrap_docker_volume_ls.txt`.

**Quote:** *"fail if any of the seven external volumes (`seek-filestore`, …, `dmac-cc-users`) exist"*

**Why defect:** `startup/steps/volumes.py` creates `{prefix}{name}` when `INSTANCE_PREFIX` is set (`startup/lib/instance.py`). Greenfield check against bare names can (a) **false-pass** when prefixed volumes exist (`NExtSEEK-seek-filestore` present but `seek-filestore` absent), or (b) **false-fail** when MBP legitimately uses prefixed install. Task 8 mentions adjustment; Task 2 validator spec does not define the branch.

**Fix:** Task 2: if `startup/.instance.json` exists in evidence or preflight records `instance_prefix`, derive expected volume names via `volume_names_for_prefix(prefix)`; parse `pre_bootstrap_docker_volume_ls.txt` against that set. MBP Task 10 Step 0: record `instance_prefix: ""` explicitly for default greenfield gate.

---

### MED-1 — Legacy acceptance not fully aligned with Task 6 `logical_root` contract

**Location:** Task 5 Step 3 — updates `test_cc_realstack.py` / `validate_cc_acceptance.py` for compose DNS only; Task 6 Step 4 — `path_mappings` schema uses `logical_root`; Task 10 — SPEC-7 §8 artifact mapping from legacy names.

**Why defect:** `validate_cc_acceptance.py` still checks `published_files.json` with `host_root`-era user scoping (`uid/` prefix heuristic). After Task 6, `_publish_artifacts` returns `logical_root`-relative display paths. Task 5 does not require updating legacy validator field names (`forced_result.json` → `forced_cc_result.json`) or publish-path assertions. Two parallel acceptance contracts can diverge.

**Fix:** Task 5 Step 3 bullet: migrate `validate_cc_acceptance.py` publish scoping to `logical_root` paths OR mark legacy validator deprecated with grep guard forbidding use for Step 7 sign-off. Task 10 Step 4 should reference only Step 7 validator for gate.

---

### MED-2 — Tracker substep 7b prose contradicts plan Task 7 (`DMAC_USER_ROOT`)

**Location:** `integration-plan.json` step 7b description lists `DMAC_USER_ROOT`; plan Task 7 Step 2 retires host-path bind and `DMAC_USER_ROOT` documentation.

**Why defect:** Authority is plan > tracker, but Task 11 only mutates tracker `status` fields. An implementer skimming tracker prose during Task 7 env-template work may re-document `/srv/dmac/users` host bind.

**Fix:** Note in Task 7 Step 1: ignore tracker 7b key list for retired keys; follow plan env list. Optional: user sign-off to amend tracker description at Task 11 (status-only mutation rule may block — implementer must follow plan).

---

### MED-3 — Task 2 validator scope is a single monolithic deliverable

**Location:** Task 2 Step 2 — one validator implementing preflight, greenfield, topology, proxy, segmentation, migration_policy, secret scan, cross-artifact correlation, host_label branching, etc.

**Why defect:** High first-slice integration risk. A partial validator that passes synthetic bundles but omits a branch (e.g., MBP snapshot exception, `migration_policy`, Compose floor) could ship before Task 10 reveals the gap.

**Fix:** Split Task 2 into incremental commits: (a) preflight + step3 gate, (b) topology + secret scan, (c) live-artifact checks stubbed until Tasks 9–10. Or require Task 2 Step 2 checklist mapping each SPEC-7 §8 artifact to exactly one validator function with unit test.

---

### MED-4 — Step 3 / G7-10 re-grounding relies on preflight prose, not explicit Step 3 artifact hashes

**Location:** Task 1 — hashes PLAN-3/SPEC-3 files; records "files changed by Step 3 that Step 7 will touch"; no hash of Step 3 post-deploy `cc_engine.py` / upload paths.

**Why defect:** Step 3 introduces turn-scoped `output/artifacts/<turn_id>/` and `cc_traces` persistence. Task 6 `path_mappings` / `_publish_artifacts` refactor could break Step 3 paths if re-grounding is file-hash-only on planning docs.

**Fix:** Task 1 preflight: add `step3_live_artifact_inventory` (paths/hashes of committed `evidence/3-ui-based-io-live/*`) and require Task 6 Step 4 re-read PLAN-3 turn-scoped layout before cutover.

---

### MED-5 — `pre_step3_snapshot_tag` recorded but not validated

**Location:** SPEC-7 §8 / Task 1 — `pre_step3_snapshot_tag` may be empty; PLAN-3 Task 13 Step 3 requires `:pre-step3` snapshot.

**Why defect:** Weak rollback oracle. Empty tag accepted even when Step 3 deploy used snapshot procedure.

**Fix:** Validator: if `live_gate_transcript_committed` and transcript mentions `pre-step3`, require non-empty `pre_step3_snapshot_tag` and `docker image inspect` proof in evidence (dev-VM bundle) or document N/A for MBP greenfield.

---

### MED-6 — Compose ≥2.26 floor enforced without compose-YAML subpath usage

**Location:** Task 1/2 — both Engine and Compose floors required; compose mounts `dmac-cc-users` at top level (no compose `subpath:` key).

**Why defect:** Conservative but may block MBP with Engine 26+ and Compose 2.25.x where docker-py sibling subpaths would work. Operator confusion if `docker compose version` is the failure surface unrelated to actual mount path.

**Fix:** Document in Task 8: Compose floor is forward-looking / conservative. Or narrow validator to Engine floor for docker-py mounts and Compose floor only if compose YAML adds `volume.subpath`.

---

### MED-7 — Cross-user isolation not in MBP authoritative gate

**Location:** Task 10 success conditions — single forced-CC turn; Step 2 isolation tests hermetic only.

**Why defect:** G7-10 shared volume raises stakes for subpath bugs. MBP gate proves one user, not isolation.

**Fix:** Accept as known gap (Step 2 hermetic isolation tests are the bar) OR add optional Task 9 dev-VM two-user smoke (non-gating) called out in risk register.

---

### LOW-1 — Coverage target names test module path

**Location:** Global Constraints — `--cov=nextseek_api.cc_assistant.tests.validate_step7_compose_deploy`

**Why note:** Measures test file coverage, not a pure implementation module. May inflate pass while implementation modules lack coverage.

**Fix:** After extracting validator helpers to a non-test module, retarget `--cov` to that module.

---

### LOW-2 — Redundant `docker>=7.1.0` root pin

**Location:** Task 6 Step 2

**Why note:** Already transitive via `dmac_assistant/pyproject.toml` and `uv.lock`. Plan now says "add explicit root pin if hermetic imports need it" — acceptable.

---

### LOW-3 — `cc_runner_available()` network comment stale

**Location:** Live `cc_engine._run_kwargs` docstring says container joins "nextseek compose network"

**Why note:** Plan correctly puts agents on `dmac-cc-net`. Task 5 updates `cc_runner_available()` strings but does not mention `_run_kwargs` docstring. Cosmetic for execution.

---

## Positive Observations (iter-13 hardening verified)

1. **1c memory on volume subpaths** — Task 6 Step 4 substeps 1–7 (copy merged CLAUDE.md to `cc_state_mnt`, drop `user_memory_file` bind, transcripts RO subpath) address the file-overlay impossibility; aligned with live `test_cc_engine_memory_mounts.py` behavior to replace.
2. **SPEC §8 ↔ PLAN alignment** — `preflight.json`, `docker_compose_meets_subpath_floor`, committed transcript gate, MBP `integration_plan_snapshot.json` exception are consistent across SPEC and PLAN.
3. **Step 3 hard gate** — No handoff-only fallback; `git cat-file` check on transcript at `deploy_commit`.
4. **DEPLOY prerequisites** — Task 8 documents Engine ≥26 and Compose ≥2.26 before bootstrap.
5. **Artifact naming** — Task 10 legacy → SPEC-7 §8 mapping table prevents `forced_result.json` cheats in Step 7 bundles.
6. **Security** — Bedrock token only in proxy; DEPLOY forbids `docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK`; secret-scan negative controls enumerated.

---

## Top Findings (priority order)

1. **HIGH-1** — Fix docker-py `Mount(subpath=...)` API in Task 6 and hermetic tests (`volume_options` is wrong).
2. **HIGH-2** — Define instance-prefix-aware greenfield volume parsing in Task 2 validator.
3. **MED-1** — Align or deprecate legacy `validate_cc_acceptance.py` relative to Task 6 `logical_root`.
4. **MED-3** — Decompose Task 2 validator or add per-check unit test matrix.
5. **MED-4** — Strengthen Step 3 live re-grounding beyond planning-doc hashes.

---

## Final Verdict

**CONDITIONAL_ACCEPTANCE** — Zero CRITICAL defects; plan is structurally sound and matches locked SPEC-7 G7-1–G7-10. Two HIGH defects (docker-py API shape, greenfield volume oracle) and seven MEDIUM gaps should be fixed in-plan before or during the first implementation slice; none block starting Task 1 hermetic work, but **Task 6 must not proceed without HIGH-1 resolved** and **Task 10 MBP gate must not sign off without HIGH-2 resolved**.

---

*Cold-context review. Did not read prior `.vetting/` iteration files. Sibling PLAN-3/SPEC-3 consulted only for Step 3 deploy gate and shared-file consistency.*
