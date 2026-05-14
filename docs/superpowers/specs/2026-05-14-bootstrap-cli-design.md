# NExtSEEK Bootstrap CLI — Design Spec

**Date:** 2026-05-14
**Status:** Design — awaiting user review before plan

## 1. Overview

A self-contained `bootstrap` CLI shipped inside the NExtSEEK repo that turns a fresh `git clone` into a fully running local Docker stack — Django app, SEEK, MySQL, Neo4j, Solr, nginx, and the embedded chat assistant — in one command. Replaces the manual runbook in `readmes/CD_README_FINAL.md` (currently in the retired top-level docker wrapper repo).

The CLI also supports parallel/test installs on different ports without disrupting an existing install.

## 2. Goals & non-goals

### Goals
- Anyone with access to the repo can `git clone` NExtSEEK and run `./bootstrap install` to get a working stack with seeded demo data and two test users.
- Idempotent: re-running `install` against an existing install is safe and reports state without destruction.
- Side-by-side installs supported via `--instance <name>` flag and per-instance port/volume namespacing.
- Maintainer flow (`./bootstrap dump-db`) for regenerating the shipped seed dumps from a configured source DB, with the secrets file kept local and never committed.
- Bootstrap, seed data, templates, and README all live inside `NExtSEEK/` — no external repo or external resource directory required.

### Non-goals
- Not a production deployment tool. Bootstrap targets local Docker installs (developer machines, test machines).
- Not a public open-source install path yet. `chat_nextseek` remains a private separate repo for development, vendored into NExtSEEK as a snapshot for distribution. A future PyPI/public-mirror path is out of scope.
- Not a database migration tool. `manage.py migrate` is run from the existing entrypoint script — bootstrap just ensures the right preconditions for it.
- Does not modify the chat_frontend build process. The Vite-built static assets in `static/js/chat_assistant/` continue to be produced by `chat_frontend/`'s own build; bootstrap runs `collectstatic` only.

## 3. Architecture

### Folder layout (new code in **bold**)

```
NExtSEEK/
├── bootstrap                          ← bash entrypoint (uv preflight)
├── bootstrap/                         ← Python package
│   ├── __init__.py
│   ├── cli.py                         ← typer app, registers subcommands
│   ├── steps/
│   │   ├── prereqs.py                 ← docker/compose/uv/ports/disk checks
│   │   ├── config.py                  ← interactive prompts → env + local_settings
│   │   ├── volumes.py                 ← named-volume create with INSTANCE_PREFIX
│   │   ├── seed.py                    ← gunzip | mysql / gunzip | cypher-shell
│   │   ├── build.py                   ← docker compose build/up sequencing
│   │   ├── users.py                   ← idempotent test-user + demo project seed
│   │   ├── validate.py                ← compose ps, manage.py check, curl health
│   │   └── doctor.py                  ← standalone diagnostics
│   ├── lib/
│   │   ├── ui.py                      ← rich console wrappers, banners, spinners
│   │   ├── docker_ops.py              ← compose / exec helpers
│   │   ├── env.py                     ← .env read/write (preserve comments)
│   │   ├── ports.py                   ← free-port detection
│   │   └── prompts.py                 ← typer prompts with sensible defaults
│   ├── templates/
│   │   ├── db.env.template
│   │   ├── nextseek.env.template
│   │   └── local_settings.py.template
│   ├── seed/
│   │   ├── README.md                  ← seed provenance + regen instructions
│   │   ├── dmac.sql.gz                ← shipped (~3.7 MB)
│   │   ├── seek_production.sql.gz     ← shipped (~11 MB)
│   │   ├── neo4j.cypher.gz            ← shipped (~9.6 MB)
│   │   └── regenerate/
│   │       ├── dump_mysql.sh          ← parameterized mysqldump driver
│   │       ├── dump_neo4j.py          ← env-driven port of export_neo4j.py
│   │       ├── scrub.py               ← optional PII/secret post-scrubber
│   │       └── dump-source.env.example← placeholder template (tracked)
│   └── tests/
│       ├── test_env_writer.py
│       ├── test_ports.py
│       ├── test_prereqs.py
│       └── test_user_seed.py
├── chat_nextseek/                     ← VENDORED (own .git/ removed); see §8
├── docker-compose.yml                 ← parameterized ports + volume prefix (§6)
├── README.md                          ← rewritten (§11)
└── .gitignore                         ← `chat_nextseek/` line removed; `bootstrap/.instance.json` added
```

### Component boundaries

Each `steps/*.py` module exposes one public function that takes a typed config object and writes/runs side effects. `lib/*` modules are pure helpers with no side effects beyond their stated job. The split exists so each step is independently testable (pure functions in `lib/` with pytest; side-effecting `steps/` with integration tests using a disposable Docker namespace).

## 4. CLI subcommands

```
./bootstrap install [--instance NAME] [--port-offset N] [--*-port N] [--yes]
./bootstrap reset   [--instance NAME] [--keep-config] [--yes]
./bootstrap rebuild [--instance NAME] [--service SVC]
./bootstrap doctor  [--instance NAME]
./bootstrap dump-db [--source SRC] [--target DIR]    ← maintainer only
./bootstrap seed-users                                ← idempotent re-seed
```

| Command | Behavior |
|---|---|
| `install` | Full first-run: prereqs → vendor verify → config prompts → write env files → create volumes → seed DBs → build/start stack → seed users → validate. Idempotent: detects existing install and prompts before destructive steps. |
| `reset` | Drops volumes for the current instance, then re-runs install. `--keep-config` preserves env files. |
| `rebuild` | `docker compose build --no-cache <service>` + `up -d --force-recreate <service>`. Defaults to `nextseek`. No volume changes. |
| `doctor` | Read-only diagnostic: container health, env drift, port collisions, secret hygiene, `manage.py check`, smartadmin audit. Exits non-zero on failure. |
| `dump-db` | Regenerates `seed/*.gz` from a source DB. Requires `seed/regenerate/dump-source.env` (gitignored, maintainer-only). Errors helpfully if missing. |
| `seed-users` | Idempotent: ensures `demo`/`demopassword` (admin) and `user`/`userpassword` (regular) exist in SEEK, both bound to a "Demo" project. |

## 5. Install flow

The user experience for a clean install:

```
$ ./bootstrap install
┌──────────────────────────────────────────────────────────────┐
│ NExtSEEK Bootstrap                                           │
└──────────────────────────────────────────────────────────────┘

[1/9] Checking prerequisites
       docker 28.x, compose v2.x, uv 0.x, ports free, ≥5 GB disk free

[2/9] Verifying vendored chat_nextseek/ is present

[3/9] Configure
       Instance name? [nextseek]
       Use demo credentials? [Y/n]   (db: seek_db_user/seek_db_password;
                                       neo4j: neo4j/demopassword)
       Django secret key? [auto-generate]
       Anthropic / GCP / Bedrock API keys now or later? [later]

[4/9] Writing docker/db.env, docker/nextseek.env, dmac/local_settings.py,
       bootstrap/.instance.json

[5/9] Creating docker volumes (6, prefixed by instance)

[6/9] Importing seed databases
       mysql:  dmac (3.7 MB), seek_production (11 MB)
       neo4j:  674k statements (~30 seconds, runs async)

[7/9] Building NExtSEEK image, starting the full stack

[8/9] Seeding test users
       demo / demopassword  (admin)         already present
       user / userpassword  (regular)       already present
       Demo project: id=1                    already present

[9/9] Health checks
       SEEK     → http://localhost:3000   [200]
       NExtSEEK → http://localhost:8000   [200]
       Neo4j    → http://localhost:7474   [200]

Ready. Chat: http://localhost:8000/chat
For production credentials, edit dmac/local_settings.py — see README.md.
```

Each step is its own function in `steps/`; failure aborts the run with a remediation hint and a one-line command to resume from that step.

## 6. Multi-instance design

### docker-compose.yml changes (one-time)

Two surgical edits to enable side-by-side installs:

**a) Parameterize the four published ports** with env-var fallbacks (defaults preserve current behavior):
```yaml
nextseek_nginx:
  ports: [ "${NEXTSEEK_PORT:-8000}:80" ]
neo4j:
  ports: [ "${NEO4J_HTTP_PORT:-7474}:7474", "${NEO4J_BOLT_PORT:-7687}:7687" ]
seek:
  ports: [ "${SEEK_PORT:-3000}:3000" ]
```

**b) Parameterize the six external volume names** with an `INSTANCE_PREFIX` env var (default empty preserves current names):
```yaml
volumes:
  seek-filestore:
    name: "${INSTANCE_PREFIX:-}seek-filestore"
    external: true
  # … same pattern for seek-mysql-db, seek-solr-data, seek-cache,
  #    nextseek-static-files, neo4j-data
```

### Per-instance state

`bootstrap/.instance.json` (gitignored, written by `install`):
```json
{
  "name": "test",
  "prefix": "test-",
  "ports": {"nextseek": 8001, "seek": 3001, "neo4j_http": 7475, "neo4j_bolt": 7688},
  "compose_project_name": "nextseek-test",
  "created": "2026-05-14T12:34:56-04:00"
}
```

All subsequent subcommands read this file to know which instance they're operating on.

### Port allocation

- Default install (`--instance` omitted or matches cwd basename): ports 3000/8000/7474/7687.
- `--instance NAME` without explicit ports: bootstrap auto-detects free ports starting from defaults+1, walking up. Reports the chosen ports before applying.
- `--port-offset N`: shifts every port by `+N`.
- `--nextseek-port`/`--seek-port`/`--neo4j-http-port`/`--neo4j-bolt-port`: explicit override.

### Two-clone workflow (recommended)

```bash
# Existing install at ~/code/dmac/docker/NExtSEEK keeps running on default ports.
# Spin up a parallel test install:
git clone <NExtSEEK-remote> ~/code/dmac/docker/NExtSEEK-test
cd ~/code/dmac/docker/NExtSEEK-test
./bootstrap install --instance test
```

Bootstrap auto-picks free ports, uses the `test-` volume prefix, sets `COMPOSE_PROJECT_NAME=nextseek-test`. Both stacks coexist. `./bootstrap reset --yes` from the test clone nukes test data only.

### Backward compatibility

Empty `INSTANCE_PREFIX` + default ports = the existing volumes (`seek-filestore`, etc.) are used as-is. **The current production install is untouched.**

## 7. Configuration & templates

Templates live in `bootstrap/templates/` and use `${VAR}` substitution (Python's `string.Template`). Bootstrap reads existing env files when present and merges new keys without clobbering user edits — every write goes through `lib/env.py` which preserves comments and ordering.

### `db.env.template`
Mirrors the current `docker/db.env` (already tracked, demo defaults). No real secrets.

### `nextseek.env.template`
Built from the existing `docker/nextseek.env.example`. Prompts the user for: Django secret key (auto-generated default), CSRF trusted origins (based on chosen ports), Neo4j password (defaults to `demopassword`). API keys default to `SET_IN_LOCAL_ENV` — user fills in later if/when they want chat working with real models.

### `local_settings.py.template`
Built from `dmac/local_settings.example.py`. Bootstrap fills in `SEEK_URL`, `PUBLISH_URL`, `ASSISTANT_PARTICIPATING_PROJECTS`, instantiates `NEXTSEEK_CHAT_CONFIG = ChatConfig()`. The `_PROD_OVERRIDES` block is included but commented out with a clear "fill in to enable PROD toggle in the UI" comment; the user edits this manually.

## 8. chat_nextseek vendoring

Per the decision in turn 5, chat_nextseek is **vendored** into NExtSEEK rather than cloned at install time.

### One-time setup steps (manual, before first bootstrap commit)
1. Delete `NExtSEEK/chat_nextseek/.git/` in the local working tree. The separate canonical chat_nextseek repo elsewhere is untouched.
2. Remove the `chat_nextseek/` line from `NExtSEEK/.gitignore`.
3. `git add chat_nextseek/` from NExtSEEK root. The nested `chat_nextseek/.gitignore` automatically excludes secrets (`.env`, `.venv/`, `outputs/`, `*.sqlite`).
4. Commit: "Vendor chat_nextseek for self-contained bootstrap install."

### Ongoing dev workflow

- Day-to-day chat_nextseek dev continues in its own canonical repo (cdemu's chat_nextseek private repo, wherever maintainer keeps a working checkout).
- When ready to ship a new chat_nextseek snapshot to NExtSEEK, run `bootstrap/scripts/sync_chat_nextseek.sh <source-path>` — `rsync -a --delete --exclude='.git/' --exclude='.venv/' --exclude='outputs/' --exclude='__pycache__/' <source>/ chat_nextseek/` and stage for commit in NExtSEEK.
- Bootstrap doctor checks for a `chat_nextseek/.chat_nextseek_snapshot` file containing the source commit SHA, so drift is detectable.

### Single `agent_model_catalog.json`

The duplicate at `NExtSEEK/agent_model_catalog.json` is deleted as part of the vendoring change. Any code that references it switches to `chat_nextseek/agent_model_catalog.json`. (Today the files are byte-identical, so no behavior change.)

## 9. Seed data strategy

### What ships
`bootstrap/seed/dmac.sql.gz`, `bootstrap/seed/seek_production.sql.gz`, `bootstrap/seed/neo4j.cypher.gz`. Already generated and verified for this spec: ~24 MB total committed. Secret-scan clean. Both `demo` and `user` accounts baked in.

### Loader (`bootstrap/steps/seed.py`)
```
gunzip -c bootstrap/seed/dmac.sql.gz \
  | docker compose exec -T db mysql -uroot -p"$MYSQL_ROOT_PASSWORD" dmac

gunzip -c bootstrap/seed/seek_production.sql.gz \
  | docker compose exec -T db mysql -uroot -p"$MYSQL_ROOT_PASSWORD" seek_production

gunzip -c bootstrap/seed/neo4j.cypher.gz \
  | docker compose exec -T neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD"
```
Streams in-place (no decompressed file lands on disk). Progress reported via `rich.progress`.

### Regen flow (maintainer only)

`bootstrap/seed/regenerate/dump-source.env.example` (tracked) contains placeholders only:
```
MYSQL_HOST_DEV=fairdata-dev.example.com
MYSQL_USER=...
MYSQL_DEV_PASSWORD=SET_LOCAL_ONLY
MYSQL_PORT=3306
NEO4J_URI=neo4j+s://...
NEO4J_USER=...
NEO4J_PASSWORD=SET_LOCAL_ONLY
NEO4J_DATABASE=...
```

The real `dump-source.env` is gitignored and only the maintainer has it. `./bootstrap dump-db` errors with a clear "you appear to be running this without the maintainer secrets file — this command is maintainer-only" message if the real file is absent. Same flow for both MySQL and Neo4j.

`dump_mysql.sh` codifies the verified-working command from this conversation, including `--column-statistics=0` for MariaDB-source compatibility. `dump_neo4j.py` is a port of `resources/export_neo4j.py` with hardcoded creds replaced by env reads from `dump-source.env`.

## 10. Test users & demo project

Both shipped seed dumps include the two accounts. `bootstrap/steps/users.py` is idempotent: if either account is missing from the running DB (e.g., after a reset), it creates them via SEEK's user-creation flow, binds both to a project called "Demo" with `id=1`, and assigns `demo` admin role / `user` regular role.

Login URLs after install: `http://localhost:8000/login` (NExtSEEK) and `http://localhost:3000/users/login` (SEEK).

## 11. README rewrite

The current `NExtSEEK/README.md` (18 KB) is comprehensive but assumes Docker fluency. New structure:

1. **What NExtSEEK is** (1 paragraph)
2. **Quick start** (3 lines): clone, run `./bootstrap install`, open the URL
3. **System requirements** (Docker, uv, ~5 GB disk, 4 GB free RAM)
4. **What bootstrap does** (one-paragraph summary, link to `bootstrap/README.md` for deep detail)
5. **Development workflow** — new section, see below
6. **Architecture overview** (Django + SEEK + Neo4j, role of chat_nextseek, where the React chat lives)
7. **Configuration** (env files, local_settings.py PROD toggle)
8. **Troubleshooting** (link to `bootstrap/README.md` and `./bootstrap doctor`)
9. **Contributing** (chat_nextseek vendoring workflow, snapshot sync)

### Development workflow section

Captures the day-to-day "I changed code, what do I run" answers:

| Change you made | Command to apply it |
|---|---|
| Python view / model / settings change (no static asset change) | `docker compose -f docker-compose.yml up -d --build nextseek` |
| Static-file change (CSS/JS/images directly in `static/`) | `docker compose exec nextseek uv run manage.py collectstatic --noinput` (after rebuild) |
| chat_frontend (React) change | Rebuild `chat_frontend/` per its own README, then `collectstatic` — bootstrap does not handle this |
| chat_nextseek change pulled in from canonical repo | `bootstrap/scripts/sync_chat_nextseek.sh <source>`, commit snapshot, rebuild |
| Schema migration | `docker compose exec nextseek uv run manage.py migrate` (or restart — entrypoint runs migrate) |
| Full reset (drop data, re-seed) | `./bootstrap reset` |

The Python-only-change path is the common one; calling out the **rebuild ≠ collectstatic** distinction up front prevents the "I rebuilt but my CSS change isn't showing" confusion.

Explicit commands are written out as copy-pasteable blocks, not paraphrased — anyone reading the README should be able to copy a line and run it.

## 12. Error handling & UX

- **Every failure mode has a remediation hint.** "Port 8000 in use → try `--port-offset 1` or set `--nextseek-port`."
- **`./bootstrap doctor` is the single diagnostic entry point** — anything user-fixable should be detected by doctor with a one-line fix.
- **No silent retries.** If a Docker command fails, surface the stderr and stop.
- **Resume hints.** Each phase, on failure, prints the exact command to resume (`./bootstrap install --resume-from seed`).
- **Banner shows real progress.** `rich.progress` for the long-running steps (MySQL/Neo4j imports), spinners for everything else, exit codes for everything.
- **`--yes` flag** skips all confirmations for CI / scripting.

## 13. Testing

### Pure unit tests (pytest)
`bootstrap/tests/test_env_writer.py` — comment preservation, key add/update without reorder, idempotency.
`bootstrap/tests/test_ports.py` — free-port detection, offset math, collision handling.
`bootstrap/tests/test_prereqs.py` — version parsing for docker/compose/uv, port-in-use detection (mocked).
`bootstrap/tests/test_user_seed.py` — user-existence detection logic against a fake SEEK API.

### Integration tests (manual or CI-gated, slow)
- Single-instance install on a clean Docker env: `./bootstrap install --yes` from a fresh clone, then `curl localhost:8000` returns 200.
- Multi-instance side-by-side: install default, then install `--instance test` in a second clone, verify both up simultaneously.
- Reset: install, reset, install — verify no orphan volumes/containers.
- Dump-db round-trip (maintainer only): run dump-db, install from the just-generated dumps, verify users present.

The integration tests run against the actual Docker daemon — no mocks — because the failure modes that matter (port collisions, volume name clashes, env interpolation) only show up in the real environment.

## 14. Migration / backward compatibility

The current production install at `/home/cdemu/code/dmac/docker/NExtSEEK` survives this change unchanged:
- `INSTANCE_PREFIX=""` (default) → its existing volumes `seek-filestore`, `seek-mysql-db`, etc. are still used.
- Default ports preserved.
- `docker/db.env`, `docker/nextseek.env`, `dmac/local_settings.py` are read in place; bootstrap only writes new templates if files are missing.

The retired top-level docker wrapper repo (`/home/cdemu/code/dmac/docker`) is no longer touched by the bootstrap flow.

The first run of `./bootstrap install` from the existing checkout should detect "config already exists, stack is running" and offer to either run `doctor` or skip the install steps that are already done.

## 15. Open questions / future work

- **chat_nextseek public mirror or PyPI release** — would let bootstrap install fully without any private-repo access. Out of scope for this spec.
- **Snapshot drift detection for vendored chat_nextseek** — the `.chat_nextseek_snapshot` SHA file is a minimum; a stronger story would compare file hashes to the canonical repo's HEAD on `doctor`.
- **Sample-data scrub for public seeds** — current dumps came from dev (which you cleaned by hand). A `scrub.py` hook would let `dump-db` apply a documented PII/secret allowlist before writing the gz. Skeleton ships, body deferred.
- **Auto-update path** — `./bootstrap upgrade` to pull NExtSEEK + re-sync chat_nextseek + run any required migrations. Not in v1.
- **CI workflow** — GitHub Actions running `./bootstrap install` + smoke tests on a fresh Ubuntu runner. Worth doing once the CLI is implemented and stable.
