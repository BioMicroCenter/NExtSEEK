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
Container-CC from the NExtSEEK repo alone with `git pull`/clone, gitignored secrets, and
`docker compose build && docker compose up -d`.

## 2. Goal / success criteria

- **Repo-owned runtime:** the NExtSEEK repo contains the actual Container-CC runtime code/assets
  needed to build the agent image and Bedrock proxy image. A clean host does not need the
  standalone `dmac-assistant` repo.
- **Compose-native topology:** root compose owns the proxy service, CC image build target,
  `dmac-cc-net`, and nginx dual-homing. No required manual network creation or network connect.
- **Operator env contract:** operators fill only gitignored local files
  (`docker/nextseek.env`, `docker/db.env`, `dmac/local_settings.py`, and any gitignored
  proxy-secret file if retained). Tracked templates contain key names and safe defaults only.
- **Isolation preserved:** the CC agent gets only the logged-in user's own NExtSEEK credentials
  plus non-secret topology. Shared AWS/GCP/DB/backend credentials remain out of reach. Bedrock
  credentials live only in `bedrock-proxy`.
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

not on dmac-cc-net:
  db, seek, seek-workers, solr, neo4j
```

- `nextseek`: Django/NExtSEEK app. It may keep Docker socket access for spawning transient
  CC sibling containers, but it must not leak shared backend secrets into those containers.
- `nextseek_nginx`: dual-homed via compose networks, providing the CC agent's only route back
  to NExtSEEK.
- `bedrock-proxy`: in-tree source build. It holds `AWS_BEARER_TOKEN_BEDROCK`, allowlists the
  approved Bedrock model path, exposes no host port, and logs no token/header/body secrets.
- CC agent image: built from NExtSEEK-owned runtime assets. The future implementation should
  preserve the current `NEXTSEEK_CC_IMAGE`/`dmac-assistant:poc` contract unless it deliberately
  changes that contract with tests and docs.
- `dmac-cc-net`: compose-managed segmented network. It must not be created manually.

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

Do **not** port the old standalone websocket/FastAPI server layer as runnable infrastructure.
NExtSEEK's Django bridge remains the only integrated app server path. If old server files are
needed for reference, they must not be wired into compose or imported by runtime code.

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

`docker/nextseek.env.example` must document every non-secret CC key needed by the integrated
runtime, including image/network/proxy URL/router config/user-root/server knobs. It must not
contain real secrets or secret-like sample values.

Real secrets remain in gitignored files. Any new proxy-specific secret file must have:

- a tracked `.example` with key names only,
- a gitignore/build-context exclusion for the real file,
- and a test/validator proving the real file cannot be committed or copied into the image build
  context by accident.

## 7. Deploy docs

`nextseek_api/cc_assistant/DEPLOY.md` becomes the authoritative operator procedure:

1. pull or clone the correct NExtSEEK branch,
2. copy/fill gitignored config from tracked examples,
3. run `docker compose build`,
4. run `docker compose up -d`,
5. run the Step 7 verifier.

The required path must not include the old manual Phase A/B bootstrap. Top-level README/startup
docs should point operators to the authoritative CC deploy doc instead of duplicating it.

## 8. Evidence contract

Step 7 evidence must live under a tracked generated-evidence path:

```
nextseek_api/cc_assistant/tests/acceptance_evidence/step7/<run_id>/
```

Required generated artifacts:

- `meta.json` - run id, host label, repo branch/commit, timestamp, verifier version.
- `compose_config.json` - normalized `docker compose config` data or equivalent parse.
- `compose_services.txt` / `docker_ps.txt` - service/image status after bring-up.
- `images.json` - image tags/ids used by the run.
- `network_inspect.json` - `dmac-cc-net` peers.
- `cc_runner_available.json` - exact result of `cc_runner_available()`.
- `forced_cc_result.json` - terminal forced-CC result with sentinel, error flag, cost.
- `proxy_log_window.txt` - only the log window for the run.
- `agent_env_scan.txt` - sanitized env scan of the transient CC agent.
- `secret_scan_report.json` - scanner results for every evidence artifact.
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

- compose-config parser tests,
- env-template key and no-secret tests,
- build-context secret exclusion tests,
- runtime-file presence tests for prompt/plugin/context,
- agent-env leak tests,
- evidence validator positive/negative tests.

Docker/local:

- root compose builds proxy and CC image from NExtSEEK-owned source,
- compose-up creates the segmented topology without manual network commands,
- `cc_runner_available()==(True, "ok")`.

Live paid gate:

- one forced-CC turn on the MBP clean environment, with a per-run sentinel,
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

## 12. Out of scope

- Actual prod deployment.
- Step 4 CI/CD runner implementation.
- Step 5 port-correctness audit.
- Retargeting or merging the PR to `dev`.
- Reintroducing the standalone dmac-assistant app/server as a parallel runtime.
