# Step 1c live verification — cross-session memory

**Date:** 2026-06-29  
**Branch:** `feat/dmac-assistant-full-integration`  
**Hermetic suite:** 119 passed (`nextseek_api/cc_assistant/tests/`, excluding `test_cc_realstack.py`)

## Implementation summary

| Component | Status |
|-----------|--------|
| `CCMemoryConfig` + container path constants | done |
| BAML `Summarize()` + `GCPFlash` (`gemini-3.5-flash`) in shared `baml_src/` | done |
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

**Verified 2026-06-29** on the live `nextseek` dev instance (single-user `demo`), image **`e9a2f222`** (gunicorn + Celery `batch_upload`). Rollback snapshot: `nextseek-nextseek:pre-step1c` (`cacd40b3`). NExtSEEK `29dd8a3`, dmac-assistant `a429f13`.

| # | Check | Result |
|---|-------|--------|
| — | Summarizer model (runtime) | **PASS** — live BAML log: `Function Summarize: Client: GCPFlash (gemini-3.5-flash) - 5891ms. StopReason: STOP. Tokens(in/out): 678/528`; no model-not-found errors; baked image inlines `gemini-3.5-flash`; `GCP_API_KEY` present (len 39) |
| 1 | Session A force-CC turn plants distinctive fact | **PASS** — codeword `QUOKKA-SIGMA-91` planted via bash (router → `container_cc`) |
| 2 | Session B new chat moves A out of foreground | **PASS** — driven via dockerized Playwright (`pw/xsession_1c.mjs`) |
| 3 | Cross-session recall from injected memory | **PARTIAL / mechanism-verified** — rendered `_memory/<A2-session>/CLAUDE.md` contained the distilled prior-session summary **including `QUOKKA-SIGMA-91`** plus the RO transcript-pointer block, shaky claims tagged `_(unverified)_`. The agent's *conversational* recall was NOT demonstrated: OI-4 router refuses recall-style prompts before they reach CC (see open question below) |
| 4 | Filesystem render | **PASS** — `/dmac/cc-state/demo/_memory/7b10def2-.../CLAUDE.md` |
| 5 | `fresh_session=true` skips memory | **PASS** — POST `cc/query/async` `fresh_session=true,force_new=true,mode=standard`: turn completed, session dir created, **no** `_memory/<sid>/CLAUDE.md` rendered (clean-room guard at `services/cc_assistant.py` ~L240 `if not fresh:`) |
| 6 | `--resume` single transcript (1b regression) | **PASS** — memory listed prior transcripts; resume intact |
| 7 | Celery sweep backstop | **PASS** — `cc_assistant.sweep_cc_summaries` registered (beat 300s) |

In-container verify: orjson 3.11.9, `SessionSummary` importable, gunicorn-only (no daphne), no `.predeploybak` (recreate-safe), `GET /` → 200.

Live spend ≈ `$0.45` (3 Opus CC turns; router-refused recall turns incurred no Opus), logged to `spend_ledger.jsonl` — within the $2 cap.

**Open question (OI-4 × 1c):** recall-style natural-language questions are router-gated out of `container_cc`, so 1c memory only benefits turns that already route to CC. No code change made; awaiting user decision. See handoff `2026-06-29-step1c-gemini-3-5-flash-deployed-verified.json` (ANN-7).
