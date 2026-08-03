# Project Instructions — chat_nextseek

Conversational multi-agent AI over the NExtSEEK biological sample database. Python 3.14, `src/` layout, package name `chat_nextseek`. Three surfaces: Streamlit UI (`app.py`), CLI (`cli.py`), and MCP server (`mcp_server.py`).

## Tech Stack
- **Python 3.14**, packaged with `uv` (lock: `uv.lock`). Nix dev shell available (`nix develop`).
- **LLM providers**: Anthropic, GCP Gemini, OpenAI, AWS Bedrock — routed per-agent.
- **Data**: NExtSEEK REST API (`requests`/`aiohttp`), MySQL (`mysql-connector-python`), Neo4j (`neo4j`).
- **Schemas**: Pydantic 2 in `src/chat_nextseek/schemas/`.
- **Eval**: BAML (`baml-py`) under `src/chat_nextseek/evaluator/`.
- **Pipelines**: nf-core. Launch target is MIT's **Luria** SLURM cluster (ssh + sbatch) in `src/chat_nextseek/luria/`; samplesheet/params/launch emission and the curated param + reference bundles stay in `src/chat_nextseek/seqera/`. The Seqera Tower client there is **retired but dormant** (see Code Style & Conventions).

## Build & Run
- **Install**: `uv sync` (or `nix develop` for a reproducible shell).
- **Streamlit UI**: `uv run cli.py -s` (default mixed profile). Add `-m gcp|anth|aws:opus|...` to switch profile.
- **Standalone query**: `uv run cli.py -q "Find me mice treated with NDMA"`.
- **MCP server**: `uv run mcp_server.py`.
- **Add dep**: `uv add <pkg>` — do not edit `pyproject.toml` deps by hand.

## Testing
- **Unit tests**: `uv run pytest tests/` — files match `tests/test_*.py`. The `tests/evaluator/` subdir is Django-stack dependent; exclude it by default: `uv run pytest tests/ --ignore=tests/evaluator -q`.
- **E2E tests**: `uv run e2e.py` (catalog `e2e/catalog.json`, 11 task families; default samples at ratio 0.33). The variant count drifts as cases are added, so do not quote a frozen number: `uv run e2e.py --list` prints every family with its live count. `uv run e2e.py --ratio full` runs all variants; `--family <name>` / `--variant <id>` scope it. See chat_nextseek/README.md → Testing for the full flag set.
- `cli.py -st` (sample) and `cli.py -ft` (full) are thin shims over `e2e.py`. The old `smart_test.py` / `test.py` / `testing.json` harness was retired.

## Project Structure
```
cli.py / app.py / mcp_server.py      CLI, Streamlit UI, MCP server entry points
e2e.py                               E2E runner (cli.py -st/-ft shim over it)
agent_model_catalog.json             Per-agent model/provider/thinking routing
e2e/catalog.json                     E2E variant catalog (11 families; `e2e.py --list` for counts)

src/chat_nextseek/
  orchestrator.py                    Multi-agent dispatcher (standard + planner pipelines)
  agents/                            Agent implementations — one module per agent
                                     (entity, parser, api, reporter, chatter, memory,
                                     system, graph, seqera) + planner/ subpackage
  pipeline/                          Full-agentic nf-core agent: agent.py (one Bedrock
                                     tool loop) + agent_tools.py. Tools exposed to the
                                     model: resolve_samples, write_samplesheet,
                                     configure_run, submit_to_luria (only when
                                     config.LURIA_ENV_COMPLETE), conclude, handoff.
                                     configure_run builds params.yml + launch.yml
                                     (curated params + species references)
  helpers/                           Shared utilities (dates, lineage, lab_code, results,
                                     text, json_io) + tools/ (nextseek_api, neo4j,
                                     catalog_match, memory_code)
  config.py                          ChatConfig: env + catalog loading
  llm_clients.py                     GeminiClient / BedrockClient / OpenAIClient
  session.py / chat_memory.py        SQLite session + memory store
  artifacts.py / tee.py              Output writers
  portable.py                        Stable public surface for external consumers
  schemas/                           Pydantic models (chat, entity, graph, memory,
                                     planner, router, tools, system)
  prompts/                           *.txt prompts loaded at runtime
  context/                           Cached catalogs (API spec, sampletypes, assays, Neo4j)
  luria/                             Luria SLURM launch backend: ssh.py, submitter.py,
                                     run_script.py, fetchngs_helpers.py + templates/
  seqera/                            Samplesheet/params/launch emitter, ENA, curated
                                     params + reference bundles. Also the dormant
                                     Tower client + Datasets v2 (retired, see below)
  reports/                           Report templates incl. nf-core
  evaluator/                         BAML eval harness + dashboard

tests/                               pytest suite
tests/evaluator/                     Evaluator subsystem tests
```

## Code Style & Conventions
- **Naming**: `snake_case` for files, functions, variables. PascalCase for Pydantic models.
- **Prompts live in `prompts/*.txt`** — edit prompt text there, not in the agent module. New agent → new `prompts/<name>.txt` + loader call.
- **Schemas live in `schemas/`** — re-export new models from `schemas/__init__.py` so callers import from the package.
- **Agent names are stable string keys** (`entity`, `parser`, `api`, `reporter`, `report_writer`, `report_coder`, `chatter`, `memory`, `memory_coder`, `graph`, `system`, `multi_parser`, `planner`, `context_engineer`, `evaluator`, `seqera_agent`, `pipeline_agent`). **Adding an agent requires registering it in every profile of `agent_model_catalog.json`.**
- **Seqera Tower is retired, not deleted.** `build_pipeline_tool_schemas` (`pipeline/agent_tools.py`) never appends `submit_to_tower`, so the model cannot call it; Luria is the only exposed launch target. `tool_submit_to_tower`, its schema, `seqera/tower_client.py`, `seqera/tower_datasets.py` and `emitter.emit_launch_artifacts` are all left intact and dormant for a future re-enable. Do not "clean them up" as dead code.
- **`handoff` is always exposed** and is a terminal tool like `conclude`. When a pipeline build is open and the user asks about something else (a sample search, a lineage question, a report), the agent calls `handoff`; `pipeline/agent.py` clears the build state and returns `action="passthrough"` so the orchestrator's normal parser takes the turn. Without it an open build could trap the conversation.
- **Context catalogs are cached JSON in `src/chat_nextseek/context/`** — delete a file to force a refresh from the live source.
- **Logs/artifacts** land under `LOG_DIR` (Streamlit) or `NEXTSEEK_OUTPUTS_DIR` (CLI) per run. `outputs/` is gitignored.
- **Don't commit**: `.env`, `*.sqlite`, `.mcp.json`, `AGENTS.md` (all gitignored).

## Commit & PR Workflow
- **Conventional commits** with module scopes: `feat(pipeline_agent): …`, `fix(orchestrator): …`, `test(smart): …`, `docs(pipeline_agent): …`.
- **Branches**: `feat/<topic>` (latest: `feat/pipeline-params-resources`, merged to `dev`).
- Run `uv run pytest tests/ --ignore=tests/evaluator` before opening a PR. For changes touching routing or agent behaviour, also run `uv run e2e.py` (or `cli.py -st`).

## When Modifying Common Areas
- **Routing change** → `prompts/parser_core_routing.txt` + parser logic in `agents/parser.py` + `schemas/router.py` + add a variant in `e2e/catalog.json`.
- **New agent** → implement as `agents/<name>.py`, add prompt in `prompts/`, register in `agent_model_catalog.json` (every profile), wire dispatch in `orchestrator.py`.
- **Pipeline / launch change** → `pipeline/agent.py`, `pipeline/agent_tools.py`, `luria/*.py` (submit path), `seqera/*.py` (incl. `seqera/pipeline_params.py` and `seqera/emitter.py::emit_luria_launch_artifacts`), `prompts/pipeline_agent.txt`. Curated run params + species→reference bundles live in `reports/templates/nfcore/<key>.json` + `reports/templates/nfcore/reference_bundles.json`. Launch env: `LURIA_USER`, `LURIAKEY`, `LURIA_WORKING_PATH` (host is hardcoded `luria.mit.edu`); `PIPELINE_LAUNCH_MODE` defaults to `luria`.
- **Touching `helpers/` or `agents/`** → these are packages with one module per concern; keep edits scoped to the relevant module.

## Logs (debugging failures)

Three log surfaces when something fails in the full Docker stack. Send the
paste from one of these and a query-string or timestamp; I can take it from there.

### 1. `docker logs nextseek` — container stdout/stderr (Django + gunicorn)
The first place to look for crashes (unhandled exceptions, ImportError, gunicorn
worker death). Useful invocations:
```bash
docker logs nextseek 2>&1 | tail -100        # recent
docker logs nextseek 2>&1 | grep -i error    # filter
docker logs -f nextseek                       # follow live
```

### 2. `logs/` — host bind-mounted, persistent across container restarts
At the repo root (parent of `chat_nextseek/`), mounted into the container at
`/app/logs:z`:
- `logs/django.log` — Django request/response cycle, app-level errors (largest, most signal)
- `logs/nextseek.log` — nextseek-specific logger
- `logs/seek.log` — SEEK integration calls
- `logs/django_crontab.log` — scheduled job output

Files are root-owned (container writes them as root). `sudo tail -n 200 logs/django.log`
if you hit permission issues.

### 3. `outputs/<timestamp>_<user>/` — per-run agent traces
Also at the repo root, mounted at `/app/outputs:z`. **One directory per chat turn**,
named `YYMMDD_HHMMSS_<user>`. Each contains:
- `console.txt` — full stdout of that orchestrator run (entity → parser → API → chatter `[DEBUG]` prints)
- `files/` — artifacts the run produced (reports, samplesheets, exports)
- Depending on mode: `prompts.json`, `chat.txt`, API call logs

Best for "wrong answer" or "weird agent behavior on this specific query." Most
recent is `ls -dt outputs/*/ | head -1`.

### What to send when something fails
Fastest debug path — paste any one of:
- `docker logs nextseek 2>&1 | tail -80` (crashes / unhandled errors)
- The path `outputs/<timestamp>_<user>/console.txt` (wrong agent behavior)
- A line range from `logs/django.log` (slow/silent failures)

Or just the query string that triggered it — grep `outputs/*/console.txt` finds the run.

## Portability Contract (read before adding an agent)

`chat_nextseek` is also consumed as a library by external tools — currently
`dmac_assistant`'s nextseek plugin, which exposes a curated subset of agents
as containerized Claude Code tools. The contract every plugin-facing agent
must satisfy:

- Signature: `f(config: ChatConfig, [session,] *, ...) -> PydanticModel`
- All I/O via `config` (paths/credentials) or `helpers/tools/*` (REST, Neo4j, file writes)
- `session` is read-only — never write state another agent will read in the same turn
- No `input()`, no streaming, no interactive loops
- Register the agent's catalog key in every profile of `agent_model_catalog.json`
- If the agent should be plugin-portable, add it to `portable.py.__all__`
  and update `tests/test_portable_contract.py`

External consumers should `from chat_nextseek.portable import <name>`.
The shim modules `chat_nextseek.agents` and `chat_nextseek.helpers` will
continue to expose plugin-facing symbols for backward compatibility — these
forwarders are permanent public API and must not be removed.
