# `NessieAI/tests/chat_nextseek/`

## What this is

The tests for the NS engine in `NessieAI/chat_nextseek/` (`NessieAI/chat_nextseek/README.md`): agents,
prompts, call budgets and failover, query scope, the graph reviewer, nf-core pipeline selection, and
the e2e tool's models. Most are free Django-lane tests; nothing here calls a paid model.

## Layout

| Path | What it holds |
|---|---|
| `test_*.py` | the engine tests, one file per behaviour |
| `evaluator/` | tests of the offline evaluator and its CLI, dashboard and demo server; has its own `conftest.py` |
| `graph_scope/` | the graph scope lane: tests that run against a throwaway Neo4j, driven by `lane.sh` (see its header for the arguments) |
| `fixtures/` | recorded inputs: `query_scope_replay.json`, `graph_review_replay.json` and `nfcore/`, copies of nf-core pipeline docs and schemas that tests compare against; never edit the `nfcore/` copies |
| `e2e/playwright/fixtures/` | `normalize_cases.json`, input for the browser spot tests |

## Running

The Django lane and the graph scope lane are blocks in `NessieAI/tests/README.md`. For example, in
the app image:

```bash
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src/NessieAI/chat_nextseek/src \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest NessieAI/tests/chat_nextseek -q -p no:cacheprovider
```

The `graph_scope/` tests skip unless `GRAPH_SCOPE_NEO4J_URI` is set; use `lane.sh` for them.
