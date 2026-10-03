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
   3 CC models)         default stack    writes only)        via the proxy,
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
   `NessieAI/docker/bedrock-proxy/proxy-secret.env` (mode 0600) itself — never copy
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

## Approach 1 (unit B): first deploy and rollback

Unit B (the turn pass, the direct ops road, the turn memory) changes the app,
the agent image and the sidecar at once, and adds two migrations. Follow this
section the first time a box takes it; later redeploys use the Procedure above.

**What ships together.** `app` (Django, the CC engine, migrations
`0025_cc_turn` and `0026_ccturn_ops_cost_partial`, the committed chat bundle),
`cc-agent` (the plugin bins, the prompt hook, the skill and manifest, the
container `CLAUDE.md`, the entrypoint and its baked `expected-settings.json`)
and `nextseek-sidecar` (the turn-pass frame). Rebuild them with one verb,
`./startup.sh rebuild --component custom-stack`; it also rebuilds
`bedrock-proxy`, which is harmless. Why together:

- the new app with the old agent image, or the reverse, makes every
  Container-CC op exit `CONFIG_MISSING` (the new agent needs
  `NEXTSEEK_TURN_PASS`; the old one needs a password the new app no longer
  gives it);
- the new app and agent with the old sidecar make the sidecar road (the light
  rollback below) refuse every frame;
- the deploy clone's checkout and the app image must be the same commit: the
  doctor's CC wiring probe calls `build_agent_environment` with this commit's
  arguments.

`custom-stack` first tags all four images `pre-<timestamp>-<sha>` (each printed
as "rollback tag verified"), then builds app, cc-agent, sidecar and proxy in
that order, then recreates `nextseek`, the sidecar and the proxy together. A
Container-CC turn that starts between the agent image's build and the app's
recreate ends with a setup error, so deploy at a quiet time.

**Pre-checks, read-only, in this order.**

1. Record the deploy clone's commit before you fast-forward it
   (`git rev-parse HEAD`): the full rollback checks it out again.
2. Free disk: `df -h /`. The rebuild measures free disk for its images itself
   (DEPLOYMENT.md §3.2), but migration 0025 copies a table inside the MySQL
   data directory and needs its own room.
3. The size of `assistant_query_task`:

   ```bash
   docker exec -it <mysql-container> mysql -u<user> -p dmac -e "SELECT TABLE_ROWS, ROUND((DATA_LENGTH+INDEX_LENGTH)/1048576) AS mb FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'assistant_query_task';"
   ```

   0025 adds a foreign key to this table, which MySQL does only by copying the
   table: writes to it wait for the copy, the app's boot waits for the
   migration, and the data directory needs about `mb` free on top of what it
   holds. Tell the owner the number before the go.
4. The dump (DEPLOYMENT.md §5.3): `assistant_query_task` and
   `assistant_chat_session` at least; the whole `dmac` schema is safer. The
   full rollback deletes the turn rows, and this dump is their only copy.
5. `DJANGO_SECRET_KEY` is set in `docker/nextseek.env`. Check it without
   printing it:

   ```bash
   docker exec nextseek sh -c 'test -n "$DJANGO_SECRET_KEY" && echo set || echo MISSING'
   ```

   The key that encrypts each turn's held login is derived from it: with no
   secret key no pass is issued, and every Container-CC turn ends with a setup
   error. Do not change it during the deploy; a running turn could no longer
   read its login.
6. **Never run `manage.py sqlmigrate nextseek_api 0025` on a box.** It is not
   a preview: it runs the migration's table heal and creates
   `assistant_cc_turn`.

**Switches.** All optional, with code defaults, so the env template needs no
change:

| Setting | Default | The other value |
|---|---|---|
| `NEXTSEEK_CC_OPS_ROAD` | `direct`: the op tools call the assistant endpoints with the turn pass | `sidecar`: the WebSocket sidecar, kept one release as the light rollback |
| `NESSIE_VOCAB_PRERUN` | `on`: each question's vocabulary is resolved beside the router | `off`: no pre-run, and Container-CC turns also lose the vocabulary note |
| `NESSIE_PARSER_START` | `after_route`: the NS parser starts once the route is known | `early`: it starts before the route is known (not yet the default) |

To change one, add its line to `docker/nextseek.env` and recreate the app:
`docker compose -p nextseek up -d --no-deps --force-recreate nextseek`.
Rebuild never re-renders that file.

**Deploy.**

1. Fast-forward the deploy clone (Procedure step 1), then
   `./startup.sh rebuild --component custom-stack`.
2. Watch the boot, `docker logs -f nextseek`, until the workers are up with no
   `[MIGRATE-FAILED]`. Migrate runs `0025_cc_turn` first: not atomic, it
   creates `assistant_cc_turn` with its chat column matched to the chat
   table's charset, then adds `parent_cc_turn` to `assistant_query_task` (the
   table copy). Then `0026_ccturn_ops_cost_partial`. If 0025 stops part way
   (usually disk), fix the cause and restart `nextseek`: the heal skips a table
   that is already there.
3. If `static/` changed in the range (unit B's chat bundle did), run
   `docker compose exec nextseek uv run manage.py collectstatic --noinput`
   (DEPLOYMENT.md §3.2).

**Post-checks, read-only.**

```bash
docker compose exec nextseek uv run manage.py showmigrations nextseek_api | tail -3
# -> [X] 0025_cc_turn and [X] 0026_ccturn_ops_cost_partial
docker compose exec nextseek uv run manage.py shell -c "from django.conf import settings as s; print(s.NEXTSEEK_CC_OPS_ROAD, s.NESSIE_VOCAB_PRERUN, s.NESSIE_PARSER_START)"
# -> direct on after_route, unless a switch was set
```

Then the Verification block below (`cc_runner_available()` returns
`(True, 'ok')`) and DEPLOYMENT.md §6. On the first Container-CC turn, while its
agent container runs, list the password-like key names in its env (names only):

```bash
docker inspect $(docker ps -q --filter name=dmac-cc-agent-) --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1 | grep -E 'NEXTSEEK_TURN_PASS|API_PASS|NEXTSEEK_PASSWORD|SEEK_PASSWORD'
# -> NEXTSEEK_TURN_PASS only
```

The turn's final event (the finished task's progress) carries `ops_cost_usd`,
`turn_cost_usd` and `cost_partial`; `cost_partial_reason` names what was not
counted.

**Rollback, light (the ops road only).** Set `NEXTSEEK_CC_OPS_ROAD=sidecar` in
`docker/nextseek.env` and recreate the app as under Switches. It needs the
sidecar this rebuild made; everything else stays on unit B.

**Rollback, full, in this order.**

1. With the unit B image still running, reverse the migrations:

   ```bash
   docker compose exec nextseek uv run manage.py migrate nextseek_api 0024
   ```

   This drops 0026's two columns, the `parent_cc_turn` key and column, and the
   `assistant_cc_turn` table with its rows: the turn memory and ops cost of
   every unit B turn (the pre-check dump is their copy). Do not restart
   `nextseek` between this step and step 4: the unit B image migrates at every
   boot and would apply 0025 and 0026 again.
2. Repoint the three images at the tags this rebuild printed (DEPLOYMENT.md
   §5.1):

   ```bash
   T=pre-<timestamp>-<sha>                                   # from "rollback tag verified"
   docker image inspect nextseek-nextseek:$T --format '{{.Id}}'     # each MUST succeed first
   docker image inspect dmac-assistant:$T --format '{{.Id}}'
   docker image inspect nextseek-ns-sidecar:$T --format '{{.Id}}'
   docker tag nextseek-nextseek:$T nextseek-nextseek:latest
   docker tag dmac-assistant:$T dmac-assistant:poc
   docker tag nextseek-ns-sidecar:$T nextseek-ns-sidecar:latest
   ```

3. Check out the commit you recorded in the deploy clone
   (`git checkout --detach <recorded-sha>`). Without it the doctor's CC wiring
   probe fails with a TypeError: the checkout and the image disagree on
   `build_agent_environment`.
4. Recreate without building:
   `docker compose -p nextseek up -d --no-build --no-deps --force-recreate nextseek nextseek-sidecar`.
   The agent image needs no restart; the next turn uses it. Then
   DEPLOYMENT.md §6.

Skip step 1 and every chat that ran a unit B Container-CC turn can no longer be
deleted: the older code does not know `assistant_cc_turn`, and MySQL refuses
the delete (error 1451 on `assistant_cc_turn_task_id_fk`).

## Verification

CC route wired end-to-end (checks daemon, agent image, network — in order;
the image has no bare `python` on PATH, so use `uv run --no-sync`, which
executes in the app env `/app/.venv` without modifying it):

```bash
docker exec nextseek uv run --no-sync python -c "from NessieAI.cc import cc_engine; print(cc_engine.cc_runner_available())"
# -> (True, 'ok')
```

Proxy contract, from a container ON dmac-cc-net (unsigned, exactly like the
agent). The healthz and haiku probes are free; the **opus invoke is a real,
PAID one-token Bedrock call** — it needs the same per-run owner approval as
any live spend, and on a token-less install it returns 500 ("proxy
misconfigured: no bearer token"), which is expected there:

```bash
docker run --rm --network dmac-cc-net --entrypoint sh dmac-assistant:poc -c '
  B=http://bedrock-proxy:8080
  curl -s -o /dev/null -w "healthz=%{http_code}\n" $B/healthz                       # 200 (free)
  curl -s -o /dev/null -w "haiku=%{http_code}\n" -X POST $B/model/us.anthropic.claude-haiku-4-5-20251001-v1:0/invoke -d "{}"  # 403 (free; allowlist rejects pre-Bedrock)
  # PAID (approval-gated; 500 expected when the proxy token is empty):
  curl -s -o /dev/null -w "opus=%{http_code}\n"   -X POST $B/model/us.anthropic.claude-opus-5-5/invoke \
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
backend credentials (enumerated in `NessieAI/tests/cc/validate_cc_acceptance.py`).

## Acceptance (paid, gated)

```bash
# native 8/8 regression baseline
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test NessieAI.tests.ns.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb -v2'

# the Container-CC route, end-to-end (router=baml -> real Opus via proxy -> publish)
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test NessieAI.tests.cc.test_cc_realstack \
   --settings=dmac.test_settings_realstack --noinput -v2'

# reproducible re-check of a committed evidence bundle (zero spend; use
# `uv run --no-sync` — the image has no bare `python` on PATH)
docker exec nextseek uv run --no-sync python -m NessieAI.tests.cc.validate_cc_acceptance \
  outputs/cc_acceptance/<run_id>

# full Step-7 compose-deploy evidence bundle re-validation (zero spend, 61
# checks; runs on the HOST from the repo root — stdlib-only module)
python3 -m NessieAI.tests.cc.validate_step7_compose_deploy <run_dir> [repo_root]
```

Both live suites are skipped unless `RUN_REALSTACK=1` is set explicitly, and
require the owner's per-run approval — they spend real LLM budget.
