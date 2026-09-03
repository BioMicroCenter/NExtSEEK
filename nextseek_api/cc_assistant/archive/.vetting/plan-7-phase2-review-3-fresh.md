# Phase 2 Independent Review (iter 3, cold context) — PLAN-7-compose-native-prod-deploy.md

**Target:** `/home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md` (Phase 2 sections + task execution contract)  
**Authority:** `SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-9), `AGENTS.md`  
**Reviewer:** Fresh cold-context adversarial reviewer (no prior vetting transcripts)  
**Date:** 2026-06-30

---

## Phase 2 completeness (structural gate)

| Required section | Present | Notes |
|------------------|---------|-------|
| `## Permissions Required` | Yes | L421–435 |
| `## Risk Register` | Yes | L439–452 |
| `## Dependency Validation` | Yes | L456–466 |
| `## Gameability Audit` | Yes | L469–478 |
| `## Phase 2 Vetting Log` | Yes | L482–490 |

All four Phase 2 hard-gate sections exist. Remaining defects are substantive execution-contract gaps.

---

## 2A — Vet (permissions, snags, full execution)

### HIGH — Coverage gate targets the test package, not implementation modules

**Location:** Global Constraints — *"Hermetic modules (Tasks 1–2, 5–7 doc guards) run `pytest --cov=nextseek_api.cc_assistant.tests --cov-fail-under=95`"* (L29)

**Why:** Ultraplan default and sibling PLAN-3 pattern require ≥95% on **implementation modules** (`--cov=nextseek_api.cc_assistant.<module>`). Measuring `nextseek_api.cc_assistant.tests` only scores test-file line coverage. Tasks 3–4 (runtime/proxy port), 5 (`docker-compose.yml`), and 6 (`docker/nextseek.env.example`, `cc_engine` env boundary) are already excepted — but Tasks 1–2 and 5–7 still change production paths (`validate_step7_compose_deploy.py`, compose wiring, env templates, `DEPLOY.md`) with **zero enforced implementation coverage** under this command. A lazy implementer can ship broken validators/compose with green coverage.

**Fix:** Mirror PLAN-3: per hermetic task, run `--cov=nextseek_api.cc_assistant.tests.validate_step7_compose_deploy` (and any other pure modules) with `--cov-fail-under=95`; add the exact command to Tasks 1, 2, 5, 6, 7 success conditions.

---

### HIGH — MBP greenfield procedure omits required host `/srv/dmac/users` bootstrap

**Location:** Task 9 Steps 0–5 (L349–373); compare `docker-compose.yml` L28 (`/srv/dmac/users:/dmac/users`) and current `DEPLOY.md` Phase B L67–68 (`mkdir -p /srv/dmac/users`; `chmod -R 777 /srv/dmac`)

**Why:** Root compose bind-mounts `/srv/dmac/users` for Container-CC Step 2 user trees. Current DEPLOY Phase B documents host prep; Task 9 documents `./startup.sh install` for external volumes but **never** host dir creation/permissions. `startup/` contains no `/srv/dmac` handling (verified via repo grep). On a clean MBP, Docker may auto-create the path with wrong ownership/mode, breaking uid-1001 scratch writes and failing the authoritative 7d forced-CC gate despite compose bring-up succeeding.

**Fix:** Add Task 9 Step 0b (or fold into Step 0): create `/srv/dmac/users` with documented permissions; record commands in generated evidence; extend Task 7 numbered procedure and doc guards to require this when the compose bind is present; add validator check that `preflight.json` or `meta.json` records host bootstrap commands.

---

### MEDIUM — `preflight.json` required by plan but absent from locked SPEC-7 §8 evidence contract

**Location:** Task 1 L82–88; Task 2 validator scope; vs `SPEC-7-compose-native-prod-deploy.md` §8 Required generated artifacts (L148–162)

**Why:** Authority hierarchy: locked design > plan. SPEC §8 lists 11 required artifacts; plan adds mandatory `preflight.json` with `step3_deploy_gate`. A cold implementer reconciling plan vs spec could omit `preflight.json` from MBP bundles while satisfying SPEC §8, yet fail plan Task 2 — or conversely skip gate fields believing SPEC is complete. Execution contract is internally inconsistent.

**Fix:** Escalate SPEC-7 §8 amendment (add `preflight.json` with required `step3_deploy_gate` schema) via `/ultraplan amend`, or explicitly state in Task 1 that plan extends §8 and validator MUST treat §8 + preflight as joint contract.

---

### MEDIUM — Task 7 doc guards are negative-only; volume bootstrap not enforced in success conditions

**Location:** Task 7 Step 1 L287–289 vs Step 2 L293; Task 7 success conditions L296–300

**Why:** Task 7 Step 2 documents `./startup.sh install` before compose up, but Step 1 doc guards only fail **forbidden** commands in the numbered procedure (Phase A/B sidecar, manual network create/connect). Success conditions require "Doc guard tests pass" — not that volume bootstrap or host prep appear. Implementer can write a 5-step DEPLOY matching SPEC §7 literally (pull → secrets → build → up → verifier) without startup.sh or `/srv/dmac` prep and still pass Task 7 if forbidden patterns are absent. Risk Register #2 remedy ("Document `./startup.sh install` in DEPLOY") is not wired into falsifiable success conditions.

**Fix:** Extend Task 7 Step 1 doc guards: when `docker-compose.yml` declares `external: true` volumes, numbered procedure MUST include volume bootstrap (startup.sh or documented subset); validator fails if absent. Mirror for `/srv/dmac/users` when bind mount present.

---

## 2B — Stress test

### MEDIUM — `step3_deploy_gate.user_signoff_recorded` is self-attested boolean with no artifact oracle

**Location:** Task 1 L78 (`user_signoff_recorded: true`); Global Constraints L27 ("user sign-off")

**Likely failure:** Implementer sets `user_signoff_recorded: true` in generated JSON without `/handoff` report, user OK, or Task 13 evidence cross-reference.

**Catastrophic failure:** Step 7 starts on stale pre-Step-3 DEPLOY/compose state; merge collisions with PLAN-3 Task 13 deploy land in production docs.

**Fix:** Require `user_signoff_handoff_path` or `step3_live_evidence_run_id` pointing at `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/`; validator parses handoff JSON or live bundle for step 3 `done` + non-empty `live_gate_transcript.txt` containing Task 13 command markers (migrate, Playwright, tracker flip).

---

### MEDIUM — `live_evidence_path` not canonicalized; transcript content oracle is weak

**Location:** Task 1 L78–82 (`live_evidence_path` must exist; `live_gate_transcript.txt` non-empty); PLAN-3 Task 13 Step 8 (L1578–1579) pins `evidence/3-ui-based-io-live/`

**Likely failure:** Preflight collector accepts arbitrary directory with one-line fake transcript while tracker is manually `"done"`.

**Fix:** Pin expected relative path `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/` in Task 1; validator asserts transcript contains Task 13 markers (e.g. `migrate nextseek_api 0007`, `cc_runner_available`, Playwright/upload steps) not merely `len > 0`.

---

### MEDIUM — Hidden dependency: full stack required before `cc_runner_available()` and forced-CC

**Location:** Task 9 Step 3–4; `cc_engine.cc_runner_available()` (checks image + network only, not nextseek health); `docker-compose.yml` external volumes + host bind

**Likely failure:** Operator runs compose CC services only; `cc_runner_available()` passes but forced-CC fails because nextseek/db/seek not healthy — or compose up fails earlier on missing volumes/host paths.

**Fix:** Task 9 Step 3 success: record `docker compose ps` all required services healthy; validator checks `compose_services.txt` for nextseek/seek/db running before forced-CC artifact is accepted.

---

### MEDIUM — Coverage command not in per-task success conditions

**Location:** Global Constraints L29 vs Tasks 1, 2, 5, 6, 7 success-condition blocks (no `--cov-fail-under` mention)

**Coverage risk:** Implementer completes tasks when unit tests pass once; never runs coverage command. Declared 95% floor is aspirational only.

**Fix:** Add explicit coverage invocation to each hermetic task success condition (as PLAN-3 does globally + by module).

---

## 2C — Validate external dependencies

### MEDIUM — SPEC-7 §7 deploy bar still contradicts plan volume-bootstrap requirement

**Location:** `SPEC-7-compose-native-prod-deploy.md` §7 L131–135 (5 steps: pull → secrets → build → up → verifier); plan Task 9 Step 0 + Task 7 Step 2 (`./startup.sh install`)

**Why:** Verified: root `docker-compose.yml` L132–144 declares six `external: true` volumes; `./startup.sh install` creates them (`startup/steps/volumes.py`, README). SPEC §2 goal states deploy bar is git pull + secrets + `docker compose build && up` only. Plan correctly adds bootstrap but locked spec does not. Task 7 validator references "SPEC-7 §7 steps 1–5" — implementer cannot satisfy both literal SPEC §7 and clean-host reality without spec amendment.

**Fix:** Escalate SPEC §7 step 2 or 3 to include NExtSEEK-owned volume bootstrap; or `/ultraplan amend` narrowing §2 "compose-only" to "full stack including external volume prep via startup.sh."

---

### LOW (non-blocking) — Docker Compose multi-network dual-home

**Location:** Task 5; Dependency Validation L460

**Status:** Standard Compose v2 `networks:` list on services — OK. `cc_runner_available()` path verified at `cc_engine.py` L121–146.

---

### LOW (non-blocking) — Bedrock spend cap claimed but not in evidence schema

**Location:** Permissions L430 ("cap enforced"); SPEC §8 `meta.json` fields (no `budget_cap_usd`)

**Status:** `validate_cc_acceptance.py` uses `budget_cap_usd`; Step 7 SPEC §8/`forced_cc_result.json` do not. Must-verify at Task 2 implementation.

---

## 2D — Gameproof

### MEDIUM — Task 1 preflight: hand-crafted JSON passes hash + boolean gates

**Success condition (quoted):** *"A generated `preflight.json` exists and is valid JSON"* + *"`step3_deploy_gate` with all required fields; validator fails if step 3 is not `done`"* (L86–88)

**Cheapest fake:** Write `preflight.json` with correct file hashes, `tracker_step3_status: "done"`, `user_signoff_recorded: true`, and a fake `live_evidence_path` containing `"ok\n"`.

**No-op test:** Empty Step 7 branch with crafted preflight passes Task 1 if validator trusts JSON fields without re-reading `integration-plan.json` or validating transcript content.

**Remedy:** Validator MUST re-read `integration-plan.json` step 3 status at validation time; cross-check transcript against PLAN-3 Task 13 command inventory.

---

### MEDIUM — Task 9 MBP "no prior Container-CC state" gameable via pre-existing Docker volumes

**Success condition (quoted):** *"MBP evidence proves the host had no prior required Container-CC state"* (L377)

**Cheapest fake:** Reuse MBP with existing `seek-filestore` etc. from prior `./startup.sh install`; skip true greenfield; still pass compose up.

**Remedy:** Record `docker volume ls` / volume creation timestamps in evidence; validator fails if CC-relevant volumes predate run `meta.json` timestamp unless `greenfield_exception` documented.

---

### MEDIUM — G7-6 plugin snapshot fallback evidence not in validator artifact list

**Location:** Task 3 Step 3 (L161–162); SPEC §5 fallback evidence requirements; SPEC §8 artifact list

**Cheapest fake:** Commit stale plugin context snapshot with no generation-attempt record; Task 3 Step 3 says "generate evidence" but names no file in §8 or Task 2 validator checks.

**Remedy:** Add optional `plugin_context_provenance.json` to §8/Task 2 validator when snapshot path used.

---

### LOW — Task 9 forced-CC weaker than legacy `validate_cc_acceptance` BAML router check

**Success condition:** *"Forced CC turn completes with sentinel in reply"* (L380)

**Cheapest fake:** Direct `cc_engine` invocation bypassing UI/BAML (still valid for infra proof per SPEC §10).

**Remedy:** Accept as intentional infra gate; optionally record invocation path in `forced_cc_result.json`.

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log iter 3 row not yet filled (expected — this review).
- `integration-plan.json` step 7a description still says "cc-runner" while plan pins `docker/cc-runtime/` — cosmetic tracker prose drift; Task 10 only mutates status fields.
- `cc_runner_available()` error string still references `dmac's make image-build` — update during Task 3/5 doc pass.
- Typo in vetting log header: "Hardener (orchestrator) — **not a reviewer**" — fine.

---

## Finding counts

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 10 |
| LOW | 4 (non-blocking) |

---

## Verdict rationale

Phase 2 structural sections are complete and prior hardening (Step 3 deploy gate, startup.sh in Task 7 Step 2 text, `user_signoff_recorded` field, Task 5 realstack migration, gameability table) is real progress. However, as an execution contract the plan still has **two HIGH** defects (wrong coverage module; missing MBP host bootstrap) and **multiple MEDIUM** gameability/spec-alignment gaps that would let a lazy implementer pass local gates without honoring G7-7 greenfield intent or the Step 3 sequencing gate.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
