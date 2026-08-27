# Container-CC (cc_assistant) deployment — compose-native procedure

The Container-CC integration is deployed **entirely by the root
`docker-compose.yml`** — the bedrock auth-proxy, the NS sidecar, the
segmented `dmac-cc-net` network, the `dmac-cc-users` volume, and the
`dmac-assistant:poc` agent image are all first-class, declaratively-owned
parts of the one-command stack bring-up. There is **no** separate repo
checkout, no manual network surgery, and no host-path preparation in the
required path. (The old two-part manual sidecar bootstrap this file used to
describe is retired; if you find yourself hand-creating networks or host
directories, you are following a stale document.)

Full-stack deployment hygiene (redeploys, rollback, verification checklist,
config inventory) lives in the repo-root [`DEPLOYMENT.md`](../../DEPLOYMENT.md).
This file covers only what is CC-specific.

Topology (preserves the OI-3 agent isolation):

```
            dmac-cc-net  (dedicated; NO neo4j/mysql/seek/solr)
        ┌───────────────┬─────────────────┬──────────────────┐
        │               │                 │                  │
  dmac-bedrock-proxy   nextseek_nginx   nextseek-sidecar   <per-turn CC agent>
  (holds AWS bearer    (dual-homed,     (no credentials;   (uid 1001, ZERO aws
   token; allowlist     also on the      _staging-subpath    creds; Bedrock only
   opus-4-8 only)       default stack    writes only)        via the proxy,
                        network)                             NExtSEEK only via
                                                             nginx as the user)
```

The `nextseek` service itself is **never** on `dmac-cc-net`; nginx is the only
dual-homed service. Per-turn agent containers are spawned from the
`dmac-assistant:poc` image by the app via the docker socket — the compose
`cc-agent` stanza is a build target only, never a running service.

## Procedure

0. **Prerequisites:** Docker Engine ≥ 26 (API 1.45+, needed for the
   sidecar's volume-subpath mount) and Compose plugin ≥ 2.26. Check with
   `docker version` and `docker compose version`.
1. **Pull/clone the deploy branch:**
   `git clone -b dev https://github.com/BioMicroCenter/NExtSEEK.git`
   (or `git fetch origin dev && git merge --ff-only origin/dev` in an
   existing deploy clone).
2. **Fill the gitignored config.** Secrets live **only** in gitignored
   files: export `AWS_BEARER_TOKEN_BEDROCK` (and optionally `AWS_REGION`) in
   your shell before install so the installer renders
   `docker/bedrock-proxy/proxy-secret.env` (mode 0600) itself — never copy
   the token out of a running container, and never commit it. The same token
   also belongs in `docker/nextseek.env` for the native (non-CC) chat path;
   see DEPLOYMENT.md §8.
3. **Bootstrap volumes + config + seeds + stack:** `./startup.sh install`
   — this creates the external volumes compose expects (including
   `dmac-cc-users` with its `_staging` subpath bootstrap), renders all env
   files, seeds the databases, builds every image **including the cc-agent
   image**, and starts the CC services.
4. **(Redeploys)** use the guarded component verbs from DEPLOYMENT.md §3:
   `./startup.sh rebuild --component cc-agent`, `bedrock-proxy`, or
   `nextseek-sidecar`; use `custom-stack` when all first-party images changed.
   These create verified local rollback tags and private GHCR baselines.
   The agent target is build-only: the next chat turn uses it, with no
   persistent container to restart.
5. **(Redeploys)** let the rebuild CLI recreate affected long-running
   services with `--no-deps --force-recreate`; do not bypass its safety gates
   with raw Compose commands.
6. **Verify** — run the checks below plus DEPLOYMENT.md §6.

## Verification

CC route wired end-to-end (checks daemon, agent image, network — in order;
the image has no bare `python` on PATH, so use `uv run --no-sync`, which
executes in the app env `/app/.venv` without modifying it):

```bash
docker exec nextseek uv run --no-sync python -c "from nextseek_api.cc_assistant import cc_engine; print(cc_engine.cc_runner_available())"
# -> (True, 'ok')
```

Proxy contract, from a container ON dmac-cc-net (unsigned, exactly like the
agent). The healthz and sonnet probes are free; the **opus invoke is a real,
PAID one-token Bedrock call** — it needs the same per-run owner approval as
any live spend, and on a token-less install it returns 500 ("proxy
misconfigured: no bearer token"), which is expected there:

```bash
docker run --rm --network dmac-cc-net --entrypoint sh dmac-assistant:poc -c '
  B=http://bedrock-proxy:8080
  curl -s -o /dev/null -w "healthz=%{http_code}\n" $B/healthz                       # 200 (free)
  curl -s -o /dev/null -w "sonnet=%{http_code}\n" -X POST $B/model/us.anthropic.claude-sonnet-4-6/invoke -d "{}"  # 403 (free; allowlist rejects pre-Bedrock)
  # PAID (approval-gated; 500 expected when the proxy token is empty):
  curl -s -o /dev/null -w "opus=%{http_code}\n"   -X POST $B/model/us.anthropic.claude-opus-4-8/invoke \
       -H content-type:application/json -d "{\"anthropic_version\":\"bedrock-2023-05-31\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"   # 200
'
docker logs dmac-bedrock-proxy 2>&1 | grep -c -E "ABSK|Authorization"   # 0  (token never logged)
```

Per-container env boundaries (key **names** only — never dump values):

```bash
docker inspect nextseek-sidecar   --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
docker inspect dmac-bedrock-proxy --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
```

The sidecar holds no credentials (only its base-URL/staging/port config); the
proxy holds exactly the Bedrock token + region; the agent env is built solely
by `cc_engine.build_agent_environment` and contains none of the 16 shared
backend credentials (enumerated in `tests/validate_cc_acceptance.py`).

## Acceptance (paid, gated)

```bash
# native 8/8 regression baseline
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.assistant.tests.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb -v2'

# the Container-CC route, end-to-end (router=baml -> real Opus via proxy -> publish)
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.cc_assistant.tests.test_cc_realstack \
   --settings=dmac.test_settings_realstack --noinput -v2'

# reproducible re-check of a committed evidence bundle (zero spend; use
# `uv run --no-sync` — the image has no bare `python` on PATH)
docker exec nextseek uv run --no-sync python -m nextseek_api.cc_assistant.tests.validate_cc_acceptance \
  outputs/cc_acceptance/<run_id>

# full Step-7 compose-deploy evidence bundle re-validation (zero spend, 61
# checks; runs on the HOST from the repo root — stdlib-only module)
python3 -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir> [repo_root]
```

Both live suites are skipped unless `RUN_REALSTACK=1` is set explicitly, and
require the owner's per-run approval — they spend real LLM budget.
