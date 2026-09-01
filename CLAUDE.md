# Project Instructions — NExtSEEK (docker monorepo)

A Django/Mezzanine extension of the FAIRDOM **SEEK** platform for active
scientific data curation, with a graph-backed sample database (Neo4j) and an
embedded AI assistant (`chat_nextseek`). This repo is the self-contained
**Docker bring-up** of the whole stack. Day-to-day usage runs everything in
containers via `./startup.sh`.

> `chat_nextseek/` is a vendored subpackage with its **own** `CLAUDE.md` and
> `README.md` — read those before working inside it. This file covers the
> outer repo (Django app, docker stack, startup CLI).

## The stack (`docker-compose.yml`)

| Service | Image / build | Role |
|---|---|---|
| `nextseek` | built from `Dockerfile` | Django + gunicorn (this repo's app) |
| `nextseek_nginx` | nginx | serves static + proxies to gunicorn (published port) |
| `bedrock-proxy` | built from `docker/bedrock-proxy` | model gateway for the Container-CC route; reachable only on `dmac-cc-net`, never published to the host |
| `cc-agent` | built from `docker/cc-runtime` | **build target only** (tags `dmac-assistant:poc`, `command: ["true"]`). The Django worker spawns one ephemeral sibling container per CC turn via the host docker socket; nothing long-running comes from this stanza |
| `nextseek-sidecar` | built from `docker/ns-sidecar` | brokers per-request NExtSEEK ops for the CC agent over a WebSocket; writes only under the reserved `_staging/` subpath of `dmac-cc-users` |
| `db` | mysql:8.0 | two schemas: `dmac` (NExtSEEK) + `seek_production` (SEEK) |
| `neo4j` | neo4j | sample/assay relationship graph |
| `seek` / `seek_workers` | fairdom/seek:1.15.1 | upstream SEEK Rails app + delayed-job workers |
| `solr` | fairdom/seek-solr:8.11 | SEEK search index |

External named volumes: `seek-filestore`, `seek-mysql-db`, `seek-solr-data`,
`seek-cache`, `nextseek-static-files`, `neo4j-data`, `dmac-cc-users` (created by
startup). `nextseek-luria-ssh` is compose-managed, not external.

## The assistant (two engines behind one endpoint)

`POST /nextseek_api/cc-assistant/query/async/` is the router-dispatched chat
entry point. Per turn a router picks one of two engines: `nextseek_query`, the
deterministic `chat_nextseek` pipeline run in-process by Django, or
`container_cc`, a sandboxed Claude Code agent run as an ephemeral sibling
container. A third outcome, `unrelated`, returns a fixed out-of-scope reply
without running either engine. Both engines write the same `QueryTask` rows and
stream over the one existing websocket consumer
(`ws/assistant/progress/{task_id}/`), so `chat_frontend` needs no per-route code.

- **Router** (`nextseek_api/cc_assistant/router.py`) wraps `dmac_assistant`'s
  BAML `RouteQuery`. Every dmac import is lazy and guarded, and on a BAML
  failure (or dmac's own `<router_unavailable>` sentinel, which would otherwise
  send *everything* to CC) it falls back to a keyword heuristic. A vendoring or
  `uv sync` hiccup degrades routing; it never stops Django booting.
- **Overrides** live in `_decide_route` (`nextseek_api/services/cc_assistant.py`),
  in precedence order `force_route` > `pipeline_agent` > sticky CC > the router.
  An admin-only `force_route` (`ns`/`cc`) and the `cc/query/async/` endpoint beat
  the router; non-admin `force_route` is ignored. An open `pipeline_agent` wizard
  only keeps a turn the router *already* sent to NExtSEEK. **Sticky CC**: when the
  previous turn in the chat routed `container_cc` *and* completed, an NS-classified
  turn is converted to `container_cc` (`source: "sticky"`); `unrelated` is never
  converted, and a CC turn that errored does not make the chat sticky. The chat
  then stays on CC until a new chat, an admin `force_route`, or an intervening
  `unrelated` turn (logged as `unrelated`/completed, so the next turn no longer
  sees a preceding CC turn), an accepted consequence of the rule rather than a bug.
- **`dmac_assistant/`** is a vendored subset of the upstream dmac-assistant repo.
  Only the BAML router (`dmac_assistant.router.*`) and `run_tracker.diff_files`
  are imported; the FastAPI/websocket bridge is deliberately not vendored. The
  BAML client is generated at image build time (see `Dockerfile`), not committed.
- **`nessie_tests/`** is a router-aware e2e harness that drives the real endpoint
  above. `route` tier is cheap and pre-merge, `full` tier is paid and needs a
  seeded instance. See `nessie_tests/README.md`.

## Build & Run

The supported entry point is the **startup CLI** (`./startup.sh`, a Typer app
under `startup/` that runs as its own isolated `uv` project):

```
./startup.sh install          # first-time: prereqs → config → volumes → seeds → build → users → health
./startup.sh doctor           # read-only diagnostic (run this first when something's broken)
./startup.sh rebuild          # rebuild+restart one service (default: nextseek), volumes untouched
./startup.sh reset            # DESTRUCTIVE: drop volumes + re-install
./startup.sh seed-filestore   # (re)load the SEEK filestore blobs into a running stack
./startup.sh dump-db          # maintainer-only: regenerate seed dumps
```

See `startup/README.md` for full subcommand docs and known failure modes.

Seeds live in `startup/seed/`: `dmac.sql.gz`, `seek_production.sql.gz`,
`neo4j.cypher.gz` (DB dumps, committed). The SEEK content blobs
(`filestore.tar.gz`, ~215MB) are NOT in git — hosted on S3 and downloaded on
demand by the startup CLI, then streamed into the `seek` container. See
`startup/seed/README.md`.

## Layout

```
dmac/                  Django project: settings.py, test_settings.py, urls.py, wsgi/asgi
seek/                  main app (models, views, urls, SEEK integration, search, snapshot)
nextseek_api/          REST API app; services/ + assistant/ + cc_assistant/ (router, CC engine)
api_app/               API app
chat_frontend/         Vite/React UI for the chat panel (npm run build:embedded → collectstatic)
chat_nextseek/         VENDORED assistant subpackage (own CLAUDE.md + README.md)
dmac_assistant/        VENDORED BAML router + run_tracker (own README.md)
nessie_tests/          router-aware e2e harness for the assistant (own README.md)
startup/               Typer install/bring-up CLI (isolated uv project) + seed/ data
docker/                db.env / nextseek.env (rendered), nginx.conf, init scripts
themes/ static/ templates/   Mezzanine theme + collected static + templates
manage.py              Django entry point (DJANGO_SETTINGS_MODULE=dmac.settings)
docker-compose.yml Dockerfile gunicorn.conf.py
```

## Testing

`uv`-managed (`uv.lock`, `pyproject.toml`). Tests use **pytest-django**:

```
uv run pytest                 # config in [tool.pytest.ini_options]
./scripts/run_tests.sh [targets]   # same suite, inside the stack image
```

The whole pytest config is two keys under `[tool.pytest.ini_options]` in
`pyproject.toml`, so a bare `uv run pytest` is blunter than it looks:

- `DJANGO_SETTINGS_MODULE = "dmac.settings"`, the **real** settings, not
  `dmac.test_settings`
- no `testpaths` key, so collection starts at the repo root and walks the whole tree
- no `python_files` key either, so only pytest's defaults (`test_*.py`,
  `*_test.py`) are collected; the legacy Django `tests.py` modules are not
- `markers = ["host_only: ..."]`, for source-tree/host-lane tests that in-container
  runs exclude

In practice pass the test settings module and the paths explicitly:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest <paths> --no-migrations -q'
```

`chat_nextseek/` has its own separate test suite. Run it from inside that
directory per `chat_nextseek/CLAUDE.md`. `nessie_tests/` likewise has its own
runners rather than being driven by the root pytest invocation: its unit tests
run from that directory in an isolated env and its `tests_container/` tests run
inside the `nextseek` container. See `nessie_tests/README.md`.

On macOS the host route is unavailable: the pinned `mysqlclient` does not
build there. `./scripts/run_tests.sh` covers that case, and the worktree case
the `docker exec` command above does not — it mounts *this* checkout over
`/app` in the stack image rather than running the code baked into the image,
and passes your arguments straight through. It needs the gitignored
`dmac/local_settings.py` copied into the checkout.

## Development workflow

| What you changed | Command |
|---|---|
| Python views / models / settings | `docker compose up -d --build nextseek` (entrypoint runs `migrate`) |
| `static/` CSS/JS/images (hand-edited) | rebuild **then** `docker compose exec nextseek uv run manage.py collectstatic --noinput` |
| `chat_frontend/` React source | `npm run build:embedded` in `chat_frontend/`, then `collectstatic` (see the note below) |
| `chat_nextseek/` snapshot | `startup/scripts/sync_chat_nextseek.sh <source>`, commit, then `./startup.sh rebuild` |
| Wipe + re-seed everything | `./startup.sh reset` |

**Gotcha:** rebuilding does **not** auto-run `collectstatic`. If you changed
anything under `static/`, run it after the rebuild or your change isn't served.

**Gotcha (frontend):** the two Vite scripts are not interchangeable.
`npm run build` uses `vite.config.ts` and emits `chat_frontend/dist`, which is
gitignored (`chat_frontend/.gitignore`) and served by nothing. Only
`npm run build:embedded` (`vite.config.embedded.ts`) emits what the site
actually loads, into `static/js/chat_assistant/`, and those built files are
**committed**. The `Dockerfile` has no npm/node step at all, so the image ships
whatever bundle is in git: every UI change is a two-step commit (source, then
rebuilt bundle).

## Config & secrets (all gitignored)

- `docker/db.env` — MySQL credentials
- `docker/nextseek.env` — Django secret, Neo4j password, LLM API keys (chat
  features stay disabled until real keys are filled in)
- `dmac/local_settings.py` — Django settings overlay (template:
  `dmac/local_settings.example.py`)
- `startup/.instance.json` — per-instance state (name, prefix, ports)

`./startup.sh reset` re-renders the first three from templates.

## Conventions

- **Never commit secrets.** The four files above are gitignored — keep it that way.
- **uv, not pip.** `uv add <pkg>` / `uv run …`; don't hand-edit dependency pins.
- **Conventional commits** with module scopes: `feat(startup): …`,
  `fix(pipeline): …`, `refactor(schemas): …`.
- **Don't commit** the raw `filestore/` working dir or `startup/seed/filestore.tar.gz`
  (both gitignored — the snapshot is hosted on S3 and downloaded on demand),
  `logs/`, `outputs/`, or `.env` files.
- **Deferred work becomes a GitHub issue, not a silent TODO.** When you find a
  bug you won't fix now, or finish a plan with residuals, draft a structured
  issue per [docs/ISSUE-CONVENTIONS.md](docs/ISSUE-CONVENTIONS.md), validate it
  (`scripts/validate_issue.py` — on this box run it via the repo-mounted
  container lane), and ask the user before filing. Claude Code users: the
  committed `nextseek-issues` skill automates this workflow.
- **New `nextseek_api` ViewSets follow the committed skill.** When adding or
  changing REST ViewSets, read [`.claude/skills/nextseek-viewset/SKILL.md`](.claude/skills/nextseek-viewset/SKILL.md)
  and run `scripts/validate_viewset_conventions.py` before calling the work done.

## Debugging a failing stack

- `docker logs nextseek 2>&1 | tail -100` — gunicorn/Django crashes
- `logs/django.log` (host bind-mount, root-owned) — app-level request errors
- `outputs/<timestamp>_<user>/console.txt` — per-chat-turn agent traces

For deeper SEEK/assistant issues see `startup/README.md` and
`chat_nextseek/CLAUDE.md`.

## Going to production

`NExtSTEPS.md` (repo root) lists the credentials, env vars, and config to
change before exposing an install beyond a private localhost demo. Rotating the
default `demo`/`user` passwords is the minimum.

**Deploying, redeploying, rolling back, or verifying a real instance:**
`DEPLOYMENT.md` (repo root) is the authoritative deployment-hygiene runbook —
follow it exactly (rollback tags before rebuilds, mysqldump gate before
migration deploys, scoped service recreation, the post-deploy verification
checklist, and the Container-CC isolation invariants). The Container-CC
subsystem specifics live in `nextseek_api/cc_assistant/DEPLOY.md`.

## Adding a Container-CC operation

Do not invent a parallel op catalog. Follow
[`.claude/skills/add-cc-op/SKILL.md`](.claude/skills/add-cc-op/SKILL.md)
(`/add-cc-op`): shim + `_DISPATCH`/`_CMDS` + `OpSpec` + export +
`gen_op_surfaces`. Registration SoT is `ops.py` / exported `ops.json`,
not `plugin.json` or `discover_ops`.

## Session reports / handoffs

- 2026-09-01 — Scoped a frontend-only rewrite of /seek/samples/attributes/. The page already runs entirely on the attributes API and that API is verified on production, so this is a UI job with no server-side risk. Spec at docs/superpowers/specs/2026-09-01-sample-attributes-gui-rewrite-design.md. START WITH THE FIRST handoff_note. See `.claude/reports/2026-09-01-rewrite-the-sample-attributes-gui-frontend-only.json`.
- 2026-09-01 — Approved a CI/CD design (spec at docs/superpowers/specs/2026-09-01-nextseek-ci-cd-design.md) for a new session to implement. Also: three defects that had never let the attributes mutation API complete a write on production are fixed and live, the Sample Attributes GUI now runs on that API, and the batch assay-registration endpoint is merged. dev = 32043fe8, deployed to the dev box. START WITH THE FIRST handoff_note. See `.claude/reports/2026-09-01-ci-cd-design-plus-the-attribute-and-assay-work-now-on-dev.json`.
- 2026-08-04 — Superuser-only Users admin ViewSet merged to dev and pushed (6d99f85); mints SEEK logins via Rails runner. HTTP E2E deferred until deploy on shared box. See `.claude/reports/2026-08-04-users-admin-viewset-shipped.json`.
