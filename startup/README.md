# `startup/`

## What this is

The bring-up CLI for the whole Docker stack, plus the data that bring-up installs.
`./startup.sh` is an 18-line shell wrapper whose last line execs
`uv run --project startup python -m startup` (`startup.sh:18`), so this directory is
its **own uv project** with its own lockfile and its own five runtime dependencies:
typer, rich, neo4j, orjson and PyMySQL (`startup/pyproject.toml:6-23`). The isolation
is deliberate and is recorded in the root project's own dependency file
(`pyproject.toml:154-157`).

Measured 2026-09-03 with `find startup -type f` excluding the generated `.venv/` and
`.pytest_cache/`: 89 files, 60 of them Python, and 28 of those Python files are test
modules matching `tests/test_*.py`. Two files carry most of the weight —
`startup/cli.py` is 810 lines and `startup/steps/schema_fixups.py` is 1014.

It is not a library. Nothing in the running Django application imports it; the only
in-repo importers are test modules and deploy scripts, listed below.

## Surface

The boundary is a **Typer command tree over a set of ordered phases, plus a committed
data payload those phases read**. So "the surface" here is the subcommands and the
phase sequence behind each, not a set of public functions; and "a dependency edge" is
mostly an external binary, a Compose service name or a repo-relative path, not an
import. The two directions are worked out separately under *Depends on / depended on
by*.

**Seven subcommands**, all declared with `@app.command()` in one file.

| Command | Defined at | What it drives |
|---|---|---|
| `install` | `startup/cli.py:378` | 9 numbered phases, body in `_install_impl` (`startup/cli.py:86`) |
| `doctor` | `startup/cli.py:421` | read-only diagnosis, `startup/steps/doctor.py:75` |
| `reset` | `startup/cli.py:450` | volume drop then a re-entry into install |
| `rebuild` | `startup/cli.py:511` | one first-party component, rollback-tagged |
| `ci` | `startup/cli.py:697` | the smoke suite as a subprocess |
| `seed-filestore` | `startup/cli.py:746` | the SEEK blob archive into a running stack |
| `dump-db` | `startup/cli.py:781` | maintainer-only seed regeneration |

**The nine install phases** are printed by nine `ui.step(n, 9, …)` calls in
`_install_impl`: prerequisites (`startup/cli.py:114`), vendored-`chat_nextseek`
verification (`startup/cli.py:125`), instance and port resolution
(`startup/cli.py:133`), config rendering (`startup/cli.py:221`), volume creation
(`startup/cli.py:241`), seed import (`startup/cli.py:251`), image build and stack
start (`startup/cli.py:308`), test-user verification (`startup/cli.py:354`) and health
checks (`startup/cli.py:362`). Three unnumbered steps run between phases 6 and 7:
schema fixups (`startup/cli.py:288`), SEEK's `site_base_host`
(`startup/cli.py:300-302`) and, after phase 7, stale-chat cleanup
(`startup/cli.py:349`).

**Three layers.** `cli.py` holds argument parsing and phase ordering and nothing else.
`steps/` holds one module per phase — 14 of them, each a pure-ish function over
`(repo_root, compose_env)`. `lib/` holds 7 primitives: subprocess wrappers around
docker (`startup/lib/docker_ops.py:1`), `.env` read/write that preserves comments and
key order (`startup/lib/env.py:1`), per-instance state
(`startup/lib/instance.py:1`), port probing (`startup/lib/ports.py:1`), the rebuild
component map (`startup/lib/rebuild_policy.py:1`), clean-source verification
(`startup/lib/deploy_source.py:1`) and the Rich console wrappers
(`startup/lib/ui.py:1`).

**The data payload.** `startup/seed/` ships three gzipped dumps loaded in phase 6, and
`startup/seed/sql/` ships eight `CREATE TABLE IF NOT EXISTS` files, five of which are
registered as table fixups at `startup/steps/schema_fixups.py:109-152`. All five of
those tables are absent from the committed dump: measured 2026-09-03,
`zgrep -c 'CREATE TABLE \`<name>\`' startup/seed/dmac.sql.gz` returns 0 for
`sample_attributes_unique`, `sample_type_requirements`, `assay_context`,
`projects_context` and `project_template_bundles`, and 1 for `sample_types_context`.
So those five tables reach an install only through the fixup step, never through the
seed. `startup/templates/` holds the three files rendered into `docker/db.env`,
`docker/nextseek.env` and `dmac/local_settings.py`
(`startup/steps/config.py:149-170`).

**Two managed indexes** on Rails-owned SEEK tables are declared at
`startup/steps/schema_fixups.py:376-392`, and applying them is opt-in behind an
environment flag, default off (`startup/steps/schema_fixups.py:976-995`).

**`startup/dev/`** is a separate, hand-run lane, not part of any subcommand:
`startup/dev/run_full_test_lane.sh:6-14` runs `nextseek_api/tests startup/tests` in one
pytest invocation inside a pinned app image on a `--internal` docker network, and
`startup/dev/provision_embedding_model.sh:51` verifies the embedding-model cache the
lane needs against a committed manifest.

### Per-command behaviour that `--help` does not show

`install` is idempotent for prerequisites, config, volumes, users and validation, and
each seed is skipped independently when its target already holds tables
(`startup/cli.py:262-273`). Ports are not merely checked: `allocate_ports` walks forward
from each default until it finds a free one, up to 200 attempts
(`startup/lib/ports.py:28-42`), so a busy 8000 produces a working install on a different
port rather than an error. `--seek-public-url` resolves in a never-clobber order —
explicit flag, then the value already rendered into `docker/nextseek.env`, then the
stored instance value, then `http://localhost:<seek port>`
(`startup/steps/config.py:78-101`) — and one resolved value feeds both the app's link
building and SEEK's own DB-backed identity (`startup/cli.py:145-156`). That second layer
is set only when the row is absent; an existing row is reported as an admin decision and
left alone (`startup/steps/seek_settings.py:171-198`). An unknown `--ci-profile` exits 2
before anything is written (`startup/cli.py:104-109`).

`doctor` is read-only and reports the two CI lines without ever failing on them, because
a box that does not run CI is not broken (`startup/steps/doctor.py:23-27`); the
credential file is opened only to learn which keys it names, never their values
(`startup/steps/doctor.py:40-44`).

`rebuild` creates and verifies a local rollback tag for every affected image before it
builds anything (`startup/cli.py:580-590`), then recreates long-running targets with
`--no-deps --force-recreate` (`startup/cli.py:599-607`). It ends by running the smoke
suite with the readiness gate on, and skips that step automatically after
`--no-restart`, where the containers still carry the old image
(`startup/cli.py:631-637`). A CI failure is reported with its counts and the junit path
and exits with the suite's own code, and the rebuild is deliberately not undone
(`startup/cli.py:646-656`).

`ci` prints what is about to run first — profile and where it came from, the stack URL,
the credential file as a path only, and the exact argv (`startup/cli.py:660-681`).
Widening past the box's declaration needs an answered terminal prompt, and the
acknowledgement is set for that one subprocess and never written back
(`startup/cli.py:716-728`, `startup/ci/runner.py:64-67`).

`seed-filestore` skips when the volume already holds assets unless `--force`
(`startup/cli.py:772-774`). The ~215MB archive is not in git; it is fetched from a fixed
S3 URL and sha256-verified (`startup/steps/seed_filestore.py:30-34`), and during install
a failed download only warns so the rest of the install still completes
(`startup/cli.py:328-334`).

`dump-db` refuses without `startup/seed/regenerate/dump-source.env`
(`startup/cli.py:788-792`), and runs the MySQL and Neo4j dump scripts in turn
(`startup/cli.py:796-805`).

For a second install on one machine, pass `--instance NAME`: that is what produces a
non-empty volume prefix and a distinct Compose project (`startup/cli.py:144`,
`startup/cli.py:165`), which are then exported to every docker invocation
(`startup/lib/instance.py:27-43`). `--port-offset N` shifts every default port
(`startup/cli.py:139-142`).

**What startup writes** (all gitignored): `docker/db.env`, `docker/nextseek.env`,
`dmac/local_settings.py` (`startup/steps/config.py:151`,
`startup/steps/config.py:159`, `startup/steps/config.py:167`),
`docker/bedrock-proxy/proxy-secret.env` at mode 0600
(`startup/steps/config.py:188-202`), the repo-root `.env`
(`startup/steps/config.py:240-247`), `startup/.instance.json`
(`startup/lib/instance.py:53-60`), and the junit report the CI shim reads back
(`startup/ci/runner.py:22-27`).

## Running and testing

The CLI has its own hermetic suite: no docker daemon, no database, nothing paid. It
does need two things blocked, because `startup/tests/conftest.py:7` registers a plugin
from the main Django project and `startup/tests/test_schema_fixups.py:32` imports
`MySQLdb` at module scope, and neither loads under this project's isolated
dependencies.

```
cd startup && uv run --project . --group test python -m pytest tests/ -q \
  -p no:nextseek_api.attributes.tests.attribute_fixtures \
  --ignore=tests/test_schema_fixups.py
```

Run 2026-09-03 on this host: **2 failed, 425 passed, 1 skipped, 5 errors in 2.59s**.
The 5 errors are all `fixture 'disposable_attribute_db' not found`, the direct price of
the `-p no:` block. The 2 failures are host-independent test-side defects, both in the
managed-index tests, and both are described in the paired CLAUDE.md.

Adding the coverage gate the same day gives 88.89% against a `--cov-fail-under=95`
bar, because `startup/steps/schema_fixups.py` drops to 53% once its MySQLdb-backed
module is ignored:

```
cd startup && uv run --project . --group test python -m pytest tests/ -q \
  -p no:nextseek_api.attributes.tests.attribute_fixtures \
  --ignore=tests/test_schema_fixups.py --cov=startup.cli --cov=startup.lib \
  --cov=startup.steps --cov=startup.ci --cov-fail-under=95
```

The full-coverage lane needs mysqlclient and a disposable MySQL server, which is what
`scripts/attribute_api_test.sh schema` provisions; the lane driven by
`startup/dev/run_full_test_lane.sh:106-108` additionally needs an exact pinned app
image and a provisioned embedding-model cache. (not run) — both need infrastructure
this host does not have: a MySQL server and the pinned
`ghcr.io/biomicrocenter/nextseek:baseline-20260805` image.

### When bring-up misbehaves

`./startup.sh doctor` first: it runs the same prerequisite and health checks install
does and exits non-zero if any of them fail (`startup/cli.py:438-446`). Then, by
symptom:

- A missing or empty `docker/nextseek.env` shows up as the containerised
  `manage.py check` failing (`startup/steps/validate.py:33-42`); re-running `install`
  regenerates config without dropping volumes.
- Chat features staying inert usually means the three API keys are still their rendered
  placeholders (`startup/templates/nextseek.env.template:37-39`), and the Bedrock path
  additionally warns at install time when its own env file has no token
  (`startup/cli.py:50-55`).
- A cloned tree with no `chat_nextseek/` aborts phase 2 with the remediation printed
  (`startup/cli.py:126-129`).

`startup/pytest.ini:2` sets `pythonpath = ..`, which is what makes both `startup.*` and
`nextseek_api.*` importable from inside `startup/tests/`.

## Depends on / depended on by

Depends on. Not imports: this package imports nothing first-party outside itself. No
line matching `^\s*(from|import)\s+(dmac|nextseek_api|seek|chat_nextseek|ci)\b` exists
in any `.py` file under `startup/cli.py`, `startup/lib/`, `startup/steps/` or
`startup/ci/`; the only first-party imports there are `startup.*`. Its real edges are:

- External binaries: `docker`, `docker compose` and `uv` are each probed
  (`startup/steps/prereqs.py:77-82`) in the first install phase, before anything is
  written (`startup/cli.py:114-122`), plus `git`, shelled out to for the
  clean-source check (`startup/lib/deploy_source.py:20-29`) and the rollback tag
  (`startup/steps/rollback_tags.py:47`).
- Compose service names as build and restart targets: `nextseek`, `cc-agent`,
  `nextseek-sidecar` and `bedrock-proxy` are named at
  `startup/lib/rebuild_policy.py:102-168`.
- Compose service names as `exec` targets: `db` takes the SQL seeds
  (`startup/steps/seed.py:85`) and the schema-fixup table probe
  (`startup/steps/schema_fixups.py:160-161`), `neo4j` takes the graph seed
  (`startup/steps/seed.py:56`) and `seek` takes the filestore archive on stdin
  (`startup/steps/seed_filestore.py:94-103`).
- Seven external named volumes it creates by name (`startup/steps/volumes.py:6-16`);
  six are declared `external: true` at `docker-compose.yml:503-520` and the seventh at
  `docker-compose.yml:530-531`, so Compose fails rather than creating them itself.
- The vendored `chat_nextseek/pyproject.toml`, whose absence aborts install phase 2
  (`startup/cli.py:126-129`).
- `ci/smoke/` by path string, launched as a subprocess and never imported, precisely so
  requests and playwright stay out of this project (`startup/ci/runner.py:1-5` and
  `startup/ci/runner.py:38`).
- A MySQL driver chosen at runtime, preferring host mysqlclient and falling back to the
  declared PyMySQL (`startup/steps/schema_fixups.py:898-912`).
- `~/.config/nextseek/ci.env` for smoke credentials, read only for the key names
  (`startup/steps/doctor.py:13` and `startup/steps/doctor.py:59-63`), and
  `~/.config/nextseek/ghcr.env` for the registry push
  (`startup/steps/registry_push.py:14-18`).

Depended on by. Non-test and cross-boundary consumers, derived by grepping the tree for
`^\s*(from|import)\s+startup(\.|\s|$)` and for the literal strings `startup.sh`,
`startup/.instance.json` and `startup/seed`. This package's own 28 test modules are
omitted, and so are the two files under `docs/superpowers/plans/` whose `from startup…`
lines sit inside quoted code samples in a plan document rather than being imports that
file performs.

- `.github/workflows/ci-smoke.yml:60` reads `startup/.instance.json` by path to learn
  the box-declared profile, deliberately rather than naming one itself.
- `seek/tests/test_context_seed_tables.py:10` binds `startup/seed/sql/` as a directory
  and reads three DDL files out of it at `seek/tests/test_context_seed_tables.py:19`,
  then asserts against `KNOWN_TABLE_FIXUPS` at
  `seek/tests/test_context_seed_tables.py:45`.
- `nextseek_api/cc_assistant/tests/validate_step7_compose_deploy.py:57` imports
  `REQUIRED_VOLUMES` as the authoritative volume list, behind a defensive `except
  ImportError` fallback at
  `nextseek_api/cc_assistant/tests/validate_step7_compose_deploy.py:58-62`.
- `scripts/plan018_v4_9_task8_deploy.py:914-915` imports the instance-state and config
  modules, and `scripts/plan018_v4_9_task8_deploy.py:1104` imports the registry push.
- `scripts/attribute_api_test.sh:356` is not an import that file performs: it is Python
  source inside a heredoc opened at `scripts/attribute_api_test.sh:353` and executed
  inside a container.
- `chat_nextseek/README.md:172` documents `startup/dev/lane_local_settings.py` as the
  file that constructs the Django-wide assistant config at settings-import time.
- `.gitignore:229-231` and `.gitignore:261` reserve the four runtime paths this CLI
  writes or downloads, so none of them can be committed by accident.

See `startup/CLAUDE.md` for the invariants, the traps and the one command to run.
