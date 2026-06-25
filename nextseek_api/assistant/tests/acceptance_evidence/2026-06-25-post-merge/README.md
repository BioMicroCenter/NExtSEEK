# Real-stack acceptance evidence — POST-MERGE validation

**Date:** 2026-06-25
**Branch:** `integration/dmac-assistant`
**Merge commit:** `5fb3635` — Merge `feat/native-assistant-granular-ops` into `integration/dmac-assistant`
**Test:** `nextseek_api/assistant/tests/test_granular_realstack.py` (gated by `RUN_REALSTACK=1`)

## What this validates

This is a fresh re-run of the 8-test real-stack acceptance against the **merged**
code, to confirm the granular-ops assistant work still passes end-to-end after
merging onto `integration/dmac-assistant` (which carries the 2026-06-05 fixes:
Django 5.2 LTS pin, the Docker-env settings guard, and an `extra_state` migration
guard). It exercises, at runtime:

- the merged granular endpoints (`granular.py`, `write_gate.py`, `services/assistant.py`);
- the unified migration graph — the test DB built cleanly with both `0005` guards +
  `0006_merge_extra_state_guards` (no "Conflicting migrations" error);
- Django 5.2 LTS (integration's pin) running the feat code;
- the reseeded local Neo4j graph (51,032 nodes / 623,157 rels, loaded via UNWIND
  batching) — `test_08` pulled a real published UID from it;
- both live LLM providers (Gemini 3.5 Flash + Claude Opus 4.7 via Bedrock).

(The original feat-branch evidence is preserved in the parent
`acceptance_evidence/` directory; this dated subdir is the post-merge re-run.)

## How it was run (reproducible)

```bash
# from BMC/nextseek-worktrees/dmac-integration, stack up via ./startup.sh install
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=demo -e SEEK_TEST_PASS=demopassword \
  nextseek sh -lc 'cd /app && uv run python manage.py test \
  nextseek_api.assistant.tests.test_granular_realstack \
  --settings=dmac.test_settings_realstack --noinput --keepdb -v2'
```

`SEEK_TEST_USER/PASS` = the install-seeded local admin (`demo`/`demopassword`),
valid on this local stack. Result: **`Ran 8 tests in 51.837s ... OK`** (8/8) — see
`realstack_run.txt`.

## Result — 8/8 OK

1. `entity("mouse samples treated with NDMA")` → sampletype **MUS** ✓
2. `graph("lineage of mouse samples")` → real Neo4j node rows ✓
3. `api-read(plan)` → ≥1 real sample row from `seek_production` ✓
4. `api-write confirmed=false` → **WRITE_BLOCKED**, `samples` count unchanged ✓
5. `api-write confirmed=true` vs `advanced_search` (read-safe) → executes ✓
6. `report(published)` → `saved_files` with DB-sourced bytes + HTTP download path ✓
7. `parse(...)` → `target_endpoint = /nextseek_api/samples/advanced_search/` ✓
8. `generate-submission(GEO, real published UID)` → non-null GEO output + bundle ✓

## Strict cost accounting

`realstack_ledger.jsonl` holds the machine-captured per-call provider token usage
(the test wraps each LLM client's `chat()` and records the actual `response.usage`).
Cost = `actual_tokens × published rate`:

- **Gemini 3.5 Flash:** $1.50 / $9.00 per 1M tok (in/out)
- **Claude Opus 4.7 (Bedrock, `us.` cross-region):** $5.50 / $27.50 per 1M tok

| model | in tok | out tok |
|---|---|---|
| `gemini-3.5-flash` | 52021 | 89 |
| `gemini-3.5-flash` | 52020 | 62 |
| `gemini-3.5-flash` | 7154 | 163 |
| `gemini-3.5-flash` | 2071 | 106 |
| `gemini-3.5-flash` | 2069 | 97 |
| `gemini-3.5-flash` | 52021 | 62 |
| `us.anthropic.claude-opus-4-7` | 13344 | 318 |
| `us.anthropic.claude-opus-4-7` | 2006 | 336 |
| **TOTAL** | | **$0.3587** |

Re-verify the arithmetic from the ledger:
`in×1.5e-6 + out×9.0e-6` (Gemini), `in×5.5e-6 + out×27.5e-6` (Opus), summed.
Independently recomputed from `realstack_ledger.jsonl` = **$0.3587** (matches the
test's self-reported `cumulative_cost`).
