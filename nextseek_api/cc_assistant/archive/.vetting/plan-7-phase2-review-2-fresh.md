# PLAN-7 Phase 2 Review (iteration 2 — fresh independent)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-9)  
**Reviewer:** Fresh cold-context adversarial pass (no prior review/fix-log reads)  
**Date:** 2026-06-30

---

## Phase 2 Section Presence (hard gate)

| Required section | Present | Notes |
|------------------|---------|-------|
| `## Permissions Required` | Yes | L421–436; includes Docker, gitignored secrets, `./startup.sh install`, MBP, Bedrock spend |
| `## Risk Register` | Yes | L440–454; 8 ranked risks with rollback/remedy |
| `## Dependency Validation` | Yes | L457–467; 6 dependencies |
| `## Gameability Audit` | Yes | L470–480; **partial** — 5/10 tasks only |
| `## Phase 2 Vetting Log` | Yes | L483–490 |

**Verdict on section existence:** All five Phase 2 sections are present in the plan file.

---

## Hardened-Item Verification

| Hardened item | Status | Evidence |
|---------------|--------|----------|
| Step 3 FULLY DEPLOYED hard gate + `step3_deploy_gate` in Task 1 | **Present (with gap)** | Global Constraints L27–28; Task 1 L78–88; validator L82; Risk #1; Gameability Task 1 |
| MBP volume bootstrap via `startup.sh` in Task 9 Step 0 | **Present (not wired to DEPLOY)** | Task 9 Step 0 L350–352; Permissions L429; Risk #2; Dependency Validation L462 |
| `docker/cc-runtime/` pinned, not lean `cc-runner` | **Present** | File Structure L47; Task 3 L149–152, L156; Task 5 L221; Risk #3 |
| Task 8 non-gating vs Task 9 authoritative | **Present** | Task 8 L311; Task 9 L343; Task 10 L402; Risk #7 |
| Legacy realstack test update in Task 5 | **Present** | Task 5 Step 3 L224–226; Risk #5; Dependency Validation L465 |
| DEPLOY.md merge order documented | **Present** | Global Constraints L28; Task 7 L289; Risk #4 |

---

## 2A — Vet (permissions, contract completeness, execution snags)

### MEDIUM — `user sign-off` required globally but absent from `step3_deploy_gate` schema

**Location:** Global Constraints L27 vs Task 1 L78–82  
**Quote:** Global Constraints require Step 3 to be `done` **with user sign-off**; `step3_deploy_gate` fields are only `tracker_step3_status`, `live_evidence_path`, `deploy_commit`, `pre_step3_snapshot_tag`.  
**Why:** A cold implementer can satisfy the validator by setting `integration-plan.json` step 3 to `"done"` and creating an empty `live_evidence_path` file. The global HARD GATE prose is stronger than the machine-checkable contract.  
**Fix:** Add `user_signoff_recorded: bool` (or a path to Step 3 Task 13 evidence artifact with required keys) to `step3_deploy_gate`; validator must fail if sign-off evidence is missing or does not reference Step 3 live gate artifacts.

### MEDIUM — Authoritative DEPLOY procedure omits volume bootstrap required by Task 9

**Location:** Task 7 L291–294 vs Task 9 Step 0 L350–352 vs Risk Register #2 L445  
**Quote:** Task 7 Step 2 documents `pull → secrets → build → up → verifier` (SPEC-7 §7 steps 1–5). Task 9 Step 0 requires `./startup.sh install` **before** `docker compose up -d`. Risk #2 remedy says *"Document `./startup.sh install` in DEPLOY"* but Task 7 success conditions do not require it.  
**Why:** G7-7 greenfield on a clean host fails at compose up without external volumes. Operators following only `DEPLOY.md` hit the exact failure Risk #2 describes; bootstrap is relegated to Task 9 verification notes, not the authoritative operator path.  
**Fix:** Extend Task 7 Step 2 and doc guards to require `./startup.sh install` (or documented subset) as step 2 or 3 in the numbered procedure on clean hosts; validator must fail if numbered steps omit volume bootstrap while compose still declares `external: true` volumes.

### LOW — Permissions table defers resolution to Phase 5.5 without cross-reference

**Location:** Permissions Required L436  
**Quote:** *"Resolved in Phase 5.5 before Step 7 execution."*  
**Why:** Acceptable deferral, but no pointer to where Phase 5.5 output lands (file/path). Cold implementer may proceed without resolved permissions.  
**Fix:** Add one line naming the permissions resolution artifact or tracker field.

---

## 2B — Stress Test

### HIGH — Clean-host compose-up failure mode under-documented in operator path

**Location:** Task 7 vs Task 9 Step 0; Risk #2  
**Likely failure:** `docker compose up -d` exits on missing `external: true` volumes (`seek-filestore`, `seek-mysql-db`, etc.).  
**Catastrophic failure:** MBP greenfield falsely classified as "Step 7 broken" or implementer "cheats" by reusing dev VM volumes, undermining G7-7.  
**Hidden dependency:** Root compose external volumes + `startup/lib/docker_ops.py` / `./startup.sh install` — captured in Task 9 Step 0 but not in Task 7 authoritative docs.  
**Rollback:** Risk register correctly identifies remedy; Task 7 does not implement it.

### MEDIUM — Task 3 runtime directory wording allows brief ambiguity

**Location:** Task 3 L140 vs L149–152  
**Quote:** *"Create: in-tree CC runtime directory chosen by the implementer"* vs explicit `docker/cc-runtime/` pin and negative test against `docker/cc-runner/`.  
**Likely failure:** Implementer picks a different directory name; tests may pass if updated ad hoc, weakening G7-3 pin.  
**Fix:** Replace L140 with *"Create: `docker/cc-runtime/`"* — remove "chosen by implementer."

### MEDIUM — Coverage floor is global prose only, not task-enforced

**Location:** Global Constraints L29; Tasks 1–2, 5–7 success conditions  
**Quote:** *"target **≥95%** where pytest applies"* with no `--cov-fail-under=95` in any task success condition.  
**Likely failure:** Hermetic modules ship below 95% with all stated success conditions green.  
**Fix:** Mirror PLAN-3 pattern: add explicit `--cov-fail-under=95` to Tasks 1, 2, 5, 6, 7 success conditions (or name justified modules in Risk Register).

---

## 2C — Validate External Dependencies

### MEDIUM — Dependency Validation table is thin vs external claims

**Location:** Dependency Validation L457–467  
**Quote:** Only one external link (Docker compose networks). Standalone `dmac-assistant` port source marked *"Must-verify at execution"*. Legacy acceptance marked *"Hardened — Task 5 Step 3 migration"* without confirming current file state.  
**Why:** 2C lens expects version/API/tool verification where claims are made. Plan asserts `./startup.sh install` creates volumes and `cc_runner_available()` behavior without citing repo anchors or version pins for Docker Compose v2 / BuildKit.  
**Fix:** Add repo-verified anchors (e.g. `startup.sh`, `docker_ops.py`, `cc_runner_available` definition path) and note must-verify-at-execution items in Task 1 preflight capture.

### LOW — Bedrock proxy / spend cap dependency unspecified

**Location:** Permissions L431; Task 9 Step 4  
**Why:** Forced-CC turn requires Bedrock API; no spend cap mechanism or env guard named in plan (operator concern, not blocking design).  
**Fix:** Reference existing cap/guard if any, or add Task 9 preflight note to record cap setting.

---

## 2D — Gameproof

### MEDIUM — Gameability Audit covers 5/10 tasks; Tasks 3, 4, 6, 8, 10 absent

**Location:** Gameability Audit L470–480  
**Why:** Phase 2 hard gate expects per-task cheapest-fake → remedy. Missing high-risk tasks:
- **Task 3:** Port partial tree / symlink lean image — partially mitigated by negative test but not in audit table.
- **Task 4:** Copy real proxy secret into build context — mitigated in Task 4 success conditions, not audited.
- **Task 6:** Template documents keys without values — gameable via empty strings.
- **Task 8:** Dev-VM bundle passed off as 7d gate — mitigated by Task 8 label + Task 10, not in audit.
- **Task 10:** Tracker marked done without MBP bundle — mitigated in Task 10 L402, not in audit.  
**Fix:** Extend Gameability Audit rows for Tasks 3, 4, 6, 8, 10 with cheapest-fake and remedy (many remedies already exist in task bodies — surface them in the table).

### MEDIUM — `live_evidence_path` existence-only check

**Location:** Task 1 L82, L88; Gameability Audit Task 1 L474  
**Cheapest fake:** `touch /tmp/step3-live-evidence` satisfies *"live evidence path must exist on disk"* with no content validation.  
**Mutation oracle:** Corrupting Step 3 deploy evidence does not fail Step 7 preflight if tracker reads `"done"`.  
**Fix:** Validator must parse Step 3 evidence artifact(s) for required keys (e.g. deploy commit, user OK timestamp, command transcripts) per sibling PLAN-3 Task 13 contract; reject empty files.

### LOW — Task 5 compose test uses live subprocess (good) but Task 3 build success is Docker-only

**Location:** Task 3 success conditions L166–171  
**Cheapest fake:** `docker build` succeeds once manually while committed tree remains incomplete; presence tests could pass if ported incrementally without compose wiring.  
**Remedy:** Already partially addressed by Task 5 compose-config tests; note cross-task dependency in Gameability Audit Task 3 row.

---

## G7-1–G7-9 Traceability (summary)

| Decision | Plan coverage |
|----------|---------------|
| G7-1 session deliverable | Global Constraints L25–26 |
| G7-2 no standalone repo | Tasks 3–5, 9; Task 9 Step 0 NExtSEEK-owned bootstrap |
| G7-3 full runtime not lean image | File Structure, Task 3 negative test, Risk #3 |
| G7-4 in-tree proxy | Task 4 |
| G7-5 exclude old WS/server | Task 3 |
| G7-6 plugin context | Task 3 Step 3 |
| G7-7 MBP greenfield | Task 9 authoritative; Task 8 non-gating |
| G7-8 generated evidence | Task 2 validator |
| G7-9 screenshot scan | Task 2 L128–129; Task 9 L385 |

No locked-design contradictions found. Sequencing gate (Step 7 after Step 3 deploy) is strengthened, not weakened.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log L488 self-records iteration 2 as `UNCONDITIONAL_ACCEPTANCE`; this fresh review independently finds remaining MEDIUM+ gaps.
- Task numbering in Risk Register uses "5/7" for DEPLOY collision — clear in context.
- `Permissions Required` table is well-formed; Phase 5.5 deferral is standard ultraplan pattern.

---

## Finding counts

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 7 |
| LOW | 4 |

**Top fixes before Phase 3 / execution:**

1. Wire `./startup.sh install` into Task 7 authoritative DEPLOY numbered procedure (closes HIGH + Risk #2 remedy gap).
2. Strengthen `step3_deploy_gate` to enforce user sign-off / Step 3 live evidence content, not path existence alone.
3. Complete Gameability Audit for Tasks 3, 4, 6, 8, 10.
4. Add `--cov-fail-under=95` to hermetic task success conditions or document justified exceptions in Risk Register.
5. Remove Task 3 "chosen by implementer" ambiguity — lock to `docker/cc-runtime/`.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
