# Spec: Step 7 - Compose-native prod deploy

**Date:** 2026-06-30
**Tracker:** `integration-plan.json` step **7** ("Compose-native prod deploy") - substeps 7a-7d.
**Status:** design, awaiting user review -> writing-plans
**Builds on:** Steps **0-3**. Step 7 must be re-grounded after Step 3 implementation before editing any compose, env, deploy, artifact, or evidence paths.
**Deadline:** NExtSEEK prod before **2026-07-14**.

> **Planning-session boundary.** This document specifies the future Step 7 implementation.
> It is not itself an implementation of Step 7.

---

## 1. Problem

Container-CC works on the current dev VM only because important deployment pieces still live
outside the tracked root compose workflow:

- `nextseek_api/cc_assistant/DEPLOY.md` still describes a manual Phase A/B bootstrap:
  separate `dmac-assistant` repo build, `docker network create dmac-cc-net`,
  separate bedrock-proxy compose, and ad-hoc `docker network connect` for nginx.
- The root `docker-compose.yml` does not own `bedrock-proxy`, a CC agent image build target,
  or top-level networks.
- `docker/nextseek.env.example` does not document the required Container-CC non-secret
  topology keys.
- The current runtime depends on images (`dmac-assistant:poc`, `dmac-bedrock-proxy:poc`)
  whose source is not fully owned by NExtSEEK.

That violates the user's deploy bar: a clean non-dev machine must be able to deploy full
Container-CC from the NExtSEEK repo alone with `git pull`/clone, gitignored secrets, external
volume bootstrap (including CC user-tree persistence), and `docker compose build && docker compose up -d`.

## 2. Goal / success criteria

- **Repo-owned runtime:** the NExtSEEK repo contains the actual Container-CC runtime code/assets
  needed to build the agent image and Bedrock proxy image. A clean host does not need the
  standalone `dmac-assistant` repo.
- **Compose-native topology:** root compose owns the proxy service, CC image build target,
  `dmac-cc-net`, nginx dual-homing, and the **`dmac-cc-users` external named volume** for
  Step 2 per-user trees. No required manual network creation or network connect.
- **Volume-backed CC user trees:** Step 2 layout (`input/`, `scratch/`, `cc-state/`, `output/`)
  persists in compose-declared external volume **`dmac-cc-users`**, mounted into `nextseek` at
  `DMAC_USER_ROOT_MOUNT` (default `/dmac/users`). Data survives container recreate; it is lost
  only on explicit volume removal. **No required host bind** under `/srv/dmac/users` and no
  manual host `mkdir`/`sudo`/`chmod` for operator bring-up.
- **Operator env contract:** operators fill only gitignored local files
  (`docker/nextseek.env`, `docker/db.env`, `dmac/local_settings.py`, and any gitignored
  proxy-secret file if retained). Tracked templates contain key names and safe defaults only.
- **Isolation preserved:** the CC agent gets only the logged-in user's own NExtSEEK credentials
  plus non-secret topology. Shared AWS/GCP/DB/backend credentials remain out of reach. Bedrock
  credentials live only in `bedrock-proxy`.
- **Capability completeness (G7-11, amended 2026-07-01):** the deployed stack delivers a
  **functional** assistant, not just a running one — **all 9 `nextseek-*` plugin ops** shipped in
  the CC agent image have a working backend and are proven live. The 7 sidecar-backed ops
  (bin commands `nextseek-entity-extract`, `nextseek-parse`, `nextseek-api-read`,
  `nextseek-api-write`, `nextseek-graph`, `nextseek-report`, `nextseek-generate-submission`)
  require the in-tree **NS sidecar** service on `dmac-cc-net`. Step 7 is **incomplete while any
  shipped plugin op lacks a working backend** (user ruling 2026-07-01; see Amendment Log).
- **Greenfield verification:** Step 7 is not complete until the user's local MBP, not prod and
  not this dev VM, proves compose-only bring-up from the Step 7 branch before dev merge.
- **Generated evidence only:** acceptance evidence is machine-generated and validated. Markdown
  may index evidence but is never the evidence itself.

## 3. Target topology

Root compose owns the whole deployment shape:

```
default compose network:
  nextseek <-> db / seek / solr / neo4j / nextseek_nginx

dmac-cc-net:
  bedrock-proxy <-> nextseek_nginx <-> transient per-turn CC agent containers
  nextseek-sidecar <-> nextseek_nginx (HTTP)  /  <- transient CC agents (WS :8765)

not on dmac-cc-net:
  db, seek, seek-workers, solr, neo4j
```

- `nextseek`: Django/NExtSEEK app. It may keep Docker socket access for spawning transient
  CC sibling containers, but it must not leak shared backend secrets into those containers.
  Mounts external volume **`dmac-cc-users`** at **`/dmac/users`** (`DMAC_USER_ROOT_MOUNT`) for
  Step 2 per-user trees. Django mkdir/publish uses in-container paths under that mount; sibling
  CC containers receive **volume subpath** mounts (not host bind sources under `/srv/dmac`).
- `nextseek_nginx`: dual-homed via compose networks, providing the CC agent's only route back
  to NExtSEEK.
- `bedrock-proxy`: in-tree source build. It holds `AWS_BEARER_TOKEN_BEDROCK`, allowlists the
  approved Bedrock model path, exposes no host port, and logs no token/header/body secrets.
- CC agent image: built from NExtSEEK-owned runtime assets. The future implementation should
  preserve the current `NEXTSEEK_CC_IMAGE`/`dmac-assistant:poc` contract unless it deliberately
  changes that contract with tests and docs.
- `dmac-cc-net`: compose-managed segmented network. It must not be created manually.
- `nextseek-sidecar` (G7-11): in-tree source build of the upstream NS sidecar (post-T16/T17
  rewire: a lean `httpx` WS→HTTP forwarder). Serves the agent's 7 sidecar-backed plugin ops on
  WS port 8765, `dmac-cc-net` only, **no host `ports:`**. Reaches NExtSEEK exclusively via the
  dual-homed `nextseek_nginx` (`NEXTSEEK_BASE_URL=http://nextseek_nginx`). **Holds no
  credentials**: per-request user Basic auth travels inside each WS frame from the agent
  (which holds only the logged-in user's own NExtSEEK login — unchanged OI-3 boundary); its env
  is non-secret topology only (`NEXTSEEK_BASE_URL`, `SIDECAR_STAGING_DIR`, `SIDECAR_WS_PORT`).
  The upstream A-1 backend-network attachment (`nextseek-local` for `db:3306`) is dead code
  post-T16 and is **not** ported — the sidecar never joins the default stack network.

## 4. Runtime port scope

Port the full runtime tree required for the CC agent image, not a static image and not a
submodule dependency. This includes, subject to re-grounding against the standalone repo at
implementation time:

- CC image Dockerfile and Docker build hygiene, including secret-excluding `.dockerignore`.
- `container/` runtime assets: `CLAUDE.md`, entrypoint, and runner helpers.
- Claude Code plugin tree (`build_context/plugins/nextseek`) including commands, skills, bin
  scripts, and context catalogs.
- Route/model config files consumed by the integrated NExtSEEK router.
- Runtime docs/context/BAML/e2e helper assets needed by the image build or runtime.
- `pyproject.toml`/`uv.lock` pieces required for the agent image dependency install.
- Bedrock proxy source, Dockerfile, and safe env template.

- **NS sidecar tree (`sidecar/app/…`, Dockerfile) — G7-11 (amended 2026-07-01):** the sidecar is
  **in scope** and ported in-tree. It is *not* the "old standalone server layer": the excluded
  layer is the deprecated dmac-assistant **app/WS chat server** (`src/` FastAPI server,
  `main.py`), which duplicated what NExtSEEK's Django bridge now does. The sidecar is the
  **op-proxy backend** that 7 of the 9 shipped plugin bin commands hard-require
  (`_sidecar_client.py` → WS `nextseek-sidecar:8765`; no fallback path — exit 7 without it).
  The original phrasing below conflated the two, which silently shipped an agent with 2/9
  working NExtSEEK tools; that boundary error is corrected by this amendment.

Do **not** port the old standalone **app/chat websocket/FastAPI server** layer as runnable
infrastructure. NExtSEEK's Django bridge remains the only integrated **app server** path. If old
server files are needed for reference, they must not be wired into compose or imported by
runtime code. This exclusion does **not** cover the NS sidecar op-proxy (see above).

## 5. Plugin context/catalog policy

Preferred path: regenerate plugin context/catalog files during Step 7 implementation from
NExtSEEK-owned tooling.

Fallback path: if generation is blocked by network availability, unavailable source state, or
operator-only credentials, commit a generated snapshot into the NExtSEEK runtime tree. The
fallback is acceptable only if generated evidence records:

- the attempted generation command,
- the failure/blocker,
- the snapshot source commit/path,
- and validator checks proving the image contains the expected context files.

## 6. Compose and env contract

`docker-compose.yml` must make the compose graph self-contained for Container-CC. Future
implementation must add tests or validators that inspect `docker compose config`, not just
source text, because compose interpolation is the deploy contract.

Root compose must declare external volume **`dmac-cc-users`** and mount it into `nextseek` at
`DMAC_USER_ROOT_MOUNT`. The six existing SEEK stack external volumes remain required. Bootstrap
all external volumes via **`./startup.sh install`** (or a documented volume-create subset with
the same effect) before `docker compose up -d` on a clean host.

`docker/nextseek.env.example` must document every non-secret CC key needed by the integrated
runtime, including image/network/proxy URL/router config, **`DMAC_CC_USERS_VOLUME`**
(default `dmac-cc-users`), **`DMAC_USER_ROOT_MOUNT`**, and server knobs. It must not
document `/srv/dmac/users` as the deploy-time host root. Legacy env name `DMAC_USER_ROOT`
(if retained for compatibility) must not imply a required host bind path in the Step 7 deploy
contract. It must not contain real secrets or secret-like sample values.

Real secrets remain in gitignored files. Any new proxy-specific secret file must have:

- a tracked `.example` with key names only,
- a gitignore/build-context exclusion for the real file,
- and a test/validator proving the real file cannot be committed or copied into the image build
  context by accident.

## 7. Deploy docs

`nextseek_api/cc_assistant/DEPLOY.md` becomes the authoritative operator procedure:

1. pull or clone the correct NExtSEEK branch,
2. copy/fill gitignored config from tracked examples,
3. bootstrap external Docker volumes — run **`./startup.sh install`** (or the documented
   volume-create subset that creates the six SEEK volumes **and** `dmac-cc-users`),
4. run `docker compose build`,
5. run `docker compose up -d`,
6. run the Step 7 verifier.

The required path must not include the old manual Phase A/B bootstrap or host preparation
under `/srv/dmac/users`. Top-level README/startup docs should point operators to the
authoritative CC deploy doc instead of duplicating it.

## 8. Evidence contract

Step 7 evidence must live under a tracked generated-evidence path:

```
nextseek_api/cc_assistant/tests/acceptance_evidence/step7/<run_id>/
```

Required generated artifacts:

- `preflight.json` — **readiness + re-grounding snapshot** (Task 1). Records branch/commit,
  dirty status, file hashes for compose/DEPLOY/env/PLAN-3/SPEC-3, docker version summary, and
  **`step3_deploy_gate`** object:
  - `integration_plan_path`, `integration_plan_sha256`, `tracker_step3_status` (must be `"done"`)
  - `live_evidence_path` (must be `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/`)
  - `live_gate_transcript_committed` (boolean — must be true: secret-scanned
    `live_gate_transcript.txt` committed on the branch under test)
  - `user_signoff_handoff_path` (supplementary SRS handoff JSON — not a substitute for committed
    live transcript)
  - `canonical_integration_plan_sha256`, `docker_engine_meets_subpath_floor` (bool),
    `docker_compose_meets_subpath_floor` (bool, Compose plugin ≥2.26)
  - `deploy_commit` — full SHA of `HEAD` at preflight collection on the branch under test; validator re-checks `live_gate_transcript.txt` at this exact SHA and requires `preflight.deploy_commit == meta.json.repo_commit`
  - `pre_step3_snapshot_tag` — git tag or annotated ref for `:pre-step3` image snapshot taken before Step 3 deploy (empty string if not tagged)
- `pre_bootstrap_docker_volume_ls.txt` — `docker volume ls` before `./startup.sh install` (MBP required).
- `pre_bootstrap_docker_network_ls.txt` — `docker network ls` before bootstrap (MBP required).
- `meta.json` - run id, **`repo_commit`**, **`repo_branch`**, **`host_label`** (locked enum: `"mbp"` for MBP authoritative gate; `"dev-vm"` or `"nextseek-dev"` for dev smoke only), timestamp, verifier version, `budget_cap_usd`.
- `compose_config.json` - normalized `docker compose config` data or equivalent parse.
- `compose_services.txt` / `docker_ps.txt` - service/image status after bring-up.
- `images.json` - image tags/ids used by the run.
- `network_inspect.json` - `dmac-cc-net` peers — **full `docker network inspect` JSON
  (containers keyed by ID with Names), captured DURING the forced turn** (iter-2 R2-M1: the
  agent is force-removed at turn end; a names-only post-turn capture can satisfy neither the
  closed-set rule nor the agent-name presence check). Companion `network_inspect_matrix.json`:
  same format, captured during the capability-matrix window.
- `cc_runner_available.json` - exact result of `cc_runner_available()`.
- `forced_cc_result.json` - terminal forced-CC result with sentinel, error flag, cost.
- `proxy_log_window.txt` - only the log window for the run.
- `agent_env_scan.txt` - sanitized env scan of the transient CC agent.
- `pre_turn_seed_scan.txt` - **REQUIRED seed-presence proof (amended 2026-06-30 — additive/strengthening).** A root-mounted recursive listing of the `dmac-cc-users` volume (`docker run --rm -v <vol>:/v alpine find /v -maxdepth 4`), written by the harness **immediately after seeding the foreign tree and before the turn**. The validator **fails** the bundle unless this scan is non-empty and contains **every** `meta.json.foreign_token` (`SENTINEL_FOREIGN`, `otherproj`, `bob`). This proves the foreign subtree actually exists at the volume root, so the foreign-absent oracle on `subpath_isolation_scan.txt` is **not vacuous**: skipping the seed turns *this* scan RED *before* the turn runs, while a real `Subpath=""` leak turns the *in-turn* scan RED — the two scans together are the cross-user isolation gate. (Without this artifact, an unseeded volume yields an in-turn scan with zero foreign tokens and the gate passes a genuine whole-volume leak.)
- `subpath_isolation_scan.txt` - **REQUIRED cross-user isolation proof (amended 2026-06-30; capture mechanism corrected 2026-06-30 — requirement unchanged).** A **recursive** listing of the transient CC agent's mounted trees (e.g. `docker exec <cid> find /data/input /data/scratch /data/shared … -maxdepth 4`), **captured during the turn** from the live sibling polled by `label=nextseek.cc.run=<run_id>` — the agent is force-removed when the turn ends, so a post-turn `docker exec` cannot produce this artifact, and a non-recursive `ls` cannot reach a seeded foreign sentinel — taken with at least one **other** user's tree seeded on `dmac-cc-users` (e.g. `otherproj/bob/input/SENTINEL_FOREIGN`), proving the sibling container sees **only** the forced-CC user's own `<project>/<user>/` subpath and no foreign user tree. The validator **fails** the bundle if this artifact is absent, empty, or shows any foreign user path / seeded foreign token. **Authenticity binding (amended 2026-06-30 — additive/strengthening):** the file MUST be written by the **test harness directly from the live `docker exec … find` subprocess stdout** (never hand-authored by an operator), and the bundle MUST also carry an **in-container live sentinel** — a per-run file (e.g. `LIVE_<sentinel>`) the agent writes into its own `/data/scratch` *during* the turn — whose filename the captured scan MUST contain (recorded as `meta.json.live_sentinel`). The validator fails the bundle if the live sentinel is absent from the scan, so a fabricated/stale clean scan (e.g. one hand-edited to hide a real `Subpath=""` leak) cannot pass the cross-check. This enforces the OI-3 / G7-10 per-user `VolumeOptions.Subpath` isolation invariant at runtime — a check hermetic mount tests alone cannot make (an empty/constant `Subpath` keeps the key present yet mounts the whole volume root). **Seed-presence pairing + leak-detector clarification (amended 2026-06-30 — additive/strengthening):** the foreign-absent check here is meaningful **only** because `pre_turn_seed_scan.txt` independently proves the foreign tree was planted at the volume root before the turn. The leak detector is **foreign-token absence in-turn, gated by foreign-token presence pre-turn** — *not* the live sentinel. The live sentinel is present under **both** the correct and the leaking mount (the agent writes `LIVE_<sentinel>` to the **container path** `/data/scratch`, which the harness `find`s on that same container path regardless of where it is mounted), so it does **not** drop on a leak; its sole role is an anti-stale / anti-substitution binding (it keeps a clean scan from a *different* run out of this bundle).
- `secret_scan_report.json` - scanner results for every evidence artifact.
- `plugin_ops_matrix.json` — **REQUIRED capability-completeness proof (G7-11, amended
  2026-07-01; schema hardened 2026-07-02 iter-1).** Per-op live results for **all 9** plugin
  ops, keyed by **bin command name** (`nextseek-entity-extract` … `nextseek-plan`; wire-op
  mapping recorded per row), executed in the **dedicated gate executor**
  `dmac-cc-matrix-<run_id>` (harness-spawned: same CC image as `images.json`, attached to
  `dmac-cc-net`, agent env contract with the gate user's own creds, recorded `docker inspect`
  provenance; the matrix CANNOT run inside a live turn — the engine's 180 s hard turn cap is
  not relaxable and 7 ops invoke server-side LLM agents). Per-op record:
  `{op, transport, exit_code, excerpt, container_id, container_name, image, wall_secs}` — the
  validator joins row `container_id` → the matrix-window full `docker network inspect` JSON
  (`network_inspect_matrix.json`, containers keyed by ID with Names) and requires
  `Name == dmac-cc-matrix-<run_id>` and `image` == the CC image in `images.json`. Companion
  REQUIRED artifacts: `gate_access_log_window.txt` (nginx access-log window for the gate;
  validator matches ≥1 hit per op endpoint inside the window — anti-fabrication) and
  `post_sweep_user_tree_scan.txt` (recursive `find` of the gate user's subtree after the
  recorded trusted-sweep invocation; every recorded `published_path` must appear on disk).
  Fails on: any missing op; any exit 7
  (`TRANSPORT_ERROR` — missing backend); any nonzero exit except the pinned Layer-2 write form
  (unconfirmed write leg = exit 5 + stderr `WRITE_BLOCKED`; user-approved confirmed leg =
  exit 0). `nextseek-report`/`nextseek-generate-submission` rows must record `published_path`
  under the gate user's own `{project}/{user}/` subtree (the post-matrix trusted-sweep
  invocation's output, recorded in **`sweep_invocation.json`** `{command, exit_code,
  output_excerpt, timestamp}` — iter-3 L-1/L-3; dead `/staging/...`-only paths fail). The
  executor's env is produced by the production `build_agent_environment` code path and proven
  by **`matrix_env_scan.txt`** (same no-shared-creds validation as `agent_env_scan.txt` —
  iter-3 M-1); every row's `Name` must equal `dmac-cc-matrix-<run_id>` (no in-turn-agent
  exception — iter-3 M-3). The validator evaluates per-op `wall_secs` against the documented
  150 s in-turn headroom constant: ops exceeding it stay bundle-green (capability ≠ latency)
  but MUST be listed in `validator_output.txt` and surfaced by Task 11's handoff as a named
  user decision (iter-3 M-2). Data-dependent ops target the seeded fixture recorded in
  **`seeded_fixture.json`** (companion REQUIRED artifact when the matrix runs: sandbox
  project/sample ids created via the gate user's authenticated REST calls). Secret-scanned like
  every artifact; `meta.json` gains `matrix_spend_estimate_usd` (best-effort + method note).
- `validator_output.txt` - output of the zero-spend Step 7 validator.
- Optional screenshots plus OCR output or documented visual review entries in
  `secret_scan_report.json`.

Handwritten Markdown can summarize paths and reproduction steps, but a validator must not accept
Markdown prose as proof.

## 9. Secret scan requirements

The validator must fail evidence if any generated artifact or screenshot review detects:

- `AWS_BEARER_TOKEN_BEDROCK`, `Authorization`, `Bearer `,
- `ABSK` or other known Bedrock token markers,
- Django secret key values,
- GCP/API keys,
- MySQL/Neo4j/SEEK shared backend credential key names or known dev secret values,
- unredacted `NEXTSEEK_PASSWORD` except where the evidence explicitly proves only the logged-in
  user's own credential was injected into the agent and masks the value,
- any real token/password-looking value matched by the scanner's high-confidence patterns.

The scanner must include negative-control tests with seeded fake leaks so reviewers can verify
the evidence gate is not vacuous.

## 10. Testing

Hermetic / zero-spend:

- compose-config parser tests (including **`dmac-cc-users` volume mount** and **absence of
  `/srv/dmac/users` host bind** as the primary CC store),
- env-template key and no-secret tests,
- build-context secret exclusion tests,
- runtime-file presence tests for prompt/plugin/context,
- agent-env leak tests,
- evidence validator positive/negative tests.

Docker/local:

- root compose builds proxy and CC image from NExtSEEK-owned source,
- compose-up creates the segmented topology without manual network commands,
- `cc_runner_available()==(True, "ok")`.

Docker/local additions (G7-11):

- compose-config tests: `nextseek-sidecar` service present, `dmac-cc-net` only, no host
  `ports:`, healthcheck defined, env carries only non-secret topology keys,
- validator/acceptance network-peer rules updated: `dmac-cc-net` membership is the **closed
  set** {`nextseek_nginx` (bare or compose-prefixed), `dmac-bedrock-proxy`, `nextseek-sidecar`}
  plus transient agents matching the general pattern `^dmac-cc-agent-[0-9a-f-]{1,64}$`, plus
  the gate executor `dmac-cc-matrix-<run_id>` — anything else fails; additionally the bundle's
  own `dmac-cc-agent-<meta.run_id>` must appear in the DURING-TURN full network inspect
  (`network_inspect.json`; the agent is force-removed at turn end, so only in-turn capture can
  contain it), and the matrix executor in `network_inspect_matrix.json`. (Name-based because
  `docker network inspect` exposes no labels; deterministic agent naming is the TARGET
  contract Task 13 Step 2b introduces — current branch code sets labels only — G7-11 iter-1
  H-2, iter-2 R2-M1/L4/L5.)

Live paid gate:

- one forced-CC turn on the MBP clean environment, with a per-run sentinel,
- **all 9 `nextseek-*` plugin ops proven live** (`plugin_ops_matrix.json`, §8) — no exit-7
  transport errors; the 7 sidecar ops exercised through the running `nextseek-sidecar`,
- proxy invoke 200 for the allowed model,
- no shared secrets in agent env,
- no token/header leak in proxy log,
- segmented network excludes backend services,
- generated evidence bundle passes the Step 7 validator.

## 11. Resolved decisions

- **G7-1 - session deliverable:** this planning session produces the Step 7 spec and plan, not
  the Step 7 implementation.
- **G7-2 - integration model:** port actual runtime/proxy code into NExtSEEK; do not depend on
  the standalone `dmac-assistant` repo on a clean deployment host.
- **G7-3 - CC image:** future implementation ports the full CC runtime tree rather than using
  the lean proof image or prebuilt static image as the production target.
- **G7-4 - proxy:** proxy source is in-tree and compose-built.
- **G7-5 - old server layer:** exclude the old standalone WS/server layer as runnable infra.
- **G7-6 - plugin context:** generate if possible; otherwise commit a generated snapshot with
  evidence explaining the fallback.
- **G7-7 - greenfield verification:** run on the user's local MBP before dev merge; not prod,
  not this dev VM.
- **G7-8 - evidence:** generated bundle + validator only; Markdown is not proof.
- **G7-9 - screenshots:** optional, but if included they must be scanned or manually reviewed
  and recorded in `secret_scan_report.json`.
- **G7-11 - NS sidecar in scope; capability completeness (2026-07-01 amend, user ruling):**
  the NS sidecar (post-T16/T17 lean `httpx` WS→HTTP op-proxy) is Step 7 infrastructure: ported
  in-tree, compose-owned on `dmac-cc-net` (no host ports, no credentials, per-request user Basic
  auth only), with `cc_engine` passing `NEXTSEEK_SIDECAR_HOST`/`NEXTSEEK_SIDECAR_PORT` to agents.
  **Step 7 is not done while any shipped plugin op lacks a working backend**, and 7d's live bar
  includes all 9 ops (§8 `plugin_ops_matrix.json`). Deferring the sidecar was a spec-boundary
  error (G7-5's phrasing conflated the op-proxy with the deprecated app server); per the user,
  capability gaps discovered mid-step are **in-scope defects of the current step**, not
  future-step input.
- **G7-10 - CC user persistence (2026-06-30 amend):** Step 2 per-user trees persist in
  external named volume **`dmac-cc-users`**, created by `./startup.sh install` (or equivalent
  volume bootstrap), mounted into `nextseek` at `DMAC_USER_ROOT_MOUNT`. Sibling CC containers
  mount **volume subpaths**, not host paths under `/srv/dmac/users`. Host bind prep is **not**
  part of the deploy contract. Logical layout under the mount (`<project>/<user>/…`) unchanged
  from Step 2 D5.

## 12. Out of scope

- Actual prod deployment.
- Step 4 CI/CD runner implementation.
- Step 5 port-correctness audit.
- Retargeting or merging the PR to `dev`.
- Reintroducing the standalone dmac-assistant **app/chat server** (`src/` FastAPI/WS server,
  `main.py`) as a parallel runtime. (The NS sidecar op-proxy is explicitly **in** scope — G7-11.)

---

## Amendment Log

### 2026-06-30 — G7-10: named volume for CC user trees (Option A)

**Proposed change:** Replace host bind `/srv/dmac/users` as the Step 7 deploy persistence
mechanism with external named volume **`dmac-cc-users`**, bootstrap via `./startup.sh install`,
sibling mounts via volume subpaths. Expand §7 deploy procedure with volume bootstrap step.

**Reason:** User decision after Phase 2 vetting — align CC persistence with `seek-filestore` and
other external volumes; eliminate undeclared host `mkdir`/`sudo`; data survives container
recreate.

**User approval:** Explicit ("go ahead") after choosing Option A over host-bind automation.

**Blast radius:**

| Artifact | Impact |
|----------|--------|
| `PLAN-7-compose-native-prod-deploy.md` Task 6 | Already added — implements G7-10 |
| `PLAN-7` Tasks 8, 10 | Deploy docs and MBP gate updated — no `/srv/dmac` prep |
| `SPEC-2` D5 / `SPEC-3` E8 | Logical tree + mount path unchanged; **host path `/srv/dmac/users` wording superseded for deploy** by G7-10 — update at Step 3/7 implementation, not retroactive spec lock conflict |
| `cc_config.py`, `cc_provision.py`, `cc_engine.py` | Task 6 implementation — volume subpath sibling mounts |
| `startup/steps/volumes.py` | Add `dmac-cc-users` to `REQUIRED_VOLUMES` |
| `docker-compose.yml` | Replace host bind with named volume |
| Dev VM live Step 2 data | One-time migration or wipe (Task 6 Step 4) |

**Re-vet:** PLAN-7 requires fresh Phase 2 reviewer (iter 5) after plan/spec alignment.
SPEC-3 Phase 2 unaffected unless E8 default text is harmonized during Step 3 execution.

### 2026-06-30 — §8: add `preflight.json` + committed Step 3 live transcript gate

**Proposed change:** Add `preflight.json` as a required generated artifact in §8 with
`step3_deploy_gate` schema. Step 7 **must not start** until Step 3 tracker is `done` **and**
secret-scanned `live_gate_transcript.txt` is committed on the integration branch (PLAN-3 Task 13).

**Reason:** User decision during Phase 2 vetting — preflight captures readiness baseline and
anti-stale-state; committed live transcript is mandatory (handoff-only fallback rejected).

**User approval:** Explicit MCQ selection (`amend_now`, `transcript_required`).

**Blast radius:**

| Artifact | Impact |
|----------|--------|
| `PLAN-7` Task 1, Task 2, Gameability | Align gate checks with locked §8 |
| `PLAN-3` Task 13 Step 9 | Transcript commit is hard prerequisite for Step 7 |
| Step 7 validator | Reject bundles missing preflight or committed live transcript on branch |

**Re-vet:** PLAN-7 fresh Phase 2 reviewer after amend.

### 2026-06-30 — §8: add `docker_compose_meets_subpath_floor` to preflight schema

**Proposed change:** Extend `preflight.json` in §8 with `docker_compose_meets_subpath_floor` (bool, Compose plugin ≥2.26) alongside existing `docker_engine_meets_subpath_floor`.

**Reason:** PLAN-7 Task 1/2 require both Engine and Compose subpath floors; locked §8 previously listed Engine only — authority drift.

**User approval:** Phase 2 iter-13 hardening (prior user approved §8 preflight amend 2026-06-30).

**Blast radius:** PLAN-7 Task 1 preflight collector, Task 2 validator, DEPLOY prerequisites docs.

**Re-vet:** Fresh Phase 2 reviewer after plan hardening.

### 2026-06-30 — §8: add `deploy_commit` to preflight schema

**Proposed change:** Extend `preflight.json` in §8 with `deploy_commit` (full SHA at collection time); validator requires `preflight.deploy_commit == meta.json.repo_commit` and re-checks committed `live_gate_transcript.txt` at that SHA.

**Reason:** PLAN-7 Task 1/2 already required `deploy_commit`; locked §8 omitted it — authority drift (iter-16 finding).

**User approval:** Phase 2 iter-16 hardening (prior user approved §8 preflight amend 2026-06-30).

**Blast radius:** PLAN-7 Task 1 preflight collector, Task 2 validator.

**Re-vet:** Fresh Phase 2 reviewer after amend.

### 2026-06-30 — §8: add REQUIRED `subpath_isolation_scan.txt` cross-user isolation artifact

**Proposed change:** Add `subpath_isolation_scan.txt` to the §8 required generated artifacts. The Step 7 validator must fail any bundle that lacks it, has it empty, or finds a foreign `<project>/<user>/` path in it. This promotes the previously "recommended" PLAN-7 Task 10 subpath spot-check to a hard, validator-enforced acceptance gate.

**Reason:** iter-18 Phase 2 fresh review (CRITICAL): every hermetic Task 6 mount assertion checked only that the `VolumeOptions.Subpath` **key** existed, never its per-user **value**. A one-line mutation (`Subpath=""`) mounts the whole `dmac-cc-users` root into each agent — a cross-user data leak that breaks the already-locked OI-3 zero-cross-user-exposure invariant — yet passed all gating checks. This amendment only **adds** a stricter required artifact to raise the gate to the level the locked invariant already demands; it weakens, removes, or contradicts **no** existing locked decision (G7-1…G7-10 unchanged).

**User approval:** Phase 2 iter-18 hardening — additive gate enforcing the existing locked OI-3 invariant (no new product decision).

**Blast radius:** PLAN-7 Task 2 validator (new required-artifact check), Task 6 Step 1/2 (concrete `Subpath`-value hermetic assertions + anti-empty negative control), Task 10 Step 4 (emit `subpath_isolation_scan.txt`; promoted from "recommended" to required), Task 9 (same §8 artifact set).

**Re-vet:** Fresh Phase 2 reviewer after amend.

### 2026-07-01 — G7-11: NS sidecar in scope; capability-completeness bar (user ruling)

**Defect being corrected:** §4's exclusion of "the old standalone websocket/FastAPI server
layer" was read throughout planning and 24 vetting iterations as excluding the **NS sidecar**.
That conflated two different things: the deprecated dmac-assistant **app/chat server** (truly
superseded by NExtSEEK's Django bridge) and the **sidecar op-proxy** that 7 of the 9 shipped
`nextseek-*` plugin bin commands hard-require (`_sidecar_client.py` → WS
`nextseek-sidecar:8765`, no fallback, exit 7 without it). Consequence, verified empirically
2026-07-01 on the live dev instance: the deployed agent has carried all 9 commands since
2026-06-25 while 7 of them fail with `TRANSPORT_ERROR: sidecar unreachable (gaierror)` — the
integration has been shipping an assistant with 2/9 of its NExtSEEK toolkit, undetected because
no live gate ever exercised the sidecar ops.

**User ruling (verbatim intent):** the sidecar belongs **in Step 7** — deferring it was against
the clearly stated goals of the integration; Step 5 is a *verification* step, not a parking lot
for known-missing work; capability gaps discovered mid-step are in-scope defects of the current
step. Step 7 is incomplete while any shipped plugin op lacks a working backend.

**Proposed change (applied):** §2 capability-completeness goal; §3 topology adds
`nextseek-sidecar` (dmac-cc-net only, no host ports, no credentials, per-request user Basic
auth, `NEXTSEEK_BASE_URL=http://nextseek_nginx`; upstream A-1 backend-network attachment is
dead post-T16 and not ported); §4 carve-out distinguishing the excluded app/chat server from
the in-scope sidecar; §8 adds REQUIRED `plugin_ops_matrix.json` (all 9 ops live, exit-7 fails
the bundle); §10 sidecar compose tests + closed-set `dmac-cc-net` membership rule + live-gate
all-9-ops bar; §11 G7-11; §12 clarified.

**Grounding:** upstream sidecar rewire commits 0f5bfdd/bfdf0e3/3c40a1a (T16/T17, 2026-06-13,
ancestors of pinned port source a429f13): 7 ops rewired to NExtSEEK HTTP forwarding, torch +
chat_nextseek dropped, image is `httpx`/`websockets`/`pydantic` only; `sidecar/app/config.py`
requires only `NEXTSEEK_BASE_URL`/`SIDECAR_STAGING_DIR`(/`SIDECAR_WS_PORT`); healthcheck is one
HTTP GET to `{NEXTSEEK_BASE_URL}/nextseek_api/assistant/me/` (200/401 healthy — no DB, no
backend network). Full dossier: session artifact `sidecar-dossier.md` (2026-07-01).

**Blast radius:** PLAN-7 gains a sidecar task wave (port, compose+env wiring, staging sweep
design, capability gate, validator peer-rule closed set); Tasks 7/8 (env template, DEPLOY.md)
must document sidecar keys/service; Tasks 9/10 bundles must include `plugin_ops_matrix.json`;
`cc_engine.build_agent_environment` adds `NEXTSEEK_SIDECAR_HOST`/`NEXTSEEK_SIDECAR_PORT`;
validator + `validate_cc_acceptance` peer rules move from stem-blocklist to closed-set
membership for `dmac-cc-net`.

**User approval:** 2026-07-01 — explicit ruling that the sidecar is Step 7 work; tracker 7a/7d
descriptions amended same day with user-approved wording (status fields untouched).

**Re-vet:** Fresh adversarial reviewers on the amendment + new task wave before implementation,
same Phase-2 bar (defect-lineage ledger, threads stay OPEN until a fresh reviewer clears them).
