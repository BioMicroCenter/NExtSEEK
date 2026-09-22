# `NessieAI/chat_nextseek/`

## What this is

The deterministic NExtSEEK query engine: a multi-agent pipeline that turns a
natural-language question into REST calls against this repo's own API, Cypher
against Neo4j, report exports, and nf-core pipeline launches. Django runs it
in process for the non-sandboxed half of the chat endpoint, described at
`nextseek_api/services/cc_assistant.py:15-21`.

It is edited in place in this repo, like any other code here. It started as a copy of a
separate repository, refreshed wholesale by a sync script; that script is retired
(`NessieAI/history/retired/startup/scripts/sync_chat_nextseek.sh`) and must never be run
again, because its `rsync --delete` would replace this tree with the old one. The outer
project installs the package as an editable local path dependency
(`pyproject.toml:136`), with a commented git+revision form kept for when the package
goes public (`pyproject.toml:140-141`). The dist and import name stay `chat_nextseek`.

The package is a `src/` layout (`NessieAI/chat_nextseek/pyproject.toml:32-33`) whose
importable half is what Django touches; the Streamlit app, the CLI and the MCP server at
the directory root are the standalone half. Its tests live in
`NessieAI/tests/chat_nextseek/`, and its catalog-driven E2E runner in `NessieAI/tests/e2e/`.

## Surface

This boundary has **three different surfaces**, and they are worth separating
because only one of them is an import edge.

### 1. The importable package: the only surface Django uses

`portable.py` is the declared stable API: a fixed list of symbols, each an agent or a
tool with a contract stated in its module docstring
(`NessieAI/chat_nextseek/src/chat_nextseek/portable.py:1-14`), listed at
`NessieAI/chat_nextseek/src/chat_nextseek/portable.py:27-43` and pinned against drift by
`NessieAI/tests/chat_nextseek/test_portable_contract.py:13-26`.

| Entry point | Where |
|---|---|
| Single-turn chat pipeline | `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:596` |
| Planner pipeline | `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:1387` |
| Direct pipeline-launch entry | `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:268` |
| Caller-identity binding for a turn | `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:175-194` |
| Per-agent model, provider and thinking resolution | `NessieAI/chat_nextseek/src/chat_nextseek/config.py:1321-1343` |
| Chat-log helpers shared with Django | `NessieAI/chat_nextseek/src/chat_nextseek/chat_memory.py:201-248` |
| Session state over SQLite or MySQL | `NessieAI/chat_nextseek/src/chat_nextseek/session.py:19-26` |

Beneath those sit the agent modules (`NessieAI/chat_nextseek/src/chat_nextseek/agents/__init__.py:15-29`),
the shared helpers and I/O tools (`NessieAI/chat_nextseek/src/chat_nextseek/helpers/__init__.py:10-40`),
the two tool loops and the one call they share
(`NessieAI/chat_nextseek/src/chat_nextseek/tool_loop.py`: recovery, prompt caching and a
ledger entry per tool call; the nf-core builder at
`NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:211-225` and the follow-up
agent at `NessieAI/chat_nextseek/src/chat_nextseek/agents/followup.py` are its two callers),
the Luria launch backend, whose cluster host is hardcoded and whose three
required environment variables are checked together
(`NessieAI/chat_nextseek/src/chat_nextseek/config.py:46-59`), the prompt files in
`NessieAI/chat_nextseek/src/chat_nextseek/prompts/`, and the cached catalogs in
`NessieAI/chat_nextseek/src/chat_nextseek/context/`.

The two monolith modules the package was refactored out of, `agents.py` and
`helpers.py`, are gone: a `find` for files of either name anywhere under
`NessieAI/chat_nextseek/` returns nothing. Their package `__init__`
files now carry the re-exports instead
(`NessieAI/chat_nextseek/src/chat_nextseek/agents/__init__.py:1-7`).

### 2. Standalone entry points: carried, not run by this repo

`app.py` is a Streamlit UI (`NessieAI/chat_nextseek/app.py:7-10`), `cli.py` an argparse
front end with a provider-profile flag (`NessieAI/chat_nextseek/cli.py:393`), and
`mcp_server.py` an MCP server exposing resources, prompts and tools
(`NessieAI/chat_nextseek/mcp_server.py:2-8`). Nothing in this repository executes any of
them: outside this directory, the only mentions of those files are prose and coverage
records, such as `NessieAI/docs/nessie-blocked-capabilities.md:393`,
`NessieAI/history/docs/testing-review/01-chat_nextseek-e2e-harness-review.md:3`,
`NessieAI/tests/nessie_tests/FAMILIES.json:2779-2780` and `NessieAI/schema_rag/README.md`.
A grep for `streamlit` over `docker-compose.yml` returns nothing.

The catalog-driven E2E runner moved to the test tree: `NessieAI/tests/e2e/__main__.py:1-13`,
run as `python -m NessieAI.tests.e2e`. Its catalog is `NessieAI/tests/e2e/catalog.json`,
whose variants carry a tag for the Playwright browser tier.

### 3. Data other build steps read

`NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md` is named as the
canonical capabilities document by `NessieAI/build_tools/gen_op_surfaces/constants.py:38-46`.
It and five catalogs beside it (`projects_db.json` and four `min_*.json`, the
`CANONICAL_CONTEXT_FILES` list at `NessieAI/build_tools/gen_op_surfaces/constants.py:30-37`)
are the only copies the agent image bakes: it takes them from the named build context
`chat_nextseek`, declared at `docker-compose.yml:159-161` and consumed at
`NessieAI/docker/cc-runtime/Dockerfile:57-62`. Those COPYs deliberately land after the
plugin copy at `NessieAI/docker/cc-runtime/Dockerfile:52`, and a generator check enforces
that ordering (`validate_canonical_context_final_writers` in
`NessieAI/build_tools/gen_op_surfaces/docker_blocks.py`).

`labs_db.json`, which the daily context export writes beside those catalogs, is the
opposite: runtime-only. It holds the labs mined from SEEK institution titles
(`NessieAI/chat_nextseek/src/chat_nextseek/labs.py`), real lab titles included, so it is
gitignored in the context directory, excluded from the app build context by the root
`.dockerignore`, and absent from `CANONICAL_CONTEXT_FILES`: no image bakes it and no commit
carries it. The CC route gets lab resolution through the entity op, which runs in the app.

## Running and testing

The package's suite is `NessieAI/tests/chat_nextseek/`, including `evaluator/`. Its
command is the Django lane in `NessieAI/tests/README.md`. The image installs this
package editable from its own baked copy, so to test uncommitted source either mount a
writable copy of this directory over `/app/NessieAI/chat_nextseek`, or put the checkout's
copy first on the path with the `PYTHONPATH` form that file gives.

There is no pytest configuration in `NessieAI/chat_nextseek/pyproject.toml`, so the root
project's block applies (`pyproject.toml:146-147`); `NessieAI/conftest.py` only stops a
walk of `NessieAI/` from collecting `NessieAI/history/`, `NessieAI/docker/` and
`NessieAI/chat_frontend/`.

The failures that lane shows are recorded in `ci/pytest-baseline.txt`, and they fall into
three groups:

- Errors from two module-scoped fixtures in one test module
  (`NessieAI/tests/chat_nextseek/test_shortlist_recall.py:44-55`) that build a config
  object with no provider key set, which raises at
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:490-493`.
- Failures that need the gitignored `docker/db.env` and `dmac/local_settings.py`,
  which `NessieAI/tests/e2e/import_env.py:23-25` walks up to find; both are kept
  out of the image at `.dockerignore:53-55`.
- One stale stub; see `NessieAI/chat_nextseek/CLAUDE.md` "Landmines".

`evaluator/` aborts collection unless two modules are excluded. It is not
Django-stack dependent: a case-insensitive grep for `django` over
`NessieAI/tests/chat_nextseek/evaluator/` returns nothing. See
`NessieAI/chat_nextseek/CLAUDE.md` for the two real blockers and the flags that get past
them.

The catalog E2E lane and its Playwright tier (`NessieAI/tests/e2e/__main__.py:10-13`)
drive real agents end to end, so they need a seeded running instance, live LLM provider
credentials, and a Chromium install for the browser tier.

CI runs this directory in the no-stack lanes step of `.github/workflows/ci-pytest.yml`,
scored against `ci/pytest-baseline.txt`.

## Depends on / depended on by

Depends on, outside this directory:

- The host's `openssh-client` binaries, which the Luria launch path shells out to as subprocesses (`NessieAI/chat_nextseek/src/chat_nextseek/luria/ssh.py:1` and `NessieAI/chat_nextseek/src/chat_nextseek/luria/ssh.py:36-37`).
- `docker/db.env`, `docker/nextseek.env` and `dmac/local_settings.py`, read by file path from the repo root by the E2E runner's `NessieAI/tests/e2e/import_env.py:31-34`.
- `nextseek_api.assistant.excel_export`, imported lazily and behind a guard by the orchestrator: an allowed back-edge (`NessieAI/CLAUDE.md` "Boundary").
- The NExtSEEK REST API, whose base URL and Basic-auth pair are read from the environment at `NessieAI/chat_nextseek/src/chat_nextseek/config.py:561-563` and then overridden per turn with the caller's own identity at `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:195-199`.
- Neo4j, whose URI defaults to a localhost bolt endpoint at `NessieAI/chat_nextseek/src/chat_nextseek/config.py:600`.
- The SEEK database, read by the daily context export (`_fetch_context_files_from_db` in `NessieAI/chat_nextseek/src/chat_nextseek/config.py`) over the MySQL connection it already holds for the `dmac.*_context` tables. Its first statement is one fixed SELECT over `seek_production.institutions` and `seek_production.work_groups` (`INSTITUTIONS_SQL` in `NessieAI/chat_nextseek/src/chat_nextseek/labs.py`), run inside a read-only transaction, at most once per UTC day per starting process and never per turn. It writes `labs_db.json` and adds each project's labs to the project rows of `projects_db.json`; the config exposes them as `ChatConfig.LABS` and `LABS_STATUS`. `python -m chat_nextseek.labs --report` runs the same read and prints the result without writing anything.

Depended on by. Non-test importers only; the test modules under `NessieAI/tests/` that
import this package are omitted. So is the import at
`startup/tests/test_validate.py:67`, which is not an import that file performs:
it is fixture source inside the string literal opened at
`startup/tests/test_validate.py:65`.

- The stack image itself copies this directory in whole (`.dockerignore:1-3`) and installs it editable, so the container imports this source rather than a divergent site-packages copy (`pyproject.toml:134-136`) and the code is baked in rather than mounted (`DEPLOYMENT.md` §0).
- `NessieAI/ns/turn.py:37-38` imports the config class and the three orchestrator entry points for the classic assistant ViewSet path: the `query` and `query/async` endpoints in `nextseek_api/services/assistant.py` run them on their threads through `NessieAI/ns/turn.py`.
- `NessieAI/cc/turn.py:30` imports the same two entry points for the router-dispatched endpoint's turn, plus a chat-log helper at `NessieAI/cc/turn.py:29`; the nf-core agent is imported by the routing policy at `NessieAI/router/policy.py:21`.
- `NessieAI/ns/granular.py:62` and the other lazy call-time imports through `NessieAI/ns/granular.py:208` are the granular per-agent ops; they are deferred deliberately, for the reason given at `NessieAI/ns/granular.py:3-7`.
- `NessieAI/cc/cc_turn_complete.py:11` shares this package's chat-log derivation so the two writers cannot diverge, argued at `NessieAI/cc/cc_turn_complete.py:7-10`.
- `NessieAI/cc/step7_llm_cost_ledger.py:88` reads provider token usage out of this package's LLM clients.
- `NessieAI/ns/retry.py:378` (`_get_orchestrator`) imports the orchestrator inside a function body, for the evaluator retry endpoint in `nextseek_api/services/evaluator.py`.
- `startup/dev/lane_local_settings.py:19` constructs the Django-wide config singleton at settings-import time, and `startup/dev/lane_local_settings.py:69` optionally builds a second one for the production toggle.
- `NessieAI/build_tools/gen_op_surfaces/route_capabilities.py:35` reads the capabilities document as generator input, not as an import.
- `_project_context_row` in `nextseek_api/services/context_catalog.py` imports `PROJECT_ROW_SQL` from `chat_nextseek.context_rows` inside the function, so the SEEK project page filters `projects_context` by the same project-row rule as the config's maps and never needs this package to import.
- See `NessieAI/chat_nextseek/CLAUDE.md` for what breaks when any of these edges moves.

Not a dependency, despite appearances: `NessieAI/dmac_assistant/` does **not** import this
package. Its mentions of `chat_nextseek` are comments, README prose, and the path constant
at `NessieAI/dmac_assistant/src/dmac_assistant/config.py:32-34`, which points at a
`vendor/chat_nextseek/` directory that does not exist in this repo. The
sandboxed agent image does not carry the package either, stated at
`NessieAI/docker/cc-runtime/Dockerfile:110-112`; its plugin reaches these agents over the
network through Django.

See `NessieAI/chat_nextseek/CLAUDE.md` for the invariants and the traps.
