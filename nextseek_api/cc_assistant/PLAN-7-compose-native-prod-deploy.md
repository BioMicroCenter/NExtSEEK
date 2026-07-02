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
  (locked decisions G7-1 through G7-10). Every task below traces to the spec.
- **This plan is for a future implementation session.** Do not treat this document as evidence
  that Step 7 is implemented.
- **Re-ground after Step 3 is fully deployed (HARD GATE):** Step 7 implementation **MUST NOT start** until Step 3 Task 13 live verification passes, is deployed on the dev instance, and `integration-plan.json` step **3** is `done` with user sign-off. Reading updated SPEC-3/PLAN-3/DEPLOY files alone is **insufficient**. Task 1 preflight must record `step3_deploy_gate` (see Task 1).
- **Branch ancestry (locked):** Step 7 work branches from **`feat/dmac-assistant-full-integration` at the commit where PLAN-3 `cc-step3-ui-io` has merged back** and `live_gate_transcript.txt` is committed on that branch. Do **not** branch Step 7 from pre-merge `feat/…` or from an unmerged `cc-step3-ui-io` tip unless that tip already contains the committed transcript and will merge without rewriting gate SHAs.
- **DEPLOY.md merge order:** PLAN-3 Task 13 may append Step-3 deploy notes first. Step 7 Task 8 rewrites Phase A/B only on the **post-Step-3-deployed** commit hash recorded in preflight — never on stale pre-deploy docs.
- **Coverage targets (Phase 2 hardened):** Hermetic implementation modules run `--cov=nextseek_api.cc_assistant.tests.validate_step7_compose_deploy --cov-fail-under=95`. The **behavior-bearing Task 6 mount refactor is covered explicitly** (not left to the validator floor), but **rescoped to the pure code Task 6 actually adds/edits**: `cc_engine.py` is **not** whole-module gated because it is a pre-existing *procedural* module that also holds the **live-only `run_cc_turn`** (`cc_engine.py:398–607` — ~31% of the 672-line module; called **only** by the live `test_cc_realstack.py`, never hermetically) and the `cc_runner_available` live `client.networks.get` branch, so a whole-module `--cov=cc_engine --cov-fail-under=95` is unreachable within this task's surgical scope (same situation as PLAN-3 Task 5 `translate`). It splits into three honest pieces:
  - **Reachable hermetic floor (gated, commit-blocking):** run `--cov=nextseek_api.cc_assistant.cc_config --cov-fail-under=95` over the Task 6 hermetic `cc_config` tests — run `test_cc_config_paths.py` **and** `test_cc_memory_config.py` together so the whole pure module reaches the floor (**measured 100% pre-refactor** at vetting time — read as the current-`cc_config.py` baseline, not a post-refactor guarantee; the `--cov-fail-under=95` floor stays commit-blocking, so any Step 3 drop forces the implementer to add tests). `cc_config.py` is the pure module Task 6 Step 3 refactors; this floor is genuinely producible and **commit is blocked below it**.
  - **`cc_engine` pure mount helpers (surfaced, not whole-module gated):** run `--cov=nextseek_api.cc_assistant.cc_engine --cov-report=term-missing` with **no `--cov-fail-under`** (informational only, like PLAN-3 Task 5) to surface helper coverage. The pure mount-assembly logic (`_mount_volume_subpath`, `_build_volumes`, `_run_kwargs`, **including the per-user `Subpath`-value branches**) is proven by the Task 6 Step 1/2 **concrete per-user `Subpath`-value hermetic assertions + anti-empty negative control** — not by a whole-module percentage. **Both nets are real and commit-blocking — neither is assumed:** (1) the `cc_config` floor above is gated at `--cov-fail-under=95` (commit blocked below it); (2) the behavior-bearing mount logic is gated by the Task 6 Step 1/2 hermetic tests, which are **ordinary failing-then-passing tests whose RED blocks the commit** (not informational) — they assert the *exact* `Subpath` string of **every** sibling mount in the spawn set: `input → "proj/alice/input"`, `shared → "proj/shared"` (**project-scoped — no `{user_id}` segment**), `scratch → "proj/alice/scratch"`, `cc-state → "proj/alice/cc-state/sess"`, `transcripts → "proj/alice/_memory/sess/transcripts"`, and a **per-mount anti-empty/anti-constant negative control fails** any mount (including `shared`) whose `Subpath` is `""`, `"/"`, the volume root, or any constant ≠ that mount's exact expected value — **not** a blanket "must contain `{project_dirname}/{user_id}/`" rule, which would falsely fail the legit project-scoped `shared` value (Task 6 Step 1 "Per-mount Subpath VALUE isolation"). The `--cov-report=term-missing` run is the *only* informational piece here (line surfacing), and it is **not** the isolation net — the value assertions are. The live §8 `subpath_isolation_scan.txt`/`pre_turn_seed_scan.txt` gate (Tasks 9/10) covers the procedural `run_cc_turn` spawn seam hermetic tests cannot reach. The two hermetic nets plus the live gate are independent and each enforced.
  - **`run_cc_turn` runtime mount/spawn behavior (justified, non-deferrable live-gate exception):** covered by the **live realstack gate** — the REQUIRED `subpath_isolation_scan.txt` §8 cross-user-isolation gate + forced-CC turn (Tasks 9/10). This is the procedural seam hermetic tests cannot reach; the live gate is mandatory and not skippable, so the floor is **rescoped, not deleted**, and **no blanket `# pragma: no cover` is applied to `run_cc_turn`** (cc_engine carries no coverage pragmas).

  Tasks 3–4, 9–10 (Docker/live) use **justified exceptions** — acceptance is generated evidence + Step 7 validator (G7-8).
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
- **`docker/cc-runtime/`** (canonical build target — **not** `docker/cc-runner/Dockerfile`, which is an explicitly non-production lean proof image) containing the full agent-image runtime assets needed to build/run Container-CC.
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
  dual-homing, and **`dmac-cc-users` named volume** (replaces host bind `/srv/dmac/users`).
- `startup/steps/volumes.py` - add `dmac-cc-users` to `REQUIRED_VOLUMES`.
- `nextseek_api/cc_assistant/cc_config.py`, `cc_provision.py`, `cc_engine.py`, **`cc_sweep.py`**, **`nextseek_api/services/cc_assistant.py`** — volume-backed
  CC user trees with subpath mounts; refactor `_run_kwargs` for `mounts=` API; retire `host_user_root` path translation in 1c memory block.
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

It must record: current branch/commit, dirty status, **`step3_deploy_gate`**:
- `integration_plan_path`: resolved at collection time from env **`INTEGRATION_PLAN_PATH`** (default: repo-relative `../state/integration-plan.json` from NExtSEEK root — **never** hardcode a user home path). Validator reads the path **from `preflight.json`**, not a baked-in absolute.
- `tracker_step3_status` must be `"done"` — validator re-reads the integration plan file at `integration_plan_path` at validation time.
- Record `integration_plan_sha256` of the file at collection time. Validator **rejects arbitrary tracker files inside the evidence bundle** — **except** MBP-only: when `meta.json.host_label` matches MBP pattern, allow `integration_plan_path` == `<run_dir>/integration_plan_snapshot.json` only if (a) basename is exactly `integration_plan_snapshot.json`, (b) `canonical_integration_plan_sha256` is recorded (from monorepo handoff or `../state/` read at gate time), and (c) snapshot SHA matches `integration_plan_sha256`.
- `live_gate_transcript_committed`: **bool** — collector sets `true` only after `git cat-file -e ${deploy_commit}:nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt` succeeds.
- `deploy_commit`: full SHA of `HEAD` at preflight collection on the branch under test (validator re-checks transcript at this exact SHA).
- `user_signoff_handoff_path`: path to a supplementary handoff report JSON (SRS fields) — **not** a substitute for committed live transcript.
- `live_evidence_path` (**required**): must be `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/` with non-empty **`live_gate_transcript.txt` committed on the branch under test** (PLAN-3 Task 13 Step 9 — secret-scanned). Validator fails if path missing, file empty, or not in git at `deploy_commit`. **User decision (2026-06-30):** handoff-only fallback **rejected**.
- `pre_step3_snapshot_tag`, `docker_engine_meets_subpath_floor` (bool), `docker_compose_meets_subpath_floor` (bool, Compose plugin ≥2.26), `canonical_integration_plan_sha256`. (`deploy_commit` is defined above — not repeated here.)
- `had_host_bind_data` (**bool**) — `true` iff the host has pre-cutover `/srv/dmac/users` data to migrate (detect via host-bind path existence / preflight hash). Governs the conditional `migration_policy` requirement on dev-VM bundles (Tasks 2, 6 Step 5, 9 Step 2); `false` on greenfield hosts.
- `port_source_path`, `port_source_commit` — the `dmac-assistant` working clone the Task 3 runtime port is taken from, pinned at preflight (see Task 3).

**Note:** `preflight.json` is a **locked** SPEC-7 §8 required artifact (amended 2026-06-30).

- [ ] **Step 2: Add a validator assertion**

The Step 7 validator must reject an evidence bundle missing `preflight.json`, missing branch/commit/file hashes, or **missing/failing `step3_deploy_gate`** (tracker step 3 must be `"done"` at resolved `integration_plan_path`; **committed** `live_gate_transcript.txt` must exist on branch at `deploy_commit`; handoff JSON is supplementary).

Also record: Step 3 doc commit state, compose services, top-level compose networks, CC keys in `docker/nextseek.env.example`, whether `DEPLOY.md` still contains old manual bootstrap commands, `docker version` / `docker info` summary, **`docker compose version`** (Compose ≥2.26 if compose YAML uses volume subpaths), and Task 6 subpath floor (`docker_engine_meets_subpath_floor`).

- [ ] **Success conditions**

- A generated `preflight.json` exists and is valid JSON.
- It includes hashes for `docker-compose.yml`, `docker/nextseek.env.example`, `DEPLOY.md`, `SPEC-3-ui-based-io.md`, and `PLAN-3-ui-based-io.md` if present.
- It includes `step3_deploy_gate` with all required fields; validator fails if step 3 is not `done`.
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

Checks must cover **preflight.json** + **`step3_deploy_gate`** (tracker SHA, **`live_gate_transcript_committed == true`**, MBP snapshot exception rules, supplementary handoff must parse SRS fields and cite step 3 `done` or match `integration_plan_sha256`), **`preflight.deploy_commit == meta.json.repo_commit`** (both collected in same `<run_id>` bundle — Task 9/10 must regenerate preflight immediately before other §8 artifacts), **live transcript content markers** (non-empty plus allowlist substrings **guaranteed to appear in real PLAN-3 Task 13 stdout** — not command-text the transcript is not contracted to echo. **Byte-identical allowlist, shared verbatim with PLAN-3 Task 13 Step 8 — both sides MUST name the same strings:** (1) the **migration marker**, accepted as **`Applying nextseek_api.0007` OR `[X] 0007_ccsessiontranscript`** (at least one required — grep `Applying nextseek_api\.0007|\[X\] 0007_ccsessiontranscript`): the first is Step 3 fresh-migrate stdout (`migrate nextseek_api 0007_ccsessiontranscript` → `Applying nextseek_api.0007_ccsessiontranscript… OK`), the second is `python manage.py showmigrations nextseek_api` stdout that prints `[X] 0007_ccsessiontranscript` on **any already-applied DB**; this OR is **idempotency-robust** — a legitimate re-run whose `migrate` prints `No migrations to apply.` still passes via the `showmigrations` form (PLAN-3 Task 13 Step 8 is contracted to capture `showmigrations nextseek_api` stdout for exactly this reason), so an already-deployed instance's committed transcript can never permanently fail this gate. (2) `cc_assistant.upload` (Step 0 `inspect registered | grep cc_assistant.upload` output / registered-task name — present on any worker that registered the task, idempotent). (3) `cc_traces` (the JSON key in Step 6's saved `GET …?include=turns` excerpt) — not stub-only. Do **not** require the *command* substrings `migrate nextseek_api 0007` or `inspect registered`: PLAN-3 Task 13 Step 8 contracts only saved **stdout/stderr + exit codes**, not echoed command lines, so a legitimate already-committed transcript may omit them and a hard start-gate must not falsely reject it. Exit codes are saved but PLAN-3 leaves their format unpinned, so do **not** hard-require an `exit-code` substring), **MBP greenfield:** parse `pre_bootstrap_docker_volume_ls.txt` — fail if any of the seven external volumes exist unless `meta.json.greenfield_exception` + handoff ref. **Volume name resolution:** read `startup/.instance.json` `prefix` when present — expected names are `{prefix}seek-filestore`, …, `{prefix}dmac-cc-users` (empty prefix = bare names as listed). Task 10 Step 0: require default instance (no `--instance` prefix) **or** document prefix-adjusted oracle in validator; parse `pre_bootstrap_docker_network_ls.txt` — fail if `dmac-cc-net` exists unless exception. **`meta.json.host_label` (locked enum):** MBP authoritative gate → **`"mbp"`** (exact string; Task 10 must write this literal; validator exact-match, not regex). Dev-VM smoke → **`"dev-vm"`** or **`"nextseek-dev"`** only. Reject all other values. **Independently parse** `preflight.json` docker version strings and fail when Engine <26 / API <v1.45 even if the boolean flag claims true; fail when `preflight.json.docker_engine_meets_subpath_floor` is not `true`. **Engine ≥26 / API v1.45 is the unconditional, real floor** — the per-user runtime subpaths are applied via **docker-py (Engine API) `VolumeOptions.Subpath`**, not compose YAML (Task 6 `_mount_volume_subpath`), so Engine — not Compose — gates the isolation mount. The **Compose ≥2.26 floor is CONDITIONAL**: enforce it (and require `docker_compose_meets_subpath_floor == true`) **only when `compose_config.json` shows the compose YAML itself uses `volume.subpath:` syntax**. Tasks 5/6 mount the **whole** `dmac-cc-users` volume at `/dmac/users` with **no** YAML subpath syntax, so Compose ≥2.26 is **not** hard-required for them — **unless the G7-11 sidecar staging mount (Task 14) uses YAML `subpath:` syntax in compose, in which case the conditional floor BINDS on every deploy host** (iter-1 M-3 amendment; the check mechanism already self-detects this via `compose_config.json`). A valid host whose compose file never uses subpath syntax MUST NOT be rejected on the Compose floor alone (reconciled with the conditional DEPLOY Step 0 / Task 1 Step 2 wording). Compose topology, image/service status, `cc_runner_available`, forced-CC success, **`forced_cc_result.json.cost <= meta.json.budget_cap_usd`** (default 2.0), proxy invoke, network segmentation, agent env de-credentialing, proxy token logging, **cross-artifact correlation** (`run_id` in proxy log; agent container in network inspect), dev-only **`migration_policy`** required **iff** `host_label` is `dev-vm` or `nextseek-dev` **AND** `preflight.json.had_host_bind_data == true` (greenfield dev-VM with no prior `/srv/dmac/users` data: `migration_policy` is **optional** and the bundle must still pass — matches Task 9 Step 2 / Task 6 Step 5; forbidden when `host_label == "mbp"`), **`pre_turn_seed_scan.txt`** present, non-empty, and **containing every `meta.json.foreign_token`** (`SENTINEL_FOREIGN`, `otherproj`, `bob`) — the harness-written root-mounted volume listing taken **after seeding the foreign tree, before the turn** (Task 10 Step 4). This proves the foreign subtree exists at the volume root, so the foreign-absent oracle below is **not vacuous**: a skipped seed step leaves this scan missing the tokens ⇒ **RED before the turn**, which is what stops an unseeded `Subpath=""` whole-volume leak from passing green. **`subpath_isolation_scan.txt`** present, non-empty, and passing the **seed-gated foreign-absent oracle** (REQUIRED §8 cross-user isolation gate — Task 6/Task 10): the scan is a recursive `find … -maxdepth 4` listing **captured during the turn from the live sibling** (see Task 10 Step 4 — the agent is force-removed at turn end, so post-turn `docker exec` is impossible), and the validator MUST confirm it (a) **contains the own marker filename** (`meta.json.own_marker`, i.e. `OWN_<run_id>` under the user's own `/data/input` subpath — a hand-written empty stub fails); (b) **contains the live in-container sentinel filename** (`meta.json.live_sentinel`, i.e. `LIVE_<sentinel>` written by the agent into its own `/data/scratch` *during* the turn — see Task 10 Step 4; this is an **anti-stale / anti-substitution** binding that ties the scan to **this** run's live container so a stale/blank scan reused from a different (clean) run cannot be substituted, and a hand-edited clean scan that omits the agent-authored live sentinel fails this cross-check — it is **not** a leak detector: the agent writes it to container path `/data/scratch`, which the `find` lists under both correct and leaking mounts); and (c) **contains none** of `meta.json.foreign_tokens` via pinned `grep -nE 'SENTINEL_FOREIGN|(^| |/)otherproj(/|$| )|(^| |/)bob(/|$| )'` (any match ⇒ fail). **The actual leak detector is the pair (pre-turn foreign-present) ∧ (in-turn foreign-absent)** — neither alone suffices. Writing `subpath_isolation_scan.txt` directly from the live `docker exec … find` subprocess stdout (Task 10 Step 4) is a **harness-implementation requirement** (a text-file validator cannot prove file authorship); the validator's enforceable guarantees are the seed-scan/in-turn-scan pair plus the live-sentinel and own-marker cross-checks, and that `meta.json.foreign_tokens`, `meta.json.own_marker`, and `meta.json.live_sentinel` are pairwise disjoint. The old non-recursive `ls` + slash-bearing `{project}/{user}/` matcher is rejected — it shows only top-level project names and cannot surface the empty/root-`Subpath` leak. And secret-scan pass.

**`host_label` branching table:** MBP → require pre-bootstrap volume absent + snapshot rules; dev-VM → require `migration_policy` **only when `preflight.json.had_host_bind_data == true`** (greenfield dev-VM: optional); monorepo dev → default `integration_plan_path` outside bundle.

- [ ] **Step 3: Add secret negative controls**

Synthetic evidence containing `AWS_BEARER_TOKEN_BEDROCK`, `Authorization: Bearer`, `ABSK`,
`GCP_API_KEY`, `MYSQL_PASSWORD`, `NEO4J_PASSWORD`, `demopassword`, Django `SECRET_KEY` values,
and unredacted `NEXTSEEK_PASSWORD` (per SPEC-7 §9) must fail.

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
- Create: `docker/cc-runtime/` (canonical — not `docker/cc-runner/`)
- Modify: image build references in compose/tests.
- **Port source (canonical):** the `dmac-assistant` working clone at **`/home/taishajo/work/dmac-assistant`** (per project guide `CLAUDE.md`, which also documents the `NEXTSEEK_SERVER=gunicorn|daphne` entrypoint toggle that the ported runtime must preserve). Task 1 preflight pins this as `port_source_path` + `port_source_commit`; do **not** hardcode a different path.

**Purpose:** Replace dependency on the standalone `dmac-assistant` checkout with tracked
NExtSEEK-owned runtime source.

- [ ] **Step 1: Write runtime presence tests**

Tests must fail until `docker/cc-runtime/` contains the runtime files required by the CC image:
Dockerfile, build ignore, `container/CLAUDE.md`, entrypoint, runner helpers, plugin manifest,
plugin skill/command/bin scripts, route config, and context/catalog files or generation tooling.
**Explicit negative test:** compose CC image build context must **not** be `docker/cc-runner/Dockerfile` (lean proof image — violates G7-3).

- [ ] **Step 2: Port the runtime assets**

Port broadly enough to build the full production-capable CC image into **`docker/cc-runtime/`**, **from the `dmac-assistant` working clone recorded in preflight `port_source_path`/`port_source_commit`** (default `/home/taishajo/work/dmac-assistant` per project guide). Exclude the old standalone WS/FastAPI server layer as runnable infrastructure. If any old server files are copied for
reference, put them under a clearly non-runtime path and do not wire them into compose.

**Boundary (locked):** `docker/cc-runtime/` = CC **container image** assets only. The in-tree **`dmac_assistant/`** Python package (root `pyproject.toml` path dep) remains the Django-side dependency for router BAML, `run_tracker.diff_files`, etc. — distinct from the external `dmac-assistant` repo checkout on deploy hosts (G7-2). Do not duplicate or delete `dmac_assistant/` modules unless a named sub-step ports a specific module into `docker/cc-runtime/`.

- [ ] **Step 3: Handle plugin context**

Attempt generation first — **discover the generation entrypoint from the `dmac-assistant` plugin build tooling at `port_source_path` and record the exact command attempted in evidence**. If blocked, commit a snapshot and generate evidence recording the
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
- Modify: `nextseek_api/cc_assistant/cc_engine.py` — `cc_runner_available()` messages
- Test: `test_step7_compose_deploy.py`

- [ ] **Step 1: Write compose-config tests**

Parse **`docker compose -f docker-compose.yml config`** subprocess output (not a hand-edited golden fixture) and assert services/networks.

- [ ] **Step 2: Update compose**

Add compose-owned `bedrock-proxy`, the CC image build target (`docker/cc-runtime/`), `dmac-cc-net`, and nginx
dual-homing. Pin `services.bedrock-proxy.container_name: dmac-bedrock-proxy` (or document `DMAC_PROXY_CONTAINER` in env template) so migrated realstack tests align.

- [ ] **Step 3: Update legacy acceptance tests**

Update or deprecate with enforced grep guards: `test_cc_realstack.py`, `validate_cc_acceptance.py` — replace external `dmac-bedrock-proxy` assumptions with compose service DNS **`bedrock-proxy`** for in-network URLs; keep host-side log/inspect on container name **`dmac-bedrock-proxy`** (`PROXY_CONTAINER` / `DMAC_PROXY_CONTAINER`). Do **not** conflate service name and container name in one global replace.

**Publish-scope migration (required):** update `validate_cc_acceptance.py` check 16 / copier-scope oracle from flat `{user_id}/` prefixes to nested `{project}/{user}/` or `logical_root` prefix (Task 6 `path_mappings`). Align `test_cc_realstack.py` artifact capture with turn-scoped `artifacts` keys (SPEC-7 §8 names where reused). Hermetic negative control: pre-nested `{user_id}/`-only patterns must fail validation.

Update `cc_runner_available()` detail strings to cite `docker compose build` + `NEXTSEEK_CC_IMAGE` (no standalone `make image-build` / manual sidecar bring-up). Hermetic test: assert detail strings exclude `make image-build`.

- [ ] **Success conditions**

- Compose CC image tag matches `NEXTSEEK_CC_IMAGE` default (`dmac-assistant:poc`) or env template documents override; hermetic compose-config test asserts tag alignment (required for `cc_runner_available()`).
- `docker compose config` includes `bedrock-proxy`, the CC image build target, and `dmac-cc-net`.
- `nextseek_nginx` is attached to both the default stack network and `dmac-cc-net`.
- Backend services (`db`, `seek`, `seek_workers`, `solr`, `neo4j`) are not attached to
  `dmac-cc-net`.
- **`nextseek` itself must NOT be attached to `dmac-cc-net`** — only `nextseek_nginx` is dual-homed (OI-3 segmentation).
- The primary deploy path requires no manual `docker network create` or
  `docker network connect`.

- [ ] **Commit**

`git commit -m "feat(cc-step7): make Container-CC topology compose-native"`

---

### Task 6: Named Docker volume for CC user trees (user decision — Option A; **SPEC G7-10**)

**User decision (2026-06-30):** Persist Step 2 per-user trees (`input/`, `scratch/`, `cc-state/`,
`output/`) in a **named external Docker volume** (`dmac-cc-users`), same pattern as
`seek-filestore` — **not** a host bind under `/srv/dmac/users`. Data must survive
`docker compose up` / container recreate (lost only on explicit volume removal, e.g.
`docker compose down -v` or `startup.sh reset`).

**Files:**
- Modify: `docker-compose.yml` — replace `- /srv/dmac/users:/dmac/users` with
  `dmac-cc-users:/dmac/users` (`external: true` in `volumes:`).
- Modify: `nextseek_api/cc_assistant/cc_config.py`, `cc_provision.py`, `cc_engine.py`, `cc_sweep.py`, **`nextseek_api/services/cc_assistant.py`**
- Modify: `startup/steps/volumes.py` — add `dmac-cc-users` to `REQUIRED_VOLUMES`;
  extend `startup/tests/test_volumes.py`.
- Modify: `docker/nextseek.env.example` — document `DMAC_CC_USERS_VOLUME`; remove
  `/srv/dmac/users` host-root guidance.
- Keep **`docker>=7.1.0`** in **`dmac_assistant/pyproject.toml`** (the path dep that pins docker today — root `pyproject.toml` only path-depends on `dmac-assistant`; do **not** add a divergent root-level `docker` pin). **`docker>=7.1.0` is already present** (`dmac_assistant/pyproject.toml`) — no version change; regenerate the lockfile only if other deps change in this task. PyPI latest is **7.1.0** — **no** `Mount.subpath` kwarg. **Primary path:** `_build_volumes` emits Engine-API PascalCase mount dicts with `VolumeOptions.Subpath` until a PyPI release ships PR #3270; record must-verify release tag at execution — **do not pin `docker>=7.2.0`** (unsatisfiable).
- Modify/extend: `test_cc_engine_volumes.py`, `test_cc_engine_memory_mounts.py`, `test_cc_provision_paths.py`, `test_cc_provision_input_mnt.py`, `test_cc_config_paths.py`, `test_cc_engine_publish.py`, `test_cc_upload_validate.py`, `test_cc_upload_list.py`, `cc_upload_tasks.py`, `test_step7_compose_deploy.py`.

**Purpose:** Align CC persistence with NExtSEEK's compose-native deploy bar (no manual host
`mkdir`/`sudo`/`chmod` for operator trees). Greenfield bootstrap is `./startup.sh install`
(or volume-create subset) only — same as the six existing external volumes.

- [ ] **Step 1: Write failing hermetic tests**

- Compose config test: `nextseek` service mounts named volume `dmac-cc-users` at `/dmac/users`; **no** host bind `/srv/dmac/users`.
- `startup/steps/volumes.py` test: `dmac-cc-users` in `REQUIRED_VOLUMES`.
- **docker-py subpath floor:** hermetic test asserts serialized mount payload uses Engine-API **PascalCase** keys (`Type`, `Source`, `Target`, `VolumeOptions` with nested `Subpath`) — fail on lowercase `volume_options` or missing `VolumeOptions`.
- **Per-mount Subpath VALUE isolation (REQUIRED — anti cross-user-leak / OI-3 gate):** the hermetic test asserts the **concrete `Subpath` string** of **every** sibling mount in the spawn set equals its **own explicit enumerated value** below (exact-string per-mount equality) — the per-user (or, for `shared`, per-project) tail relative to the `dmac-cc-users` volume root. There is **no single strip-prefix rule**: the values come from the real *per-source* layout in `build_user_dirs` (`cc_provision.py:99–109`), where `input_src`/`shared_src` are host paths with **no** `input_mnt`/`shared_mnt` field, `shared_src` is **project-scoped** (`shared_src=f"{project_host}/shared"`, no `{user_id}` segment), `scratch`/`cc-state` derive from `scratch_mnt`/`cc_state_mnt`, and transcripts is the `_memory/<session>` (`memory_mnt`) tail **plus** a `/transcripts` child. Post-G7-10 the refactored `build_user_dirs` exposes each tail directly via its `*_subpath` field (no `host_user_root`). For a representative user (`project_dirname="proj"`, `user_id="alice"`, `session_id="sess"`, `user_root_mount="/dmac/users"`):
  - input (RO, `/data/input`) → `mount["VolumeOptions"]["Subpath"] == "proj/alice/input"`  *(from `input_src = {user}/input`)*
  - shared (RO, `/data/shared`) → `"proj/shared"`  *(from `shared_src = {project}/shared` — **project-scoped**: NO `{user_id}` segment, by SPEC-2 D5; shared is shared across users within a project)*
  - scratch (RW, `/data/scratch`) → `"proj/alice/scratch"`  *(from `scratch_mnt`)*
  - cc-state (RW, `/home/user/.claude`) → `"proj/alice/cc-state/sess"`  *(from `cc_state_mnt`)*
  - transcripts (RO, `/home/user/.cc-memory/transcripts`) → `"proj/alice/_memory/sess/transcripts"`  *(from `memory_mnt` tail + `/transcripts`)*

  This is exactly the post-cutover `_build_volumes` spawn set (`cc_engine.py:380–394`) — input, shared, scratch, cc-state, transcripts — with the RO `user_memory_file` file bind **dropped** (Step 4 point 3, replaced by an RW byte-copy into the cc-state subpath); **no spawn-set mount is left un-enumerated**.
  **Negative control (per-mount anti-empty / anti-root / anti-constant):** for **each** enumerated mount *independently*, a `Subpath` that is `""`, `"/"`, the volume root, or any constant ≠ that mount's exact expected value above MUST **fail** the test — including `shared` (a `Subpath=""` or `Subpath="proj"` on `/data/shared` must fail just as it must on the user-scoped mounts). Do **not** gate on a blanket "Subpath must contain `{project_dirname}/{user_id}/`" rule: that rule would **falsely fail** the legitimately project-scoped `shared` value (`proj/shared`, which has no `{user_id}` segment) while leaving `shared` otherwise unguarded. The per-mount exact-equality assertion is the gate; asserting key presence only is insufficient. (Empty/root `Subpath` mounts the whole `dmac-cc-users` volume root into every agent → cross-user data leak — the one-line mutation that must never pass a green suite. Because `shared` is now enumerated, a `dirs.shared` `Subpath=""` mutation fails the **hermetic** suite, not only the live `find /data/shared` gate.)
- **`_run_kwargs` cutover:** failing test — `_run_kwargs(...)["mounts"]` present and `"volumes"` key **absent**; grep guard forbids `"volumes": volumes` in spawn path after cutover.
- Spawn contract test: mock/spy `containers.run` receives `mounts=` list, not host-path `volumes` dict keys.
- **Step 3 upload regressions:** `cc_upload_tasks` / `list_input_files` resolve paths via `*_mnt` only; update `test_cc_provision_input_mnt.py` for post-G7-10 `CCPaths`; grep-guard `host_user_root` in `cc_upload_tasks.py` and upload list module.
- **`cc_sweep.py` + `services/cc_assistant.py`:** mount-relative `transcript_path`; grep guards forbid `host_user_root` in **`services/cc_assistant.py`**, `cc_sweep.py`, and `cc_assistant/` modules.
- **`UserDirs` / `cc_provision.py`:** failing tests for `*_subpath` fields; `build_user_dirs` without `host_user_root`.
- **Atomic cutover:** hermetic test — mock `containers.run` receives `mounts=` only; `_session_metas` mount-relative; no `host_user_root` in spawn path.
- Negative test: grep guard fails if `docker-compose.yml` reintroduces `/srv/dmac/users:/dmac/users` as the primary CC store.

- [ ] **Step 2: Wire compose + startup bootstrap**

Add `dmac-cc-users` external volume; mount into `nextseek` at `DMAC_USER_ROOT_MOUNT`
(`/dmac/users`). Ensure `./startup.sh install` creates the volume on clean hosts.
Confirm **`docker>=7.1.0`** is already pinned in `dmac_assistant/pyproject.toml` (no version change); regenerate the lockfile only if other deps change. Implement `_mount_volume_subpath(source, target, subpath, *, read_only=False)` using **Engine-API PascalCase** (primary on docker-py 7.1.0):

```python
def _mount_volume_subpath(source: str, target: str, subpath: str, *, read_only: bool = False) -> dict:
    """docker-py 7.1.0: Mount() has no subpath kwarg — patch VolumeOptions onto Mount dict subclass."""
    m = docker.types.Mount(target=target, source=source, type="volume", read_only=read_only)
    m["VolumeOptions"] = {"Subpath": subpath}  # PascalCase key required by Engine API
    return m
```

**Subpath derivation (explicit):** the caller (`_build_volumes`) sets each `subpath` argument to that mount's **enumerated volume-relative value** from Step 1 — supplied post-G7-10 by the refactored `build_user_dirs` `*_subpath` fields, **not** by stripping a prefix from a `*_mnt` path. There is no general strip rule: `input` has **no** `input_mnt` (only `input_src`), `shared` has **no** `shared_mnt` and is project-scoped (`proj/shared`, no `{user}` segment), and transcripts is the `_memory/<session>` (`memory_mnt`) tail **plus** `/transcripts`. Each value is the per-user (or per-project, for `shared`) tail relative to the `dmac-cc-users` volume root — e.g. input → `proj/alice/input`, shared → `proj/shared`, transcripts → `proj/alice/_memory/sess/transcripts`. **Never** pass an absolute path or the full host/`*_mnt` string as `Subpath` — an absolute/over-long Subpath is rejected by the Engine.

Hermetic test: assert `"VolumeOptions" in mount` and that, **per mount**, **`mount["VolumeOptions"]["Subpath"]` equals that mount's concrete enumerated value** (e.g. input → `"proj/alice/input"`, shared → `"proj/shared"` — see Step 1 "Per-mount Subpath VALUE isolation"), **not** merely that the `Subpath` key exists; **reject**, per mount, an empty/root/constant `Subpath` and lowercase `volume_options`. Do **not** hand-roll lowercase `type`/`source`/`target`/`volume_options` dicts.

- [ ] **Step 3: Refactor `cc_config.CCPaths` (gate before path builder)**

Retire `host_user_root` / laptop default; implement `{users_volume, user_root_mount}` end-state; update `test_cc_config_paths.py`. **Task 6 Step 4 is blocked until this step passes.**

- [ ] **Step 4: Refactor path builder + sibling spawn**

Inside nextseek, Django continues mkdir/chmod via `*_mnt` paths under `/dmac/users/...`.
Sibling CC containers mount **subpaths of `dmac-cc-users`** to: `/data/input`, `/data/shared`,
`/data/scratch`, cc-state (RW → `/home/user/.claude`), and **`{project}/{user}/_memory/{session}/transcripts`** RO → `/home/user/.cc-memory/transcripts`.

**1c memory (G7-10 — no single-file volume subpath):** Docker volume subpaths mount **directories**, not file overlays. Preserve 1c MERGE semantics by:
1. Render/sync merged user-tier `CLAUDE.md` (existing L317–347 flow).
2. **Copy bytes** to `Path(dirs.cc_state_mnt) / "CLAUDE.md"` on the volume **before** spawn (cc-state subpath mounts to `/home/user/.claude` — session store root **is** the container `.claude` root; **not** `{cc_state_mnt}/.claude/CLAUDE.md`, which would land at `/home/user/.claude/.claude/CLAUDE.md`).
3. **Drop** `user_memory_file` RO file bind — only RW-mount cc-state `.claude` subpath via `Mount`.
4. Mount `{project}/{user}/_memory/{session}/transcripts` RO → `/home/user/.cc-memory/transcripts`.
5. Refactor `_session_metas`: store mount-relative `transcript_path`; remove host translation.
6. Spawn only after provision mkdir for **every** subpath that backs a mount (including `.claude` under cc-state **and the `_memory/<session>/transcripts` child specifically — not just `_memory/<session>`**; Engine `VolumeOptions.Subpath` fails container start if the exact subdir is absent in the volume) — **hermetic negative test:** mount/spawn fails or preflight errors when any one subpath directory is missing inside the volume.
7. Refactor `path_mappings` / `DMAC_PATH_MAPPINGS` in `run_cc_turn` — **locked post-cutover schema:**

```python
# Replace host_root/output_src with logical_root (in-container display path under user_root_mount)
"path_mappings": {
    "output": {"container_root": "/data/output", "logical_root": f"{user_root_mount}/{project}/{user}/output"},
    "scratch": {"container_root": "/data/scratch", "logical_root": f"{user_root_mount}/{project}/{user}/scratch"},
    ...
}
```

`_publish_artifacts` returns display paths under `logical_root` (not host bind strings). Hermetic test in `test_cc_engine_publish.py` asserts concrete expected strings.
8. Refactor `_publish_artifacts` display paths + update `test_cc_engine_publish.py` assertions for mount-relative paths.

Hermetic tests: merged CLAUDE.md bytes in cc-state path; `list[docker.types.Mount]` with cc-state + transcripts (no host file bind key). Update/remove `test_user_memory_mounted_ro_nested_over_session_claude` for volume mode. Grep guards: no `host_user_root` in **`services/cc_assistant.py`**, `cc_sweep.py`, or `cc_assistant/` modules. **Atomic cutover tests:** `_session_metas` mount-relative transcript path; `cc_sweep` reads without host translation; memory staging copies CLAUDE.md to `dirs.cc_state_mnt`.

`_build_volumes` returns mount payloads with volume subpaths via **raw-dict `Subpath` on docker-py 7.1.0** (primary); `_run_kwargs` passes `mounts=` to `containers.run`. **Also correct the stale `_run_kwargs` docstring** that still says the agent "joins … the nextseek compose network" — the agent defaults to the segmented `dmac-cc-net` (`DEFAULT_NETWORK`); fix the comment during this cutover so a reader auditing segmentation is not misled.

**Note (1c, deliberate G7-10 consequence):** dropping the RO file bind for an RW copy of merged `CLAUDE.md` into the cc-state subpath means the agent can transiently overwrite its own `CLAUDE.md` within a turn; it is re-rendered/copied next turn before spawn — accepted, not a defect.

**Permission model (locked):** Django (root in nextseek) mkdir + `chmod 0777` on volume paths via `*_mnt` before spawn; Task 10 sentinel scratch write proves uid-1001 writability.

- [ ] **Step 5: Dev migration note (execute at deploy time with sign-off)**

One-time: copy existing dev data from `/srv/dmac/users` into the new volume **or** wipe and recreate nested layout per Step 2 D3 dev policy. Record **`migration_policy: copy|wipe`** + command transcript in evidence `meta.json` with sign-off reference — validator fails if absent on dev-VM bundles **only when `preflight.json.had_host_bind_data == true`** (i.e. the dev-VM had pre-cutover `/srv/dmac/users` data). A **greenfield dev-VM** bundle (`had_host_bind_data == false`, nothing to migrate) need **not** include `migration_policy` and must still pass — consistent with Task 2 and Task 9 Step 2.

- [ ] **Success conditions**

- CC user data persists across `docker compose up -d --force-recreate nextseek` (verify with
  sentinel file under a test user's `scratch/`).
- `path_mappings` / `DMAC_PATH_MAPPINGS` and **`_publish_artifacts` display paths** use mount-relative logical roots (retire `output_src` / `artifacts_published` host strings).
- **1c memory after cutover:** merged CLAUDE.md at `{cc_state_mnt}/CLAUDE.md` (in-container `/home/user/.claude/CLAUDE.md`) before spawn; cc-state + transcripts `Mount` list tests pass — **no** `user_memory_file` bind; hermetic negative: nested `.claude/.claude/CLAUDE.md` path must **not** be used.
- **Atomic cutover gate:** hermetic spawn-path test passes (no `host_user_root` in `run_cc_turn` → `containers.run`).
- No required deploy step creates or chmods `/srv/dmac/users` on the host.
- Hermetic volume-mount tests pass; existing Step-2 isolation/volume tests updated (not
  deleted).
- `cc_runner_available()` and forced-CC turn still succeed after volume cutover.

- [ ] **Commit**

`git commit -m "feat(cc-step7): persist CC user trees in dmac-cc-users named volume"`

---

### Task 7: Extend env template and preserve secret boundary

**Files:**
- Modify: `docker/nextseek.env.example`
- Modify/extend: CC env/security tests

- [ ] **Step 1: Write env-template tests**

Assert every required non-secret CC key is documented and no real secret-like value is present.

- [ ] **Step 2: Add keys**

Document `NEXTSEEK_CC_IMAGE`, `NEXTSEEK_CC_NETWORK`, `DMAC_BEDROCK_PROXY_URL`,
`DMAC_ROUTER_ENABLED`, `DMAC_ROUTE_CAPABILITIES_FILE`,
`DMAC_ROUTER_MODEL_CLASS_MAP_FILE`, `DMAC_USER_ROOT_MOUNT`, `DMAC_CC_USERS_VOLUME`,
`NEXTSEEK_SERVER`, **`NEXTSEEK_SIDECAR_HOST` / `NEXTSEEK_SIDECAR_PORT` (G7-11, iter-1 M-3)**,
and any additional Step 3/7 keys discovered during re-grounding. **G7-11 live-gate
prerequisite keys:** the 7 sidecar ops invoke NExtSEEK-side LLM agents, so the template must
name (as gitignored-secret key names, never values) the server-side `GCP_API_KEY` and Bedrock
reach the capability gate requires. Also note the compose mount target/volume name must change
in tandem with `DMAC_CC_USERS_VOLUME`/`DMAC_USER_ROOT_MOUNT` overrides.
(`DMAC_USER_ROOT` host-path bind is **retired** by Task 6 — do not re-document `/srv/dmac/users`.)

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

### Task 8: Rewrite deploy docs and top-level pointers

**Files:**
- Modify: `nextseek_api/cc_assistant/DEPLOY.md`
- Modify: `README.md` and/or startup docs only as pointers.
- Test: doc grep guard in `test_step7_compose_deploy.py`

- [ ] **Step 1: Write doc guards**

Tests must fail if the required deploy path reintroduces separate `dmac-assistant` repo build,
manual `docker network create`, manual `docker network connect`, or Phase A/B sidecar bootstrap
as required steps. **(G7-11, iter-1 M-3):** the guard targets the *manual Phase A/B bootstrap
command patterns* — the compose-owned `nextseek-sidecar` SERVICE is a legitimate, required part
of the post-G7-11 deploy path and must NOT be flagged; do not key the guard on the bare token
"sidecar". Validator must parse the **numbered procedure** and fail if forbidden commands appear there; **also** scan the **entire** `DEPLOY.md` (not only numbered steps) for `/srv/dmac/users` co-occurring with `mkdir` or `chmod` as a required deploy action — same appendix dodge as Phase A/B sidecar bootstrap. **Positive requirements:** when compose declares `external: true` volumes, numbered procedure MUST include `./startup.sh install` (or documented volume-create subset that creates **`dmac-cc-users`** plus the six SEEK volumes).

- [ ] **Step 2: Rewrite docs**

Document the authoritative path:

**Prerequisites (numbered step 0):** Docker Engine **≥26** (API v1.45+ for volume subpaths) and Compose plugin **≥2.26** when compose YAML uses volume subpaths — enforced by Step 7 validator on evidence bundles; operators must verify `docker version` / `docker compose version` before bootstrap.

Then: pull/clone → fill gitignored config (including **Bedrock token in gitignored `bedrock-proxy` env file** — do **not** use `docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK`) → **`./startup.sh install`** (external volumes, including `dmac-cc-users`; **default instance — no `--instance` prefix** for 7d gate unless validator reads `startup/.instance.json` and adjusts expected volume names) → `docker compose build` → `docker compose up -d` → run Step 7 verifier. **No** host bind prep under `/srv/dmac/users`.

- [ ] **Success conditions**

- `DEPLOY.md` names only gitignored files for secrets.
- Old manual sidecar bootstrap is retired from the required path.
- Top-level docs point to `DEPLOY.md` instead of duplicating stale instructions.
- Doc guard tests pass.

- [ ] **Commit**

`git commit -m "docs(cc-step7): replace manual sidecar bootstrap with compose deploy path"`

---

### Task 9: Local compose build/up verification on the dev VM (non-gating smoke)

**Label:** Task 9 is **non-gating smoke** on the dev VM. **Task 10 (MBP greenfield) remains the authoritative 7d gate** per G7-7. Do not mark tracker step 7 done after Task 9 alone.

**Files/evidence:**
- Generated local evidence bundle under `acceptance_evidence/step7/<run_id>/`.

- [ ] **Step 1: Build and start locally with per-change sign-off**

Run root compose build/up for the affected services on the dev VM only after sign-off.

- [ ] **Step 2: Generate evidence**

Capture compose config, image ids, service status, network inspect, and
`cc_runner_available()`. **Regenerate `preflight.json` in the same `<run_id>` directory immediately before other §8 artifacts** (must match `meta.json.repo_commit`). Set `meta.json.host_label` to **`dev-vm`** or **`nextseek-dev`** (allowed enum — validator rejects unknown labels). **Require** `meta.json.migration_policy` (`copy|wipe`) + command transcript when dev-VM had pre-cutover `/srv/dmac/users` data (detect via preflight hash or explicit `had_host_bind_data: true` flag).

**Forced-CC artifact set (locked):** when Task 9 Step 3 runs the validator, the bundle must include the same §8 generated artifacts Task 10 requires (`forced_cc_result.json`, `proxy_log_window.txt`, `agent_env_scan.txt`, etc.) — Task 9 is non-gating for tracker completion but **not** a validator-skip smoke. No `host_label` branch exempts dev-vm bundles from forced-CC artifacts.

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

### Task 10: MBP greenfield verification before dev merge (authoritative 7d gate)

**Files/evidence:**
- Generated MBP evidence bundle under `acceptance_evidence/step7/<run_id>/`.

**Purpose:** Satisfy tracker substep 7d without deploying prod.

- [ ] **Step 0: Bootstrap external volumes (required before compose up)**

Root `docker-compose.yml` declares seven `external: true` volumes (six SEEK stack volumes plus
**`dmac-cc-users`**). On a clean host, run **`./startup.sh install`** (or the documented
volume-create subset) before `docker compose up -d`. Record volume bootstrap in generated
evidence `meta.json` / `preflight.json`. **Also** capture `pre_bootstrap_docker_volume_ls.txt`
and `pre_bootstrap_docker_network_ls.txt` (assert `dmac-cc-users` absent before bootstrap, or
document signed reuse in handoff). **No** host `/srv/dmac/users` prep (Task 6).

- [ ] **Step 1: Prepare clean MBP state**

Use a fresh clone or clean checkout of the Step 7 branch. Do not use this dev VM and do not use
prod. **Tracker path (MBP):** set `INTEGRATION_PLAN_PATH=<run_dir>/integration_plan_snapshot.json` per Task 1 MBP exception; preflight records `canonical_integration_plan_sha256` from monorepo handoff at gate time.

- [ ] **Step 2: Fill only gitignored local config**

Use tracked examples as templates. Do not copy from the standalone `dmac-assistant` repo.

- [ ] **Step 3: Compose bring-up**

Run `docker compose build && docker compose up -d`.

- [ ] **Step 4: Forced-CC acceptance**

Adapt `tests/test_cc_realstack.py` (or documented `docker exec nextseek …` one-liner) to emit SPEC-7 §8 artifacts under `acceptance_evidence/step7/<run_id>/`. Record `budget_cap_usd: 2.0` in `meta.json`. Set **`meta.json.host_label` to exactly `"mbp"`** (locked — validator exact-match). Run one forced CC turn with a per-run sentinel through the compose-owned proxy. **The forced-turn prompt MUST instruct the agent to write a per-run live-sentinel file named `LIVE_<sentinel>` into its own `/data/scratch`** (`<sentinel>` = the same unpredictable per-run forced-CC sentinel string). This file is authored **inside the live container during the turn**, so it appears in the during-turn `find` of `/data/scratch` and binds the captured scan to this exact run (a stale/blank/hand-edited scan cannot contain it). Record its filename as `meta.json.live_sentinel`.

**Subpath isolation scan (REQUIRED — locked SPEC-7 §8 artifact `subpath_isolation_scan.txt`):**

**Seed (before the turn) — explicit mechanism (normal provisioning never creates a foreign tree, so the harness MUST plant it):** `build_user_dirs` + Django mkdir only ever create the logged-in user's own `{project}/{user}/...` subtree; there is **no** native path that writes a *foreign* project/user dir at the volume root. Seed it with a helper container that mounts the named volume **at its root** (prefix-aware — the volume is `{prefix}dmac-cc-users`; bare `dmac-cc-users` on the default MBP instance):

```bash
# Resolve the prefixed volume name (empty prefix on the default MBP instance → bare name):
vol=$(docker volume ls -q | grep -E '(^|_)dmac-cc-users$' | head -1)
# (1) FOREIGN subtree planted at the VOLUME ROOT (NOT under the forced-CC user's subtree),
#     3 levels deep so a -maxdepth 4 find surfaces it only on a root-/empty-Subpath leak:
docker run --rm -v "$vol":/v alpine sh -c \
  'mkdir -p /v/otherproj/bob/input && touch /v/otherproj/bob/input/SENTINEL_FOREIGN'
# (2) Forced-CC user's OWN marker file, by name, inside the user's own input subpath
#     (realstack own tree: personal-ccacc-ccacc/ccacc) so it appears at /data/input/OWN_<run_id>:
docker run --rm -v "$vol":/v alpine sh -c \
  'mkdir -p /v/personal-ccacc-ccacc/ccacc/input && touch /v/personal-ccacc-ccacc/ccacc/input/OWN_<run_id>'
```

(Equivalently, the own marker may be written by Django into `input_mnt` before spawn — either way it lands at `{project}/{user}/input/OWN_<run_id>`.) The foreign dirs MUST land at the volume **root**, not under the user subtree. Record `meta.json.foreign_tokens = ["SENTINEL_FOREIGN", "otherproj", "bob"]`, `meta.json.own_marker = "OWN_<run_id>"`, `meta.json.live_sentinel = "LIVE_<sentinel>"` (the agent-authored scratch file, see Capture), and the forced-CC user's own `{project}/{user}` (realstack: `personal-ccacc-ccacc/ccacc`). Foreign tokens MUST be disjoint from the forced-CC user's own project/user strings, from `own_marker`, and from `live_sentinel`.

**Pre-turn seed scan (REQUIRED — `pre_turn_seed_scan.txt`; proves the seed exists at the volume root BEFORE the turn):** normal provisioning never plants a foreign tree, so the foreign-absent oracle below is **vacuous** unless the seed is proven present — if the implementer skips the seed step, a genuine `Subpath=""` whole-volume leak shows zero foreign tokens in-turn (because none were ever seeded) and passes green. To close that hole, **immediately after seeding and before spawning the turn**, the harness writes `pre_turn_seed_scan.txt` directly from a root-mounted helper's stdout:

```bash
docker run --rm -v "$vol":/v alpine find /v -maxdepth 4 -printf '%y %p\n' \
  > "$run_dir/pre_turn_seed_scan.txt"
```

The validator asserts `pre_turn_seed_scan.txt` is non-empty and **contains every `meta.json.foreign_token`** (`SENTINEL_FOREIGN`, `otherproj`, `bob`) — i.e. the foreign subtree is present at the volume root at seed time. Skipped seed ⇒ this scan is missing those tokens ⇒ the gate goes **RED before the turn even runs**, so a `Subpath=""` leak can no longer ship green on an unseeded volume.

**Capture (DURING the turn — NOT after):** `cc_engine.run_cc_turn` runs the sibling with `detach=True` and, in its `finally` block (`cc_engine.py:598-607`), calls `container.stop()` then `container.remove(force=True)` on every exit — so **after the turn there is no container to `docker exec` into**. Mirror the existing `_capture_agent_env` pattern (`test_cc_realstack.py:89-104`): run `run_cc_turn` on a background thread (as `test_cc_realstack.py:152-167` already does), poll `docker ps -q --filter label=nextseek.cc.run=<run_id>` until the cid appears, then — **while the container is alive** — the **test harness (not the operator) writes `subpath_isolation_scan.txt` directly from the `docker exec … find` subprocess stdout** (redirect/`subprocess` capture straight to the file — no hand-authoring step exists, so a forger cannot substitute a clean text file). The captured **recursive** listing:

```bash
cid=$(docker ps -q --filter label=nextseek.cc.run=<run_id> | head -1)
docker exec "$cid" find /data/input /data/scratch /data/shared \
  /home/user/.claude /home/user/.cc-memory/transcripts \
  -maxdepth 4 -printf '%y %p\n'
```

`-maxdepth 4` reaches the depth-4 `otherproj/bob/input/SENTINEL_FOREIGN` that a root-mounted leak exposes; a non-recursive `ls -la /data/input` would list only the top-level volume entries (`otherproj`, `personal-…`) and never the slash-joined `otherproj/bob/` path nor the 3-deep sentinel, so it cannot surface the leak. (`/data/output` is intentionally **not** in the `find` roots — it is not a sibling RO subpath mount; the isolation gate covers the RO/RW sibling mounts only.)

**Oracle (seed-presence + positive allowlist + live-sentinel cross-check + foreign-absent, pinned — Task 2 validator runs this exact check):** the bundle PASSES iff **BOTH** scans pass:
- **(0) Seed proven present (pre-turn):** `pre_turn_seed_scan.txt` is non-empty AND **contains every** `meta.json.foreign_token` (`SENTINEL_FOREIGN`, `otherproj`, `bob`) — proves the foreign tree was actually planted at the volume root before the turn. (Skipped seed ⇒ this fails ⇒ RED, so the foreign-absent check in (1) is never vacuous.)
- **(1) Foreign absent in-turn + provenance bound:** `subpath_isolation_scan.txt` is non-empty AND **contains the own marker filename** `meta.json.own_marker` (the `OWN_<run_id>` file under the user's own `/data/input` subpath — proves a *live, correctly-mounted own subtree* was actually listed; a hand-written empty stub fails this) AND **contains the live sentinel filename** `meta.json.live_sentinel` (the `LIVE_<sentinel>` file the agent wrote into its own `/data/scratch` during the turn — an **anti-stale / anti-substitution** binding that ties the scan to this run's live container; a stale/hand-edited clean scan reused from a *different* run lacks it and fails this cross-check — it is **not** a leak detector, see Mutation proof) AND **contains none** of `meta.json.foreign_tokens` — the validator runs `grep -nE 'SENTINEL_FOREIGN|(^| |/)otherproj(/|$| )|(^| |/)bob(/|$| )'` and fails the bundle on **any** match.

The leak detector is **(0) ∧ (1)** together: foreign-token *absence in-turn* is only meaningful once foreign-token *presence pre-turn* is proven.

**Mutation proof (three scenarios):**
- *(a) Honest run:* seed present ⇒ `pre_turn_seed_scan.txt` contains all foreign tokens (gate-0 GREEN); correct per-user `Subpath` ⇒ `/data/input` is the `{project}/{user}/input` subpath, the foreign tree is unreachable in-turn, the in-turn grep is empty, and both `OWN_<run_id>` (under `/data/input`) and `LIVE_<sentinel>` (under `/data/scratch`) are present ⇒ gate-1 GREEN ⇒ **GREEN**.
- *(b) Skipped seed:* if the harness omits the foreign seed `docker run … mkdir … SENTINEL_FOREIGN`, then `pre_turn_seed_scan.txt` is missing `SENTINEL_FOREIGN`/`otherproj`/`bob` ⇒ gate-0 **RED** *before the turn runs*. This closes the vacuous-oracle hole: an unseeded volume can no longer ship a `Subpath=""` leak green just because there were no foreign tokens to find.
- *(c) Real `Subpath=""`/`"/"` leak (seed present):* the whole `dmac-cc-users` root mounts at `/data/input`; the in-turn harness-captured `find` lists `otherproj/bob/input/SENTINEL_FOREIGN`, the foreign grep matches ⇒ gate-1 **RED**.

**Why the live sentinel is NOT the leak detector (corrected — prior rationale was wrong):** under a `Subpath=""` leak the agent still writes `LIVE_<sentinel>` to the **container path** `/data/scratch`, and the harness `find`s that **same container path** — so `find /data/scratch` lists `LIVE_<sentinel>` (now at depth 1 of the volume root) under **both** the leaking and the correct mount. The live sentinel therefore does **not** drop on a leak; it is purely an **anti-stale / anti-substitution binding** that proves the scan came from *this* run's live container (keeping a clean scan from a *different* run out of the bundle). Leak detection rests entirely on **foreign-token absence in-turn, gated by foreign-token presence pre-turn** (scenarios b/c above).

**Fabrication friction (what the validator actually enforces):** a text-file validator cannot prove *who* authored a file, so "harness-written, never hand-authored" is a **harness-implementation requirement**, not a validator guarantee. What the validator *does* enforce is the scan **pair**: `pre_turn_seed_scan.txt` must contain every foreign token (seed proven), and `subpath_isolation_scan.txt` must contain this run's unpredictable agent-authored `LIVE_<sentinel>` and `OWN_<run_id>` while omitting all foreign tokens. To pass while hiding a real `Subpath=""` leak a forger would have to hand-edit the foreign tokens out of an in-turn scan that *also* reproduces this run's live sentinel — a clean scan from a different run lacks the sentinel and fails the cross-check. The harness writing `subpath_isolation_scan.txt` straight from the live `docker exec … find` subprocess stdout is the mechanism that makes that hand-edit the only forgery path. This is a hard gate, not a recommendation — it is the only live check that catches an empty/wrong `Subpath` that hermetic tests cannot (the Risk-Register rank-2 cross-user-leak catastrophe).

**Legacy → SPEC-7 §8 artifact mapping (required):**

| Legacy (realstack today) | SPEC-7 §8 name | Field mapping |
|--------------------------|----------------|---------------|
| `forced_result.json` | `forced_cc_result.json` | `total_cost_usd` → `cost`; include `run_id`, `sentinel`, `is_error` |
| `proxy_log.txt` | `proxy_log_window.txt` | window scoped to run timestamps only |
| `network.json` | `network_inspect.json` | full `dmac-cc-net` inspect |
| `ledger.json` | *(optional supplement)* | do not substitute for `forced_cc_result.json.cost` |

Validator **rejects** legacy filenames under `acceptance_evidence/step7/`. `forced_cc_result.json.run_id` must match `meta.json.run_id`; `cost > 0` for Opus forced turn unless documented zero-cost exception.

- [ ] **Step 5: Evidence and validator**

Generate the required evidence bundle (**fresh `preflight.json` first**, then §8 artifacts), scan text/JSON/log/env artifacts, scan or manually review
any screenshots, and run the Step 7 validator.

- [ ] **Success conditions**

- MBP evidence proves the host had no prior required Container-CC state.
- No standalone `dmac-assistant` repo is required.
- `cc_runner_available()==(True, "ok")`.
- Forced CC turn completes with sentinel in reply.
- Proxy log window contains allowed-model invoke success and no token/header leak.
- Agent env scan contains no shared AWS/GCP/DB/backend creds.
- `dmac-cc-net` excludes backend services.
- **`pre_turn_seed_scan.txt` (written by the harness from a root-mounted helper after seeding, before the turn) contains every seeded foreign token (`SENTINEL_FOREIGN`, `otherproj`, `bob`)** — proves the foreign tree exists at the volume root, so the foreign-absent check below is not vacuous (a skipped seed turns this RED before the turn).
- **`subpath_isolation_scan.txt` (written by the harness directly from the live sibling's `docker exec … find … -maxdepth 4` stdout DURING the turn) proves the sibling agent mounted only the test user's own `{project}/{user}/` subpath** — the scan contains the own marker filename (`OWN_<run_id>`), contains the agent-authored live sentinel filename (`LIVE_<sentinel>`, written into `/data/scratch` during the turn), and **none** of the seeded foreign tokens (`SENTINEL_FOREIGN`, `otherproj`, `bob`) despite a second user seeded on `dmac-cc-users` (REQUIRED §8 cross-user isolation gate; a real empty `Subpath` leak makes the in-turn foreign-token grep match ⇒ RED; the live sentinel is an anti-stale binding present under both correct and leaking mounts, **not** a leak detector; a stale/fabricated clean scan from another run fails the live-sentinel cross-check).
- `secret_scan_report.json` passes, including screenshot review if screenshots exist.
- Step 7 validator passes on the MBP bundle.

- [ ] **Commit**

`git commit -m "test(cc-step7): add MBP greenfield compose verification evidence"`

---

### Task 11: Tracker update and handoff

> **Sequencing:** MBP Task 10 completes on NExtSEEK-only clone; tracker update runs from **monorepo/dev checkout** after evidence is committed — not blocked on Task 10 bundle generation.

**Files:**
- Modify: tracker at **`INTEGRATION_PLAN_PATH`** (default `../state/integration-plan.json` from NExtSEEK root) — status fields only.
- Create: structured handoff report via `/home/taishajo/work/state/handoff.sh`.

- [ ] **Step 1: Update tracker statuses**

Only after Tasks 1–10 pass **including MBP Task 10 evidence**, mark Step 7/substeps done according to the tracker protocol. Task 9 dev-VM smoke alone is insufficient.

- [ ] **Step 2: Write handoff**

Use the shared handoff system. Include generated evidence bundle paths and validator command
outputs.

- [ ] **Success conditions**

- `integration-plan.json` remains valid JSON and only permitted fields changed.
- Handoff distinguishes user-stated decisions from agent inferences.
- Handoff records verification status honestly and points to generated evidence, not prose.

- [ ] **Commit**

Commit tracker/doc/evidence changes according to the active branch policy at implementation time.

---

## Permissions Required

| Permission / resource | Tasks | Notes |
|----------------------|-------|-------|
| Read/write NExtSEEK repo on Step 7 branch | all | Port runtime + proxy source |
| Docker Compose v2 + BuildKit | 3–6, 9–10 | Local build/up + volume wiring |
| `uv` CLI (required by `./startup.sh install`) | 10 Step 0 | `startup.sh` exits if `uv` missing |
| Docker socket (dev VM / MBP) | 9–10 | Full stack + transient CC agents |
| `docker ps --filter label` + `docker exec` into the **live** transient CC agent | 9–10 | Isolation scan must capture **during** the turn (agent force-removed at turn end, `cc_engine.py:598-607`); poll by `label=nextseek.cc.run=<run_id>` |
| `alpine` image pull for the seed + pre-turn-seed-scan helper containers (`docker run --rm -v "$vol":/v alpine …`) | 10 Step 4 | Plants the foreign tree at the volume root and captures `pre_turn_seed_scan.txt`; pre-pull during Step 0 or reuse an already-present image (e.g. the CC/Debian runtime image) on an egress-restricted host |
| Gitignored secrets: `docker/nextseek.env`, `docker/db.env`, `dmac/local_settings.py`, Bedrock proxy token file | 7, 10 | Never committed |
| `INTEGRATION_PLAN_PATH` env (default `../state/integration-plan.json`) | 1, 10 | Portable tracker gate — no hardcoded home paths |
| **Write** monorepo `work/state/integration-plan.json` | 11 only | Task 11 runs from monorepo checkout after MBP evidence committed — distinct from NExtSEEK-only clone |
| `./startup.sh install` (external volume bootstrap incl. `dmac-cc-users`) | 6, 10 Step 0 | Required on clean hosts before compose up |
| MBP local Docker + network | 10 | G7-7 greenfield gate |
| Bedrock API spend (forced-CC turn) | 10 | Per-run sentinel; cap enforced |
| GitHub egress (plugin context generation) | 3 | G7-6; snapshot fallback if blocked |
| Service-account `docker:cli` helper (dev VM smoke) | 9 | Optional non-gating |
| Per-change sign-off before touching running dev/MBP | 9–10 | Global constraint |
| Dev VM data migration `/srv/dmac/users` → volume (optional) | 6 Step 5 | Sign-off; transcript in evidence only |

Resolved in Phase 5.5 before Step 7 execution.

---

## Risk Register

| Rank | Task | Likely failure | Catastrophic failure | Rollback |
|------|------|----------------|---------------------|----------|
| 1 | 1 | Start Step 7 before Step 3 deployed | Compose/DEPLOY edits on stale state; collision with Step 3 deploy | Block until `step3_deploy_gate` passes |
| 2 | 6 | Sibling volume subpath mounts wrong in docker-py | CC turn fails or cross-user leak | Hermetic **concrete per-user `Subpath`-value** assertions + anti-empty negative control (Task 6 Step 1/2); **REQUIRED live `subpath_isolation_scan.txt`** §8 gate (Task 10) — key-presence-only checks do **not** catch an empty/constant `Subpath` |
| 3 | 6 | Skip dev migration from `/srv/dmac/users` | Lost 1b/1c transcripts on cutover | Task 6 Step 5 migration transcript or explicit wipe sign-off |
| 4 | 10 | Missing external volumes on clean MBP | False "greenfield" failure or cheat via dev VM state | `./startup.sh install` creates `dmac-cc-users` + six SEEK volumes |
| 5 | 3 | Wire lean `docker/cc-runner/` instead of full runtime | Prod image missing plugins/context (G7-3 violation) | Pin `docker/cc-runtime/`; negative test |
| 6 | 5/8 | DEPLOY.md merge conflict with PLAN-3 Task 13 | Operators get contradictory procedures | Merge order: Step 3 deploy first, then Step 8 rewrite on recorded hash |
| 7 | 5 | Legacy `test_cc_realstack.py` still passes manual topology | Regression via old acceptance path | Task 5 Step 3 updates realstack tests |
| 8 | 4 | Proxy secret copied into build context | Token leak in image layer | Build-context guards + secret scan |
| 9 | 9 vs 10 | Mark tracker done after dev VM only | 7d gate skipped | Task 9 labeled non-gating; Task 11 requires MBP bundle |
| 10 | 2 | Validator accepts Markdown-only evidence | False Step 7 completion (G7-8 violation) | Generated bundle + `validator_output.txt` required |

Pause-and-ask: any failed MBP forced-CC turn, secret scan failure, or `step3_deploy_gate` mismatch.

---

## Dependency Validation

| Dependency | Validation | Status |
|------------|------------|--------|
| Docker Compose v2 multi-network | [Docker docs — compose networks](https://docs.docker.com/reference/compose-file/networks/) — services list multiple networks for dual-homed nginx | OK — plan uses compose-managed `dmac-cc-net` |
| External compose volumes (7 incl. `dmac-cc-users`) | Verified: Task 6 adds CC volume; `./startup.sh install` creates all external volumes | OK — Task 10 Step 0 documents bootstrap |
| Standalone `dmac-assistant` repo (port source) | Task 1 preflight must record source commit at implementation time | Must-verify at execution |
| `cc_runner_available()` | Checks Docker + image + network only (not proxy health) | OK — Step 7 validator adds proxy/segmentation checks |
| Legacy acceptance (`validate_cc_acceptance.py`) | Encodes external repo topology today | Hardened — Task 5 Step 3 migration |
| PLAN-3 overlap (`DEPLOY.md`, `cc_config.py`, `cc_engine.py`) | Step 3 not started at vetting baseline | Sequencing gate enforced |

---

## Gameability Audit

| Task | Success condition | Cheapest fake | Remedy |
|------|-------------------|---------------|--------|
| 1 | `preflight.json` valid JSON + hashes | Hand-crafted JSON matching pre-Step-3 tree | Require `step3_deploy_gate.tracker_step3_status == "done"` + tracker SHA + **committed** `live_gate_transcript.txt` on branch |
| 2 | Validator passes synthetic bundle | Markdown index without generated JSON artifacts | Reject Markdown-only; require `validator_output.txt` |
| 5 | compose config includes services | Committed golden fixture never re-run | Subprocess `docker compose config` on live YAML |
| 7 | Doc grep guard | Move Phase A/B to "Historical" section | Parse numbered procedure; fail on `/srv/dmac` host prep (Task 6) |
| 10 | Forced-CC sentinel in reply | Direct engine call bypassing UI | `forced_cc_result.json` + proxy log window + agent env scan |
| 10 | Screenshots as proof | Unreviewed PNGs | `secret_scan_report.json` OCR/manual review entries (G7-9) |
| 6 | Volume persistence | Reuse pre-existing volume without greenfield proof | Record `docker volume ls` before bootstrap in evidence |
| 6 | Per-user subpath isolation | `Subpath=""`/constant mounts whole volume root (cross-user leak) yet "Subpath" key still present | Hermetic **concrete `Subpath`-value** assertion + anti-empty negative control; REQUIRED **paired** live §8 gate — `pre_turn_seed_scan.txt` must contain **every** foreign token (proves the seed exists; skipped seed ⇒ RED) AND `subpath_isolation_scan.txt` (**recursive `find -maxdepth 4` captured during the turn**) must contain own marker + live sentinel and **none** of `SENTINEL_FOREIGN`/`otherproj`/`bob` (real leak ⇒ foreign token present ⇒ RED). Leak detector = pre-turn-present ∧ in-turn-absent; the live sentinel is an anti-stale binding, not a leak detector |

---

## Phase 2 Vetting Log

| Iteration | Reviewer | Verdict | MEDIUM+ resolved |
|-----------|----------|---------|------------------|
| 1 | Independent cold-context (2026-06-30) | CONDITIONAL_ACCEPTANCE | 2 Critical, 8 High — hardening applied |
| 2 | Fresh re-vet (2026-06-30, iter 2) | CONDITIONAL_ACCEPTANCE | DEPLOY startup.sh, step3_deploy_gate fields, coverage command |
| 3 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-2 residual fixes applied |
| 4 | Fresh re-vet (2026-06-30, iter 3) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 10 MEDIUM — see `.vetting/plan-7-phase2-review-3-fresh.md` |
| 5 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-3 fixes |
| 6 | Fresh re-vet (2026-06-30, iter 4) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 6 MEDIUM — see `.vetting/plan-7-phase2-review-4-fresh.md` |
| 7 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-4: doc guards, uv permission, container_name pin |
| 8 | User decision | **Option A locked in plan + SPEC G7-10** | Task 6: `dmac-cc-users` named volume; retires `/srv/dmac` host bind |
| 9 | Orchestrator | **SPEC-7 amend applied** | G7-10 + §7 six-step deploy; Amendment Log 2026-06-30 |
| 10 | Fresh re-vet (2026-06-30, iter 5) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 4 HIGH, 11 MEDIUM — see `.vetting/plan-7-phase2-review-5-fresh.md` |
| 11 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-5: portable tracker path, Task 6 Mount API, cc_sweep, preflight gate |
| 12 | Fresh re-vet (2026-06-30, iter 6) | **CONDITIONAL_ACCEPTANCE** | 4 HIGH, 11 MEDIUM — see `.vetting/plan-7-phase2-review-6-fresh.md` |
| 13 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-6: cc_assistant.py, memory mounts, gate SHA, Task 2 validator scope |
| 14 | User decisions (2026-06-30) | **Locked** | Turn-scoped artifacts (PLAN-3); transcript required + SPEC §8 preflight amend + MBP tracker snapshot |
| 15 | Fresh re-vet (2026-06-30, iter 7) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 2 HIGH, 11 MEDIUM — see `.vetting/plan-7-phase2-review-7-fresh.md` |
| 16 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-7: MBP snapshot exception, greenfield validator, preflight fields, image tag |
| 17 | Fresh re-vet (2026-06-30, iter 8) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 4 HIGH, 10 MEDIUM — see `.vetting/plan-7-phase2-review-8-fresh.md` |
| 18 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-8: 1c memory mount strategy, Engine floor, network pre-bootstrap, SPEC §8 sync |
| 19 | Fresh re-vet (2026-06-30, iter 9) | **CONDITIONAL_ACCEPTANCE** | 5 HIGH, 10 MEDIUM — see `.vetting/plan-7-phase2-review-9-fresh.md` |
| 20 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-9: Task 6 cc-state copy substeps, success conditions, atomic cutover |
| 21 | Fresh re-vet (2026-06-30, iter 10) | **CONDITIONAL_ACCEPTANCE** | 3 HIGH, 10 MEDIUM — see `.vetting/plan-7-phase2-review-10-fresh.md` |
| 22 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-10: cc_config.py, test inventory, cc_runner_available strings |
| 23 | Fresh re-vet (2026-06-30, iter 11) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 6 MEDIUM — see `.vetting/plan-7-phase2-review-11-fresh.md` |
| 24 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-11: path_mappings substep, cc_config Step 2b, Task 5 cc_engine |
| 25 | Fresh re-vet (2026-06-30, iter 12) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 8 MEDIUM — see `.vetting/plan-7-phase2-review-12-fresh.md` |
| 27 | Fresh re-vet (2026-06-30, iter 13, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 14 MEDIUM — see `.vetting/plan-7-phase2-review-13-fresh.md` |
| 29 | Fresh re-vet (2026-06-30, iter 14, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 7 MEDIUM — see `.vetting/plan-7-phase2-review-14-fresh.md` |
| 30 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-14: Mount subpath= kwarg, instance-prefix volume oracle |
| 31 | Fresh re-vet (2026-06-30, iter 15, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 4 HIGH, 8 MEDIUM — see `.vetting/plan-7-phase2-review-15-fresh.md` |
| 32 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-15: docker>=7.2 pin, services/cc_assistant grep, preflight commit match, version parse |
| 33 | Fresh re-vet (2026-06-30, iter 16, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 3 HIGH, 3 MEDIUM, 1 LOW — see `.vetting/plan-7-phase2-review-16-fresh.md` |
| 34 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-16: docker 7.1 raw Subpath primary, CLAUDE.md staging path, upload cutover inventory, validate_cc_acceptance scope, Task 9 forced-CC artifacts, _run_kwargs test, SPEC deploy_commit |
| 35 | Fresh re-vet (2026-06-30, iter 17, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 1 HIGH, 4 MEDIUM — see `.vetting/plan-7-phase2-review-17-fresh.md` |
| 36 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-17: PascalCase VolumeOptions Mount helper, host_label=mbp, branch ancestry, dmac_assistant pyproject pin, DEPLOY full-file guard |
| 37 | Fresh re-vet (2026-06-30, iter 18, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 CRITICAL, 1 HIGH, 3 MEDIUM — see `.vetting/plan-7-phase2-review-18-fresh.md` |
| 38 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-18: concrete per-user `Subpath`-**value** assertion + anti-empty negative control (Task 6) and required `subpath_isolation_scan.txt` live gate (Task 2/10) **+ SPEC-7 §8 strengthened** (CRITICAL); celery marker → `inspect registered` (HIGH); `--cov=cc_engine --cov=cc_config` floor; `migration_policy` required-ness made consistent (Tasks 2/6/9); runtime-port source pinned — see `.vetting/plan-7-phase2-fix-log-iter18.md` |
| 39 | Fresh re-vet (2026-06-30, iter 19, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 2 HIGH, 1 MEDIUM — see `.vetting/plan-7-phase2-review-19-fresh.md` (both HIGH = iter-18 live-gate regressions) |
| 40 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-19: isolation scan respecified to **during-turn** poll-by-label capture (agent is force-removed in `finally`) (HIGH-1); recursive `find -maxdepth 4` + positive-allowlist/foreign-sentinel oracle, mutation-RED proof (HIGH-2); transcript markers → guaranteed stdout `Applying nextseek_api.0007`/`cc_assistant.upload`/`cc_traces` (MEDIUM); **SPEC-7 §8 capture mechanism corrected** (requirement intact) — see `.vetting/plan-7-phase2-fix-log-iter19.md` |
| 41 | Fresh re-vet (2026-06-30, iter 20, **canonical prompt**) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 4 MEDIUM — see `.vetting/plan-7-phase2-review-20-fresh.md`. HIGH = cross-plan marker handshake (PLAN-7:132 ↔ PLAN-3 Task 13 Step 8) + fresh-vs-idempotent migration stdout; MEDIUMs = foreign-tree seeding unspecified, scan-file authenticity not bound to live capture, Compose-≥2.26 vs docker-py Engine-API floor |
| 42 | Hardener (orchestrator, **single owner of cross-plan thread B**) — **not a reviewer** | *(pending fresh re-vet)* | Iter-20: **thread A closed** — prefix-aware foreign-tree seeding step + `subpath_isolation_scan.txt` harness-written from live `docker exec … find` stdout + agent-authored `LIVE_<sentinel>` cross-check (`meta.json.live_sentinel`); **thread B closed** — byte-identical 4-string marker allowlist across PLAN-7:132 ↔ PLAN-3 Task 13 Step 8, idempotency-robust (`showmigrations` `[X] 0007_ccsessiontranscript` fallback); Compose-≥2.26 → **Engine ≥26** real floor; **SPEC-7 §8 authenticity-binding clause added**. See `.vetting/plan-7-phase2-fix-log-iter20.md` + `.vetting/defect-lineage.md` |
| 43 | Fresh re-vet (2026-06-30, iter 21, **canonical prompt, un-steered**) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, **0 MEDIUM**, 3 LOW — see `.vetting/plan-7-phase2-review-21-fresh.md`. Threads **A + B CLOSED** (during-turn capture + mutation-robust oracle + byte-identical markers independently verified). New thread **D** = cc_engine whole-module ≥95% floor unproducible + false pragma claim (child of iter-18) |
| 44 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-21: **thread D** rescoped — hermetic ≥95% floor → pure `cc_config` (probe-measured 100%); cc_engine helpers via `--cov-report=term-missing`; `run_cc_turn` on the live realstack gate as justified exception; false pragma claim removed; Subpath derivation (strip `user_root_mount`) stated. SPEC-7 untouched. See `.vetting/plan-7-phase2-fix-log-iter21.md` |
| 45 | Fresh re-vet (2026-06-30, iter 22, **canonical prompt, un-steered**) | **CONDITIONAL_ACCEPTANCE** | 1 HIGH, 2 MEDIUM, 2 LOW — see `.vetting/plan-7-phase2-review-22-fresh.md`. **Thread A REOPENED** (iter-21 closure premature): isolation oracle **vacuous** unless foreign seed proven planted (HIGH); iter-20 "live sentinel = leak detector" rationale factually wrong (MEDIUM). Thread D held |
| 46 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-22: **thread A re-closed soundly** — REQUIRED `pre_turn_seed_scan.txt` paired gate (gate-0 proves seed planted; gate-1 proves isolation), 3-scenario walkthrough; wrong sentinel rationale corrected everywhere; both hermetic nets made commit-blocking; `alpine` helper in Permissions; **SPEC-7 §8 additively strengthened**. See `.vetting/plan-7-phase2-fix-log-iter22.md` |
| 47 | Fresh re-vet (2026-06-30, iter 23, **canonical prompt, un-steered**) | **CONDITIONAL_ACCEPTANCE** | **0 HIGH**, 3 MEDIUM (one root cause), 3 LOW — see `.vetting/plan-7-phase2-review-23-fresh.md`. **Thread A re-CLOSED** (paired live seed/in-turn gate backstops any leak — confirmed). New thread **F** = Task 6 hermetic Subpath-value net incomplete (`shared` mount un-enumerated; strip-prefix derivation wrong vs `*_src`/`*_mnt`). Marker handshake + docker-py Mount re-verified |
| 48 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-23: **thread F** — enumerate every spawn-set mount's concrete per-user Subpath value incl. `shared` (project-scoped, correct negative control); replace strip-prefix shorthand with real `*_src`/`*_mnt`+`/transcripts` values. See `.vetting/plan-7-phase2-fix-log-iter23.md` |
| 49 | Fresh re-vet (2026-06-30, iter 24, **canonical prompt, un-steered**) | **UNCONDITIONAL_ACCEPTANCE** | **0C / 0H / 0M** (3 LOW/cosmetic) — see `.vetting/plan-7-phase2-review-24-fresh.md`. Reviewer live-probed docker-py 7.1.0 `Mount`/`VolumeOptions.Subpath` survives into HostConfig; all 5 Subpath values match `build_user_dirs` (`cc_provision.py:99–109`); marker handshake byte-identical; paired seed/in-turn isolation oracle sound; coverage exception legit. **Thread F CLOSED** |

**Phase 2 status: ✅ COMPLETE** — iter-24 independent fresh reviewer returned **UNCONDITIONAL_ACCEPTANCE** (zero MEDIUM+). All threads A–F CLOSED; hard-gate sections present. Per loop rule, not reopened. See `.vetting/defect-lineage.md`. **Phase 3 (task-spec writing) is gated on a user Phase-2→3 checkpoint — do NOT auto-advance.**

---

# Sidecar wave (Amendment G7-11, 2026-07-01) — Tasks 12-16

> **Why this wave exists:** G7-5's phrasing conflated the deprecated app/chat server with the
> **NS sidecar op-proxy** that 7 of the 9 shipped `nextseek-*` plugin ops hard-require
> (`_sidecar_client.py` → WS `nextseek-sidecar:8765`, no fallback, exit 7 without it). Verified
> live 2026-07-01: the deployed agent fails those 7 ops with `TRANSPORT_ERROR (gaierror)`.
> **User ruling:** the sidecar is Step 7 work; Step 7 is incomplete while any shipped plugin op
> lacks a working backend. SPEC-7 G7-11 + §8 `plugin_ops_matrix.json` are the spec of record.
> **Sequencing:** Tasks 12-15 join the tracker-7a scope and MUST complete before Task 7 (env
> template), Task 8 (DEPLOY.md), and Tasks 9-10 (evidence gates) are executed, so 7b/7c/7d
> document and prove the sidecar-inclusive stack, never the 2/9 facade. Task 16 (debt fixes)
> may run any time in the wave. Grounding dossier: session artifact `sidecar-dossier.md`
> (2026-07-01) — re-verify its file:line cites at implementation time.
> **Tracker-7b obligation (iter-2 R2-L1):** tracker 7b still enumerates retired
> `DMAC_USER_ROOT` and omits `NEXTSEEK_SIDECAR_HOST`/`NEXTSEEK_SIDECAR_PORT`; description
> edits need user sign-off, so Task 11 Step 1 MUST propose the 7b wording fix at the next
> tracker touch (recorded here so the obligation survives; the iter-1 hardener's "all three
> locked texts amended" claim covered only the in-plan texts).

## Wave-global constraints

- Port source stays the pinned clone `/home/taishajo/work/dmac-assistant` @ `a429f13`
  (`port_source_commit`); the T16/T17 rewire (0f5bfdd/bfdf0e3/3c40a1a, 2026-06-13) is included.
- **OI-3 unchanged:** the sidecar holds no credentials (env = `NEXTSEEK_BASE_URL`,
  `SIDECAR_STAGING_DIR`, `SIDECAR_WS_PORT` only); per-request user Basic auth arrives inside
  each WS frame from the agent; NExtSEEK reach is exclusively via dual-homed `nextseek_nginx`.
  Do NOT port the upstream A-1 `nextseek-local` backend-network attachment (dead post-T16).
- Same execution discipline as Tasks 1-6: TDD/validator-first, hermetic zero-spend tests in the
  canonical harness, byte-verbatim ports with hardcoded-sha256 parity tests (Task 4 pattern),
  generated evidence only, secret-scan before every commit, per-change sign-off before touching
  any running instance.

### Task 12: Port the NS sidecar into NExtSEEK

**Files:** create `docker/ns-sidecar/` (Dockerfile adapted to a self-contained context — upstream
uses `context: ..` and COPYs `sidecar/__init__.py` + `sidecar/app/`; restructure so
`docker build docker/ns-sidecar/` works alone, Task 4 precedent), `docker/ns-sidecar/app/…`
byte-verbatim from `sidecar/app/` @ a429f13, `PORT-EVIDENCE.json`, tests
(`test_step7_sidecar_port.py`).

- [ ] Step 1: failing presence/parity tests — Dockerfile, all 10 non-`__init__` `app/*.py`
  modules PLUS both package files (`app/__init__.py` and the parent package `__init__.py` —
  iter-1 L-5: the upstream Dockerfile COPYs both and `python -m sidecar.app.server` with
  `PYTHONPATH=/app` requires the package shape; the self-contained restructure must preserve
  the import layout, test-pinned), no `local-nextseek*.env`, no upstream compose fragment
  wired; hardcoded-sha256 parity constants for every logic-bearing module (conscious-update
  rule comment, Task 4 pattern).
- [ ] Step 2: port; build-context/secret guards (`.dockerignore`; no env files COPYd); grep
  guards: no `SESSION_DB_`, no `chat_nextseek`, no torch imports (T16/T17 lean contract).
- [ ] Step 3: `docker build docker/ns-sidecar/` from the worktree succeeds (throwaway tag,
  removed after); hermetic tests stay docker-free.
- [ ] Success: image builds standalone; parity tests pin a429f13 bytes; healthcheck module
  ported as-is (single HTTP GET to `{NEXTSEEK_BASE_URL}/nextseek_api/assistant/me/`, 200/401
  healthy — verify this claim against the ported source and record in the report).
- [ ] Commit: `feat(cc-step7): port NS sidecar into NExtSEEK (G7-11)`

### Task 13: Compose service + agent env wiring

**Files:** `docker-compose.yml`, `docker/nginx.conf` (explicit `access_log` directive — iter-3
L-4: the gate's `gate_access_log_window.txt` evidence source must not ride on the compiled
default a conf edit could silently drop; add a hermetic guard test), `nextseek_api/cc_assistant/cc_engine.py`
(`build_agent_environment` + any sidecar-availability detail strings), `cc_config.py` (env
names), `test_step7_compose_deploy.py`, `test_cc_engine_env.py` (or equivalent).

- [ ] Step 1: failing compose-config tests — service `nextseek-sidecar` exists; image/build
  target `docker/ns-sidecar/`; **`dmac-cc-net` only** (exact set == {"dmac-cc-net"}); **no
  `ports:` key**; healthcheck present with cold-start tolerance (upstream used `retries: 6`,
  `start_period: 20s` — the sidecar is legitimately unhealthy until Django serves through
  nginx; encode a start_period so Task 9/10 bring-up evidence doesn't capture a red-herring
  "unhealthy"); env carries only the three non-secret keys with `NEXTSEEK_BASE_URL` pinned to
  the literal `http://nextseek_nginx` (do NOT assert equality with the agent's rewritten URL —
  `_rewrite_loopback_url` passes non-loopback URLs through unchanged, so agent URL ≠ sidecar
  URL is legitimate). **The compose SERVICE name is the load-bearing identity**: service DNS
  alias `nextseek-sidecar` MUST equal `_sidecar_client.py`'s `NEXTSEEK_SIDECAR_HOST` default —
  that is what agents resolve. Additionally pin `container_name: nextseek-sidecar` for
  host-side ops and the validator's closed-set peer check (analogous to `dmac-bedrock-proxy`;
  the DNS behavior comes from the service name, not `container_name`).
- [ ] Step 2: failing agent-env tests — `build_agent_environment` emits
  `NEXTSEEK_SIDECAR_HOST=nextseek-sidecar` and `NEXTSEEK_SIDECAR_PORT=8765` (defaults
  overridable via documented env); existing agent env-leak tests still prove no shared creds.
- [ ] Step 2b (iter-1 H-2): deterministic agent container naming — `_run_kwargs` gains
  `name=f"dmac-cc-agent-{run_id}"` so agents are identifiable in `docker network inspect`
  output by name (today they get random Docker names, making any name-based network-membership
  check unevaluable). Hermetic tests: name present in `containers.run` kwargs and equal to the
  expected pattern; **fail-closed name-charset guard (iter-2 R2-L2): `run_id` must match a
  Docker-name-safe pattern (`^[0-9a-f-]{1,64}$` — Celery task UUIDs satisfy it; `_USER_ID_RE`
  admits `@`/`+` and is NOT sufficient) before use in `name=`, error out otherwise**; spawn
  path removes/handles a stale same-name container before `run` (genuinely needed: Celery
  retries reuse the same task_id, so a crashed prior attempt can hold the name — `docker rm
  -f` the exact name on `409 Conflict`, then retry once); existing label
  `nextseek.cc.run=<run_id>` retained.
- [ ] Step 2c (iter-1 M-1): `_staging` bootstrap owned here — extend the startup volume step
  (Files: `startup/steps/volumes.py` or a sibling step + `startup/tests/`) so bootstrap runs a
  one-shot helper (`docker run --rm -v <vol>:/v alpine sh -c 'mkdir -p /v/_staging && chown
  1001 /v/_staging'`) after volume creation; startup tests cover it; DEPLOY/Task 8 inherits it
  as part of `./startup.sh install` (no new operator step). Sidecar `docker compose up` on a
  fresh volume must not fail container-create (Engine refuses a subpath mount whose dir is
  absent — compose `restart:` does NOT retry create failures).
- [ ] Step 3: wire compose + engine. **The sidecar ships in Task 13 with NO `dmac-cc-users`
  mount** (iter-1 M-2 — the placeholder option is deleted): `SIDECAR_STAGING_DIR` points at a
  container-local path (upstream `config.py` only requires the var; `staging.py` mkdirs
  lazily), and a compose test asserts the sidecar has no `dmac-cc-users` mount. Task 14 lands
  the `_staging` subpath mount and flips that test (documented two-state, Task 3/4 precedent).
  Download/stage ops are therefore non-functional-by-design between Tasks 13 and 14 — that gap
  is closed inside this wave, before Tasks 9/10 run any gate. The AGENT mount set stays exactly
  the five enumerated subpaths throughout.
- [ ] Success: `docker compose config` parses with the new service; all Task 5/6 topology tests
  still green (nextseek still NOT on dmac-cc-net; backends excluded); zero skips; every commit
  in this task is green (no deliberately-failing tests committed).
- [ ] Commit: `feat(cc-step7): compose-own the NS sidecar + agent env wiring (G7-11)`

### Task 14: Staging flow design + implementation (download/stage ops)

**Purpose:** upstream stages downloads into `SIDECAR_STAGING_DIR` and a host-run bridge swept
`.complete`-marked artifacts into the agent's scratch the same turn. This integration has no
bridge; the flow must be redesigned for the G7-10 volume world.

**Locked invariants (vetting enforces; mechanism is implementer-grounded in `staging.py` +
`_sidecar_client.py` + `ops.py` at implementation time):**
- Staged artifacts land ONLY in the requesting user's own `{project}/{user}/` subtree (scratch
  or a dedicated child), reachable by that user's agent through its EXISTING enumerated mounts —
  the agent mount set does not grow.
- No cross-user path is ever constructible from a WS request (negative tests: foreign
  project/user components in a staging key must be rejected/normalized — hermetic).
- The sidecar's staging mount is the RESERVED top-level `_staging/` subtree of `dmac-cc-users`
  (recommended; a dedicated volume is the fallback if vetting rejects the reservation) —
  `_staging` cannot collide with project dirnames (`project_dirname()` always emits
  `{pid}-{slug}` with a hyphen; `_staging` has none) and is created before sidecar start
  (Engine subpath rule) by the Task 13 Step 2c bootstrap. **Compose-floor consequence:** if the
  sidecar's compose mount uses YAML `volumes: … subpath:` syntax, the SPEC-7 conditional
  Compose ≥2.26 floor becomes BINDING on every deploy host (Task 2's conditional check
  self-detects this via `compose_config.json`; the Task 2 prose is amended accordingly).
  **Dedicated-volume fallback blast radius (pre-enumerated, iter-1 M-1):**
  `startup/steps/volumes.py` `REQUIRED_VOLUMES` 7→8, Task 2's "seven external volumes"
  greenfield oracle, Task 10 Step 0's "seven `external: true` volumes" wording, `DEPLOY.md`
  bootstrap text, and the validator's expected-volume-set constant — all change together or
  the fallback is rejected.
- Sweep (staging → user subtree) is performed by trusted code (`cc_engine`/Django) — never by
  the agent, never via a whole-volume agent mount, and **never by the sidecar** (its mount is
  locked to `_staging/`, so it cannot write `{project}/{user}/` paths by construction; the
  iter-1-reviewed "or the sidecar itself" alternative was dead-on-arrival and is struck).
- **Same-turn sweep + artifact surfacing (iter-1 H-1; refined iter-2 R2-H1/R2-L6):** the
  PRODUCTION flow's invariant: artifacts staged by turn N are swept — post-op, pre-publish,
  triggered from `run_cc_turn`'s existing publish path in `cc_engine.py` (post-loop,
  pre-`_publish_artifacts`, mirroring upstream `ws.py:276-293`: sweep `.complete`-marked
  request dirs BEFORE the turn's artifact diff) — landing under the requesting user's own
  subtree AND appearing in turn N's published artifact set. The op-result path payload
  (sidecar-container `/staging/...` strings, meaningless in the agent) must be translated to
  the published user-visible location or explicitly documented as superseded by published
  artifacts. Deferring turn-N artifacts to a later schedule (janitor, hourly, next-turn) FAILS
  the invariant; **housekeeping of OLDER completed strays** (e.g. `.complete` dirs left by a
  crashed/timed-out earlier turn) in a later sweep run is PERMITTED recovery, not a violation
  (upstream `staging_sweep.py` keeps `.complete` breadcrumbs for exactly this), and the sweep
  entrypoint below doubles as that recovery path. Hermetic tests: a fake-staged `.complete`
  dir surfaces in the same simulated turn's publish set; a non-`.complete` dir does not; an
  older stray sweeps without being attributed to the current turn; negative control fires.
- **Single trusted sweep entrypoint (iter-2 R2-H1 fix (b) — gate flow decoupled from the
  180 s turn cap):** the sweep is implemented ONCE as a callable trusted-code entrypoint
  (`cc_engine`/Django function, invokable in-process by `run_cc_turn` AND via a documented
  `docker exec nextseek …` management command). `run_cc_turn` calls it in-turn (production
  invariant above). The Task 15 capability gate — whose 9 ops CANNOT run inside a live turn:
  `cc_engine._TIMEOUT_HARD_MAX = 180` s is a hard engine safety bound this wave must NOT relax,
  and 7 ops invoke server-side LLM agents sized at up to 300 s recv each — invokes the SAME
  entrypoint once, immediately after the matrix completes, with the invocation itself recorded
  in evidence (command, exit code, output excerpt). The gate therefore proves the identical
  trusted sweep code path, with explicit provenance, without weakening the turn cap or
  requiring an unachievable in-turn matrix.

- [ ] Step 1: read the real upstream contract (`staging.py` key semantics, `.complete` marker,
  path payload returned to the agent) and write the design note into the task report + a
  failing hermetic test skeleton encoding the invariants above.
- [ ] Step 2: implement + hermetic tests (positive flow with fakes; the cross-user negative
  controls; subpath-reservation guards mirroring Task 6's per-mount exact-value pattern).
- [ ] Success: hermetic suite green; invariants each have a firing negative control
  (mutation-demonstrated RED); no agent-side mount changes beyond the enumerated five.
- [ ] Commit: `feat(cc-step7): user-scoped sidecar staging flow (G7-11)`

### Task 15: Capability gate — all 9 ops live + validator closed-set peers

**Files:** `validate_step7_compose_deploy.py`, `test_step7_compose_deploy.py`,
`test_cc_realstack.py` (RUN_REALSTACK-gated harness additions), `validate_cc_acceptance.py`.

- [ ] Step 1: failing validator tests — new REQUIRED §8 artifact `plugin_ops_matrix.json`.
  **Keys are exactly the 9 bin command names** (`nextseek-entity-extract`, `nextseek-parse`,
  `nextseek-api-read`, `nextseek-api-write`, `nextseek-graph`, `nextseek-report`,
  `nextseek-generate-submission`, `nextseek-query`, `nextseek-plan` — iter-1 L-3: three name
  spaces exist; the matrix uses bin names, with the wire-op mapping recorded per row). Per-op
  record (iter-1 M-4 provenance pinning; iter-3 L-2): `{op, transport, exit_code, excerpt,
  container_id, container_name, image, wall_secs}` where the harness writes
  `container_id`/`container_name`/`image` from `docker inspect` of the executor; the validator requires executor `image` == the CC image recorded in
  `images.json` and the executor attached to `dmac-cc-net` (via `network_inspect.json` join on
  container id/name). Bundle FAILS on: any missing op, any exit 7 (missing backend — the
  amendment's defining failure), any nonzero exit EXCEPT the pinned Layer-2 write form, or
  fabricated-looking excerpts (per-op response-field allowlist). **Layer-2 write alternative
  (machine-checkable pin):** an unconfirmed write leg records exit 5 with stderr code
  `WRITE_BLOCKED`; if the sandbox write is user-approved, the confirmed leg records exit 0 —
  the validator matches these exact fields, nothing looser. **Sweep cross-check (iter-1 H-1;
  hardened iter-2 R2-M4):** `nextseek-report` and `nextseek-generate-submission` rows must
  additionally record `published_path` under the gate user's own `{project}/{user}/` subtree,
  AND the bundle must carry `post_sweep_user_tree_scan.txt` — a harness-captured recursive
  `find` of the gate user's subtree taken AFTER the recorded sweep invocation — in which every
  `published_path` appears (an on-disk artifact check, not a bare string assertion); exit 0
  with dead `/staging/...`-only paths fails. **Anti-fabrication binding (iter-2 R2-M4):** the
  bundle must carry `gate_access_log_window.txt` — the nginx access-log window for the matrix
  run (harness-written from `docker logs` of the nginx container, timestamps inside the gate
  window) — and the validator matches, per op, at least one hit on that op's assistant
  endpoint path within the window (all 9 ops traverse nginx: sidecar ops via
  sidecar→nginx→Django, viewset ops via agent→nginx). **Endpoint-mapping notes (iter-3 L-4):**
  `nextseek-query` and `nextseek-plan` both hit `POST /nextseek_api/assistant/query/async/` —
  their check is ONE shared endpoint-hit requirement, not two (a literal per-op count would
  double-count one request); and the artifact depends on nginx access logging, which
  `docker/nginx.conf` currently leaves to the compiled default — Task 13 pins an explicit
  `access_log` directive so a future conf edit cannot silently kill the gate's evidence
  source. A matrix row set copied from another run cannot supply this window. Per-op request nonces echoed in server responses MAY additionally
  be recorded where the op's contract already echoes request fields (optional strengthening,
  not required).
- [ ] Step 2: failing validator tests — `dmac-cc-net` membership becomes a CLOSED SET,
  **enforceable from names** (iter-1 H-2 — `docker network inspect` carries no labels and
  agents previously had random names; Task 13 Step 2b fixes that): allowed = nginx pattern
  (`nextseek_nginx` / compose-prefixed form), `dmac-bedrock-proxy`, `nextseek-sidecar`, names
  matching the general agent pattern `^dmac-cc-agent-[0-9a-f-]{1,64}$` (iter-2 R2-L5:
  concurrent legitimate turns from other users are lawful on a shared dev VM; MBP greenfield
  has none), and the reserved gate executor `^dmac-cc-matrix-<run_id>$`; anything else fails;
  exact-name `nextseek` rejection retained. ADDITIONALLY the bundle's own
  `dmac-cc-agent-<meta.run_id>` name MUST appear in the during-turn inspect (see Step 3
  capture spec). The existing fragile run_id-substring-in-network-inspect check is REPLACED by
  this deterministic name presence (closes the self-referential-injection seam). Mirror in
  `validate_cc_acceptance.py` where its peer rules are reused; hermetic tests cover bare +
  compose-prefixed forms of every legitimate peer, the general-agent and matrix names, and a
  planted stranger.
- [ ] Step 3: implement; the matrix runs in a DEDICATED GATE EXECUTOR, not inside the live
  turn (iter-2 R2-H1/R2-M2 — the 180 s turn hard cap makes an in-turn 9-op matrix
  structurally unachievable and this wave must not relax the cap): the harness spawns
  `dmac-cc-matrix-<run_id>` with the SAME image as `images.json`'s CC image, attached to
  `dmac-cc-net`, **with env produced by the SAME `build_agent_environment` code path used for
  production agents** (iter-3 M-1: the harness must not hand-assemble the env — it calls the
  engine's own builder, or spawns via an engine helper that does, so every existing env guard
  applies by construction), executes the 9 ops sequentially inside it via `docker exec`
  (per-op timeout pinned; harness controls executor lifetime and removes it after), records
  its `docker inspect` provenance, then invokes the trusted sweep entrypoint (Task 14) and
  captures `post_sweep_user_tree_scan.txt`. **REQUIRED companion artifacts (iter-3 M-1/L-3):**
  `matrix_env_scan.txt` — sanitized env scan of the executor, validated with the SAME
  no-shared-creds rules as `agent_env_scan.txt`; and `sweep_invocation.json` — the recorded
  trusted-sweep invocation `{command, exit_code, output_excerpt, timestamp}` (the validator
  enforces presence + exit 0 whenever the matrix artifacts are present).
  **In-turn viability evaluation (iter-3 M-2 — the gate must not silently certify ops the
  180 s production turn can never run):** the validator computes, per op, an
  `in_turn_viable` verdict = `wall_secs` < the documented headroom constant (pinned:
  150 s = `_TIMEOUT_HARD_MAX` − boot/prompt slack); ops failing it do NOT fail the bundle
  (capability ≠ latency), but the validator output MUST list them and Task 11's handoff MUST
  surface them as a named user decision (raise the cap? accept ops as gate-proven but
  in-turn-degraded? async op pattern?). `wall_secs` is thereby evaluated, not just recorded. **Capture spec (iter-2 R2-M1):** `network_inspect.json`
  becomes the FULL `docker network inspect dmac-cc-net` JSON (containers keyed by ID with
  `Name` fields), captured TWICE into the bundle: `network_inspect.json` DURING the forced
  turn (same poll window as the isolation scan — the post-turn agent is force-removed, so
  only an in-turn capture can contain the agent name) and `network_inspect_matrix.json`
  during the matrix window (contains `dmac-cc-matrix-<run_id>`). Matrix rows gain
  `container_name`; the validator joins row `container_id` → inspect JSON key → `Name` and
  requires `Name == "dmac-cc-matrix-<run_id>"` for EVERY row (iter-3 M-3: the former
  "or the agent name if exec'd in-turn" branch is struck — all matrix rows come from the gate
  executor, no exceptions), image == `images.json` CC image.
- [ ] Step 3b (iter-1 H-3 — seeded fixture, no unachievable gates): the gate harness creates a
  minimal seeded fixture BEFORE the matrix run, as the gate user via the authenticated REST
  API: one sandbox project + a small set of sample UIDs (recorded in a new generated artifact
  `seeded_fixture.json`: what was created, ids, requests used — secret-scanned). All
  data-dependent ops (`report`, `generate-submission`, `api-read`, `graph`) target the seeded
  fixture. Where an op has legitimate empty-data semantics instead, the ONLY acceptable form
  is **exit 0 with a documented SUCCESSFUL-empty excerpt shape** (empty rows / empty
  saved_files WITH the op's success marker, e.g. `ok: true` / no `error` field — iter-2 R2-M3
  + iter-3 L-5: a documented NONZERO exit is never acceptable, and an exit-0 excerpt carrying
  a failure payload such as `{"ok": false, "error": "graph agent produced no cypher"}` MUST
  FAIL the shape check — agent failure is not empty data; if an op cannot produce a
  successful result against the fixture, that is a fixture defect this step fixes by seeding
  more, not a validator allowance). The write-op exercise targets ONLY seeded sandbox
  entities (write-gate confirmed leg needs the user's explicit sign-off recorded in evidence;
  otherwise the exit-5 `WRITE_BLOCKED` leg alone is recorded and the validator accepts the
  pinned Layer-2 form). **Server-side LLM prerequisites:** the 7 sidecar ops invoke
  NExtSEEK-side LLM agents (GCP + Bedrock via the server) — `GCP_API_KEY` (gitignored
  `docker/nextseek.env`) and server-side Bedrock reach are live-gate prerequisites; Task 7's
  key list and Task 8's prerequisites gain them; per-op `wall_secs` + a per-op timeout bound
  the spend, and `meta.json` gains `matrix_spend_estimate_usd` (best-effort estimate + method
  note; exact per-op server-side cost is not programmatically available and that limitation is
  recorded, not hidden).
- [ ] Success: hermetic suite green, zero skips; validator rejects a synthetic 8/9 matrix, a
  matrix with one exit-7, a matrix whose executor image/name provenance mismatches the
  matrix-window inspect, a report row without a user-subtree `published_path`, a
  `published_path` absent from `post_sweep_user_tree_scan.txt`, a bundle missing any of
  {matrix, seeded_fixture, gate_access_log_window, post_sweep_user_tree_scan, either inspect,
  matrix_env_scan, sweep_invocation}, a matrix_env_scan carrying shared creds, a
  sweep_invocation with nonzero exit, and an access-log window lacking a hit for any op
  (query/plan share one endpoint check); closed-set peer check passes the
  legitimate trio + general-pattern agents + this run's required agent name + the matrix
  executor, and fails a planted stranger (bare and compose-prefixed forms both tested).
- [ ] Commit: `test(cc-step7): all-9-ops capability gate + closed-set dmac-cc-net peers (G7-11)`

### Task 16: Debt fixes from the Tasks 1-6 final review

- [ ] Model-pin the Step-7 validator's proxy-invoke check (allowed Opus model id, not
  `_INVOKE_200_GENERIC_RE`-any-model).
- [ ] Create `nextseek_api/cc_assistant/tests/acceptance_evidence/step7/` README (named plan
  artifact; explains generated bundles without being evidence).
- [ ] Raise `validate_step7_compose_deploy.py` coverage to ≥95% (currently 85%; collector 91%)
  and add the gate command with an explicit neutral `--rcfile` to the plan-of-record test docs
  (the repo `.coveragerc` omits `nextseek_api/*/tests/*`, which silently defeated the plan's
  own `--cov` command — record this in the report).
- [ ] Hermetic test pinning `container_name: nextseek` (now load-bearing for the exact-name
  peer check) + fix the 805722b docstring prose ("container_name: nginx*" misdescription).
- [ ] Annotate retired `DMAC_USER_ROOT` in the collector's `CC_ENV_KEYS` (recording-only).
- [ ] Commit: `test(cc-step7): close final-review debt (validator model pin, coverage, pins)`

## Wave permissions (delta over the main table)

| Permission / resource | Tasks | Notes |
|----------------------|-------|-------|
| Read pinned dmac-assistant clone (sidecar/, plugin bin) | 12, 14 | Read-only; verify still @ a429f13 |
| `docker build` sidecar image (throwaway tag) | 12 | Host build OK; no compose up |
| Live-gate execution of 9 plugin ops incl. write-gated ops | 15 (runs in 9/10) | Needs sandbox-entity decision + sign-off for writes |

## Wave risk register (delta)

| Rank | Task | Likely failure | Catastrophic failure | Rollback/guard |
|------|------|----------------|---------------------|----------------|
| 1 | 14 | Staging key admits foreign path components | Cross-user artifact delivery (OI-3 breach) | Hermetic negative controls + closed invariants; sidecar mounts only `_staging` subpath |
| 2 | 13 | Sidecar service DNS ≠ client default | 7 ops still exit-7 in "green" stack | Compose test pins service name == `_sidecar_client` default |
| 3 | 15 | Write-op live exercise mutates real data | Data corruption on dev/MBP | Sandbox-entity design + explicit sign-off; write-gate Layer-2 |
| 4 | 12 | Port drifts from a429f13 or revives A-1/DB deps | Backend-network exposure | Parity sha256 pins + grep guards (SESSION_DB_, nextseek-local) |
| 5 | 15 | Closed-set peer check misses compose-prefixed names | Stranger container passes evidence gate | Test both bare + compose-prefixed forms of all legit peers |

## Wave gameability audit (delta)

| Task | Success condition | Cheapest fake | Remedy |
|------|-------------------|---------------|--------|
| 15 | `plugin_ops_matrix.json` all exit 0 | Hand-written matrix | Harness-written from subprocess stdout; per-op excerpts must contain op-specific response fields; run_id cross-correlated; secret-scanned |
| 15 | 9/9 ops | Drop hard ops from the matrix | Validator pins the exact 9-op name set |
| 14 | "user-scoped staging" | Whole-volume agent mount "temporarily" | Task 6 unenumerated-mount test already fails any sixth agent mount; keep it |
| 12 | "sidecar ported" | Wire upstream compose fragment w/ backend network | Grep guard: no `nextseek-local`, no `db:3306` reach |

## Phase 2 Vetting Log (sidecar wave)

| Iteration | Reviewer | Verdict | Notes |
|-----------|----------|---------|-------|
| 1 | Fresh cold-context (2026-07-02) | **CONDITIONAL_ACCEPTANCE** | 0C/3H/4M/5L — see `.vetting/plan-7-g711-review-1-fresh.md`. H-1 same-turn sweep invariant missing; H-2 closed-set peer rule unimplementable from names-less inspect; H-3 9/9-exit-0 unachievable on greenfield (no seeding, server-side LLM prereqs unstated). All load-bearing upstream claims verified TRUE |
| 2 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-1: H-1 fifth invariant (in-turn sweep + published_path cross-check) + sidecar-sweeper clause struck; H-2 deterministic agent names (`dmac-cc-agent-<run_id>`, Task 13 Step 2b) + name-based closed set replacing run_id-substring seam; H-3 seeded fixture (`seeded_fixture.json`, Step 3b) + Layer-2 exit-5/WRITE_BLOCKED pin + server-side LLM prereqs into Tasks 7/8 + spend estimate; M-1 `_staging` bootstrap owned (Step 2c) + fallback blast radius pre-enumerated; M-2 placeholder deleted (no-mount two-state); M-3 Task 2/7/8 locked-text amendments; M-4 matrix provenance (container_id/image/network join); L-1..L-5 all applied |
| 3 | Fresh cold-context (2026-07-02, iter 2) | **CONDITIONAL_ACCEPTANCE** | 0C/1H/4M/6L — see `.vetting/plan-7-g711-review-2-fresh.md`. Threads G/H/I/J all RE-RAISED PARTIAL. R2-H1: in-turn matrix structurally unachievable under the 180 s hard turn cap; R2-M1 inspect timing/format/join-key; R2-M2 DOA harness-executor clause; R2-M3 empty-data hatch contradicts pinned exit rule; R2-M4 no anti-fabrication binding. Hardener mechanisms verified real (publish-path hook, uid 1001, seed dumps, endpoint existence) |
| 4 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-2: R2-H1 fix (b) — single trusted sweep entrypoint (in-turn for production, harness-invoked for the gate) + dedicated gate executor `dmac-cc-matrix-<run_id>` (no turn-cap relaxation); R2-M1 dual FULL network inspects (during-turn + matrix-window) + `container_name` in rows + id→Name join; R2-M2 clause replaced by the reserved matrix executor in the closed set; R2-M3 hatch pinned to exit-0-with-empty-shape only; R2-M4 `gate_access_log_window.txt` per-op nginx-hit match + `post_sweep_user_tree_scan.txt` on-disk published_path check; R2-L1 tracker-7b obligation recorded (preamble + Task 11); R2-L2 name-charset guard; R2-L3 §2 bin names; R2-L4 target-contract tense; R2-L5 general agent pattern + own-name-required; R2-L6 stray-housekeeping carve-out |
| 5 | Fresh cold-context (2026-07-02, iter 3) | **CONDITIONAL_ACCEPTANCE** | 0C/0H/3M/5L — see `.vetting/plan-7-g711-review-3-fresh.md`. Threads **I + J CLOSED** (exit-0-only hatch proven achievable per-op vs granular.py; all locked-text amendments verified). G/H re-raised PARTIAL: M-1 executor env asserted not verified (bypasses build_agent_environment guards, no env-scan artifact); M-2 uncapped gate never evaluates wall_secs vs the 180 s production cap; M-3 in-turn-agent-name join branch contradicts §8 |
| 6 | Hardener (orchestrator) — **not a reviewer** | *(pending fresh re-vet)* | Iter-3: M-1 executor env via production `build_agent_environment` code path + REQUIRED `matrix_env_scan.txt` (same no-shared-creds validation); M-2 per-op `wall_secs` evaluated vs pinned 150 s in-turn headroom — bundle stays green but exceeders MUST appear in validator output + Task 11 handoff as a named user decision; M-3 branch struck (all rows executor-only); L-1 §8 parenthetical fixed; L-2 container_name in Step 1 schema; L-3 `sweep_invocation.json` pinned; L-4 explicit nginx `access_log` directive (Task 13 Files) + query/plan shared-endpoint note; L-5 successful-empty shape only (ok:false payload MUST FAIL) |
| 7 | Fresh cold-context (2026-07-02, iter 4) | **UNCONDITIONAL_ACCEPTANCE** | **0C / 0H / 0M** (6 LOW/cosmetic) — see `.vetting/plan-7-g711-review-4-fresh.md`. **Threads G + H CLOSED** (build_agent_environment verified harness-callable outside a turn + sufficient for all 9 bin ops; matrix_env_scan reuses shipped `_CC_SHARED_CRED_KEYS` oracle; wall_secs machine-evaluated vs single pinned constant; in-turn-agent branch struck with no residue). Threads I/J damage-checked, not reopened |

**Sidecar-wave Phase 2 status: ✅ COMPLETE** — iter-4 independent fresh reviewer returned **UNCONDITIONAL_ACCEPTANCE** (zero MEDIUM+). All G7-11 threads G–J CLOSED. Per loop rule, not reopened. Implementation of Tasks 12-16 is gated on a user go (same checkpoint discipline as the original Phase-2→3 gate).
