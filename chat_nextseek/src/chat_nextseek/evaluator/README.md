# Native NExtSEEK Evaluator

Runs evaluator judgments and retry decisions against orchestrator outputs,
produces resumable JSON batch reports, and renders an offline HTML dashboard.

## Quickstart (5 minutes, zero prior setup)

```bash
# 1. Clone and install
git clone <repo> && cd chat_nextseek
uv sync

# 2. Put your NExtSEEK + LLM credentials into .env (see root README.md).

# 3. Smoke run — evaluator auto-regenerates src/baml_client on first call.
uv run python -m chat_nextseek.evaluator \
    --eval-batch testing.json --eval-batch-limit 5

# 4. Open the dashboard
open outputs/evaluator/eval-*.html
```

## What you'll see

After step 3, `outputs/evaluator/` contains `eval-<runid>-<timestamp>.json`
and a matching HTML dashboard. Every query lands in exactly one bucket:
`queries_passed`, `queries_retried`, `queries_failed`, `queries_unsupported`,
`queries_with_errors`, `queries_exceptions`, or `infra_failures`
(see [docs/operations.md](docs/operations.md#failure-buckets)).

## Next reads

- [docs/architecture.md](docs/architecture.md) — how the pieces fit together
- [docs/operations.md](docs/operations.md) — env vars, BAML regeneration, troubleshooting
- [docs/cli.md](docs/cli.md) — full flag reference
- [docs/runbook.md](docs/runbook.md) — reproducing the 103-question batch end-to-end
