---
name: local-dev
description: >-
  Use when working on NExtSEEK on a laptop or workstation rather than a deployed box: running Django, gate or CI
  tests locally, testing a worktree or a commit, checking the chat front end, reading the local graph or MySQL,
  or when the machine is slow, swapping or out of memory ("OOM", "my Mac froze", "Docker is eating all my RAM",
  "can I run the stack locally?", "rebuild it here"). Not for dev or prod boxes (that is `deploy`).
---

# local-dev: work on NExtSEEK without running the machine out of memory

The full stack does not fit on a laptop. Its compose memory caps (`*_MEMORY` in `docker-compose.yml`) add up to
about 41 GB; a running stack takes 10 to 18 GB, and a build plus the full graph sync at boot has taken a 30 GB
laptop to zero. So: **start only what the change needs; rebuilds, CI and Nessie checks happen on fairdata-dev.**
The workstation-specific ruling is `.claude/skills/deploy/references/local.md`; this skill applies it to any
machine. Below, `deploy/...` paths are under `.claude/skills/`.

## Never locally

`docker compose up` (the whole stack), `./startup.sh rebuild`, `./startup.sh ci`, `./startup.sh install`, any
image build. Only the operator can lift this, for a named run, in words you can quote back. Even then a full stack
needs about 15 GB free at the start and a watchdog script that polls free memory every couple of seconds and stops
the containers under ~3.5 GB. An instruction to a subagent to "stop if memory is low" is not a watchdog: it was
ignored, and the machine went down. On a machine that cannot have 15 GB free (a 16 GB laptop), the answer is
always fairdata-dev. A box only runs commits already on `origin/dev` (`deploy`), so a branch gets there by PR and
merge: tell the operator that up front when they are racing a deadline.

## Before starting any container

1. Check memory. Linux: `free -h`; under about 4 GB available, start nothing. macOS: `memory_pressure | tail -1`
   and `top -l 1 -s 0 | grep PhysMem`; a free percentage under about 20%, a compressor of several GB or yellow/red
   pressure in Activity Monitor means start nothing. Then tell the operator and offer "Out of memory" below.
2. `docker ps --format '{{.Names}} {{.Status}}'`. A container already running was started by the operator or
   another session: use it (a read in it is fine even when memory is low), and stop it only on the operator's
   explicit go (see "Out of memory").

## What to run for each change

| Change | Run |
|---|---|
| Python / Django (`nextseek_api`, `seek`, `dmac`, `ci/gate`) | Nothing live: one throwaway test container (below). `dmac.test_settings` is SQLite in memory. |
| Chat front end | No containers. In `NessieAI/chat_frontend`: `npm run test`; to look at it, `npm run dev` (the mock project needs no `.env`; the live shell needs the `VITE_API_*` values from the operator, see that folder's `README.md`). |
| Docs map, route registry, smoke-suite unit tests | No containers: the host commands in `ci/README.md` "Running and testing". |
| Graph or MySQL read | `docker start neo4j` and/or `seek-mysql` (at most two), read only, then `docker stop` what you started. |
| Seeing a code or template change in a page | fairdata-dev. The local `web` runs the code baked into the image; only `themes/NextSeek` is mounted, from the main checkout, so a worktree's change never shows there. Test the view with the test container instead. |
| A theme-only change in the main checkout | `docker start seek-mysql`, then from the main checkout (`docker/*.env` is gitignored, absent from worktrees) `docker compose up -d --no-deps web`. That is the two-container limit, so pages that call SEEK go to dev. Not yet tried end to end: report it as untried. |
| Dependency change (`pyproject.toml`, `uv.lock`), a rebuild, CI, Nessie, a graph write | fairdata-dev, through `deploy`. |

The local data is a production snapshot plus TCGA: its numbers are neither dev's nor prod's.

## The throwaway test container

All three routes reuse the `nextseek-nextseek:latest` image's virtualenv (host `uv sync` fails: `mysqlclient`
needs MySQL headers), run with `--rm`, and cannot test a dependency change. Check the image exists:
`docker images nextseek-nextseek`.

| Route | When | Command lives in |
|---|---|---|
| `scripts/run_tests.sh <pytest args>` | A checkout that has `dmac/local_settings.py` (the main checkout). One `docker compose run --rm --no-deps` container with the checkout mounted over `/app`. Defaults to `nextseek_api/tests`. | `scripts/README.md` group B, `scripts/CLAUDE.md` "Landmines" |
| Read-only worktree mount | A worktree with no `local_settings.py` (a fresh `git worktree add`), including CI's own gate and blocking-lane commands | `ci/README.md` "Running and testing" |
| Clean tree at a sha (`git archive`, copied to `/build` inside the container) | Testing a commit exactly, or base against new | `deploy/references/local.md` §1 |

Which tests: the app's own `tests/` files for the changed module first (a change under `nextseek_api/services/` →
`nextseek_api/tests/`, by name), then what CI blocks on (`python3 ci/blocking_lanes.py` prints the paths). Some
tests fail on any laptop for local reasons (`scripts/CLAUDE.md` "Landmines"), which is why you compare with the
base commit. Anything wider is a CI run on fairdata-dev.

- Never mount a checkout that has `dmac/local_settings.py` read-only: collection dies on `GCP_API_KEY` and an
  empty failure set reads as "all passed". Compare failure SETS by test id, and treat a set that dropped to zero
  as dead collection.
- A read-only mount needs `mkdir -p schema_rag/duckdb schema_rag/embedding_models` first (`ci/CLAUDE.md`).
- `run_tests.sh` in a worktree: copy `dmac/local_settings.py` in from the main checkout; set
  `NEXTSEEK_COMPOSE_DIR` if the main checkout is not at the script's default.
- **BAML client.** `NessieAI/dmac_assistant/src/dmac_assistant/router/baml_client/` is generated and gitignored.
  The image has its own, but a mount over `/app` or `/src` hides it, so router-importing tests fail at collection.
  On any checkout without it (every fresh worktree), generate it once BEFORE the first test run, whatever you
  changed (the blocking lanes include NessieAI tests). It writes into the checkout, so it needs the tree mounted
  writable; files it creates are gitignored (on Linux they belong to root):

  ```bash
  docker run --rm --network none -v "$PWD":/src -w /src nextseek-nextseek:latest \
    /app/.venv/bin/baml-cli generate --from NessieAI/dmac_assistant/baml_src --no-version-check
  ```

- Prefer these over `docker compose exec nextseek pytest`: a host-side timeout leaves its pytest running in the
  container, and the orphans make an 8 s suite take 200 s+ with the CPU idle. If it was used, check with
  `docker compose exec -T nextseek sh -c 'ps -eo pid,etimes,args | grep "[p]ytest"'` before trusting timings.

## Out of memory

Stopping keeps containers and volumes, so no data is lost. Get the operator's go first when the stack is theirs:

```bash
docker stop nextseek seek-workers seek seek-solr seek-mysql neo4j dmac-bedrock-proxy
docker ps -q --filter name=nextseek-web | xargs docker stop
```

- Never `docker rm`, `docker compose down -v` or any prune. Leave other sessions' `--rm` pytest containers
  running: killing one fakes test failures. A reboot kills them too, so tell the operator before one.
- Most services have `restart: always`, so Docker brings stopped containers back whenever it restarts (a reboot,
  Docker Desktop starting at login). After one, check `docker ps` again. Making a stop stick
  (`docker update --restart unless-stopped <names>`, undone by the next recreate) is the operator's call.
- On macOS every container shares Docker Desktop's VM, capped in Settings → Resources → Memory. That cap is what
  keeps macOS alive; changing it is the operator's call.
