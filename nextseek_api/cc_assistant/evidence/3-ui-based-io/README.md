# Step 3 UI-based I/O — verification evidence

Durable, committed proof for PLAN-3 worktree execution on branch `cc-step3-ui-io`.

## Layout

| Path | Contents |
|------|----------|
| `wave-01/` … `wave-07/` | Hermetic verify transcripts + coverage after each wave merge |
| `3-ui-based-io-live/` | Task 13 live gate (`live_gate_transcript.txt`, optional Playwright) |
| `3-ui-based-io-live.md` | Index only — reproduction commands, not proof |
| `secret_scan_report.json` | Cumulative secret scan log |

## Cold-start verification

1. Checkout `cc-step3-ui-io` (or merge commit on `feat/dmac-assistant-full-integration`).
2. Read `wave-NN/wave-NN.md` for merge SHA and reproduction one-liners.
3. Re-run commands; compare stdout/exit codes to `wave-NN/verify-transcript.txt`.
4. Wave 8: grep PLAN-7 markers in `3-ui-based-io-live/live_gate_transcript.txt`.

Hermetic test base (from repo root):

```bash
cd /home/taishajo/work/NExtSEEK
uv run --no-project --with pytest --with pytest-cov --with orjson --with pydantic --with zstandard \
  python -m pytest -q --noconftest nextseek_api/cc_assistant/tests/
```
