# Step 3 live gate — index (Task 13)

**Date:** 2026-07-01  
**Branch:** `cc-step3-ui-io` @ `988d974` (deploy)  
**Image:** `nextseek-nextseek:latest` = `befc1261e5f1`  
**Verdict:** PASS

Proof lives in `evidence/3-ui-based-io-live/live_gate_transcript.txt` (committed, secret-scanned). Playwright stdout logs: `playwright_run.log`, `resume_ui_run.log`.

## Reproduce (zero-spend markers only)

```bash
grep -E '0007_ccsessiontranscript|cc_assistant\.upload|cc_traces' \
  nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt
```

## Reproduce live gate (paid, ≤ $2 cap)

```bash
# deploy: see nextseek_api/cc_assistant/DEPLOY.md § Step 3
docker run --rm --network host \
  -v /home/taishajo/work/state/pw:/work \
  -e SEEK_USER=demo -e SEEK_PASS=... \
  mcr.microsoft.com/playwright:v1.46.1-jammy \
  bash -c 'cd /work && node step3_live_gate.mjs'
```

1b regression: `node resume_ui.mjs` (same docker invocation).
