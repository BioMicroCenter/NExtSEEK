# Step 3 live gate — index (Task 13)

**Date:** 2026-07-01  
**Branch:** `cc-step3-ui-io` @ `2939ffa` (deploy)
**Image:** `nextseek-nextseek:latest` = `sha256:0c8b8f759e29757ff548368ab634ba75cfb0e7ced786482ab913e53dff292d8a`
**Verdict:** PASS for PLAN-3 live UI upload/download gap closure

Proof lives in:

- `evidence/3-ui-based-io-live/playwright_run.txt`
  - UI upload completed.
  - CC read `/data/input/step3_probe.txt`.
  - First `report.md` download contained `STEP3-REPORT-A-7719`.
  - Second same-basename `report.md` download after reload contained `STEP3-REPORT-B-8826`.
  - The two downloaded `report.md` hashes differ.
  - CC traces persisted across reload and transcript recovery returned HTTP 200.
- `evidence/3-ui-based-io-live/resume_ui_run.txt`
  - 1b UI multi-turn resume regression verified (`BANANA-42` -> `BANANA-84`).
- `evidence/3-ui-based-io-live/xsession_1c_run.txt`
  - Non-canonical 1c probe only; it did not reuse the prior live 1c prompts and did not confirm cross-session recall. Do not treat it as a 1c verdict.

Deploy verification:

- Service-account clone fast-forwarded to `2939ffa921380209d3bb07266e6e6e6f50dc0a22`.
- `chat_frontend` embedded bundle rebuilt into `static/js/chat_assistant/` and committed as `main.embedded-7N8RL5lY.js`.
- `nextseek-nextseek:latest` rebuilt from the service-account clone.
- `docker compose -p nextseek up -d --no-deps nextseek` recreated the app container; the entrypoint ran `collectstatic --noinput` before starting the server.
- Authenticated Playwright DOM probe observed `https://nextseek-dev.mit.edu/static/js/chat_assistant/assets/main.embedded-7N8RL5lY.js`, one upload control, and one file input.

## Reproduce (zero-spend markers only)

```bash
grep -E 'STEP3_LIVE_GAP_CLOSURE_DONE|same_basename_download_hashes_differ|transcript_recover_http_200_contains_turn' \
  nextseek_api/cc_assistant/evidence/3-ui-based-io-live/playwright_run.txt
```

## Reproduce live gate (paid, ≤ $2 cap)

```bash
# deploy: see nextseek_api/cc_assistant/DEPLOY.md § Step 3
docker run --rm --network host \
  -v /home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/evidence/3-ui-based-io-live/playwright:/work \
  -e SEEK_USER=demo -e SEEK_PASS=... \
  mcr.microsoft.com/playwright:v1.46.1-jammy \
  bash -c 'cd /work && npm ci --no-audit --no-fund && node step3_live_gap_closure.mjs'
```

1b regression: `node resume_ui.mjs` (same docker invocation).
