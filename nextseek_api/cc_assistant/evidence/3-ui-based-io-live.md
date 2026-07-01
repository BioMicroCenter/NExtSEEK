# Step 3 live gate index (Task 13)

Proof lives in `3-ui-based-io-live/live_gate_transcript.txt` (PLAN-7 marker handshake).

## Reproduce

```bash
# Deploy (after wave-07 merge on cc-step3-ui-io)
cd chat_frontend && npm run build:embedded
docker commit nextseek nextseek-nextseek:pre-step3
# SA clone ff-only cc-step3-ui-io, rebuild+recreate nextseek via docker:cli
docker exec nextseek sh -lc 'cd /app && uv run python manage.py migrate nextseek_api 0007_ccsessiontranscript --fake'

# Live UI (demo login — use env vars, do not commit secrets)
docker run --rm --network host -v /home/taishajo/work/state/pw:/work \
  -e SEEK_USER=demo -e SEEK_PASS=... \
  mcr.microsoft.com/playwright:v1.46.1-jammy node step3_live_gate.mjs
docker run --rm --network host -v /home/taishajo/work/state/pw:/work \
  -e SEEK_USER=demo -e SEEK_PASS=... \
  mcr.microsoft.com/playwright:v1.46.1-jammy node resume_ui.mjs
```

Logs: `3-ui-based-io-live/playwright/step3_live_gate.log`, `resume_ui.log`.
