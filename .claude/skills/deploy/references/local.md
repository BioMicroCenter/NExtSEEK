# This workstation (`instance: local`)

**Off on this device** (operator ruling 2026-09-24: the machine was down to 434 MiB free with the
stack up, and the local stack is now stopped): `./startup.sh rebuild`, `./startup.sh ci`,
`docker compose up`, starting the whole local stack, any image build. They stay off unless the
brief explicitly says otherwise, in words you can quote back: put those words in the brief form's
`local_ruling`, or `launch.py brief` refuses a local brief with any image, CI or Nessie (exit 5).
Rebuilds happen on the dev box, then prod. There is no preflight, runner or ssh step on local.

What is still allowed here:

## 1. The throwaway test lane (free, offline)

Runs pytest inside a disposable container from the existing `nextseek-nextseek:latest` image,
over a read-only mount of a CLEAN tree. Never mount the workstation checkout itself: its
`dmac/local_settings.py` kills collection with a `GCP_API_KEY` error, and the empty failure set
then reads as "everything fixed".

A clean tree at the sha, with no change to the shared repository:

```bash
free -h                                   # stop if available memory is low
TREE=$S/tree-<sha8>; rm -rf $TREE; mkdir -p $TREE
git -C <workstation_repo> archive <sha> | tar -x -C $TREE
```

(An existing clean worktree the brief names works too. `git archive` has no `.git`: the few
tests that shell out to git need a worktree.)

Django-side suites (`nextseek_api`, `seek`, `ci/gate`, `NessieAI/tests/...`), as root inside
the container, SQLite in memory:

```bash
docker run --rm --network none -v "$TREE":/src:ro \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 -e LOG_DIR=/tmp/l \
  -e PYTHONPATH=/build/NessieAI/chat_nextseek/src -w / nextseek-nextseek:latest \
  bash -lc 'cp -a /src /build && cd /build && /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider'
```

Do not add `-p no:logging`: it removes the `caplog` fixture and makes tests that use it error.

The harness's own unit suite runs on the host:

```bash
(cd $TREE && uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 --with orjson \
  python -m pytest NessieAI/tests/nessie_tests/tests -q -p no:cacheprovider \
  --ignore=NessieAI/tests/nessie_tests/tests/test_progress_printer.py)
```

Known on origin/dev 5cffbdc5 (checked 2026-09-24): the harness suite is 5 failed, 1523 passed; the
5 are `test_v4_2_set3_replay.py` (they want a zip from another machine). The container lane
above ran `ci/gate` plus `nextseek_api/tests/test_sample_retrieve.py` as 127 passed in 17 s.

Reading it: run the same paths on the base sha and the new sha and compare the failure SETS by
test id. Check the set sizes too: a set that dropped to zero means collection died. The image's
dependencies are whatever it was built with, so a change to `pyproject.toml` or `uv.lock` cannot
be tested this way: say so in the report. Delete `$TREE` when done.

## 2. One or two containers for a read

A read against the local graph or database may start individual existing containers:

1. `free -h` first. At most 2 containers started at a time.
2. `docker ps --format '{{.Names}} {{.Status}}'`. If the one you need is already running,
   another session started it: use it and do NOT stop it.
3. Otherwise `docker start neo4j` (or `seek-mysql`), wait until it answers, read, then
   `docker stop` it when done.
4. Reads only:
   `docker exec neo4j sh -c 'cypher-shell --access-mode read -u neo4j -p "${NEO4J_AUTH#neo4j/}" --format plain "MATCH (g:GraphMeta) RETURN g.schema_version, g.synced_at"'`
5. Never `docker compose up`, a rebuild, `./startup.sh ci`, or the full stack.

The local data is a production snapshot plus TCGA, graph schema 1.2: its numbers are neither
dev's nor prod's.

## 3. Local stack mechanics (NOT used on this device; kept for reference)

For a day the operator turns local launches back on. Do not act on this section otherwise.

- The runtime checkout is `<workstation_repo>`, shared by many sessions. It
  sits DETACHED at an origin/dev commit because the local `dev` branch is checked out in another
  worktree (`wt-nessieai`). A session moved it with
  `git -C <workstation_repo> checkout --detach <sha>`; the app's refreshed
  context JSONs and `.context_db_refresh` carry over untouched. Never commit or push from it.
- App code is baked into the image; only `themes/NextSeek`, `dmac/local_settings.py`,
  `outputs/` and `logs/` are bind-mounted. `themes/NextSeek` always comes from the workstation checkout,
  even in a worktree run.
- Rebuild: `./startup.sh rebuild --no-ci --no-registry-push`, then
  `./startup.sh rebuild --component cc-agent --no-ci --no-registry-push` if needed.
- CI: `./startup.sh ci --no-nessie` in the FOREGROUND (the session's low-memory guard kills a
  backgrounded run), without `--wait-ready` once the app has been healthy a few minutes.
- `ci_profile` is `local`. Last run: 309 passed on 5cffbdc5 (2026-09-24),
  the launch folder under `<reports_dir>`.
- Disk: rollback tags `nextseek-nextseek:pre-*` pile up (16 of them, 105 GB of images on
  2026-09-24). Never prune while a build runs, never `docker volume prune`, and never remove
  `ghcr.io/biomicrocenter/nextseek:baseline-20260805`.
