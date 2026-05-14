# Bootstrap CLI reference

`./bootstrap.sh` is the entry point. All subcommands accept `--help`.

## Commands

### `install`

First-time setup. Runs all 9 phases: prereqs, vendor verify, config,
volumes, seeds, build, users, validate.

```
./bootstrap.sh install                       # default: ports 8000/3000/7474/7687
./bootstrap.sh install --instance test       # named instance with auto-assigned ports
./bootstrap.sh install --port-offset 1       # +1 on every port (8001/3001/7475/7688)
./bootstrap.sh install --yes                 # skip confirmation prompts
```

Idempotent for prereqs / config / volumes / users / validate. Seed import
is skipped if the target DB already has tables.

### `doctor`

Read-only diagnostic. Runs prereqs + health checks and reports drift.

```
./bootstrap.sh doctor
```

Exits non-zero if any check fails. **Run this first when something's broken.**

### `reset`

Destructive: drops all volumes for the current instance and re-runs install.

```
./bootstrap.sh reset                # also re-renders config files
./bootstrap.sh reset --keep-config  # preserves docker/*.env, dmac/local_settings.py
./bootstrap.sh reset --yes          # skip the confirmation prompt
```

### `rebuild`

Rebuilds and restarts one service without touching volumes. Default service
is `nextseek`.

```
./bootstrap.sh rebuild
./bootstrap.sh rebuild --service nextseek_nginx
```

### `dump-db`

**Maintainer-only.** Regenerates the gzipped seed dumps from a source DB.
Requires `bootstrap/seed/regenerate/dump-source.env` (gitignored) with the
source-DB credentials. Errors gracefully if absent.

```
./bootstrap.sh dump-db
```

## Multi-instance / side-by-side installs

To run a second isolated install on the same machine without disrupting
your existing one:

```bash
git clone <repo-url> /tmp/NExtSEEK-test
cd /tmp/NExtSEEK-test
./bootstrap.sh install --instance test
```

Bootstrap auto-detects free ports and uses a `test-` volume name prefix.
Both stacks coexist; `./bootstrap.sh reset` from `/tmp/NExtSEEK-test` nukes
only the test data.

Compose project namespacing is automatic via `COMPOSE_PROJECT_NAME`
(set in `bootstrap/.instance.json`).

## Files written by bootstrap

| Path | Tracked? | Purpose |
|---|---|---|
| `docker/db.env` | gitignored | MySQL credentials |
| `docker/nextseek.env` | gitignored | Django/Neo4j config + API keys |
| `dmac/local_settings.py` | gitignored | Django settings overlay |
| `bootstrap/.instance.json` | gitignored | Per-instance state (name, prefix, ports) |
| `logs/` | gitignored | Container runtime logs |

## Known failure modes

- **Port already in use**: Use `--port-offset N` or one of the per-service
  `--*-port` flags. `./bootstrap.sh doctor` will tell you which port is busy.
- **chat_nextseek/ missing**: Re-clone the repo. chat_nextseek is vendored —
  it must be present at clone time. If you cloned without it, run
  `bootstrap/scripts/sync_chat_nextseek.sh` from a checkout of the
  canonical repo.
- **Seed import "table already exists"**: The target DB has prior data.
  Run `./bootstrap.sh reset` for a clean install, or load into a fresh
  `--instance NAME`.
- **`manage.py check` fails with "DJANGO_SECRET_KEY not set"**: The
  `docker/nextseek.env` file is missing or empty. Run `./bootstrap.sh install`
  again — it will regenerate config without dropping volumes.
- **Chat features don't work**: API keys in `docker/nextseek.env` are
  placeholders (`SET_IN_LOCAL_ENV`). Fill in real values for
  `GCP_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, or `FDH_API`, then
  `./bootstrap.sh rebuild`.

## Maintainer: regenerating seed dumps

The shipped seeds in `bootstrap/seed/*.gz` are sanitized snapshots of a
dev environment. Regenerate them when:

- New test users / projects are added to the canonical dev DB
- Schema migrations change the data shape enough that the old dumps
  fail to load
- A SEEK upgrade introduces incompatible schema changes

To regenerate:

1. Copy `bootstrap/seed/regenerate/dump-source.env.example` to
   `dump-source.env` (gitignored) and fill in real credentials.
2. `./bootstrap.sh dump-db`

The MySQL dump script is `bootstrap/seed/regenerate/dump_mysql.sh`; the
Neo4j export is `bootstrap/seed/regenerate/dump_neo4j.py`. Both are
deliberately small and self-documenting so you can audit before running.
