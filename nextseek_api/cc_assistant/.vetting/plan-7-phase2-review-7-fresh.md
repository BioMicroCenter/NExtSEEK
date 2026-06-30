# PLAN-7 Phase 2 Pre-Execution Review — Iter 7 (Fresh, Cold Context)

**Reviewer:** Independent adversarial pre-execution reviewer (iter-7)  
**Date:** 2026-06-30  
**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `nextseek_api/cc_assistant/SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-10; §8 amended 2026-06-30)  
**User locks applied this iter:** committed secret-scanned `live_gate_transcript.txt` on branch; SPEC §8 `preflight.json`; MBP `integration_plan_snapshot.json` in evidence bundle  
**Repo spot-checks (read-only):** `docker-compose.yml` still host-binds `/srv/dmac/users`; six external volumes only; no root `bedrock-proxy`/`dmac-cc-net`; `startup/steps/volumes.py` six volumes; `cc_engine._build_volumes`/`_run_kwargs` host bind dicts; `cc_config.py` laptop default; `cc_runner_available()` cites `dmac's make image-build`; `test_step7_compose_deploy.py` absent (expected pre-implementation); tracker step 3 `not_started`

---

## 2A — Vet (correctness, completeness, spec alignment)

### CRITICAL — MBP tracker snapshot path contradicts preflight anti-gaming rule

**Location:** Task 1 Step 1 (L84) vs Task 10 Step 1 (L448)

**Quote (Task 1):** *"validator rejects paths inside the Step 7 evidence bundle directory"*  
**Quote (Task 10):** *"export `INTEGRATION_PLAN_PATH` to a bundled `integration_plan_snapshot.json` in the evidence dir"*

**Why:** User locked MBP uses `integration_plan_snapshot.json` inside the evidence bundle. Task 1 simultaneously forbids `integration_plan_path` pointing inside `<run_id>/`. A cold implementer cannot satisfy G7-7 Task 10 and Task 1/2 validator rules without picking one — the authoritative MBP gate stalls or the anti-gaming rule is silently dropped.

**Fix:** Add an explicit MBP exception: when `meta.json.host_label` matches the MBP pattern, allow `integration_plan_path` == `<run_dir>/integration_plan_snapshot.json` only if (a) filename is exactly `integration_plan_snapshot.json`, (b) `preflight.json` records `canonical_integration_plan_sha256` from handoff or monorepo path, and (c) snapshot SHA matches. Otherwise keep the in-bundle rejection for arbitrary tracker files.

---

### MEDIUM — SPEC §8 `live_gate_transcript_committed` boolean absent from Task 1 collector schema

**Location:** SPEC-7 §8 (L175–176); Task 1 Step 1 (L81–87)

**Quote (SPEC):** *"`live_gate_transcript_committed` (boolean — must be true"*  
**Quote (plan):** git-at-`deploy_commit` check described; field list omits the boolean

**Why:** Locked SPEC names a required boolean; plan describes equivalent git behavior but never requires the collector to emit `live_gate_transcript_committed`. Implementer reconciling SPEC-first may omit the field; validator/Task 2 may implement git-only check and fail SPEC §8 compliance reviews.

**Fix:** Task 1 Step 1 must list `live_gate_transcript_committed: bool` (collector sets true only after `git cat-file -e deploy_commit:…/live_gate_transcript.txt` succeeds); Task 2 validator must assert the boolean is true, not only perform the git probe ad hoc.

---

### MEDIUM — CC image tag contract not pinned in compose tasks

**Location:** SPEC-7 §3 (L82–83); Task 3 success (L177–178); Task 5 Step 2 (L232–233)

**Quote (SPEC):** *"preserve the current `NEXTSEEK_CC_IMAGE`/`dmac-assistant:poc` contract unless it deliberately changes that contract with tests and docs"*

**Why:** Task 3/5 require build success and compose services but never require the compose-built CC image to be tagged `dmac-assistant:poc` (or documented override). `cc_runner_available()` gates on `DEFAULT_IMAGE` (`dmac-assistant:poc` default). Compose-native build with a generated tag passes Task 3 yet fails Task 9/10 at `cc_runner_available()` — false-negative on the 7d gate.

**Fix:** Task 5 Step 2 success condition: compose build must produce the image referenced by `NEXTSEEK_CC_IMAGE` default (`dmac-assistant:poc`) or env template documents the compose `image:` tag; add compose-config/hermetic test asserting tag alignment.

---

### MEDIUM — `deploy_commit` semantics undefined

**Location:** Task 1 Step 1 (L87); SPEC-7 §8 (L179)

**Quote:** *"`deploy_commit`, `pre_step3_snapshot_tag` as today."*

**Why:** Transcript git oracle depends on `deploy_commit` but neither plan nor SPEC defines it (branch HEAD at preflight? evidence run commit? merge-base?). Divergent implementer choices break reproducibility and cross-bundle comparison.

**Fix:** Define in Task 1: `deploy_commit` == full SHA of `HEAD` at preflight collection on the branch under test; validator re-checks transcript at that exact SHA.

---

### MEDIUM — Task 5 Step 3 contradicts Step 2 on proxy identity

**Location:** Task 5 Step 2 (L233) vs Step 3 (L237)

**Quote (Step 2):** *"Pin `services.bedrock-proxy.container_name: dmac-bedrock-proxy`"*  
**Quote (Step 3):** *"replace external `dmac-bedrock-proxy` … with compose service names (`bedrock-proxy`)"*

**Why:** `test_cc_realstack.py` and `docker logs`/`inspect` target container **name** (`PROXY_CONTAINER`); in-network DNS uses service name `bedrock-proxy`. Step 3 wording instructs replacing `dmac-bedrock-proxy` with `bedrock-proxy` globally — implementer may break log collection while fixing env URLs.

**Fix:** Step 3 must distinguish: service/DNS hostname `bedrock-proxy` for URLs; container name `dmac-bedrock-proxy` (or `DMAC_PROXY_CONTAINER`) for host-side log/inspect; add explicit test matrix in Task 5 Step 3.

---

## 2B — Stress (edge cases, failure modes, operational load)

### MEDIUM — Docker Engine floor for volume `subpath` mounts never defined

**Location:** Task 1 Step 2 (L95); Task 6 Step 1 (L287); Global Constraints (L29)

**Quote (Task 1):** *"`docker version` / `docker info` summary (Task 6 subpath floor)"*  
**Quote (Task 6):** *"docker-py ≥7.1.0"* only

**Why:** Volume subpath mounts require sufficiently new Docker Engine/API support (distinct from docker-py 7.1.0, which is already in `uv.lock`). Hermetic tests pass; older Docker Desktop on MBP fails at sibling spawn with API errors — false G7-7 failure unrelated to compose wiring.

**Fix:** Task 6 must pin minimum Engine/API version (document in DEPLOY.md + `preflight.json.docker_engine_meets_subpath_floor: bool`); Task 2 validator fails MBP/dev bundles when floor not met.

---

### MEDIUM — `cc_runner_available()` error strings still reference standalone dmac build

**Location:** `cc_engine.py` L132–134 (spot-check); plan Task 6 modify list (L61) — volumes only

**Quote (code):** *"build it via dmac's `make image-build`, or set NEXTSEEK_CC_IMAGE"*  
**Quote (plan):** no task updates availability messaging after G7-2 port

**Why:** Post–Step 7, operators on clean MBP see misleading remediation pointing at the retired standalone repo path. Increases pause-and-ask loops and wrong fixes during Task 9/10 stress.

**Fix:** Add Task 5 or Task 8 sub-step: update `cc_runner_available()` detail strings to cite `docker compose build` + `NEXTSEEK_CC_IMAGE`; hermetic test asserts message contains no `dmac-assistant` repo reference.

---

### MEDIUM — `chmod 0777` on named-volume paths may fail silently on some hosts

**Location:** Task 6 Step 3 (L304–305); existing `cc_engine.py` L449–453 (best-effort chmod)

**Quote:** *"Lock permission model: Django (root in nextseek) mkdir + `chmod 0777` on volume paths before spawn."*

**Why:** Named volumes on Docker Desktop/macOS often ignore or partially apply Unix permission bits across VM boundary. Plan mandates 0777 as the locked model but Task 9/10 sentinel write is the only live proof — no hermetic negative if chmod is no-op.

**Fix:** Task 6 success conditions must include live sentinel write in Task 10 (already implied) **and** document fallback (init container or root-owned subpath pre-create) if sentinel fails; record permission probe in evidence `meta.json`.

---

## 2C — Dependencies (sequencing, external inputs, tooling)

### HIGH — Gameability remedy for greenfield volumes not wired into validator (dependency on honest operator)

**Location:** Task 10 Step 0 (L441–443); Task 2 Step 2 (L127); Gameability Audit (L576)

**Quote (Task 10):** *"capture `pre_bootstrap_docker_volume_ls.txt` … assert `dmac-cc-users` absent before bootstrap"*  
**Quote (Task 2):** validator checklist omits pre-bootstrap artifacts  
**Quote (Gameability):** *"Remedy: Record `docker volume ls` before bootstrap in evidence"* — no validator hook

**Why:** Task 10 requires capture; Task 2 never validates it. G7-7 greenfield intent depends on operator discipline only. MBP with pre-existing `./startup.sh install` volumes passes Task 10 success conditions as written.

**Fix:** Task 2 Step 2 must require: for MBP bundles (`host_label` pattern), validator parses `pre_bootstrap_docker_volume_ls.txt` and fails if `dmac-cc-users` or prefixed SEEK volumes exist unless `meta.json.greenfield_exception` references signed handoff; wire Gameability row Task 6 to this check.

---

### MEDIUM — `integration_plan_snapshot.json` lacks canonical provenance oracle

**Location:** Task 10 Step 1 (L448)

**Quote:** *"copied from canonical tracker at gate time"* — no validator rule

**Why:** Even with path exception fixed, MBP operator can author a local snapshot with `"3": {"status": "done"}` without copying canonical `../state/integration-plan.json`. Committed `live_gate_transcript.txt` helps but does not bind snapshot content to canonical tracker SHA.

**Fix:** Preflight must record `canonical_integration_plan_sha256` (from handoff or monorepo read at gate time); validator requires snapshot SHA match **or** handoff JSON field cross-ref; reject snapshot-only attestation.

---

### MEDIUM — Task 11 tracker update assumes monorepo layout without sequencing note

**Location:** Task 11 Step 1 (L488–493); Task 10 (NExtSEEK-only clone)

**Why:** Task 10 explicitly allows NExtSEEK-only clone lacking `../state/`. Task 11 immediately requires modifying tracker at `INTEGRATION_PLAN_PATH` default `../state/integration-plan.json`. Cold implementer on MBP after Task 10 cannot complete Task 11 without undefined context switch to monorepo checkout.

**Fix:** Task 11 Step 1 preamble: tracker update runs from monorepo/dev environment after MBP evidence commit; document required checkout layout; do not block Task 10 completion on tracker write.

---

## 2D — Gameproof (cheapest fake success paths)

### HIGH — In-bundle tracker rejection vs MBP snapshot enables validator bypass confusion

**Location:** Same as 2A CRITICAL (Task 1 L84 / Task 10 L448)

**Cheapest fake:** Point `INTEGRATION_PLAN_PATH` at `/tmp/fake/integration-plan.json` with step 3 `done` during preflight; copy unrelated snapshot into evidence for humans; validator rejects in-bundle path so implementer disables the rule entirely.

**Remedy:** Same as 2A CRITICAL fix — explicit MBP exception with canonical SHA binding, not blanket in-bundle rejection.

---

### MEDIUM — `migration_policy` dev-VM discriminator undefined

**Location:** Task 2 Step 2 (L127); Task 6 Step 4 (L310)

**Quote:** *"when `meta.json.host_label` matches dev-VM pattern"*

**Why:** No regex, enum, or example values. Implementer either always requires `migration_policy` (breaks MBP bundles) or never requires it (dev cutover unrecorded). Gameability gap for Task 6 Step 4 wipe-without-transcript.

**Fix:** Task 2 must document `host_label` patterns (e.g. `dev-vm`, `nextseek-dev`, explicit hostname allowlist) and validator branching table: required on dev, forbidden on MBP.

---

### MEDIUM — Supplementary handoff parse unspecified

**Location:** Task 1 (L85); Task 2 (L127)

**Quote:** *"supplementary handoff parse"*

**Why:** No required SRS fields, step-3 status path, or cross-ref keys. Cheapest fake: empty JSON `{}` at `user_signoff_handoff_path` if path existence alone is checked.

**Fix:** Task 2 must require handoff JSON contains tracker step 3 `done` **or** pointer matching `integration_plan_sha256`; fail on missing/parse error.

---

## Cosmetic / non-blocking notes

- Global Constraints coverage wording says *"Hermetic implementation modules"* but `--cov` target is the validator module under `tests/` — clarify intent (validator coverage vs implementation coverage).
- Phase 2 Vetting Log embedded in plan (L580–599) is orchestration metadata, not an execution step — harmless but noisy for implementers.
- Task 9 labeled non-gating vs Task 10 authoritative is clear and aligned with G7-7 — good progress from earlier iters.
- Iter-6 hardening ( `cc_assistant.py`, memory mounts, `budget_cap_usd`, portable `INTEGRATION_PLAN_PATH`, Mount API) is present in plan text; spot-check confirms code not yet migrated (expected).

---

## Summary counts

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 2 |
| MEDIUM | 11 |
| LOW | 0 |

**Substantive defect total (CRITICAL+HIGH+MEDIUM):** 14

---

## Verdict rationale

Iter-6 user locks (`live_gate_transcript.txt`, SPEC §8 preflight, MBP snapshot) are reflected in plan text, but **Task 1 and Task 10 still contradict each other** on where `integration_plan_path` may point — a hard blocker for the G7-7 MBP gate. Greenfield anti-cheat capture exists in Task 10 but is **not enforced in Task 2**, leaving G7-7 gameable. Remaining issues are spec-field alignment, proxy naming ambiguity, Engine/subpath floors, and undefined discriminators — all MEDIUM but cumulative.

**Top findings (one line each):**

1. **CRITICAL:** Task 1 forbids `integration_plan_path` inside evidence bundle while Task 10 requires MBP snapshot there — mutually exclusive.
2. **HIGH:** `pre_bootstrap_docker_volume_ls.txt` captured in Task 10 but not validated in Task 2 — greenfield gate gameable.
3. **HIGH:** Same tracker-path contradiction enables validator bypass if anti-bundle rule is dropped ad hoc.
4. **MEDIUM:** SPEC §8 `live_gate_transcript_committed` boolean not in Task 1 collector field list.
5. **MEDIUM:** Compose CC image tag vs `dmac-assistant:poc` / `cc_runner_available()` not pinned in Task 3/5 success conditions.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
