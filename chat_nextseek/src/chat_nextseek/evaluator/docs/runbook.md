# Runbook — 103-question regression batch

## Purpose

Reproduce the full 103-question evaluation that baselines the evaluator's
behavior across the routing surface.

## Prerequisites

- Clone installed via `uv sync`.
- `.env` populated with NExtSEEK creds and at least one LLM provider.
- Roughly 20 minutes of wall-clock time on a stable connection.
- **Expected cost** (as of 2026-04-23): about **$5–$15 USD** per full batch,
  depending on model profile and retry rate. This estimate will drift as provider
  pricing changes.

## Step-by-step

### 1. Verify credentials

```bash
uv run python -c "import chat_nextseek.config as c; c.ChatConfig()"
```

Any error here is an `infra_failures` candidate; fix it before proceeding.

### 2. Cold bootstrap

On a fresh clone, the first evaluator CLI call should auto-regenerate
`src/baml_client/` from `baml_src/`. If you need to force that path manually,
use the command in [operations.md](operations.md#baml-regeneration).

### 3. Run the batch (async, resumable)

```bash
uv run python -m chat_nextseek.evaluator \
    --eval-batch testing.json \
    --eval-batch-async \
    --eval-batch-concurrency 5 \
    --eval-batch-out outputs/evaluator/103-batch
```

### 4. If it gets interrupted, resume

```bash
uv run python -m chat_nextseek.evaluator \
    --eval-batch-resume outputs/evaluator/103-batch/batch-<runid>.json \
    --eval-batch-async
```

### 5. Inspect the artifacts

```bash
open outputs/evaluator/103-batch/eval-*.html
```

## Reading the report

Look at `run_status` first:

- `completed` -> every query was `PASS` or `RETRY->PASS`
- `completed_with_failures` -> at least one of `failed`, `unsupported`,
  `with_errors`, or `infra_failures` is non-zero
- `crashed` -> an uncaught exception reached the runner; inspect
  `queries_exceptions` rows first

See [operations.md#failure-buckets](operations.md#failure-buckets) for bucket semantics.

## Known shape

The 2026-04-23 5-query smoke checkpoint completed with all five queries in
`queries_passed` and zero failures. The full 103-question post-H2 rerun is still
deferred pending budget, so expect historical reports to reflect pre-fix bucket
semantics until a new full run is recorded.
