# Startup CLI reference

`./startup.sh` is the entry point. All subcommands accept `--help`.

## Commands

### `install`

First-time setup. Runs all 9 phases: prereqs, vendor verify, config,
volumes, seeds, build, users, validate.

```
./startup.sh install                       # default: ports 8000/3000/7474/7687
./startup.sh install --instance test       # named instance with auto-assigned ports
./startup.sh install --port-offset 1       # +1 on every port (8001/3001/7475/7688)
./startup.sh install --yes                 # skip confirmation prompts
./startup.sh install --seek-public-url https://seek.example.com   # real SEEK hostname
```

Idempotent for prereqs / config / volumes / users / validate. Seed import
is skipped if the target DB already has tables.

**`--seek-public-url`** — the browser-reachable SEEK base URL (host only, no
path). Omit it on a laptop and it defaults to `http://localhost:<seek port>`.
It is stored per-instance in `startup/.instance.json` and drives **both** layers
that need it, so they cannot drift apart:

- `SEEK_PUBLIC_URL` in `docker/nextseek.env` — how NExtSEEK builds links **to** SEEK
- SEEK's own DB-backed `site_base_host` — how SEEK identifies **itself** (its
  "SEEK ID", JSON-LD `@id`, sitemap). Applied after the seed and **before SEEK's
  first boot**, so the boot-time sitemap is correct and no restart is needed.

Resolution order (a hand-set value is never clobbered):

```
--seek-public-url  >  existing docker/nextseek.env  >  .instance.json  >  http://localhost:<seek port>
```

An existing `site_base_host` row in SEEK is treated as an admin decision:
startup reports a mismatch and **never overwrites it**. `./startup.sh doctor`
reports drift between the three ("SEEK public URL"). See `NExtSTEPS.md` §1d.

### `doctor`

Read-only diagnostic. Runs prereqs + health checks and reports drift.

```
./startup.sh doctor
```

Exits non-zero if any check fails. **Run this first when something's broken.**

### `reset`

Destructive: drops all volumes for the current instance and re-runs install.

```
./startup.sh reset                # also re-renders config files
./startup.sh reset --keep-config  # preserves docker/*.env, dmac/local_settings.py
./startup.sh reset --yes          # skip the confirmation prompt
```

### `rebuild`

Rebuilds and restarts one service without touching volumes. Default service
is `nextseek`.

```
./startup.sh rebuild
./startup.sh rebuild --service nextseek_nginx
```

### `seed-filestore`

Loads `startup/seed/filestore.tar.gz` into the running `seek` container's
`/seek/filestore` volume — the content blobs (data files, SOPs, avatars, ...)
that the `seek_production` metadata points at. The ~215MB archive isn't in git;
if it's not already in `startup/seed/` it's downloaded from S3 (sha256-verified)
first. `install` does this automatically in phase 7; use this command to
(re)seed an already-running stack without a full reinstall. Skips if the
filestore already holds assets unless `--force` is given.

```
./startup.sh seed-filestore
./startup.sh seed-filestore --force
```

### `dump-db`

**Maintainer-only.** Regenerates the gzipped seed dumps from a source DB.
Requires `startup/seed/regenerate/dump-source.env` (gitignored) with the
source-DB credentials. Errors gracefully if absent.

```
./startup.sh dump-db
```

## Multi-instance / side-by-side installs

To run a second isolated install on the same machine without disrupting
your existing one:

```bash
git clone <repo-url> /tmp/NExtSEEK-test
cd /tmp/NExtSEEK-test
./startup.sh install --instance test
```

Startup auto-detects free ports and uses a `test-` volume name prefix.
Both stacks coexist; `./startup.sh reset` from `/tmp/NExtSEEK-test` nukes
only the test data.

Compose project namespacing is automatic via `COMPOSE_PROJECT_NAME`
(set in `startup/.instance.json` and mirrored to the root `.env` for manual
`docker compose` commands).

## Files written by startup

| Path | Tracked? | Purpose |
|---|---|---|
| `docker/db.env` | gitignored | MySQL credentials |
| `docker/nextseek.env` | gitignored | Django/Neo4j config + API keys |
| `docker/bedrock-proxy/proxy-secret.env` | gitignored | Bedrock proxy runtime token + region |
| `dmac/local_settings.py` | gitignored | Django settings overlay |
| `.env` | gitignored | Non-secret compose project + published port vars |
| `startup/.instance.json` | gitignored | Per-instance state (name, prefix, ports) |
| `logs/` | gitignored | Container runtime logs |

## Known failure modes

- **Port already in use**: Use `--port-offset N` or one of the per-service
  `--*-port` flags. `./startup.sh doctor` will tell you which port is busy.
- **chat_nextseek/ missing**: Re-clone the repo. chat_nextseek is vendored —
  it must be present at clone time. If you cloned without it, run
  `startup/scripts/sync_chat_nextseek.sh` from a checkout of the
  canonical repo.
- **Seed import "table already exists"**: The target DB has prior data.
  Run `./startup.sh reset` for a clean install, or load into a fresh
  `--instance NAME`.
- **`manage.py check` fails with "DJANGO_SECRET_KEY not set"**: The
  `docker/nextseek.env` file is missing or empty. Run `./startup.sh install`
  again — it will regenerate config without dropping volumes.
- **Chat features don't work**: API keys in `docker/nextseek.env` are
  placeholders (`SET_IN_LOCAL_ENV`). Fill in real values for
  `GCP_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, or `FDH_API`, then
  `./startup.sh rebuild`.
- **CC Bedrock calls don't work**: `docker/bedrock-proxy/proxy-secret.env`
  is generated during install. If `AWS_BEARER_TOKEN_BEDROCK` was not exported
  before install, fill it in there and re-run `./startup.sh rebuild --service
  bedrock-proxy`.

## Maintainer: regenerating seed dumps

The shipped seeds in `startup/seed/*.gz` are sanitized snapshots of a
dev environment. Regenerate them when:

- New test users / projects are added to the canonical dev DB
- Schema migrations change the data shape enough that the old dumps
  fail to load
- A SEEK upgrade introduces incompatible schema changes

To regenerate:

1. Copy `startup/seed/regenerate/dump-source.env.example` to
   `dump-source.env` (gitignored) and fill in real credentials.
2. `./startup.sh dump-db`

The MySQL dump script is `startup/seed/regenerate/dump_mysql.sh`; the
Neo4j export is `startup/seed/regenerate/dump_neo4j.py`. Both are
deliberately small and self-documenting so you can audit before running.
