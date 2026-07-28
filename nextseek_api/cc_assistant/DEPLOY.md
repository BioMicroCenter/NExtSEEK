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
        ┌───────────────┬───────────────────┐
        │               │                   │
  dmac-bedrock-proxy   nextseek_nginx     <per-turn CC agent>   (uid 1001, ZERO aws creds)
  (holds AWS bearer    (dual-homed, also    reaches Bedrock only via the proxy,
   token; allowlist     on the default       NExtSEEK only via nginx as the user
   opus-4-8 only)       stack network)
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
4. **(Redeploys)** `docker compose build` — rebuilds whatever changed;
   `docker compose build cc-agent` alone refreshes the agent image after a
   `docker/cc-runtime/**` change (the next chat turn picks it up — no
   restart needed).
5. **(Redeploys)** `docker compose up -d` — recreate the affected
   long-running services (scope to `--no-deps <service>` per DEPLOYMENT.md
   §3).
6. **Verify** — run the checks below plus DEPLOYMENT.md §6.

## Verification

CC route wired end-to-end (checks daemon, agent image, network — in order):

```bash
docker exec nextseek python -c "from nextseek_api.cc_assistant import cc_engine; print(cc_engine.cc_runner_available())"
# -> (True, 'ok')
```

Proxy contract, from a container ON dmac-cc-net (unsigned, exactly like the
agent):

```bash
docker run --rm --network dmac-cc-net --entrypoint sh dmac-assistant:poc -c '
  B=http://bedrock-proxy:8080
  curl -s -o /dev/null -w "healthz=%{http_code}\n" $B/healthz                       # 200
  curl -s -o /dev/null -w "opus=%{http_code}\n"   -X POST $B/model/us.anthropic.claude-opus-4-8/invoke \
       -H content-type:application/json -d "{\"anthropic_version\":\"bedrock-2023-05-31\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"   # 200
  curl -s -o /dev/null -w "sonnet=%{http_code}\n" -X POST $B/model/us.anthropic.claude-sonnet-4-6/invoke -d "{}"  # 403
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

# reproducible re-check of a committed evidence bundle (zero spend)
docker exec nextseek python -m nextseek_api.cc_assistant.tests.validate_cc_acceptance \
  outputs/cc_acceptance/<run_id>

# full Step-7 compose-deploy evidence bundle re-validation (zero spend, 61 checks)
python -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir> [repo_root]
```

Both live suites are skipped unless `RUN_REALSTACK=1` is set explicitly, and
require the owner's per-run approval — they spend real LLM budget.
