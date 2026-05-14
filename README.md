# NExtSEEK

A Django/Mezzanine extension of the SEEK platform for active scientific data
curation, with a graph-backed sample database (Neo4j) and an embedded AI
assistant (chat_nextseek) for natural-language queries.

## Quick start

```bash
git clone <repo-url>
cd NExtSEEK
./bootstrap.sh install
```

Open http://localhost:8000 and log in with `demo / demopassword` (admin) or
`user / userpassword` (regular).

> **Going beyond localhost?** Read [`NExtSTEPS.md`](NExtSTEPS.md) — it lists
> the credentials, env vars, and config files to change before exposing the
> install to anyone you don't trust. Rotating the demo passwords is the
> minimum.

## System requirements

- Docker 24+ and Docker Compose v2
- [`uv`](https://docs.astral.sh/uv/) (Python package manager)
- Python 3.14 (uv will install it on first bootstrap run)
- ~5 GB free disk, 4 GB free RAM

## What bootstrap does

`./bootstrap.sh install` orchestrates the full local Docker stack: prereqs
check, config generation, volume creation, MySQL and Neo4j seed import,
container build, test-user verification, and health checks. For detail and
all available subcommands (`reset`, `rebuild`, `doctor`, `dump-db`), see
[`bootstrap/README.md`](bootstrap/README.md).

## Architecture

- **NExtSEEK** (this repo) — Django app, REST API, embedded chat panel
- **SEEK** — Upstream FAIRDOM SEEK Rails app, runs as a sibling container,
  shares MySQL with NExtSEEK
- **MySQL** — `dmac` schema (NExtSEEK) + `seek_production` schema (SEEK)
- **Neo4j** — graph of sample/assay relationships
- **Solr** — SEEK search index
- **chat_nextseek** (vendored under `chat_nextseek/`) — multi-agent LLM
  pipeline backing the chat panel. Standalone CLI and MCP server modes
  available; see `chat_nextseek/README.md`.

## Development workflow

Common changes you'll make and how to apply them to a running stack:

| What you changed | Command |
|---|---|
| Python views / models / settings (no static asset change) | `docker compose up -d --build nextseek` |
| Files under `static/` (CSS/JS/images, hand-edited) | `docker compose up -d --build nextseek && docker compose exec nextseek uv run manage.py collectstatic --noinput` |
| `chat_frontend/` React source | Rebuild per `chat_frontend/README.md`, then `collectstatic` as above |
| `chat_nextseek/` source pulled in from canonical repo | `bootstrap/scripts/sync_chat_nextseek.sh <source>`, commit, then `./bootstrap.sh rebuild` |
| New Django model field / migration | `docker compose up -d --build nextseek` (entrypoint runs `migrate` on startup) |
| Full reset (wipe data, re-seed) | `./bootstrap.sh reset` |

The Python-only-rebuild path is the common one. The key gotcha: rebuilding
does **not** automatically run `collectstatic` — if you changed CSS/JS in
`static/`, you must run `collectstatic` after the rebuild or your changes
won't be served.

## Configuration

After `./bootstrap.sh install`, three config files are written and are then
yours to edit:

- `docker/db.env` — MySQL credentials (gitignored)
- `docker/nextseek.env` — Django secret, Neo4j password, API keys (gitignored;
  chat features stay disabled until you fill in real keys)
- `dmac/local_settings.py` — Django settings overlay, including the optional
  PROD ChatConfig block for the admin-only "PROD" toggle in the chat UI
  (gitignored)

All three are gitignored. Bootstrap can re-render them via `./bootstrap.sh reset`
if you ever want a clean slate.

## Troubleshooting

Start with `./bootstrap.sh doctor` — it runs every prereq + health check and
reports failures with remediation hints.

For deeper issues, see [`bootstrap/README.md`](bootstrap/README.md) → "Known
failure modes".

## Contributing

Two repos to know about:

- **This repo (NExtSEEK)** — Django app + vendored chat_nextseek snapshot
- **chat_nextseek canonical repo** — `git@github.com:cdemurjian/chat_nextseek.git`

Day-to-day chat_nextseek development happens in the canonical repo. To
ship a new chat_nextseek snapshot into NExtSEEK, run:

```bash
bootstrap/scripts/sync_chat_nextseek.sh /path/to/canonical/chat_nextseek
```

Then commit the changes in NExtSEEK and push.

## License

See `LICENSE`.
