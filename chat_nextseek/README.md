# `chat_nextseek/`

## What this is

The deterministic NExtSEEK query engine: a multi-agent pipeline that turns a
natural-language question into REST calls against this repo's own API, Cypher
against Neo4j, report exports, and nf-core pipeline launches. Django runs it
in process for the non-sandboxed half of the chat endpoint, described at
`nextseek_api/services/cc_assistant.py:15-18`.

It is also a **vendored snapshot of a separate repository**. The canonical
source is an independent private repo, and this directory is a copy of it
refreshed wholesale by `startup/scripts/sync_chat_nextseek.sh:2-7`. The refresh
is an `rsync -a --delete` with a short exclude list
(`startup/scripts/sync_chat_nextseek.sh:36-45`), so it is a replace, never a
line-merge, and the script refuses to run against a dirty source checkout
(`startup/scripts/sync_chat_nextseek.sh:28-31`). The outer project consumes the
result as an editable local path dependency rather than a pinned git revision
(`pyproject.toml:136`), with the git+revision form kept as a commented
alternative for when the package goes public (`pyproject.toml:140-141`).

Because it is vendored from a standalone application, much of the tree is
carried along rather than exercised here. Measured 2026-09-03 by `find` over
this directory excluding `__pycache__`: 298 files, 219 of them Python, 104 of
those under `tests/` and 94 under `src/`. The package is a `src/` layout
(`chat_nextseek/pyproject.toml:32-33`) whose importable half is what Django
touches; the Streamlit app, the CLI, the MCP server and the E2E runner at the
directory root are the standalone half.

## Surface

This boundary has **three different surfaces**, and they are worth separating
because only one of them is an import edge.

### 1. The importable package — the only surface Django uses

`portable.py` is the declared stable API: twelve symbols as of 2026-09-03, each
an agent or a tool with a contract stated in its module docstring
(`chat_nextseek/src/chat_nextseek/portable.py:1-14`), listed at
`chat_nextseek/src/chat_nextseek/portable.py:27-43` and pinned against drift by
`chat_nextseek/tests/test_portable_contract.py:13-26`.

| Entry point | Where |
|---|---|
| Single-turn chat pipeline | `chat_nextseek/src/chat_nextseek/orchestrator.py:596` |
| Planner pipeline | `chat_nextseek/src/chat_nextseek/orchestrator.py:1387` |
| Direct pipeline-launch entry | `chat_nextseek/src/chat_nextseek/orchestrator.py:268` |
| Caller-identity binding for a turn | `chat_nextseek/src/chat_nextseek/orchestrator.py:175-194` |
| Per-agent model, provider and thinking resolution | `chat_nextseek/src/chat_nextseek/config.py:1321-1343` |
| Chat-log helpers shared with Django | `chat_nextseek/src/chat_nextseek/chat_memory.py:201-248` |
| Session state over SQLite or MySQL | `chat_nextseek/src/chat_nextseek/session.py:19-26` |

Beneath those sit the agent modules (`chat_nextseek/src/chat_nextseek/agents/__init__.py:15-29`),
the shared helpers and I/O tools (`chat_nextseek/src/chat_nextseek/helpers/__init__.py:10-40`),
the nf-core tool loop (`chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:211-225`),
the Luria launch backend, whose cluster host is hardcoded and whose three
required environment variables are checked together
(`chat_nextseek/src/chat_nextseek/config.py:46-59`), and
18 prompt files plus 15 cached catalog files, counted 2026-09-03 by listing
`chat_nextseek/src/chat_nextseek/prompts/` and
`chat_nextseek/src/chat_nextseek/context/`.

The two monolith modules the package was refactored out of, `agents.py` and
`helpers.py`, are gone: a `find` for files of either name anywhere under
`chat_nextseek/` outside `tests/` returns nothing. Their package `__init__`
files now carry the re-exports instead
(`chat_nextseek/src/chat_nextseek/agents/__init__.py:1-7`).

### 2. Standalone entry points — carried, not run by this repo

`app.py` is a Streamlit UI (`chat_nextseek/app.py:7-10`), `cli.py` an argparse
front end whose provider-profile flag
enumerated nine routing profiles on 2026-09-03 (`chat_nextseek/cli.py:394`), `mcp_server.py` an MCP server exposing
resources, prompts and tools (`chat_nextseek/mcp_server.py:2-8`), and `e2e.py`
a catalog-driven E2E runner (`chat_nextseek/e2e.py:1-13`). Nothing in this
repository executes any of them: a grep for the regex matching
`chat_nextseek/` followed by `app`, `cli`, `e2e` or `mcp_server` and `.py`,
run over the whole worktree and filtered to paths that do not start with
`chat_nextseek/`, returned 14 lines on 2026-09-03, and every one is prose or a
coverage record — `docs/nessie-blocked-capabilities.md:391`,
`docs/testing-review/01-chat_nextseek-e2e-harness-review.md:3`,
`nessie_tests/FAMILIES.json:2779-2780`, `nextseek_api/schema_rag/README.md:170`
and that boundary's own `nextseek_api/schema_rag/CITATIONS.txt:64`. A grep for
`streamlit` over `docker-compose.yml` likewise returns nothing.

The E2E catalog holds 11 task families and 366 variants, of which 4 are tagged
for the Playwright browser tier, counted 2026-09-03 by parsing
`chat_nextseek/e2e/catalog.json:3`.

### 3. Data other build steps read

`chat_nextseek/src/chat_nextseek/context/capabilities.md` is named as the
canonical capabilities document by `build_tools/gen_op_surfaces/constants.py:8-10`,
and the agent image copies it in from a named build context declared at
`docker-compose.yml:124-125` and consumed at `docker/cc-runtime/Dockerfile:54`.
That COPY deliberately lands after the broad plugin copy at
`docker/cc-runtime/Dockerfile:51`, and a generator check enforces that ordering
(`build_tools/gen_op_surfaces/docker_blocks.py:154-159`).

## Running and testing

The package's own suite is `chat_nextseek/tests/`: 83 top-level modules plus 21
under `tests/evaluator/`, counted 2026-09-03. There is no pytest configuration
in `chat_nextseek/pyproject.toml`, so the root project's block applies
(`pyproject.toml:146-147`); `chat_nextseek/conftest.py:1-6` explains the
consequence and puts the directory on `sys.path`
(`chat_nextseek/conftest.py:13-15`).

**Lane actually run, 2026-09-03**: a throwaway container from the stack image
with a writable scratch copy of this directory bind-mounted over
`/app/chat_nextseek`, so the editable install
(`pyproject.toml:136`) resolves to the worktree source:

```
docker run --rm --network none -v <scratch-copy>:/app/chat_nextseek:z \
  -w /app/chat_nextseek -e PYTHONDONTWRITEBYTECODE=1 nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest tests/ --ignore=tests/evaluator -q
```

Result: **849 passed, 4 failed, 2 xfailed, 18 errors in 35.08s**. All 22 non-passing
outcomes are already recorded as known failures in `ci/pytest-baseline.txt:48-73`,
and they fall into three groups:

- The 18 errors all come from two module-scoped fixtures in one test module
  (`chat_nextseek/tests/test_shortlist_recall.py:44-55`) that build a config
  object with no provider key set, which raises at
  `chat_nextseek/src/chat_nextseek/config.py:490-493`.
- Three failures need the gitignored `docker/db.env` and `dmac/local_settings.py`
  that `chat_nextseek/e2e/import_env.py:21-23` walks up to find; both are kept
  out of the image at `.dockerignore:43-45`.
- See `chat_nextseek/CLAUDE.md` for the fourth failure, a stub gone stale.

`tests/evaluator/` aborts collection in that same lane unless two modules are
excluded. The reason older copies of this file gave for skipping it — that the
subdirectory is Django-stack dependent — is false: a case-insensitive grep for
`django` over `chat_nextseek/tests/evaluator/` returns nothing at all. See
`chat_nextseek/CLAUDE.md` for the two real blockers and the flags that get past
them.

The catalog-driven E2E lane and its Playwright tier
(`chat_nextseek/e2e.py:10-13`) are **(not run)** here: they drive real agents
end to end, so they need a seeded running instance, live LLM provider
credentials, and a Chromium install for the browser tier.

The repository-level lane that does include this directory is the GitHub
workflow at `.github/workflows/ci-pytest.yml:62-64`, whose baseline was measured
2026-09-01 (`ci/pytest-baseline.txt:19-21`).

## Depends on / depended on by

Depends on, outside this directory:

- The host's `openssh-client` binaries, which the Luria launch path shells out to as subprocesses (`chat_nextseek/src/chat_nextseek/luria/ssh.py:1` and `chat_nextseek/src/chat_nextseek/luria/ssh.py:36-37`).
- `docker/db.env`, `docker/nextseek.env` and `dmac/local_settings.py`, read by file path from the parent repo at `chat_nextseek/e2e/import_env.py:29-32`.
- `nextseek_api/assistant/granular.py`, loaded by absolute file path from a test in this tree at `chat_nextseek/tests/test_generate_submission_hydration.py:27-28`.
- The NExtSEEK REST API, whose base URL and Basic-auth pair are read from the environment at `chat_nextseek/src/chat_nextseek/config.py:561-563` and then overridden per turn with the caller's own identity at `chat_nextseek/src/chat_nextseek/orchestrator.py:195-199`.
- Neo4j, whose URI defaults to a localhost bolt endpoint at `chat_nextseek/src/chat_nextseek/config.py:600`.

Depended on by. Non-test importers only; the many test modules under
`nextseek_api/` that import this package are omitted. So is the import at
`startup/tests/test_validate.py:67`, which is not an import that file performs:
it is fixture source inside the string literal opened at
`startup/tests/test_validate.py:65`.

- The stack image itself copies this directory in whole (`.dockerignore:1-3`) and installs it editable, so the container imports this source rather than a divergent site-packages copy (`pyproject.toml:134-136`) and the code is baked in rather than mounted (`DEPLOYMENT.md:47-48`).
- `nextseek_api/services/assistant.py:102-103` is the classic assistant ViewSet path, importing both orchestrator entry points and the config class.
- `nextseek_api/services/cc_assistant.py:56` imports the same two entry points for the router-dispatched endpoint, plus the nf-core agent at `nextseek_api/services/cc_assistant.py:75` and a chat-log helper at `nextseek_api/services/cc_assistant.py:77`.
- `nextseek_api/assistant/granular.py:62` and the other lazy call-time imports through `nextseek_api/assistant/granular.py:208` are the granular per-agent ops; they are deferred deliberately, for the reason given at `nextseek_api/assistant/granular.py:3-7`.
- `nextseek_api/cc_assistant/cc_turn_complete.py:11` shares this package's chat-log derivation so the two writers cannot diverge, argued at `nextseek_api/cc_assistant/cc_turn_complete.py:7-10`.
- `nextseek_api/cc_assistant/step7_llm_cost_ledger.py:88` reads provider token usage out of this package's LLM clients.
- `nextseek_api/services/evaluator.py:395` imports the orchestrator inside a function body.
- `startup/dev/lane_local_settings.py:19` constructs the Django-wide config singleton at settings-import time, and `startup/dev/lane_local_settings.py:69` optionally builds a second one for the production toggle.
- `build_tools/gen_op_surfaces/route_capabilities.py:32-34` reads the capabilities document as generator input, not as an import.
- See `chat_nextseek/CLAUDE.md` for what breaks when any of these edges moves.

Not a dependency, despite appearances: `dmac_assistant/` does **not** import this
package. A grep for `chat_nextseek` over every file under `dmac_assistant/`
returned 9 lines on 2026-09-03, all of them comments, README prose, or the path
constant at
`dmac_assistant/src/dmac_assistant/config.py:32-34`, which points at a
`vendor/chat_nextseek/` directory that does not exist in this repo. The
sandboxed agent image does not carry the package either, stated at
`docker/cc-runtime/Dockerfile:102-104`; its plugin reaches these agents over the
network through Django.

See `chat_nextseek/CLAUDE.md` for the invariants, the traps, and the one command.
