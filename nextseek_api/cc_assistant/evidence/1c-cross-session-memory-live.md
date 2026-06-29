# Step 1c live verification — cross-session memory

**Date:** 2026-06-29  
**Branch:** `feat/dmac-assistant-full-integration`  
**Hermetic suite:** 119 passed (`nextseek_api/cc_assistant/tests/`, excluding `test_cc_realstack.py`)

## Implementation summary

| Component | Status |
|-----------|--------|
| `CCMemoryConfig` + container path constants | done |
| BAML `Summarize()` + `GCPFlash` (`gemini-2.5-flash`) in shared `baml_src/` | done |
| Build-time `baml-cli generate` in Dockerfile | done |
| `cc_summary.py` (parse, grounding, summarizer, fallback) | done |
| `cc_memory.py` (select, render) | done |
| `cc_engine` RO memory mounts | done |
| `fresh_session` on `QueryRequest` | done |
| Service wiring (`cc_assistant.py`) | done |
| Celery sweep backstop | done |

## BAML codegen discipline

- **Committed:** `dmac_assistant/baml_src/*.baml` (synced from canonical `dmac-assistant` repo)
- **Gitignored:** `dmac_assistant/src/dmac_assistant/router/baml_client/` (generated)
- **Image build:** `Dockerfile` runs `uv run baml-cli generate --from /app/dmac_assistant/baml_src`

## Pre-deploy checks (run before live verify)

```bash
cd /home/taishajo/work/NExtSEEK && \
PYTHONPATH="$PWD:$PWD/dmac_assistant/src" \
uv run --no-project --with pytest --with orjson --with 'pydantic>=2.13' --with 'baml-py==0.222.0' \
  python -m pytest nextseek_api/cc_assistant/tests/ --noconftest -p no:cacheprovider -q \
  --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py
```

Expected: all pass.

## Deploy procedure (Step 0 + 1c)

1. `docker tag nextseek-nextseek:dev nextseek-nextseek:pre-step1c`
2. Fast-forward SA build-context clone; rebuild image (includes `orjson` + BAML codegen)
3. Recreate `nextseek` via SA `docker:cli` helper (per standing Step-0 procedure)
4. Verify in container:
   ```bash
   docker exec nextseek python -c "import orjson; print('orjson ok')"
   docker exec nextseek sh -lc 'cd /app && python -c "import dmac_assistant.router.baml_client.types as t; print(bool(t.SessionSummary))"'
   ```

## Live verification checklist (≤ $2 cap)

1. Session A: force-CC turn establishing distinctive fact (e.g. run code MEMTEST-Zeta-7)
2. Session B: new chat, different task (moves A out of foreground)
3. Return to A: ask cross-session question; confirm recall from injected memory
4. Filesystem: rendered `CLAUDE.md` under `/dmac/cc-state/demo/_memory/<session>/`
5. `fresh_session=true` API call: no memory mounts for that turn
6. `--resume` still appends to single transcript (1b regression)
7. Celery sweep log: `cc-1c sweep: summarized N session(s)`

## Live verification result

_Pending deploy to dev instance — hermetic implementation complete; run deploy + Playwright when user signs off per-change._
