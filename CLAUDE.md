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
| `db` | mysql:8.0 | two schemas: `dmac` (NExtSEEK) + `seek_production` (SEEK) |
| `neo4j` | neo4j | sample/assay relationship graph |
| `seek` / `seek_workers` | fairdom/seek:1.15.1 | upstream SEEK Rails app + delayed-job workers |
| `solr` | fairdom/seek-solr:8.11 | SEEK search index |

External named volumes: `seek-filestore`, `seek-mysql-db`, `seek-solr-data`,
`seek-cache`, `nextseek-static-files`, `neo4j-data` (created by startup).

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
nextseek_api/          REST API app
api_app/               API app
chat_frontend/         Vite/React UI for the chat panel (npm run build → collectstatic)
chat_nextseek/         VENDORED assistant subpackage (own CLAUDE.md + README.md)
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
```

- `DJANGO_SETTINGS_MODULE = dmac.test_settings`
- `testpaths = seek, nextseek_api, api_app`
- test files: `test_*.py`, `*_test.py`, `tests.py`

`chat_nextseek/` has its own separate test suite — run it from inside that
directory per `chat_nextseek/CLAUDE.md`.

## Development workflow

| What you changed | Command |
|---|---|
| Python views / models / settings | `docker compose up -d --build nextseek` (entrypoint runs `migrate`) |
| `static/` CSS/JS/images (hand-edited) | rebuild **then** `docker compose exec nextseek uv run manage.py collectstatic --noinput` |
| `chat_frontend/` React source | `npm run build` in `chat_frontend/` (Vite; `build:embedded` for the embedded panel), then `collectstatic` |
| `chat_nextseek/` snapshot | `startup/scripts/sync_chat_nextseek.sh <source>`, commit, then `./startup.sh rebuild` |
| Wipe + re-seed everything | `./startup.sh reset` |

**Gotcha:** rebuilding does **not** auto-run `collectstatic`. If you changed
anything under `static/`, run it after the rebuild or your change isn't served.

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
