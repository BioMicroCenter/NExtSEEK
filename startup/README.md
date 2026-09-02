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
./startup.sh install --ci-profile dev      # what the smoke suite may call here
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

**`--ci-profile`** — which CI profile this box declares: `local`, `dev` or
`prod`. It decides which routes `./startup.sh ci` may call here (see `ci` below).
It defaults to **`prod`**, the most restrictive, so a box nobody configured is
never widened by accident, and it is stored per-instance as `ci_profile` in
`startup/.instance.json`. An unknown value exits 2 before anything is written.

On a box installed **before** this option existed the key is simply absent,
which also reads as `prod`. Add it by hand — `"ci_profile": "dev"` — rather than
re-running install, which re-renders config and rotates the Django secret key.
`reset` carries the declared value across the wipe.

### `doctor`

Read-only diagnostic. Runs prereqs + health checks and reports drift.

```
./startup.sh doctor
```

Exits non-zero if any check fails. **Run this first when something's broken.**

Two of its lines are about CI and neither can fail the run, because a box that
does not run CI is not broken:

- **CI profile** — what this instance declares, or `absent -> prod`.
- **CI credentials** — whether `~/.config/nextseek/ci.env` (or `NEXTSEEK_CI_ENV`)
  exists and **names** `CI_SMOKE_USER` / `CI_SMOKE_PASS`. It never reads or prints
  a value. Check this first when `./startup.sh rebuild` fails at its CI step.

### `reset`

Destructive: drops all volumes for the current instance and re-runs install.

```
./startup.sh reset                # also re-renders config files
./startup.sh reset --keep-config  # preserves docker/*.env, dmac/local_settings.py
./startup.sh reset --yes          # skip the confirmation prompt
```

### `rebuild`

Safely rebuilds one first-party component without touching volumes. Before any
build it creates and verifies a local rollback tag for every affected image.
Long-running targets are recreated with `--no-deps --force-recreate`.

```
./startup.sh rebuild                              # shared app image + all app runtimes
./startup.sh rebuild --component cc-agent         # build-only; no persistent container
./startup.sh rebuild --component nextseek-sidecar
./startup.sh rebuild --component bedrock-proxy
./startup.sh rebuild --component custom-stack     # all first-party images
```

The default app component rebuilds one shared image and recreates `nextseek`,
`attribute_mutation_worker`, `attribute_mutation_dispatcher`, and
`attribute_mutation_recovery_scheduler`. It does not touch nginx, databases,
SEEK, or Solr. The worker and dispatcher reattach the existing
`attribute_mutation_broker` SQLite named volume: rebuild never renews or
deletes it. The explicitly destructive `reset` command does delete volumes.
`--service` remains an alias for `--component`; arbitrary Compose services are
rejected.

If the installed runtime checkout contains unrelated operator-owned files,
use `./startup.sh rebuild --source-tree <clean-origin-dev-worktree>`. The CLI
builds from that verified clean source while recreating from the installed
instance, preserving its existing bind-mounted output, log, and configuration
paths. Runtime/source SHAs must match, and dirty deployment-control files are
refused.

After a rebuild on the canonical instance (compose project `nextseek`), the
CLI tries to push each rebuilt image to its private GHCR package, gated by the
DEPLOYMENT.md §5.2 baked-secret check. **This step never fails the rebuild**:
with no credential (or an expired one) it prints a banner
telling the deployer how to fix it, records the failure in
`startup/.ghcr-push-state.json` (gitignored), and `./startup.sh doctor` keeps
flagging it until a push succeeds. Credential: a classic PAT with
`write:packages` (owner must be a BioMicroCenter org member) in
`~/.config/nextseek/ghcr.env` as `GHCR_USER=…` / `GHCR_TOKEN=…` (mode 600;
override the path with `NEXTSEEK_GHCR_ENV`). See DEPLOYMENT.md §5.2.

A rebuild ends by running the CI smoke suite against the rebuilt stack, with
the readiness gate applied. Before the run it prints what is about to happen:
the profile and where it came from, the stack URL, which credential file is in
play (path only, never a value) and the exact command. The gate then waits out
the readiness floor (300 s by default) with a `[readiness] floor: N s
remaining` line every 30 s, prints each probe, and says `ready after N s`
before the first test runs. The run closes with one line of counts in
pytest's own words, `CI passed: 207 passed, 6 skipped, 13 xfailed in 5:44
(readiness 5:04)`, read back from the junit report the suite writes to
`startup/.ci-last-run.xml` (gitignored, overwritten every run).
`--no-ci` skips the step, and it is skipped automatically after
`--no-restart`, where the running containers do not yet carry the new image.

```
./startup.sh rebuild --no-ci                      # rebuild only; run CI yourself later
```

**If CI fails after a rebuild** the counts are reported (`CI failed after
rebuild: 3 failed, 204 passed ...`), the junit report's path is printed, and
`rebuild` exits with the suite's exit code. A run that ended before any test
(a refused profile, a readiness failure) reports `exit N, no report written`. The rebuild itself succeeded and is still running: it is
*not* rolled back, because undoing a deploy is a larger and more dangerous
action than the one it would be reacting to, so the decision stays with the
deployer. See DEPLOYMENT.md for the rollback procedure if the failures are
regressions.

The suite needs `~/.config/nextseek/ci.env` to exist. Under `--wait-ready` a
missing `CI_SMOKE_USER`/`CI_SMOKE_PASS` is an **exit 2**, not a skip: a
readiness gate probes an authenticated endpoint, so with no credentials it
cannot do its job, and a rebuild must never report "CI passed" for a run that
proved nothing.

### `ci`

Runs the post-deploy smoke suite (`ci/smoke/`) against this instance's running
stack. The suite is *subprocessed*, never imported: it needs pytest, requests
and playwright, and `startup/` deliberately depends on none of them. It prints
the same banner and closing counts line as the rebuild hook (see `rebuild`).

```
./startup.sh ci                       # against http://127.0.0.1:<this instance's nextseek port>
./startup.sh ci --wait-ready          # apply the readiness floor first (what rebuild does)
./startup.sh ci --profile prod        # narrow: run only what a prod box permits
./startup.sh ci --force-profile local # widen: prompts, see below
```

**The box declares its own profile**, as `ci_profile` in
`startup/.instance.json` (`local`, `dev` or `prod`). It decides which routes in
the registry the suite may call: a route registered for `local,dev` is not
called on a box declaring `prod`. **An absent or empty `ci_profile` means
`prod`**: a machine nobody has configured gets the most restrictive profile,
never the least.

Set it with `install --ci-profile`, or on an existing install by adding the key
to `startup/.instance.json` by hand. `./startup.sh doctor` reports which value is
in force. The profile also gates whole tests, not only routes: a browser flow
whose shape is a write (`@pytest.mark.profiles("local", "dev")`) is **skipped**
under `prod` rather than run and refused.

`--profile` may only *narrow* that declaration; asking for a wider one exits
non-zero rather than running. `--force-profile` is the deliberate override: it
asks for confirmation at the terminal, and only an answered `yes` passes the
acknowledgement the suite requires. Nothing is written back to
`.instance.json`, so the widening lasts exactly one run.

Credentials come from `~/.config/nextseek/ci.env` (mode 600, never committed);
environment variables override the file and `NEXTSEEK_CI_ENV` points at a
different one. See `ci/smoke/README.md` for the file's contents and for what
each profile covers.

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
| `startup/.instance.json` | gitignored | Per-instance state (name, prefix, ports, CI profile) |
| `logs/` | gitignored | Container runtime logs |

## Tests & coverage

The startup CLI is deployment-critical and holds a **95% minimum** coverage
bar (currently ~99%). Hermetic suite (no docker daemon touched):

```
uv run --project startup --group test python -m pytest startup/tests/
```

Coverage gate (fails under 95%):

```
uv run --project startup --group test python -m pytest startup/tests/ \
  --cov=startup.cli --cov=startup.lib --cov=startup.steps --cov=startup.ci \
  --cov-fail-under=95
```

Integration lanes: `test_integration_startup.py` chains real git repos, real
state files, and multi-command CLI flows (docker mocked); set
`NEXTSEEK_STARTUP_DOCKER_TESTS=1` to also run the opt-in real-docker tests.

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
