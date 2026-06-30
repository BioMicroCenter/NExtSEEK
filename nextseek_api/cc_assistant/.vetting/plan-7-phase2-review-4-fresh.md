# PLAN-7 Phase 2 Review — Iteration 4 (Fresh, Cold Context)

**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-9)  
**Reviewer:** Independent cold-context adversarial pre-execution vetting (2026-06-30)  
**Repo spot-checks (read-only):** `docker-compose.yml` has six `external: true` volumes and bind-mounts `/srv/dmac/users`; no root `bedrock-proxy` / `dmac-cc-net`; `DEPLOY.md` still Phase A/B; only `docker/cc-runner/Dockerfile` exists (lean proof image); `startup.sh` requires `uv`; `cc_runner_available()` checks image+network only (`cc_engine.py`); `test_cc_realstack.py` defaults `PROXY_CONTAINER` to `dmac-bedrock-proxy`; tracker step 3 is `not_started`.

---

## 2A — Vet (Permissions & Execution Snags)

### HIGH — Plan-required host bootstrap contradicts locked SPEC deploy bar

**Location:** SPEC-7 §2 L29–31, §7 L131–135; PLAN-7 Task 7 Step 2 L295, Task 9 Step 0 L353, Global Constraints L27–28  
**Quote (SPEC §2):** *"A clean non-dev machine must be able to deploy full Container-CC from the NExtSEEK repo alone with `git pull`/clone, gitignored secrets, and `docker compose build && docker compose up -d`."*  
**Quote (SPEC §7):** numbered steps 1–5 ending at `docker compose up -d` — no `./startup.sh install`, no host bind prep.  
**Quote (PLAN Task 7 Step 2):** *"`./startup.sh install` (external volumes) → `sudo mkdir -p /srv/dmac/users && sudo chmod -R 777 /srv/dmac/users` → `docker compose build`"*  
**Why defect:** Authority hierarchy is locked design > plan. Verified root compose declares six external volumes (`docker-compose.yml` L132–144) and bind-mounts `/srv/dmac/users` (L28). Clean-host `docker compose up -d` fails without volume bootstrap; `./startup.sh install` creates volumes via `startup/steps/volumes.py`. Plan correctly adds these steps but locked SPEC §2/§7 omit them entirely. A cold implementer following SPEC as authority will ship a DEPLOY procedure that fails G7-7 greenfield on a truly clean MBP.  
**Fix:** Escalate `/ultraplan amend` to expand SPEC §7 (and §2 deploy bar) to include NExtSEEK-owned volume bootstrap and `/srv/dmac/users` host prep as explicit numbered steps — do not resolve by silently weakening the spec or dropping bootstrap from the plan.

### MEDIUM — `uv` CLI missing from Permissions Required

**Location:** PLAN-7 `## Permissions Required` L424–435; Task 9 Step 0 L353; repo `startup.sh` L13–15  
**Quote:** *"error: uv is not installed"* — `startup.sh` exits 2 without `uv`.  
**Why defect:** Task 9 Step 0 mandates `./startup.sh install` on clean MBP before compose up, but Permissions catalogues Docker, gitignored secrets, and sudo — not the `uv` binary the bootstrap entrypoint hard-requires. Phase 5.5 cannot resolve a permission that was never catalogued.  
**Fix:** Add `uv` CLI (and network for first `uv run --project startup` if cold) to Permissions table for Tasks 8–9.

### MEDIUM — Task 7 doc-guard success conditions omit positive bootstrap requirements

**Location:** PLAN-7 Task 7 Step 1 L287–291, Success conditions L297–302; Risk Register #2 L446  
**Quote (Task 7 Step 1):** validator *"fail if forbidden commands appear"* in §7 steps 1–5 — no positive requirement for volume/host prep.  
**Quote (Risk #2 remedy):** *"Document `./startup.sh install` in DEPLOY"* — not reflected in Task 7 success conditions.  
**Why defect:** Doc guards are negative-only (forbid Phase A/B, manual network ops). An implementer can write a SPEC-literal five-step DEPLOY (pull → secrets → build → up → verifier), pass Task 7 doc guards, and still fail Task 9 on a clean host. Risk Register remedy is not wired to a falsifiable success condition.  
**Fix:** Extend Task 7 Step 1 and success conditions: when compose declares `external: true` volumes or `/srv/dmac/users` bind-mount, numbered procedure MUST include `./startup.sh install` (or equivalent) and host bind prep; validator fails if absent.

---

## 2B — Stress Test

### HIGH — Task 7 Step 1 validator scope conflicts with Task 7 Step 2 DEPLOY content

**Location:** PLAN-7 Task 7 Step 1 L291; Task 7 Step 2 L295  
**Quote (Step 1):** *"Validator must parse the **numbered procedure** (SPEC-7 §7 steps 1–5)"*  
**Quote (Step 2):** seven arrow-separated steps including `./startup.sh install` and `sudo mkdir … /srv/dmac/users` between config fill and compose build.  
**Why defect:** Step 2 instructs authors to document bootstrap steps not present in SPEC §7's five-step contract, while Step 1 ties the validator to §7 steps 1–5 only. Without a spec amendment, implementer must either (a) hide bootstrap inside step 2 sub-bullets while validator only scans five headings, (b) add top-level steps and fail the §7-shape check, or (c) omit bootstrap and pass doc guards while breaking Task 9. Likely failure mode: Task 7 doc/validator arguments during implementation.  
**Fix:** After SPEC §7 amend, realign Task 7 Step 1 validator to the amended step count and require positive presence of bootstrap sub-steps; or explicitly state that steps 2a/2b are mandatory sub-bullets under step 2 with validator assertions on their text.

### MEDIUM — Task 9 "greenfield" success condition is not machine-checkable

**Location:** PLAN-7 Task 9 Success conditions L379  
**Quote:** *"MBP evidence proves the host had no prior required Container-CC state."*  
**Likely failure:** Operator reuses MBP with pre-existing external volumes from an earlier NExtSEEK `./startup.sh install`; compose up succeeds; forced-CC passes — but G7-7 greenfield intent (no prior CC/stack state) is unproven.  
**Catastrophic risk:** False confidence that compose-native deploy works on a truly clean host when evidence came from a partially primed machine.  
**Fix:** Require pre-bootstrap artifacts in `meta.json` / `preflight.json`: e.g. `docker volume ls` / `docker network ls` snapshots before `./startup.sh install`, asserting required volume names absent (or documenting intentional reuse with user sign-off).

### MEDIUM — Bedrock spend cap asserted in Permissions but not in acceptance oracle

**Location:** PLAN-7 Permissions L432; Task 2 Step 2 L116–118; Task 9 Success conditions L377–387; legacy `validate_cc_acceptance.py` L17–18  
**Quote (Permissions):** *"Per-run sentinel; cap enforced"*  
**Why defect:** Existing acceptance validator enforces `ledger.json: total_cost_usd <= budget_cap_usd`. Step 7 `forced_cc_result.json` records cost (SPEC §8) but Task 2 validator checks and Task 9 success conditions never require `cost <= budget_cap_usd`. A runaway forced-CC turn could pass Step 9 while violating the stated cap policy.  
**Fix:** Add `budget_cap_usd` to `meta.json`, require Task 2 validator check on `forced_cc_result.json` cost field, and add Task 9 success condition mirroring legacy check 17.

### MEDIUM — Task 5 container identity migration underspecified

**Location:** PLAN-7 Task 5 Step 2 L222–224; Step 3 L228; repo `test_cc_realstack.py` L48  
**Quote (Task 5 Step 3):** *"Align `PROXY_CONTAINER` default with compose `container_name`."*  
**Why defect:** Step 2 does not require pinning `services.bedrock-proxy.container_name` (or documenting the env override). Realstack defaults to `dmac-bedrock-proxy`; compose-native service may use a different generated name. Mutation: compose service runs but log collection in migrated realstack still targets wrong container — proxy invoke checks pass in Step 7 bundle but realstack regression path stays broken.  
**Fix:** Task 5 Step 2 must pin `container_name` (or mandate `DMAC_PROXY_CONTAINER` in env template) and add a compose-config test asserting the name matches Task 5 Step 3 migration target.

---

## 2C — Validate External Dependencies

### HIGH — (Same as 2A-1) SPEC §2/§7 vs external-volume reality — escalation required

**Location:** SPEC-7 §2, §7; PLAN-7 Dependency Validation L463–464  
**Status:** Dependency Validation correctly notes `./startup.sh install` creates external volumes, but marks "OK" while locked SPEC still states compose-only deploy. This is a **spec-plan authority conflict**, not merely "must-verify at execution."  
**Fix:** Same as 2A-1 — amend locked SPEC before Phase 3; update Dependency Validation row to "BLOCKED pending SPEC §7 amend" until aligned.

### LOW — Dependency Validation lacks repo anchors for runtime claims

**Location:** PLAN-7 `## Dependency Validation` L458–467  
**Why note:** Rows cite Docker docs and high-level OKs but omit file anchors (`startup/steps/volumes.py`, `cc_engine.py:121–146`, `startup.sh`) that Task 1 preflight could capture. Not blocking if Task 1 records them at execution time.  
**Fix:** Add repo path anchors to Dependency Validation table for implementer re-grounding.

---

## 2D — Gameproof

### Task 1 — `step3_deploy_gate` preflight

**Success condition (quoted):** *"validator fails if step 3 is not `done`"* + live evidence path with Task 13 markers.  
**Cheapest fake:** Hand-craft `preflight.json` with correct hashes from a stale tree while tracker is edited locally — **blocked** by validator re-read of `integration-plan.json` at validation time (good oracle).  
**Residual gap:** `live_gate_transcript.txt` need only be non-empty with substring markers — could be pasted from an old run if path exists. **LOW** — path is fixed and Step 3 not deployed yet; harden with transcript timestamp/hash vs `deploy_commit` if gaming becomes plausible.

### Task 7 — DEPLOY doc guards

**Success condition (quoted):** *"Doc guard tests pass"* + forbidden-command rejection in §7 steps 1–5.  
**Cheapest fake:** Five-step DEPLOY matching SPEC §7 literally (no startup.sh, no `/srv/dmac/users` prep); forbidden Phase A/B absent → Task 7 green, Task 9 red on clean MBP.  
**No-op test:** Yes — empty bootstrap still passes Task 7.  
**Remedy:** Positive bootstrap assertions (see 2A-3, 2B-1).

### Task 9 — MBP greenfield gate

**Success condition (quoted):** *"MBP evidence proves the host had no prior required Container-CC state."*  
**Cheapest fake:** Reuse MBP with existing external volumes; skip `./startup.sh install`; compose up succeeds if volumes pre-exist.  
**No-op test:** Yes for greenfield claim; no for full stack bring-up.  
**Remedy:** Pre-bootstrap volume/network snapshots (see 2B-2).

### Task 9 — Forced-CC sentinel

**Success condition (quoted):** *"Forced CC turn completes with sentinel in reply"* + proxy log + agent env scan.  
**Cheapest fake:** Direct engine forced turn bypassing UI — **acceptable** for infra proof per plan intent; proxy log window + env scan provide provenance.  
**Weaker than legacy:** No BAML router check (`validate_cc_acceptance` check 11) — **LOW**; SPEC §8 does not require router artifact for Step 7.

### Task 2 — Validator synthetic bundles

**Success condition (quoted):** *"Hermetic tests pass with no Docker, DB, network, or spend."*  
**Cheapest fake:** Validator checks JSON schema/shape only; live Task 9 catches real failures — layered defense OK unless Task 2 skips cost-cap and segmentation field semantics.  
**Remedy:** Ensure synthetic bundles include mutation cases for each §8 artifact field the live gate depends on, including cost cap.

---

## Non-blocking Cosmetic Notes

- `cc_engine.cc_runner_available()` error text still cites `dmac's make image-build` — update during Task 3/5 doc pass (not load-bearing for Step 7 gate).
- Task 1 Step 3 marker `migrate nextseek_api 0007` is abbreviated vs PLAN-3 Task 13 command `0007_ccsessiontranscript` — substring match is probably sufficient.
- Global Constraints coverage target names only `validate_step7_compose_deploy` module; acceptable for Task 2 if "justified exceptions" cover Docker/live paths (Tasks 3–4, 8–9).

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 4 (cosmetic / residual) |

**Top findings:** (1) Locked SPEC §2/§7 compose-only deploy bar contradicts plan-mandatory `./startup.sh install` and `/srv/dmac/users` host prep — requires spec escalation. (2) Task 7 validator is tied to SPEC §7 five-step shape while Task 7 Step 2 documents additional bootstrap steps — internal stall risk. (3) Task 7 doc guards are negative-only; Task 9 greenfield and budget-cap oracles are weak.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
