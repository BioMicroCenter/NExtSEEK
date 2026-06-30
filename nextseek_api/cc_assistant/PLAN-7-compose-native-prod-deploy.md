# Step 7 - Compose-native Prod Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make full Container-CC deployable from the NExtSEEK repo alone with only gitignored
operator secrets and `docker compose build && docker compose up -d`; prove it on the user's
local MBP with generated, secret-scanned evidence before dev merge.

**Architecture:** Port the actual Container-CC runtime/proxy source into NExtSEEK, while keeping
NExtSEEK's Django bridge as the only server path. Root compose owns `bedrock-proxy`, the CC
agent image build target, `dmac-cc-net`, and nginx dual-homing. A new Step 7 verifier produces
and validates a generated acceptance bundle under
`nextseek_api/cc_assistant/tests/acceptance_evidence/step7/<run_id>/`.

**Tech Stack:** Docker Compose v2, Docker BuildKit, Python 3.12/3.14 image tooling as required
by the ported runtime, Django host process, docker-py, pytest hermetic units via `uv run`,
Claude Code container runtime, generated JSON/text evidence validators.

## Global Constraints

- **Spec of record:** `nextseek_api/cc_assistant/SPEC-7-compose-native-prod-deploy.md`
  (locked decisions G7-1 through G7-9). Every task below traces to the spec.
- **This plan is for a future implementation session.** Do not treat this document as evidence
  that Step 7 is implemented.
- **Re-ground after Step 3 lands:** before touching files, read the then-current
  `SPEC-3-ui-based-io.md`, `PLAN-3-ui-based-io.md`, `docker-compose.yml`,
  `docker/nextseek.env.example`, `DEPLOY.md`, and any Step 3 evidence. Do not hardcode the
  current state of files Step 3 may edit.
- **TDD / validator-first:** where a behavior can be checked hermetically, write the failing
  test or validator before the implementation change.
- **Generated evidence only:** handwritten Markdown is an index at most. A task's success
  condition must be independently checkable from generated files, command output, or tests.
- **No committed shared secrets:** evidence bundles, screenshots, compose templates, Docker
  contexts, docs, and git status must be scanned before commit.
- **Security invariant:** the CC agent may receive only the logged-in user's own NExtSEEK login
  and password plus non-secret topology. Shared AWS/GCP/DB/backend credentials must remain out
  of reach. Bedrock token lives only in `bedrock-proxy`.
- **No prod deployment in Step 7:** 7d verifies on the user's local MBP before dev merge.
- **Per-change sign-off** before touching any running dev or MBP instance.

---

## File Structure

**Create / port**
- In-tree CC runtime directory (exact path chosen during implementation, e.g.
  `docker/cc-runtime/` or `dmac_assistant/runtime/`) containing the full agent-image runtime
  assets needed to build/run Container-CC.
- In-tree Bedrock proxy directory (e.g. `docker/bedrock-proxy/`) containing proxy source,
  Dockerfile, `.example` env, and tests/guards.
- `nextseek_api/cc_assistant/tests/validate_step7_compose_deploy.py` - zero-spend validator
  for generated Step 7 evidence.
- `nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py` - hermetic tests for compose
  parsing, env-template keys, build-context secret exclusions, and validator negative controls.
- `nextseek_api/cc_assistant/tests/acceptance_evidence/step7/.gitkeep` or README that explains
  generated bundles without serving as evidence.

**Modify**
- `docker-compose.yml` - compose-owned proxy, CC image build target, `dmac-cc-net`, nginx
  dual-homing.
- `docker/nextseek.env.example` - every required non-secret CC key.
- `.gitignore` / Docker ignore files - real proxy/env/evidence scratch secrets excluded.
- `nextseek_api/cc_assistant/DEPLOY.md` - authoritative pull/secrets/compose procedure.
- Top-level `README.md` / startup docs - pointer to the authoritative CC deploy doc.
- Existing CC env/security tests as needed to keep OI-3 coverage current.

---

### Task 1: Current-state preflight + Step 3 re-grounding

**Files:**
- Create/modify: `nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py`
- Future generated evidence: `acceptance_evidence/step7/<run_id>/preflight.json`

**Purpose:** Prevent the implementer from acting on stale planning-session file state.

- [ ] **Step 1: Write a preflight collector test/helper**

It must record: current branch/commit, dirty status, Step 3 doc commit state, compose services,
top-level compose networks, CC keys present in `docker/nextseek.env.example`, and whether
`DEPLOY.md` still contains old manual bootstrap commands.

- [ ] **Step 2: Add a validator assertion**

The Step 7 validator must reject an evidence bundle missing `preflight.json` or containing a
`preflight.json` without branch/commit and file hashes.

- [ ] **Success conditions**

- A generated `preflight.json` exists and is valid JSON.
- It includes hashes for `docker-compose.yml`, `docker/nextseek.env.example`, `DEPLOY.md`,
  `SPEC-3-ui-based-io.md`, and `PLAN-3-ui-based-io.md` if present.
- It clearly records any files changed by Step 3 that Step 7 will touch.
- The validator fails on a synthetic bundle with missing or hand-truncated preflight data.

- [ ] **Commit**

`git commit -m "test(cc-step7): add preflight reality snapshot guard"`

---

### Task 2: Define and test the generated evidence validator

**Files:**
- Create: `nextseek_api/cc_assistant/tests/validate_step7_compose_deploy.py`
- Create/extend: `nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py`

**Interfaces:**
- `validate_run(run_dir: str | Path) -> tuple[bool, list[tuple[str, bool, str]]]`
- CLI: `python -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir>`

- [ ] **Step 1: Write synthetic pass/fail bundles**

The tests should create temp bundles with the required artifact names from SPEC-7 §8.

- [ ] **Step 2: Implement the validator**

Checks must cover compose topology, image/service status, `cc_runner_available`, forced-CC
success, proxy invoke, network segmentation, agent env de-credentialing, proxy token logging,
and secret-scan pass.

- [ ] **Step 3: Add secret negative controls**

Synthetic evidence containing `AWS_BEARER_TOKEN_BEDROCK`, `Authorization: Bearer`, `ABSK`,
`GCP_API_KEY`, `MYSQL_PASSWORD`, `NEO4J_PASSWORD`, or `demopassword` must fail.

- [ ] **Success conditions**

- Hermetic tests pass with no Docker, DB, network, or spend.
- Validator rejects Markdown-only evidence.
- Validator rejects evidence with screenshots unless `secret_scan_report.json` records OCR or
  documented manual review for each screenshot.
- Validator output is deterministic and suitable for committing as `validator_output.txt`.

- [ ] **Commit**

`git commit -m "test(cc-step7): generated evidence validator and leak controls"`

---

### Task 3: Port the Container-CC runtime tree into NExtSEEK

**Files:**
- Create: in-tree CC runtime directory chosen by the implementer.
- Modify: image build references in compose/tests.

**Purpose:** Replace dependency on the standalone `dmac-assistant` checkout with tracked
NExtSEEK-owned runtime source.

- [ ] **Step 1: Write runtime presence tests**

Tests must fail until the NExtSEEK repo contains the runtime files required by the CC image:
Dockerfile, build ignore, `container/CLAUDE.md`, entrypoint, runner helpers, plugin manifest,
plugin skill/command/bin scripts, route config, and context/catalog files or generation tooling.

- [ ] **Step 2: Port the runtime assets**

Port broadly enough to build the full production-capable CC image. Exclude the old standalone
WS/FastAPI server layer as runnable infrastructure. If any old server files are copied for
reference, put them under a clearly non-runtime path and do not wire them into compose.

- [ ] **Step 3: Handle plugin context**

Attempt generation first. If blocked, commit a snapshot and generate evidence recording the
attempted generation command, blocker, source path/commit, and resulting context file inventory.

- [ ] **Success conditions**

- `docker build` for the CC image succeeds from a clean NExtSEEK checkout without the standalone
  `dmac-assistant` repo.
- Tests fail if `CLAUDE.md`, plugin manifest, plugin `SKILL.md`, plugin bin scripts, or compact
  context files are missing.
- `rg`/tests prove the old standalone WS/server layer is not imported or started by root compose.
- Build context excludes known secret filenames such as real proxy env files.

- [ ] **Commit**

`git commit -m "feat(cc-step7): port Container-CC runtime into NExtSEEK"`

---

### Task 4: Port Bedrock proxy source into NExtSEEK

**Files:**
- Create: in-tree proxy source/Dockerfile/env example.
- Modify: `.gitignore` and/or Docker ignore files.
- Test: `test_step7_compose_deploy.py`

- [ ] **Step 1: Write proxy file/build-context guards**

Tests must assert the proxy source, Dockerfile, and `.example` env exist, while real secret env
files are gitignored and excluded from Docker build context.

- [ ] **Step 2: Port proxy source**

Port the hardened proxy app with its allowlist, body cap, redacting logger, healthcheck, and
runtime token injection.

- [ ] **Success conditions**

- Proxy image builds from NExtSEEK-owned source.
- Proxy service exposes no host `ports:`.
- Real proxy secret file is not tracked and is excluded from build context.
- Tests/grep fail if a Dockerfile copies the real secret file.

- [ ] **Commit**

`git commit -m "feat(cc-step7): port Bedrock proxy into NExtSEEK compose source"`

---

### Task 5: Wire root compose topology

**Files:**
- Modify: `docker-compose.yml`
- Test: `test_step7_compose_deploy.py`

- [ ] **Step 1: Write compose-config tests**

Parse `docker compose config` output and assert services/networks rather than relying only on
source-text grep.

- [ ] **Step 2: Update compose**

Add compose-owned `bedrock-proxy`, the CC image build target, `dmac-cc-net`, and nginx
dual-homing. Preserve existing service names unless a tested migration updates all references.

- [ ] **Success conditions**

- `docker compose config` includes `bedrock-proxy`, the CC image build target, and
  `dmac-cc-net`.
- `nextseek_nginx` is attached to both the default stack network and `dmac-cc-net`.
- Backend services (`db`, `seek`, `seek_workers`, `solr`, `neo4j`) are not attached to
  `dmac-cc-net`.
- The primary deploy path requires no manual `docker network create` or
  `docker network connect`.

- [ ] **Commit**

`git commit -m "feat(cc-step7): make Container-CC topology compose-native"`

---

### Task 6: Extend env template and preserve secret boundary

**Files:**
- Modify: `docker/nextseek.env.example`
- Modify/extend: CC env/security tests

- [ ] **Step 1: Write env-template tests**

Assert every required non-secret CC key is documented and no real secret-like value is present.

- [ ] **Step 2: Add keys**

Document `NEXTSEEK_CC_IMAGE`, `NEXTSEEK_CC_NETWORK`, `DMAC_BEDROCK_PROXY_URL`,
`DMAC_ROUTER_ENABLED`, `DMAC_ROUTE_CAPABILITIES_FILE`,
`DMAC_ROUTER_MODEL_CLASS_MAP_FILE`, `DMAC_USER_ROOT`, `DMAC_USER_ROOT_MOUNT`,
`NEXTSEEK_SERVER`, and any additional Step 3/7 keys discovered during re-grounding.

- [ ] **Step 3: Re-run agent env leak tests**

Ensure only the logged-in user's own NExtSEEK credentials and non-secret topology can reach the
agent.

- [ ] **Success conditions**

- Env-template tests pass.
- Existing `test_cc_engine_env.py` or equivalent still proves shared AWS/GCP/DB/backend creds
  do not reach the agent.
- Secret scanner negative controls fail as expected.

- [ ] **Commit**

`git commit -m "docs(cc-step7): document compose-native CC env contract"`

---

### Task 7: Rewrite deploy docs and top-level pointers

**Files:**
- Modify: `nextseek_api/cc_assistant/DEPLOY.md`
- Modify: `README.md` and/or startup docs only as pointers.
- Test: doc grep guard in `test_step7_compose_deploy.py`

- [ ] **Step 1: Write doc guards**

Tests must fail if the required deploy path reintroduces separate `dmac-assistant` repo build,
manual `docker network create`, manual `docker network connect`, or Phase A/B sidecar bootstrap
as required steps.

- [ ] **Step 2: Rewrite docs**

Document the authoritative path: pull/clone -> fill gitignored config -> `docker compose build`
-> `docker compose up -d` -> run Step 7 verifier.

- [ ] **Success conditions**

- `DEPLOY.md` names only gitignored files for secrets.
- Old manual sidecar bootstrap is retired from the required path.
- Top-level docs point to `DEPLOY.md` instead of duplicating stale instructions.
- Doc guard tests pass.

- [ ] **Commit**

`git commit -m "docs(cc-step7): replace manual sidecar bootstrap with compose deploy path"`

---

### Task 8: Local compose build/up verification on the dev VM

**Files/evidence:**
- Generated local evidence bundle under `acceptance_evidence/step7/<run_id>/`.

- [ ] **Step 1: Build and start locally with per-change sign-off**

Run root compose build/up for the affected services on the dev VM only after sign-off.

- [ ] **Step 2: Generate evidence**

Capture compose config, image ids, service status, network inspect, and
`cc_runner_available()`.

- [ ] **Step 3: Run validator**

Run the Step 7 validator against the generated local bundle.

- [ ] **Success conditions**

- `cc_runner_available()==(True, "ok")`.
- Network evidence shows only proxy/nginx plus transient CC agents on `dmac-cc-net`; no backend
  service peers.
- Secret scan passes for all generated evidence.
- Validator passes.

- [ ] **Commit**

`git commit -m "test(cc-step7): record local compose-native verification evidence"`

---

### Task 9: MBP greenfield verification before dev merge

**Files/evidence:**
- Generated MBP evidence bundle under `acceptance_evidence/step7/<run_id>/`.

**Purpose:** Satisfy tracker substep 7d without deploying prod.

- [ ] **Step 1: Prepare clean MBP state**

Use a fresh clone or clean checkout of the Step 7 branch. Do not use this dev VM and do not use
prod.

- [ ] **Step 2: Fill only gitignored local config**

Use tracked examples as templates. Do not copy from the standalone `dmac-assistant` repo.

- [ ] **Step 3: Compose bring-up**

Run `docker compose build && docker compose up -d`.

- [ ] **Step 4: Forced-CC acceptance**

Run one forced CC turn with a per-run sentinel through the compose-owned proxy.

- [ ] **Step 5: Evidence and validator**

Generate the required evidence bundle, scan text/JSON/log/env artifacts, scan or manually review
any screenshots, and run the Step 7 validator.

- [ ] **Success conditions**

- MBP evidence proves the host had no prior required Container-CC state.
- No standalone `dmac-assistant` repo is required.
- `cc_runner_available()==(True, "ok")`.
- Forced CC turn completes with sentinel in reply.
- Proxy log window contains allowed-model invoke success and no token/header leak.
- Agent env scan contains no shared AWS/GCP/DB/backend creds.
- `dmac-cc-net` excludes backend services.
- `secret_scan_report.json` passes, including screenshot review if screenshots exist.
- Step 7 validator passes on the MBP bundle.

- [ ] **Commit**

`git commit -m "test(cc-step7): add MBP greenfield compose verification evidence"`

---

### Task 10: Tracker update and handoff

**Files:**
- Modify: `/home/taishajo/work/state/integration-plan.json` status fields only.
- Create: structured handoff report via `/home/taishajo/work/state/handoff.sh`.

- [ ] **Step 1: Update tracker statuses**

Only after Tasks 1-9 pass, mark Step 7/substeps done according to the tracker protocol.

- [ ] **Step 2: Write handoff**

Use the shared handoff system. Include generated evidence bundle paths and validator command
outputs.

- [ ] **Success conditions**

- `integration-plan.json` remains valid JSON and only permitted fields changed.
- Handoff distinguishes user-stated decisions from agent inferences.
- Handoff records verification status honestly and points to generated evidence, not prose.

- [ ] **Commit**

Commit tracker/doc/evidence changes according to the active branch policy at implementation time.
