# Real-stack acceptance evidence — native assistant granular ops

**Date:** 2026-06-12
**Test:** `nextseek_api/assistant/tests/test_granular_realstack.py` (gated by `RUN_REALSTACK=1`)

## Provenance note (read this)

An earlier in-session run of this acceptance produced its ledger + logs only in
the **gitignored** `outputs/` scratch dir, which was then deleted during cleanup —
so that run was not independently verifiable. This directory is the **committed
re-run** that fixes that: the files here (`realstack_run.txt`,
`realstack_ledger.jsonl`) are the captured output of the command below, run
against the live local stack. The **strongest** verification is to re-run the
committed test yourself (command below) — these files corroborate, the test
reproduces.

## How it was run (reproducible)

```bash
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<local-seek-user> -e SEEK_TEST_PASS=<pass> \
  nextseek sh -lc 'cd /app && uv run python manage.py test \
  nextseek_api.assistant.tests.test_granular_realstack \
  --settings=dmac.test_settings_realstack --noinput --keepdb -v2'
```

Runs against **real** chat_nextseek + the local Docker MySQL/Neo4j/REST + the
real (paid) LLM. `SEEK_TEST_USER/PASS` = the dev `username`/`password` from
`BMC/.env` (valid on the local stack). Result: **`Ran 8 tests ... OK`** (8/8) —
see `realstack_run.txt`.

## The 8 assertions (each validates the live response against the `models_api` model)

1. `entity("mouse samples treated with NDMA")` → sampletype **MUS**
2. `graph("lineage of mouse samples")` → real Neo4j node rows
3. `api-read(plan)` → **≥1 real sample row** from the DB
4. `api-write confirmed=false` → **WRITE_BLOCKED + `samples` row count unchanged** before/after
5. `api-write confirmed=true` vs `advanced_search` (read-safe) → executes
6. `report(published)` → `saved_files` with DB-sourced bytes (not a stub)
7. `parse(...)` → `target_endpoint = /nextseek_api/samples/advanced_search/`
8. `generate-submission(GEO, real published UID)` → non-null GEO output

## Strict cost accounting

`realstack_ledger.jsonl` holds the **machine-captured** per-call provider token
usage (the test wraps each LLM client's `chat()` and records the actual
`response.usage`). Cost = `actual_tokens × published rate`:

- **Gemini 3.5 Flash:** $1.50 / $9.00 per 1M tok (in/out) — <https://ai.google.dev/gemini-api/docs/pricing>
- **Claude Opus 4.7 (Bedrock, `us.` cross-region):** $5/$25 base + 10% cross-region = **$5.50 / $27.50** per 1M tok — <https://builder.aws.com/content/3Cl90CMMnqzCrkk6mXcmnGo1WTG/claude-opus-47-on-amazon-bedrock-apis-features-and-migration-guide>

| model | in tok | out tok | cost |
|---|---|---|---|
| `gemini-3.5-flash` | 52021 | 89 | $0.078832 |
| `gemini-3.5-flash` | 52020 | 62 | $0.078588 |
| `gemini-3.5-flash` | 7154 | 163 | $0.012198 |
| `gemini-3.5-flash` | 2783 | 115 | $0.005210 |
| `gemini-3.5-flash` | 2781 | 106 | $0.005125 |
| `gemini-3.5-flash` | 52021 | 62 | $0.078590 |
| `us.anthropic.claude-opus-4-7` | 13344 | 331 | $0.082494 |
| `us.anthropic.claude-opus-4-7` | 2006 | 332 | $0.020163 |
| **TOTAL (this committed run)** | | | **$0.3612** |

To re-verify the arithmetic: `in×1.5e-6 + out×9.0e-6` (Gemini), `in×5.5e-6 +
out×27.5e-6` (Opus), summed over `realstack_ledger.jsonl`.

This run also exercises the HTTP **download path** added on 2026-06-13
(`test_06` fetches the report's bundle URL and asserts the bytes match;
`test_08` asserts a downloadable bundle was registered for the submission).

**Session spend across all paid runs in the build session:** ~$1.46
($0.3410 + $0.0343 + $0.3627 original acceptance; $0.3611 first committed
re-run; $0.3612 this download-path refresh), under the $5 authorization cap.
