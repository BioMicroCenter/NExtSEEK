# Task 17 — §7 Live full-UI E2E: harness + frozen approval

Status (2026-07-13): **harness ported and unit-verified; the paid live run is
PENDING (not yet executed).** This directory holds the frozen approval artifacts
the live run will consume; the immutable run-dir (Playwright trace, per-turn
progress payloads, per-op verdict JSON, cost ledger, summary, identity) will be
added here when the live run is executed and its `--validate-only` replay passes.

## What changed (why this exists)

The merged NExtSEEK stack serves the assistant over Django/gunicorn (WSGI) with
an in-memory channel layer and no ASGI websocket worker, so the frontend's
`query_complete` **websocket** frame never arrives and the original E2E
transport (`chat_nextseek/e2e/playwright/ws.py::WSCapture`) times out. The
working channel is HTTP polling of
`GET /nextseek_api/cc-assistant/tasks/{task_id}/progress/`
(`CCAssistantViewSet.task_progress`, terminal `status ∈ {completed, error}`).

The harness was reworked from websocket capture to poll capture, and the
approval-driven orchestrator was made **route-aware**:

- `chat_nextseek/e2e/playwright/poll.py` (new) — `PollCapture.poll_until_complete`
  + payload adapters (`query_complete_data`, `build_debug`, `artifact_files`,
  `detect_route`, `cc_cost`).
- `chat_nextseek/e2e/playwright/runner.py` — `run_variant_browser` now captures
  `task_id`+`session_id` from the `.../query/async` 202 response, polls to a
  terminal payload, downloads artifacts by the un-suffixed `data.artifacts`
  **key** (verified live: `geo_seq_workbooks` → HTTP 200; the flat `data.files`
  manifest suffixes it to `geo_seq_workbooks_0` → 404), passes `last_reply` to
  the criteria DSL, and returns the detected `route` + per-turn `cc_cost_usd`.
- `nextseek_api/cc_assistant/scripts/full_ui_e2e.py` — cost is now gated on the
  **CC route only**. The BAML router (`nextseek_api/cc_assistant/router.py`)
  dispatches each turn to either the deterministic NS pipeline or Container-CC.
  The NS branch of `nextseek_api/services/cc_assistant.py` runs `run_query(...)`
  with no `cc_engine`/`on_turn_complete`, so it persists **no** `cc_traces` cost
  row; the CC branch (`cc_engine.run_cc_turn(..., on_turn_complete=...)`) persists
  `total_cost_usd` to `extra_state.cc_traces[*]`. So NS-routed questions pass on
  criteria + session + no-forbidden without a cost row; CC-routed questions still
  require a `cc_traces` cost under the `$15` cap. A declared-vs-detected route
  mismatch is itself a routing regression and fails the question. The
  `--validate-only` recompute mirrors all of this from raw artifacts + a fresh
  DB read.

## Unit verification (green, 2026-07-13)

- `chat_nextseek` e2e suite (poll/runner/imports/ws/pages/trio): **59 passed**
  `uv run --no-project --with pytest,pydantic,playwright,beautifulsoup4 python -m pytest chat_nextseek/tests/test_e2e_playwright_*.py -q`
- orchestrator: **33 passed**
  `uv run --no-project --with pytest python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/test_full_ui_e2e.py`
- prod-readiness manifest verifier: **33 passed** (unaffected)

## Frozen approval artifacts

- `approval.full.json` — the vetted 9-question §7 set (search, retrieve, graph,
  reporting/GEO, a CC write, a 2-turn refine, a 3-turn nf-core pipeline), each
  with `route` + machine-checkable `pass_criteria` wired to `e2e/criteria.py`,
  plus forbidden-phrase gating. `base_url http://localhost:8100`,
  `require_non_8000 true`, `max_total_usd 15.0`.
- `approval.focus.json` — a 3-question subset (RNA-with-RIN → NS pipeline + debug
  criteria; GEO submission → download-by-key + `api_artifact`; BPRC write → CC
  route + cost gate + `last_reply`) that exercises the port's whole changed
  surface.

## To run the live paid verification (pending step)

Host-side against the published nginx port (the Django login CSRF trusts only the
host origin; a browser inside the app container 403s on login):

```
cd <repo root>
uv run --no-project --with playwright,pydantic,mysql-connector-python,openpyxl,beautifulsoup4 \
  python nextseek_api/cc_assistant/scripts/full_ui_e2e.py \
  --approval nextseek_api/cc_assistant/acceptance_evidence/step7-live-e2e/approval.full.json \
  --run-dir  nextseek_api/cc_assistant/acceptance_evidence/step7-live-e2e/run-<date> \
  --db-env dev
```

Then replay from raw artifacts (must exit 0, recomputes from artifacts/DB, never
trusts `summary.json`):

```
uv run ... python nextseek_api/cc_assistant/scripts/full_ui_e2e.py \
  --approval .../approval.full.json --run-dir .../run-<date> --validate-only --db-env dev
```
