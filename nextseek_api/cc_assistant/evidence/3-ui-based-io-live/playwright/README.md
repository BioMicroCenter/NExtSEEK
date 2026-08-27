# PLAN-3 Live UI Evidence Reproduction

Run from a deployed `cc-step3-ui-io` live instance. These checks spend live CC
budget and require SEEK credentials supplied by environment variables.

```bash
docker run --rm --network host \
  -v /home/taishajo/work/NExtSEEK/nextseek_api/cc_assistant/evidence/3-ui-based-io-live/playwright:/work \
  -e SEEK_USER=demo -e SEEK_PASS=... \
  mcr.microsoft.com/playwright:v1.46.1-jammy \
  bash -lc 'cd /work && npm ci --no-audit --no-fund && node step3_live_gap_closure.mjs'
```

The 1b resume regression uses the same invocation with `node resume_ui.mjs`.
`node xsession_1c.mjs` is a non-canonical probe unless it is first aligned with
the exact prior 1c live-test prompts.
