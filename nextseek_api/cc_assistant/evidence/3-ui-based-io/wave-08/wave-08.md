# Wave 08 — Task 13 deploy + live gate

**Evidence:** `../3-ui-based-io-live/live_gate_transcript.txt` (committed)

**Merge commit on cc-step3-ui-io:** `786fe1d`

**PLAN-7 markers verified:**
- `[X] 0007_ccsessiontranscript`
- `cc_assistant.upload`
- `cc_traces` in reload JSON excerpt

**Live gate:** PASS (upload SUCCESS, cc_traces after reload, 3e no-404, 1b resume re-verified)

**Note:** `feat/dmac-assistant-full-integration` merge blocked locally by docker-owned `static/js/chat_assistant/` files (nobody:nobody). Use PR merge or `chown` + merge on host.
