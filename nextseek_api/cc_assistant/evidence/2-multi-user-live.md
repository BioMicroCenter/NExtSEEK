# Step 2 live verification - multi-user provisioning path migration

Date: 2026-06-30
Branch: `feat/dmac-assistant-full-integration`
Local HEAD / deployed SA clone: `01079f4`
Live image: `nextseek-nextseek:latest` = `048b060e2c21`
Rollback snapshot: `nextseek-nextseek:pre-step2` = `e77b60951820`

## Deploy hygiene

- Created rollback snapshot before recreate: `docker commit nextseek nextseek-nextseek:pre-step2`.
- Fast-forwarded the service-account build-context clone from local `feat/dmac-assistant-full-integration` to `01079f4`.
- Updated gitignored SA `docker/nextseek.env` to:
  - `DMAC_USER_ROOT=/srv/dmac/users`
  - `DMAC_USER_ROOT_MOUNT=/dmac/users`
- Created `/srv/dmac/users` and verified it is writable.
- Rebuilt with the service-account `docker:cli` helper:
  - `docker compose -p nextseek build nextseek`
  - `docker compose -p nextseek up -d --no-deps nextseek`
- Verified after recreate:
  - `/dmac/users` bind is mounted from `/srv/dmac/users`
  - gunicorn and `celery ... batch_upload` are running
  - `cc_engine.cc_runner_available()` returned `(True, 'ok')`
  - `curl http://127.0.0.1:8000/` returned `HTTP=200`
  - no `.predeploybak` files were found

## Hermetic pre-live gate

Command:

```bash
PYTHONPATH="$PWD:$PWD/dmac_assistant/src" \
uv run --no-project --with pytest --with orjson --with 'pydantic>=2.13' --with 'baml-py==0.222.0' \
  python -m pytest nextseek_api/cc_assistant/tests/ --noconftest -p no:cacheprovider -q \
  --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py
```

Result: `176 passed, 1 warning`.

## Live UI checks

### 1b resume re-verification

Script: `/home/taishajo/work/state/pw/resume_ui.mjs`

Run method: Dockerized Playwright with `--network host` against `https://nextseek-dev.mit.edu`.

Result: **PASS**

- Login succeeded.
- Turn 1: `Output: BANANA-42`
- Turn 2 in the same UI chat: `Output: BANANA-84`
- `ERRISH=false`; no 404 observed.
- Nested transcript path contained both turns:
  `/srv/dmac/users/1-published-data/demo/cc-state/7b30fd91-a12b-4b48-87e7-7e8a9da55822/projects/-home-user/562c3c5c-3147-41e5-abd1-fc2bd30fcbd5.jsonl`

### 1c memory re-verification

Script: `/home/taishajo/work/state/pw/xsession_1c.mjs`

Result: **MECHANISM PASS / conversational recall still router-gated**

- Session A planted `WALLABY-OMEGA-58` through the UI and routed to CC successfully.
- Session B recall prompt was refused by the existing OI-4 router before reaching CC, so conversational recall was **not** confirmed. This matches the known 1c caveat from the prior handoff; no router change was in Step 2 scope.
- The memory mechanism was verified at the new nested paths:
  - `_memory` rendered under `/srv/dmac/users/1-published-data/demo/_memory/...`
  - prior memory file included `BANANA-42` / `BANANA-84`
  - Docker logs showed `Function Summarize` using `GCPFlash (gemini-3.5-flash)` with `StopReason: STOP`

### Step 2 mount and artifact check

Script: `/home/taishajo/work/state/pw/chat_test.mjs`

Prompt asked CC via the UI to create `/data/scratch/step2_probe.txt` containing `STEP2-ARTIFACT-ROOT`.

Result: **PASS**

- UI reply reported exact contents: `STEP2-ARTIFACT-ROOT`.
- Published artifact path:
  `/srv/dmac/users/1-published-data/demo/output/step2_probe.txt`
- File content verified on host: `STEP2-ARTIFACT-ROOT`.
- Captured live sibling CC container mounts:
  - `/data/input` -> `/srv/dmac/users/1-published-data/demo/input` (`ro`)
  - `/data/shared` -> `/srv/dmac/users/1-published-data/shared` (`ro`)
  - `/data/scratch` -> `/srv/dmac/users/1-published-data/demo/scratch` (`rw`)
  - `/home/user/.claude` -> `/srv/dmac/users/1-published-data/demo/cc-state/98b89c8c-d849-4758-8d67-3c168d6f32ea` (`rw`)
  - `/home/user/.claude/CLAUDE.md` -> `/srv/dmac/users/1-published-data/demo/_memory/98b89c8c-d849-4758-8d67-3c168d6f32ea/CLAUDE.md` (`ro`)
- Captured live CC container network: `dmac-cc-net`.

## Isolation note

The live dev instance verification used the documented `demo` login. Cross-user and cross-project isolation are covered hermetically by `test_cc_provision_isolation.py`; no second live SEEK login was used in this run. The live mount capture confirms the Step 2 runtime shape for the demo user's resolved project (`1-published-data`) and shows private user paths are mounted separately from the project shared path.

## Caveats

- The visible UI text still says "Saved to your Dropbox"; this is pre-existing copy and Step 3 owns UI-based I/O wording/removal.
- The conversational 1c memory recall prompt remains router-gated by OI-4 and did not reach CC. Step 2 preserved that behavior; it did not change routing policy.
