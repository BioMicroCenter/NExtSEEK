# PLAN-7 Phase 2 Review (iteration 1)

**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-9)  
**Reviewer context:** Cold-context adversarial pre-execution vetting; repo verified @ planning-session baseline (compose lacks `bedrock-proxy`/`dmac-cc-net`; DEPLOY.md still Phase A/B; Step 3 tracker `not_started`).

**Verified claims (targeted lookups):**
- `docker-compose.yml`: no `bedrock-proxy`, no `dmac-cc-net`, no CC image build target — **TRUE**
- `docker/nextseek.env.example`: no CC topology keys (`NEXTSEEK_CC_*`, `DMAC_*`) — **TRUE**
- `DEPLOY.md`: Phase A/B manual sidecar bootstrap still required path — **TRUE**
- In-tree `dmac_assistant/` (router/BAML/build_context) exists; full Container-CC runtime (Dockerfile, `container/`, plugins, proxy) lives in external `dmac-assistant` repo — **TRUE**
- `test_cc_engine_env.py`, `test_cc_realstack.py` exist and encode manual/external topology — **TRUE**
- `integration-plan.json` step 7 (7a–7d) matches plan scope — **TRUE**
- Overlap with PLAN-3 on `DEPLOY.md`, `cc_config.py`, `cc_engine.py` — **TRUE** (Step 3 not started; PLAN-3 Task 13 also edits DEPLOY.md)

---

## 2A — Vet (permissions & execution snags)

### CRITICAL — Step 3 “fully deployed” gate is advisory, not blocking

**Location:** Global Constraints — *"**Re-ground after Step 3 lands:** before touching files, read the then-current …"*; Task 1 success conditions (preflight hashes only).

**Why:** User-stated sequencing (load-bearing per parallel vetting orchestration) requires Step 7 implementation **after Step 3 is fully deployed** (PLAN-3 Task 13 live gate). The plan weakens this to “Step 3 lands” (merge/docs) plus a read-only re-ground. Task 1 records Step 3 **doc commit state** and file hashes but never requires: `integration-plan.json` step 3 → `done`, PLAN-3 Task 13 live evidence on disk, or a deployed `cc-step3-ui-io` branch hash match. An implementer can start Task 2 immediately after Step 3 merges without deploying, producing DEPLOY.md / `cc_engine.py` edits on stale deployed state and colliding with in-flight Step 3 deploy work.

**Fix:** Add a **hard preflight gate** (Task 1 Step 2 validator assertion + hermetic test): refuse all Step 7 work unless `preflight.json` includes `step3_deploy_gate: { tracker_status: "done", live_evidence_path, deploy_commit, pre_step3_snapshot_tag }` and validator fails if any field missing. Global Constraints must say **“Step 7 MUST NOT start until Step 3 Task 13 live verification is complete and deployed on the dev instance.”** Replace “lands” with “fully deployed.”

---

### HIGH — Phase 2 governance sections absent from the plan file

**Location:** Entire plan — no `## Permissions Required`, `## Risk Register`, `## Dependency Validation`, or `## Gameability Audit`.

**Why:** Ultraplan Phase 2 hard gate (`adversarial-vetting-loop.md` §Phase governance): Phase 2 is **not done** until all four sections exist **in the plan file**. The plan cannot serve as a cold-start execution contract without the permissions catalogue and risk/gameability registers the orchestrator and implementer rely on.

**Fix:** Append the four required sections (per parallel vetting plan template) before execution. Task-level permissions to catalogue now: Docker socket, compose v2, BuildKit, gitignored Bedrock token file, MBP local Docker, dev-VM sign-off, external Docker volumes bootstrap, Bedrock spend, GitHub egress for plugin-context generation, service-account `docker:cli` helper (still referenced in current DEPLOY.md Phase B).

---

### HIGH — MBP greenfield permissions omit external-volume bootstrap

**Location:** Task 9 Steps 1–3; Goal / SPEC-7 §2 deploy bar.

**Why:** Root `docker-compose.yml` declares six `external: true` volumes (`seek-filestore`, `seek-mysql-db`, etc.). `docker compose up -d` **fails** on a clean host unless those volumes exist. README `./startup.sh install` creates them via `startup/lib/docker_ops.py`, but Task 9 and SPEC-7 §7 require only gitignored secrets + `docker compose build && docker compose up -d`. Task 9 Step 1 (“fresh clone… no prior required Container-CC state”) does not authorize `startup.sh` or document volume creation — yet `cc_runner_available()` requires the full stack.

**Fix:** Either (a) explicitly include `./startup.sh install` (or a documented volume-create subcommand) in the authoritative DEPLOY path and Task 9 procedure, with validator checks for volume existence; or (b) amend SPEC-7 deploy bar via `/ultraplan amend` if compose-only is strictly limited to CC sidecar services atop an already-running stack. Do not leave Task 9 success conditions satisfiable only on hosts that already ran startup.

---

### MEDIUM — Cross-plan file collision permissions undocumented

**Location:** Task 7 vs PLAN-3 Task 13 (both modify `DEPLOY.md`; PLAN-3 also touches `cc_engine.py`, `cc_config.py`).

**Why:** Step 7 Task 1 hashes overlap files but does not define **merge order** or ownership: Step 3 deploy notes must land and deploy first; Step 7 Task 7 **replaces** Phase A/B on the post-Step-3 file. Without this, two implementers or sequential agents can clobber each other’s DEPLOY.md.

**Fix:** Add explicit sequencing note under Global Constraints and Task 7: “Step 7 DEPLOY rewrite runs only on DEPLOY.md at the Step-3-deployed commit hash recorded in preflight.”

---

## 2B — Stress Test

### CRITICAL — Task 9 greenfield path likely unsatisfiable as written

**Location:** Task 9 success conditions — *"`docker compose build && docker compose up -d`"* on clean MBP; SPEC-7 §2 *"clean non-dev machine … docker compose build && docker compose up -d"*.

**Likely failure:** Compose up exits non-zero on missing external volumes (see 2A). MBP run never reaches `cc_runner_available()` or forced-CC turn.

**Catastrophic failure:** Implementer “fixes” greenfield by reintroducing manual Phase A/B steps or pre-seeding dev VM state into evidence — violating G7-2/G7-7 while passing a weakened validator.

**Hidden dependency:** `./startup.sh` / volume creation, MySQL/Neo4j seed import, and PLAN-3 `zstandard` image dep (PLAN-3 Task 13) all affect the image Task 9 must build.

**Fix:** Same as 2A volume bootstrap; add Task 9 Step 0 “bootstrap host stack per DEPLOY.md §… (volumes + seeds)” or narrow 7d scope with locked-design amendment.

---

### HIGH — Partial runtime port vs G7-3 and existing lean image

**Location:** Task 3 — *"exact path chosen during implementation"*; repo already has `docker/cc-runner/Dockerfile` (lean proof image, explicitly **not** production target per file header).

**Likely failure:** Implementer wires compose to existing lean `cc-runner` image to satisfy build tests quickly, violating G7-3 (“full CC runtime tree … not lean proof image”). Task 3 presence tests list plugin/context files but do not forbid using `docker/cc-runner/Dockerfile` as the build target.

**Fix:** Pin build target path in plan (e.g. `docker/cc-runtime/` or extend `dmac_assistant/` with ported assets); add explicit test: compose build target must **not** be `docker/cc-runner/Dockerfile`; image must include plugin manifest + `container/CLAUDE.md` per Task 3 Step 1.

---

### HIGH — Legacy acceptance stack left orphaned

**Location:** Tasks 2, 6, 9 vs existing `validate_cc_acceptance.py` + `test_cc_realstack.py` + `outputs/cc_acceptance/`.

**Likely failure:** New Step 7 validator passes while `test_cc_realstack.py` still documents/requires external `dmac-assistant` repo, manual `dmac-bedrock-proxy` container name, and Phase A/B topology. Future regressions slip through the old paid gate; doc guards in Task 7 don’t mention updating realstack tests.

**Fix:** Add Task 6 Step 4 (or Task 2 extension): update `test_cc_realstack.py` / `validate_cc_acceptance.py` for compose-native service names OR document deprecation with enforced grep guard; align `PROXY_CONTAINER` default with compose service name (`bedrock-proxy` vs `dmac-bedrock-proxy`).

---

### MEDIUM — No ultraplan 95% coverage floor

**Location:** Entire plan — no `--cov-fail-under=95` or per-task coverage targets.

**Why:** Ultraplan SKILL mandates 95% coverage floor with justified exceptions tied to live gates. Plan has Docker/live Tasks 8–9 without stating which modules require coverage vs live-only exemption.

**Fix:** Add coverage targets per task cluster; tie Tasks 8–9 to validator + live gates as justified exceptions with non-deferrable hermetic oracles (per 2D).

---

### MEDIUM — Task 8 (dev VM) vs G7-7 (MBP gate) ambiguity

**Location:** Task 8 (dev VM compose verification) vs Task 9 / G7-7 (MBP before dev merge).

**Likely failure:** Tracker marked done after Task 8 dev VM evidence while MBP gate (7d) still failing; Task 10 says Tasks 1–9 must pass but Task 8 is not substitutable for 7d.

**Fix:** Label Task 8 explicitly “non-gating smoke; 7d remains authoritative”; Task 10 tracker update must require MBP bundle path from Task 9 only.

---

### Rollback guidance missing

**Location:** All tasks — no “pause and ask” vs “revert” conditions.

**Fix:** Risk Register entry: per-change sign-off (Global Constraints) triggers pause; failed MBP forced-CC turn → revert compose/image changes, do not merge; DEPLOY.md rewrite → keep Step-3-era hash in preflight for rollback reference.

---

## 2C — Validate External Dependencies

### HIGH — Compose v2 external volumes behavior (verified locally)

**Claim:** Greenfield host can `docker compose up -d` without pre-created external volumes.  
**Reality:** Compose v2 fails with “external volume … not found” unless volumes exist (`startup/lib/docker_ops.py` creates them). **Must-verify before Task 9.**

---

### MEDIUM — Port source repo availability at implementation time

**Location:** Task 3 Step 2 — port from standalone `dmac-assistant` checkout.

**Risk:** Plan assumes external repo at implementation time for full runtime tree. No pinned commit/path, no fallback if repo layout diverges. Plugin context generation (G7-6) may need GitHub egress (`DEPLOY.md` notes `make image-build` vendor-sync egress).

**Fix:** Task 1 preflight must record external `dmac-assistant` commit used for port; Task 3 Step 2 must name minimum file manifest from SPEC-7 §4.

---

### MEDIUM — Proxy/container naming contract drift

**Location:** Task 5 (`bedrock-proxy` service) vs `test_cc_realstack.py` (`DMAC_PROXY_CONTAINER` default `dmac-bedrock-proxy`) vs `cc_engine.py` hostname `bedrock-proxy:8080`.

**Risk:** Compose service_name vs container_name mismatch breaks realstack and proxy log collection unless unified. SPEC-7 §3 preserves `NEXTSEEK_CC_IMAGE`/`dmac-assistant:poc` tag contract — plan does not specify compose-built image tag or update to `cc_runner_available()` error strings (still cite `dmac's make image-build`).

**Fix:** Pin compose `services.bedrock-proxy.container_name`, image tag after build, and Task 6 update to `cc_engine.py` availability messages.

---

### LOW — `NEXTSEEK_SERVER` in Task 6 env list

**Location:** Task 6 Step 2 lists `NEXTSEEK_SERVER`; SPEC-7 §6 omits it (exists in `docker/scripts/entrypoint.sh`).

**Note:** Non-blocking scope drift; include only if Step 3 re-ground shows CC deploy depends on it.

---

## 2D — Gameproof

### HIGH — Task 1 preflight is existence-check, not deploy gate

**Success condition (quoted):** *"A generated `preflight.json` exists and is valid JSON"* + hashes for overlap files.

**Cheapest fake:** Agent writes `preflight.json` with valid JSON and SHA256 of **current** (pre-Step-3) files, starts Step 7 before Step 3 deploy, passes hash checks while violating user sequencing.

**No-op test:** Empty Step 7 branch with hand-crafted `preflight.json` passes Task 1 validator if hashes match working tree.

**Remedy:** Require `step3_deploy_gate` fields (tracker, live evidence, deploy commit); mutation test — change Step 3 file without redeploy → preflight must fail.

---

### HIGH — Task 7 doc guards grep-only

**Success condition (quoted):** *"Tests must fail if the required deploy path reintroduces … Phase A/B sidecar bootstrap as required steps."*

**Cheapest fake:** Move Phase A/B commands to “Historical / optional troubleshooting” section still in DEPLOY.md; grep guard passes if it only scans “required path” headings implementer defines loosely.

**Remedy:** Validator parses DEPLOY.md ordered step list (steps 1–5 from SPEC-7 §7); fail if `docker network create`, `dmac-assistant repo`, or separate proxy compose appear in numbered procedure.

---

### MEDIUM — Task 5 compose-config tests mockable

**Success condition (quoted):** *"`docker compose config` includes `bedrock-proxy` … and `dmac-cc-net`."*

**Cheapest fake:** Test reads a committed golden `compose_config.json` fixture instead of running `docker compose config`; YAML edited but compose broken on real host.

**Remedy:** Hermetic test must subprocess `docker compose -f … config` (or parser on live file with interpolation env fixture); mutation — remove network from YAML → test red.

---

### MEDIUM — Task 3 “rg proves old WS server not started” gameable

**Cheapest fake:** Copy WS files to `reference/legacy/` (allowed) but leave import hook in entrypoint commented “TODO”; rg passes, compose still broken.

**Remedy:** Assert root compose has no WS service; entrypoint/integration test fails if WS port binds.

---

### MEDIUM — Task 2 validator negative controls incomplete vs SPEC-9

**Location:** Task 2 Step 3 lists leak strings; SPEC-9 also requires Django secret key values, `Authorization` header forms, unredacted `NEXTSEEK_PASSWORD` rules.

**Cheapest fake:** Validator passes bundle missing Django-secret or password masking checks.

**Remedy:** Copy SPEC-9 §9 bullet list verbatim into validator contract test.

---

### MEDIUM — Task 9 forced-CC sentinel without router path

**Success condition (quoted):** *"Forced CC turn completes with sentinel in reply."*

**Cheapest fake:** Direct `cc_engine` forced turn bypassing BAML router (still valid for infra proof but weaker than `validate_cc_acceptance` router check). Old realstack proved BAML route.

**Remedy:** Require `forced_cc_result.json` metadata proving compose-owned proxy path; optionally retain `routed_route_decided.json` or document intentional narrowing vs legacy validator.

---

## Cross-check: SPEC-7 locked decisions (G7-1–G7-9)

| Decision | Plan alignment | Gap |
|----------|----------------|-----|
| G7-1 planning-only | ✓ stated | — |
| G7-2 no standalone repo | ✓ Tasks 3–4 | Task 3 port source still external at implementation |
| G7-3 full runtime not lean image | ✓ Task 3 | `docker/cc-runner/` lean image not excluded |
| G7-4 in-tree proxy | ✓ Task 4 | — |
| G7-5 no WS server infra | ✓ Task 3 | gameable rg-only guard |
| G7-6 plugin context | ✓ Task 3 Step 3 | fallback evidence must be validator-gated |
| G7-7 MBP before dev merge | ✓ Task 9 | Task 8 dev VM + volume bootstrap gap |
| G7-8 generated evidence | ✓ Tasks 2, 8, 9 | separate from legacy `outputs/cc_acceptance/` |
| G7-9 screenshots | ✓ Task 2 | — |

---

## Non-blocking cosmetic notes

- Plan header date/version not duplicated in tracker (fine).
- Task internal “Step 3” sub-steps (e.g. Task 2 Step 3) refer to task sub-steps, not integration Step 3 — mildly confusing; rename to “Sub-step 3” in hardening pass.
- `integration-plan.json` path in Task 10 uses absolute `/home/taishajo/...` — portable enough for this user environment.

---

## Finding counts

| Severity | Count |
|----------|------:|
| CRITICAL | 2 |
| HIGH | 8 |
| MEDIUM | 10 |
| LOW | 1 |

**Top fixes before execution:** (1) Harden Step 3 **fully deployed** blocking gate. (2) Resolve external-volume vs compose-only greenfield contradiction. (3) Append Phase 2 governance sections. (4) Pin runtime build target vs lean `docker/cc-runner/`. (5) Reconcile legacy realstack/acceptance tests with compose-native topology.
