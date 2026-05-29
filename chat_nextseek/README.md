# NExtSEEK Chat Assistant

A conversational AI interface for querying the NExtSEEK biological sample database. Turns natural language into API calls and graph queries using a multi-agent architecture with flexible per-agent model routing.

## Quick Start

```bash
# Clone and install
git clone <repository-url>
cd chat_nextseek
cp .env.example .env   # Edit with your credentials
uv sync                # Or: pip install .

# Run the chat interface (default: GCP flash-lite + Anthropic Opus for parser/report_writer)
uv run cli.py -s

# Specific provider profiles
uv run cli.py -s -m gcp       # Pure GCP (flash-lite + pro-preview for parser/report_writer)
uv run cli.py -s -m anth      # Anthropic via AWS Bedrock (Sonnet 4.6 + Opus 4.6)
uv run cli.py -s -m aws:opus  # Claude Opus 4.6 for all agents

# Or run a standalone query
uv run cli.py -q "Find me mice treated with NDMA"
```

Open http://localhost:8501 in your browser.

## Configuration

Create a `.env` file with:

```bash
# NExtSEEK API (required)
NEXTSEEK_BASE_URL=https://nextseek-dev.mit.edu
API_USER=your_username
API_PASS=your_password
# Agent model catalog -- choose one:
CATALOG_FILE=agent_model_catalog.json   # path to catalog JSON file
# or inline as JSON string:
# AGENT_MODEL_CATALOG='{"default": {...}, "gcp": {...}}'

# At least one LLM provider (required)
GCP_API_KEY=...
# and/or AWS credentials for Bedrock
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_BEARER_TOKEN_BEDROCK=...
# and/or
OPENAI_API_KEY=sk-...
```

Per-agent model routing is configured via either `CATALOG_FILE` (path to `agent_model_catalog.json`) or `AGENT_MODEL_CATALOG` (the catalog JSON inlined as a string). `AGENT_MODEL_CATALOG` takes precedence if both are set. See [CLAUDE.md](CLAUDE.md) for full configuration options.

## Provider Profiles

| Flag | Profile | Parser / Report Writer | Everything Else |
|------|---------|------------------------|-----------------|
| *(none)* | `default` (mixed) | Anthropic Opus 4.6 + extended thinking | GCP Gemini flash-lite |
| `-m gcp` | `gcp:current` | GCP Gemini pro-preview + thinking | GCP Gemini flash-lite |
| `-m gcp:lite` | `gcp:lite` | GCP Gemini 2.5-pro + thinking | GCP Gemini 2.5-flash |
| `-m anth` | `anth:current` | Anthropic Opus 4.6 + thinking | Anthropic Sonnet 4.6 |
| `-m anth:lite` | `anth:lite` | Anthropic Opus 4.5 + thinking | Anthropic Sonnet 4.5 |
| `-m aws:son` | `aws` | Claude Sonnet 4.6 | Claude Sonnet 4.6 |
| `-m aws:opus` | `aws` | Claude Opus 4.6 | Claude Opus 4.6 |
| `-m aws:ds` | `aws` | DeepSeek V3.2 | DeepSeek V3.2 |
| `-m aws:qwen-nxt` | `aws` | Qwen3 80B | Qwen3 80B |
| `-m aws:glm` | `aws` | GLM-4.7 | GLM-4.7 |
| `-m oai` | -- | GPT (LLM_MODEL) | GPT (LLM_MODEL) |

## Usage

### CLI Reference

```
uv run cli.py -s                            # Streamlit UI, default mixed profile
uv run cli.py -s -m gcp                     # Streamlit UI, pure GCP
uv run cli.py -s -m anth                    # Streamlit UI, Anthropic Bedrock
uv run cli.py -q "Find me mice treated with NDMA."          # Standalone query
uv run cli.py -m oai -q "Find me mice treated with NDMA."   # Standalone, OpenAI
uv run cli.py -q "Find mice" -prod                          # Standalone, production credentials
uv run cli.py -st                           # E2E test suite (default ratio 0.33; routes via e2e.py)
uv run cli.py -ft                           # E2E full run (all variants; ratio=1.0)
```

For advanced E2E flags (`--seed`, `--family`, `--variant`, `--rerun`, `--list`, etc.) use `e2e.py` directly — see the [Testing](#testing) section.

### Python Package

```python
from chat_nextseek.orchestrator import run_query
from chat_nextseek.session import SQLiteSessionState
from chat_nextseek.config import ChatConfig

config = ChatConfig({})
session = SQLiteSessionState(config.SESSION_DB_PATH, "user-id")
session["results_history"] = []

result = run_query(session, config, "Find me mice treated with NDMA")
print(result["reply"])

# Follow-ups use the same session
result = run_query(session, config, "Which are from 2024?")
```

## Testing

Two complementary surfaces:

### Unit tests — `pytest tests/`

Module-level tests covering pydantic schemas, catalog matching, lineage extraction, pipeline-agent step-functions, etc. Fast (~0.5s). The `tests/evaluator/` subdir is excluded by default (Django-stack dependent):

```bash
uv run --with pytest --with pytest-asyncio pytest tests/ --ignore=tests/evaluator -q
```

### E2E tests — `e2e.py`

End-to-end variants exercising every active agent via `chat_nextseek.orchestrator.run_query`. Catalog at [`e2e/catalog.json`](e2e/catalog.json): **362 variants across 11 task families** (each one assays / NExtSEEK-endpoint combination):

| Family | Variants | What it covers |
|---|---|---|
| `search_advanced` | 66 | `/samples/advanced_search/` — keyword + sampletype + attribute filters |
| `search_tree` | 15 | `/sample-tree/{uid}/tree/` — lineage / derivation |
| `search_parents_by_child` | 18 | `/sample_types/get_parents/parents_by_child_types/` — find parents by descendant assay |
| `search_retrieve` | 19 | `/admin/samples/retrieve/` and `/samples/{uid}/` — UID lookup |
| `refine_and_recall` | 47 | Multi-turn refine + ask-about-last-results (uses `chat_log` + `results_history`) |
| `graph_query` | 59 | Neo4j Cypher via graph_agent — counts, multi-hop, project/study scope |
| `reporting` | 64 | Reporter SQL summaries + GEO / SRA / PRIDE artifact emission |
| `pipeline_nfcore` | 21 | full-agentic nf-core flow: resolve_samples → write_samplesheet (→ submit_to_tower) |
| `system_question` | 27 | Catalog / capability / definition lookups |
| `unsupported` | 7 | Out-of-scope (weather, charts, statistical analysis) |
| `writes_unsupported` | 19 | Destructive admin (create investigation, register sample, update field) — must route to unsupported |

```bash
uv run e2e.py                                    # default: sample at ratio 0.33 (~120 variants)
uv run e2e.py --ratio full                       # all 362 variants
uv run e2e.py --ratio 0.1 --seed 42              # ~11 variants (one per family, reproducible)
uv run e2e.py --family pipeline_nfcore           # one family only
uv run e2e.py --variant advanced.basic_ndma      # one variant (the cheapest smoke)
uv run e2e.py --list                             # enumerate every variant
uv run e2e.py --rerun outputs/e2e_<ts>/manifest.json [--failed-only]
uv run e2e.py --report outputs/e2e_<ts>/         # regenerate HTML from a prior run dir
uv run e2e.py --init-env [--force]               # generate chat_nextseek/.env from sibling NExtSEEK docker + dmac sources
```

`cli.py -st` and `cli.py -ft` are thin shims over `e2e.py`. Every run writes:

- `outputs/e2e_<ts>/manifest.json` — sampled variants + pass/fail + seed + ratio
- `outputs/e2e_<ts>/report.html` — single-page family-grouped report
- `outputs/e2e_<ts>/<variant_id>/turns/<label>/` — per-turn query, reply, debug.json, orchestrator run-root

Sampler enforces `max(1, round(N × ratio))` per family so every family gets at least one variant per run (variants whose `requires_env` aren't satisfied are reported as `skipped`, e.g. the `pipeline_nfcore` Tower-submit variant needs `TOWER_ACCESS_TOKEN`).

### Browser E2E (Phase E)

A subset of catalog variants are tagged for browser-driven testing via Playwright,
in addition to the in-process CLI run. The browser tier runs against the
docker UI at `localhost:8000` and asserts the full UI ↔ console.txt ↔ MySQL
chat_log trio for memory variants.

**Run modes:**
```bash
uv run e2e.py                          # CLI tier + browser tier on tagged variants
uv run e2e.py --no-playwright          # CLI only
uv run e2e.py --playwright             # browser tier only (no CLI)
uv run e2e.py --playwright --spot advanced.basic_ndma   # one browser variant
uv run e2e.py --playwright --headed    # open Chromium with a visible window
uv run e2e.py --playwright --video     # record video.webm per variant
```

**Prerequisites:**
- Docker container running (`docker compose up nextseek`)
- Container image rebuilt after the frontend testid commit (`docker compose build nextseek`) — required so the served `chat_frontend` dist carries the `data-testid` selectors that `e2e/playwright/pages.py` relies on
- `chat_nextseek/.env` populated (`uv run e2e.py --init-env`)
- Chromium installed for Playwright (`uv run playwright install chromium` — one-time)

**Output:** Each browser run produces `outputs/e2e_<ts>/playwright/<vid>/`
containing `trace.zip` (Playwright trace, openable via `npx playwright show-trace`),
`screenshot.png`, `ws_frames.jsonl` (every WebSocket frame received),
`ui_text.json`, `mysql_chat_log.json`, and `trio_diff.txt` (only on trio_match failure).

**Tagging more variants:** Add `"playwright"` to a variant's `tags` array in
`e2e/catalog.json`. The runner picks it up on the next run with no other
config changes. See `docs/superpowers/specs/2026-05-21-e2e-playwright-design.md`
for the full design.

## Architecture

**Pipeline** (`-q`):
```
User Query
    |
[Entity Agent]   -- extract sampletypes, assays, keywords, projects
    |
[Parser Agent]   -- route intent, select endpoint, build filters
    |
    |-> [API Agent]      -- construct HTTP request -> NExtSEEK REST API
    |-> [Reporter Agent] -- SQL/Neo4j project reports (samples/protocols/published/RPPR)
    |       +-> [Report Writer Agent] -- GEO / SRA / PRIDE submission exports
    |-> [Graph Agent]    -- generate Cypher -> Neo4j graph DB
    |-> [Memory Agent]   -- answer follow-ups from cached results
    |       +-> [Memory Coder] -- structured code generation for deterministic computation
    |-> [Pipeline Agent] -- full-agentic nf-core / Seqera Tower flow (one tool-loop: resolve → samplesheet → submit)
    +-> [System Agent]   -- answer capabilities / catalog entity questions
    |
[Chatter Agent]  -- summarize results for the user
    |
Response
```

Each agent can be independently routed to a different LLM provider via the catalog defined by `CATALOG_FILE` or `AGENT_MODEL_CATALOG`. The default profile uses GCP Gemini flash-lite for most agents, Anthropic Sonnet for entity/memory, and Anthropic Opus with extended thinking for the parser and report writer.

### Package layout (`src/chat_nextseek/`)

```
agents/              entity, parser, api, reporter, chatter, memory, system, graph, seqera, planner/
helpers/             generic utilities (dates, lineage, lab_code, results, text, json_io) + tools/ (nextseek_api, neo4j, catalog_match, memory_code)
pipeline/            full-agentic nf-core agent: agent.py (tool loop) + agent_tools.py (resolve_samples, write_samplesheet, submit_to_tower, conclude)
reports/             runners, metadata, protocols, nfcore, outputs, templates_meta + exporters/ + templates/
schemas/             Pydantic models
prompts/             *.txt prompt files
seqera/              Tower client + Datasets v2 + ENA + samplesheet emitter
context/             cached catalogs (API spec, sampletypes, assays, Neo4j)
evaluator/           BAML eval harness + dashboard
portable.py          stable public surface for external consumers (dmac_assistant plugin)
orchestrator.py      multi-agent dispatcher
config.py            ChatConfig: env + catalog loading
session.py           SQLite + MySQL session state
```

`agents.py` and `helpers.py` were previously monolith modules and now exist only as their respective folder packages. External consumers should import via `chat_nextseek.portable`; legacy `chat_nextseek.agents.<name>` / `chat_nextseek.helpers.<name>` import paths remain pinned by `tests/test_portable_contract.py`.

## nf-core / Seqera integration

When a query asks for an nf-core samplesheet (e.g. *"make me an nf-core samplesheet for NHP-220630FLY-1-PUB"*), the **pipeline agent** runs a single full-agentic tool loop (`pipeline/agent.py`, one Bedrock conversation per session). The LLM picks the pipeline, judges data-type fit, and groups samples into cohorts; four tools do the deterministic I/O:

1. **`resolve_samples`** — resolves UIDs to their leaf accessions (SRR/SRX/ERR/PRJ) and on to ENA HTTPS FASTQ URLs (no FASTQ download — Nextflow stages HTTPS URLs directly), and surfaces per-leaf grouping fields the LLM uses to form cohorts.
2. **`write_samplesheet`** — emits **one** `samplesheet.csv` with a `cohort` column (not per-cohort files), plus biology enrichment columns pulled from lineage parents, `params.yml`, and a `launch.yml`. It rejects any accession the agent didn't first resolve (a guardrail against hallucinated refs).
3. **`submit_to_tower`** — when the Tower env is configured, submits to Tower's REST API directly (no external `tw` binary). On a host without a shared filesystem mount to the compute env, the samplesheet is uploaded to a Tower-managed Dataset (Tower Datasets v2 API) and referenced by URL. The same `TOWER_ACCESS_TOKEN` covers both dataset upload and workflow launch.
4. **`conclude`** — ends the loop with the final reply.

Supported pipelines: `rnaseq, scrnaseq, atacseq, chipseq, sarek, methylseq, ampliseq, fetchngs`.

Required env vars to enable launch.yml emission:

```bash
TOWER_ACCESS_TOKEN=...
TOWER_WORKSPACE_ID=org/workspace       # qualified name or numeric ID
SEQERA_COMPUTE_ENV=...                  # existing compute env name in Tower
SEQERA_WORK_BUCKET=...                  # work-dir + outdir prefix (HPC path or s3:// URI)
SEQERA_AUTO_LAUNCH=false                # true to submit inline via Tower REST API
SEQERA_DEFAULT_PROFILE=docker           # e.g. singularity for HPC
SEQERA_INPUT_DIR=...                    # optional shared mount; if missing, falls back to Tower Datasets upload
TOWER_API_ENDPOINT=...                  # optional; default https://api.cloud.seqera.io
```

## Logging

Each run creates a timestamped directory under `NEXTSEEK_OUTPUTS_DIR` (standalone) or `LOG_DIR` (Streamlit) containing `console.txt`, `prompts.json`, `chat.txt`, and API logs. Use the Debug panel in the Streamlit sidebar to inspect per-agent outputs inline.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Wrong provider at startup | Check `NEXTSEEK_MODE` in `.env` -- remove it to use the default mixed profile |
| Connection errors | Verify `NEXTSEEK_BASE_URL` and credentials |
| Empty results | Check entity extraction in Debug panel; try broader terms |
| Stale context catalogs | Delete JSON files in `src/chat_nextseek/context/` to force a refresh |
| 5xx / service unavailable | Provider fallback is automatic; check `console.txt` for details |

## Development

See [CLAUDE.md](CLAUDE.md) for architecture details, coding patterns, adding agents, and contribution guidelines.

```bash
uv add package-name              # Add dependencies
uv run -- python your_script.py  # Run scripts
nix develop                      # Reproducible dev shell (uv + Python 3.14)
```

## License

See [LICENSE](LICENSE) for details.
