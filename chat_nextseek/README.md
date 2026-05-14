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
uv run cli.py -s -p                         # Streamlit UI, planner pipeline mode
uv run cli.py -q "Find me mice treated with NDMA."          # Standalone query
uv run cli.py -qp "Find NDMA mice in the GBM study"         # Standalone, planner pipeline
uv run cli.py -m oai -q "Find me mice treated with NDMA."   # Standalone, OpenAI
uv run cli.py -q "Find mice" -prod                          # Standalone, production credentials
uv run cli.py -st                           # Smart test suite (all routing paths)
uv run cli.py -st --both                   # Standard + planner runs with combined HTML report
uv run cli.py -st --only T1,T5,T9         # Subset of smart tests
uv run cli.py -st -ft                     # Full regression test (103 questions)
uv run cli.py -st -i                       # Interactive: pick output dir(s) and regenerate report
```

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

## Test Queries

The full set of example queries used for testing is defined in [`testing.json`](testing.json) and executed by [`smart_test.py`](smart_test.py). The smart test suite covers 13 routing-path diagnostic tests with auto-debug sub-queries, plus a 103-question full regression set.

```bash
uv run smart_test.py --list               # List all test IDs and descriptions
uv run cli.py -st                         # Run the 13 smart tests
uv run cli.py -st -ft                     # Run the full 103-question regression
uv run cli.py -st --both                  # Smart tests: standard + planner, combined report
```

Tests cover: keyword search, sampletype + assay search, UID search, lineage/graph queries, reporter summaries (samples/protocols/published/RPPR), report generation (GEO/SRA/nf-core/PRIDE), follow-up memory queries, system/capabilities questions, and negative controls.

## Architecture

**Standard pipeline** (`-q`):
```
User Query
    |
[Entity Agent]   -- extract sampletypes, assays, keywords, projects
    |
[Parser Agent]   -- route intent, select endpoint, build filters
    |
    |-> [API Agent]      -- construct HTTP request -> NExtSEEK REST API
    |-> [Reporter Agent] -- SQL/Neo4j project reports (samples/protocols/published/RPPR)
    |       +-> [Report Writer Agent] -- GEO / SRA / nf-core / PRIDE submission exports
    |-> [Graph Agent]    -- generate Cypher -> Neo4j graph DB
    |-> [Memory Agent]   -- answer follow-ups from cached results
    |       +-> [Memory Coder] -- structured code generation for deterministic computation
    +-> [System Agent]   -- answer capabilities / catalog entity questions
    |
[Chatter Agent]  -- summarize results for the user
    |
Response
```

**Planner pipeline** (`-qp` / `-p` in Streamlit):
```
User Query -> [Entity Agent] -> [Multi Parser] -> [Planner Agent] -> [Executor Loop per step]
                                (candidates)     (Opus, PlannerOutput)  |-- _run_plan_tool
                                                                        +-- [Context Engineer]
             -> [Plan Chatter] -> [Evaluator] -> (replan on failure)
```

Each agent can be independently routed to a different LLM provider via the catalog defined by `CATALOG_FILE` or `AGENT_MODEL_CATALOG`. The default profile uses GCP Gemini flash-lite for most agents, Anthropic Sonnet for entity/memory, and Anthropic Opus with extended thinking for the parser and report writer.

## nf-core / Seqera integration

When a query asks for an nf-core samplesheet (e.g. *"make me an nf-core samplesheet for NHP-220630FLY-1-PUB"*), the reporter pipeline:

1. **Picks a pipeline** — `pipeline_selector_agent` examines the metadata's library strategy + protocols and partitions samples into one or more cohorts (one nf-core pipeline per cohort). A study with mixed RNA-seq + amplicon data yields two cohorts.
2. **Resolves accessions** — extracts SRR/SRX/ERR/PRJ accessions from NExtSEEK metadata and resolves each to ENA HTTPS FASTQ URLs (no FASTQ download — Nextflow stages HTTPS URLs directly).
3. **Emits artifacts** — `samplesheet.csv` (per cohort, with biology enrichment columns pulled from lineage parents), `params.yml`, and a top-level `launch.yml` aggregating all cohort runs.
4. **Optional auto-launch** — when `SEQERA_AUTO_LAUNCH=true` and the Tower env is configured, submits each cohort to Tower's REST API directly. No external `tw` binary required.
5. **Cross-cluster compatible** — when chat_nextseek runs on a host without a shared filesystem mount to the Tower compute env, the samplesheet is uploaded to a Tower-managed Dataset (Tower Datasets v2 API) and referenced by URL in `params.yml`. The same `TOWER_ACCESS_TOKEN` covers both dataset upload and workflow launch.

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
