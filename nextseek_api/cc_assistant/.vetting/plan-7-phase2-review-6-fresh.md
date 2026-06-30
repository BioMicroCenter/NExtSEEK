# PLAN-7 Phase 2 Adversarial Review (iter 6 — fresh, cold context)

**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-10, G7-10 amend 2026-06-30)  
**Reviewer:** Independent cold-context adversarial vet (iter 6)  
**Date:** 2026-06-30  
**Repo spot-checks (read-only; no prior `.vetting/` reads):** Root `docker-compose.yml` still host-binds `/srv/dmac/users`; six `external: true` volumes only; no root `bedrock-proxy` / `dmac-cc-net`; `startup/steps/volumes.py` lists six volumes; `DEPLOY.md` still Phase A/B; only `docker/cc-runner/Dockerfile` (lean proof image); `cc_engine._build_volumes` / `_run_kwargs` use host-path bind dicts; `nextseek_api/services/cc_assistant.py` and `cc_sweep.py` still translate via `host_user_root`; tracker step 3 is `not_started`; `docker/nextseek.env.example` has no CC topology keys; `test_step7_compose_deploy.py` not yet present.

---

## 2A — Vet (correctness, completeness, spec alignment)

### HIGH — Task 6 modify list omits `services/cc_assistant.py` (1c memory path translation)

**Location:** File Structure Modify (L61–62); Task 6 Steps 1–3 (L282–303); live repo `nextseek_api/services/cc_assistant.py` L97–98, L297–335.

**Quote (repo):**
```python
host_path = transcript_mount_path.replace(
    paths.user_root_mount.rstrip("/"), paths.host_user_root.rstrip("/"), 1)
# ...
user_memory_file = str(written).replace(
    paths.user_root_mount.rstrip("/"), paths.host_user_root.rstrip("/"), 1)
```

**Why:** G7-10 retires `host_user_root` / host bind sources. Plan adds `cc_sweep.py` but not the primary Django CC handler (`services/cc_assistant.py`), which still stores `transcript_path` as a pseudo-host path and converts mount paths back to `host_user_root` before spawn. After Task 6, empty/legacy `host_user_root` breaks 1c sync summarization, memory file staging, and sibling mount keys — forced-CC and Step 3 upload/read paths regress even if compose topology is correct.

**Fix:** Add `nextseek_api/services/cc_assistant.py` to Task 6 modify list; refactor `_session_metas` and the inline 1c block to use mount-relative / volume-subpath semantics only; extend hermetic or grep guards; add Task 6 success condition that memory mounts still populate after cutover.

---

### HIGH — 1c memory mounts (`user_memory_file`, `transcripts_dir`) absent from Mount API cutover checklist

**Location:** Task 6 Step 3 (L299–303); File Structure (L270); live `cc_engine._build_volumes` L391–394.

**Quote (plan):** *"Sibling CC containers mount **subpaths of `dmac-cc-users`** to `/data/input`, `/data/shared`, `/data/scratch`, `/home/user/.claude`, etc."*

**Why:** Plan specifies `Mount` + `volume_options.subpath` for standard tree mounts but never names `/home/user/.claude/CLAUDE.md` (user memory) or `/.cc-memory/transcripts` (1c window). Current `_build_volumes` binds these via host-path dict keys. A lazy implementer can refactor input/scratch/cc-state only; 1c turns silently lose memory layer or fail spawn with invalid mount sources.

**Fix:** Task 6 Step 3 must explicitly list all seven sibling mount targets (input, shared, scratch, cc-state, user-memory CLAUDE.md, memory transcripts, and any probe mounts); require hermetic tests mirroring `test_cc_engine_memory_mounts.py` against `list[docker.types.Mount]`.

---

### HIGH — `step3_deploy_gate` gameable via self-authored `integration-plan.json`

**Location:** Task 1 Step 1 (L81–84); Task 1 Step 2 (L92); Gameability Audit (L565).

**Quote:** *"`tracker_step3_status` must be `"done"` — validator re-reads the integration plan file at `integration_plan_path` at validation time."*

**Why:** Validator checks status at whatever path preflight records — not canonical repo location, not hash against a known-good commit, not cross-check against handoff evidence refs. Cheapest fake: create `/tmp/fake/integration-plan.json` with step 3 `"done"`, point `integration_plan_path` there, pass gate without Step 3 deploy. Gameability table still says *"live evidence path exists"* while Task 1 allows handoff-only pass — internal contradiction weakens the gate further.

**Fix:** Require `integration_plan_path` resolve to the canonical `../state/integration-plan.json` (or env override) **and** record `integration_plan_sha256` + `integration_plan_commit_context` in preflight; validator rejects paths inside the Step 7 evidence bundle dir; require handoff JSON `evidence_refs` to cite committed Step 3 live transcript path or Task 13 command markers; align Gameability row with Task 1 optional-live-evidence rule.

---

### MEDIUM — `path_mappings` / `DMAC_PATH_MAPPINGS` cutover not in Task 6 success conditions

**Location:** File Structure Modify (L271); Task 6 Success conditions (L309–316); live `cc_engine.run_cc_turn` L479–484.

**Quote (plan):** *"Update **`path_mappings` / `DMAC_PATH_MAPPINGS`** in `run_cc_turn` to use mount-relative logical paths (not host bind sources)."*

**Why:** Requirement appears only in file-structure bullet, not Task 6 steps or success conditions. After G7-10, `dirs.output_src` / `scratch_src` semantics change; agent env may still advertise retired host bind roots → wrong artifact paths in replies (Step 3 download/upload acceptance regressions).

**Fix:** Add Task 6 Step 3 bullet + success condition + hermetic test on `build_agent_environment` / `DMAC_PATH_MAPPINGS` JSON shape post-cutover.

---

### MEDIUM — Task 2 validator checklist omits `preflight.json` / `step3_deploy_gate`

**Location:** Task 2 Step 2 (L126–128) vs Task 1 Step 2 (L90–92).

**Quote (Task 2):** *"Checks must cover compose topology, image/service status, `cc_runner_available`, forced-CC success, proxy invoke, network segmentation…"*

**Why:** Hard gate fields live in Task 1 prose but Task 2 (where the validator is implemented) does not list preflight or Step 3 gate checks. Split risks a lazy implementer delivering Task 2 without gate enforcement despite Task 1 tests.

**Fix:** Task 2 Step 2 must enumerate preflight + `step3_deploy_gate` + `migration_policy` (dev bundles) as first-class checks; cross-reference SPEC §8 superset explicitly.

---

### MEDIUM — Task 11 hardcodes non-portable tracker path

**Location:** Task 11 (L483).

**Quote:** *"Modify: `/home/taishajo/work/state/integration-plan.json` status fields only."*

**Why:** Conflicts with Task 1 / Permissions `INTEGRATION_PLAN_PATH` portability principle. MBP or CI implementer may write to wrong path or skip tracker update.

**Fix:** Reference `INTEGRATION_PLAN_PATH` (default `../state/integration-plan.json` from NExtSEEK root) consistently in Task 11.

---

## 2B — Stress Test (execution stall / failure modes)

### HIGH — MBP Task 10 preflight: default `integration_plan_path` unavailable on NExtSEEK-only clone

**Location:** Task 1 default (L82); Task 10 Steps 0–5 (L431–472); Permissions (L516).

**Quote:** *"default: repo-relative `../state/integration-plan.json` from NExtSEEK root"*

**Why:** G7-7 greenfield MBP procedure is “fresh clone of Step 7 branch.” A clone containing only `NExtSEEK/` has no sibling `work/state/`. Preflight collection and `step3_deploy_gate` validation fail unless operator manually sets `INTEGRATION_PLAN_PATH` — not documented in Task 10. Careful implementer stalls at authoritative gate.

**Fix:** Task 10 Step 0/5: document required `INTEGRATION_PLAN_PATH` export **or** copy canonical tracker into evidence (`integration_plan_snapshot.json`) with preflight pointing at bundled snapshot plus handoff cross-ref; validator accepts bundled snapshot only when `meta.json.host_label` indicates MBP and handoff refs match.

---

### MEDIUM — Task 10 lacks concrete forced-CC evidence generator

**Location:** Task 10 Step 4 (L453–455); compare existing `tests/test_cc_realstack.py`.

**Quote:** *"Run one forced CC turn with a per-run sentinel through the compose-owned proxy."*

**Why:** No command, script, or adaptation of `test_cc_realstack` / `manage.py` incantation for Step 7 bundle layout under `acceptance_evidence/step7/`. Implementer must invent procedure; risk of hand-crafted `forced_cc_result.json` that satisfies JSON shape but not live correlation.

**Fix:** Task 10 Step 4 reference a concrete generator (adapt `test_cc_realstack` to emit SPEC-7 §8 artifacts, or document `docker exec nextseek …` one-liner); Task 2 validator must correlate `forced_cc_result.json.run_id` with proxy log window timestamps.

---

### MEDIUM — `migration_policy` validator rule undefined for bundle classification

**Location:** Task 6 Step 4 (L307); Task 9 label (L392).

**Quote:** *"validator fails if absent on dev-VM bundles"*

**Why:** No discriminator (`meta.json.host_label`, `preflight.json.host`, etc.) specified in Task 2. Implementer either always requires migration (breaks MBP greenfield) or never requires it (dev cutover unrecorded).

**Fix:** Task 2 validator: require `migration_policy` + transcript when `meta.json.host_label` matches dev-VM pattern (document enum); forbid on MBP greenfield bundles.

---

### MEDIUM — uid-1001 scratch writability strategy underspecified for named volumes

**Location:** Task 6 Step 3 (L300–303); Success conditions (L311–312).

**Quote:** *"Permission model: document chosen uid-1001 scratch strategy… Task 9/10 sentinel write under `scratch/` proves writability."*

**Why:** Host-bind era used `chmod 777` on `/srv/dmac/users`. Named volumes inherit Docker volume permissions; plan lists options but does not mandate one before implementation. Dev-VM cutover may pass compose-up yet fail forced-CC at scratch write.

**Fix:** Task 6 Step 3 must lock one strategy (e.g., root mkdir + `chmod 0777` on volume paths inside nextseek, or init container); Task 9/10 sentinel write is a hard success condition with captured `stat` output in evidence.

---

## 2C — Validate External Dependencies

### MEDIUM — Docker Engine floor for volume `subpath` mounts not pinned

**Location:** Task 6 Step 1 (L287); Task 1 preflight (L94); Dependency Validation (L550–557).

**Quote:** *"Hermetic import test: `docker.types.Mount` with `volume_options={"subpath": "proj/user/scratch"}` (docker-py ≥7.1.0)."*

**Why:** Plan pins docker-py API (available transitively via `dmac_assistant` path dep) but not minimum Docker Engine/API for volume subpaths. Hermetic tests pass; older Docker Desktop on MBP fails at spawn with API error — false negative on G7-7 gate.

**Fix:** Task 1 preflight must capture `docker version` Server/API; Task 6 Dependency row must state minimum Engine version; Task 2 validator reads preflight floor or evidence `meta.json.docker_server_version`.

---

### MEDIUM — Greenfield proxy secret bootstrap not in deploy positive requirements

**Location:** Task 4 (L203–206); Task 8 Step 2 (L373–375); live `DEPLOY.md` Phase A L37–38.

**Quote (live DEPLOY):** `TOK=$(docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK)`

**Why:** Current procedure steals Bedrock token from nextseek container. Step 7 security invariant places token only in `bedrock-proxy` gitignored file. Task 8 positive requirements cover `./startup.sh install` and volume bootstrap but not proxy-secret creation without `docker exec nextseek`. Greenfield operator may stall or reintroduce token-in-nextseek pattern.

**Fix:** Task 8 numbered procedure must include: copy Bedrock token from operator-supplied secret into gitignored proxy env file (`.example` keyed); Task 8 doc guard fails if numbered steps reference `docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK`.

---

### MEDIUM — `budget_cap_usd` asserted but not validated

**Location:** Permissions (L519); Task 2 Step 2; SPEC §8 `forced_cc_result.json`; legacy `validate_cc_acceptance.py` check 17.

**Quote (Permissions):** *"Per-run sentinel; cap enforced"*

**Why:** Legacy acceptance enforces `ledger.json: total_cost_usd <= budget_cap_usd`. Step 7 SPEC §8 records cost in `forced_cc_result.json` but Task 2 never requires cap check. Runaway spend could pass Step 7 gate.

**Fix:** Add `budget_cap_usd` to `meta.json`; Task 2 validator enforces `forced_cc_result.json.cost <= budget_cap_usd` (default 2.0 to match Step 3 live gate).

---

## 2D — Gameproof (exploit / false-completion paths)

### MEDIUM — Hand-crafted Step 7 evidence bundle without live correlation

**Location:** Task 10 Step 5 (L457–460); Gameability Audit (L566–570).

**Why:** Cheapest fake: synthesize all SPEC §8 JSON/text files with consistent sentinel string, empty proxy log, and passing secret scan on dummy content. Remedy row cites proxy log + agent env scan but Task 2 does not require cross-artifact correlation (run_id, container id, timestamp overlap).

**Fix:** Task 2 validator must assert: `forced_cc_result.json.run_id` appears in `proxy_log_window.txt`; `network_inspect.json` lists transient agent; `images.json` tags match `docker_ps.txt`; reject bundles where artifacts share no temporal/container linkage.

---

### MEDIUM — Gameability table contradicts Task 1 live-evidence optionality

**Location:** Gameability Audit (L565) vs Task 1 (L85).

**Quote (table):** *"Require … live evidence path exists"*  
**Quote (Task 1):** *"If absent on disk, gate may still pass when tracker + handoff JSON prove Step 3 deploy"*

**Why:** Table tells reviewers/implementers live path is mandatory; Task 1 says optional. Orchestrator hardening drift; lazy implementer picks weaker interpretation.

**Fix:** Update Gameability row to match Task 1: live path **or** handoff with Task 13 marker strings + SRS fields.

---

### LOW — Pre-existing volume reuse on “greenfield” MBP

**Location:** Task 10 Step 0 (L436–438); Gameability (L571).

**Why:** Iter-5 added `pre_bootstrap_docker_volume_ls.txt` — good. Validator enforcement still delegated to Task 2 without explicit check text. Residual cheat: document “signed reuse” without real sign-off file.

**Fix:** Task 2: if `dmac-cc-users` present pre-bootstrap, require `meta.json.greenfield_exception` with handoff path + user sign-off ref; else fail.

---

## Non-blocking cosmetic notes

- `cc_engine.cc_runner_available()` error text still cites `dmac's make image-build` — update during Task 3/5 doc pass.
- `integration-plan.json` substep **7b** description still lists `DMAC_USER_ROOT`; plan retires host-bind semantics (Task 11 may only mutate `status`, not descriptions).
- Step 7 validator intentionally omits legacy BAML router check (`validate_cc_acceptance` check 11) — acceptable per SPEC §8 scope; document narrowing in Task 5 Step 3 when updating realstack tests.
- `cc_config._DEFAULT_HOST_USER_ROOT` is still laptop path in live repo; Step 3 E8 / Step 7 G7-10 sequencing is covered by hard gate, not a plan defect.
- Phase 2 Vetting Log references prior review files — meta; this review did not read them.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 4 |
| MEDIUM | 11 |
| LOW | 1 (+ cosmetic notes) |

**Top findings (one line each):**
1. Task 6 omits `services/cc_assistant.py` — 1c memory path translation breaks after G7-10 volume cutover.
2. Task 6 Mount API checklist skips 1c memory/transcript sibling mounts (`user_memory_file`, `transcripts_dir`).
3. `step3_deploy_gate` bypassable via arbitrary local `integration-plan.json` at `integration_plan_path`.
4. MBP greenfield default `../state/integration-plan.json` missing on NExtSEEK-only clone — authoritative gate stalls.
5. Task 2 validator omits preflight/Step-3 gate, budget cap, and cross-artifact correlation — false completion path.

**Verdict:** **CONDITIONAL_ACCEPTANCE** — plan is implementable and aligns with locked SPEC G7-10, but four HIGH and eleven MEDIUM defects remain exploitable or stall-inducing for cold implementers; UA requires zero MEDIUM+ substantive defects.
