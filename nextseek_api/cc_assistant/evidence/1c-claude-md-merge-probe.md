# Step 1c gating probe — CLAUDE.md merge behavior

**Date:** 2026-06-29  
**Image:** `dmac-assistant:poc` (`50abbf4be779`)  
**Claude Code version:** 2.1.92 (npm package) / 2.1.163 (runtime transcript)  
**Spend:** zero Bedrock/API calls (no auth env vars; all runs failed at auth before model dispatch)

## Verdict

**MERGE — proceed with the planned injection point** (`~/.claude/CLAUDE.md` nested RO bind over the 1b session RW mount).

Confidence: **confirmed** (live forced-CC turn on 2026-06-29 saw both markers in loaded instructions).

**Live confirmation (2026-06-29):** forced-CC probe via `run_1c_claude_md_live_probe.py` inside the running `nextseek` container — Opus turn, **$0.14 spend**, agent reported `saw_write_safety: true` and `saw_user_memory_marker: true`. See [Live confirmation](#live-confirmation-2026-06-29) below.

## Question under test

Does a user-tier memory file mounted at `/home/user/.claude/CLAUDE.md` **compose with** (not replace) the baked project file at `/home/user/CLAUDE.md` → `/app/CLAUDE.md` when CC runs with `workdir=/home/user` in `dmac-assistant:poc`?

## Mount layout (mirrors SPEC-1c §6.2 + 1b)

| Layer | Host path | Container path | Mode |
|-------|-----------|----------------|------|
| Session store (1b) | `/tmp/1c-claude-probe/session/.claude/` | `/home/user/.claude` | RW |
| User memory (1c) | `/tmp/1c-claude-probe/memory/CLAUDE.md` | `/home/user/.claude/CLAUDE.md` | RO (nested file bind) |
| Project memory (baked) | _(in image)_ | `/home/user/CLAUDE.md` → `/app/CLAUDE.md` | symlink at build time |

## Markers

| Tier | Path | Marker |
|------|------|--------|
| Project (baked) | `/home/user/CLAUDE.md` | `Write-safety on NExtSEEK` (line 5 of `dmac-assistant/container/CLAUDE.md`) |
| User-tier (fixture) | `/home/user/.claude/CLAUDE.md` | `USER_MEMORY_MARKER=1C_PROBE_ALPHA` |

Fixture copy: [`1c-probe-user-tier-fixture.md`](1c-probe-user-tier-fixture.md)

## Commands run

### 1. Mount layout sanity (no claude)

```bash
PROBE=/tmp/1c-claude-probe
docker run --rm \
  -v "$PROBE/session/.claude:/home/user/.claude" \
  -v "$PROBE/memory/CLAUDE.md:/home/user/.claude/CLAUDE.md:ro" \
  -w /home/user \
  --entrypoint bash \
  dmac-assistant:poc \
  -c 'readlink -f /home/user/CLAUDE.md; head -1 /home/user/CLAUDE.md; head -1 /home/user/.claude/CLAUDE.md'
```

**Result:** both files present, distinct content:

```
/app/CLAUDE.md
# In-Container Agent Instructions
# Cross-session memory (probe fixture)
```

### 2. Production entrypoint + plugin symlinks (no Bedrock auth)

```bash
docker run --rm \
  -v "$PROBE/session2/.claude:/home/user/.claude" \
  -v "$PROBE/memory2/CLAUDE.md:/home/user/.claude/CLAUDE.md:ro" \
  -v "$PROBE:/probe:rw" \
  -w /home/user \
  -e CLAUDE_CODE_USE_BEDROCK=0 \
  dmac-assistant:poc \
  claude --debug-file /probe/debug-entrypoint.log --print --dangerously-skip-permissions "Reply PROBE_OK"
```

**Result:** `Not logged in · Please run /login` (auth failure, zero spend). Post-run filesystem check:

- `/home/user/.claude/plugins/local/nextseek` → `/app/plugins/nextseek` (entrypoint DD-37 symlink intact)
- User-tier fixture and project symlink both unchanged

Full debug log: [`1c-probe-debug-entrypoint.log`](1c-probe-debug-entrypoint.log)

### 3. Debug-only attempts (no auth)

Additional runs with `-d memory`, `-d "!api"`, and unfiltered `--debug-file` all reached startup then failed auth. **No debug line in this build names loaded CLAUDE.md paths** (grep over all logs: no `Write-safety`, `USER_MEMORY`, or `/app/CLAUDE.md` in debug output).

API timing lines show dispatch attempted then immediate auth error:

```
[DEBUG] [API:timing] dispatching to firstParty model=claude-opus-4-8[1m]
[ERROR] Could not resolve authentication method...
```

## Analysis

### Mount topology — VERIFIED

The nested RO file bind on `/home/user/.claude/CLAUDE.md` over the RW session dir bind works. The baked project symlink at `/home/user/CLAUDE.md` is a **separate path** and is not shadowed by the user-tier mount.

### Runtime merge model — MERGE (not OVERWRITE)

**Filesystem resolution at `cwd=/home/user`:**

- **User instructions:** `~/.claude/CLAUDE.md` → our fixture (mounted)
- **Project instructions:** `./CLAUDE.md` → baked symlink (distinct file)

These are different scopes in Claude Code's memory hierarchy.

**Official behavior** ([Claude Code memory docs](https://code.claude.com/docs/en/memory)):

> All discovered files are concatenated into context rather than overriding each other.

Load order (broadest → most specific): managed → user (`~/.claude/CLAUDE.md`) → project (`./CLAUDE.md`, `./.claude/CLAUDE.md`) → local. Project instructions appear **after** user instructions in context.

**Overwrite would require** the user-tier file to replace `/home/user/CLAUDE.md`. That does not happen — the paths are independent.

### Production compatibility — VERIFIED

With the default image entrypoint (not `--entrypoint claude`):

- Plugin discovery symlink `~/.claude/plugins/local/nextseek` is created inside the session RW mount
- User-tier RO `CLAUDE.md` bind does not block entrypoint or plugin wiring

### Limitations

1. ~~**No system-prompt dump**~~ — **resolved** by live forced-CC turn (see below).
2. **Debug logging gap** — `claude --debug` / `-d memory` in v2.1.163 does not emit per-file CLAUDE.md discovery lines.

## Live confirmation (2026-06-29)

Script: [`run_1c_claude_md_live_probe.py`](run_1c_claude_md_live_probe.py) (monkeypatches `_build_volumes` to add the 1c memory RO bind; uses `cc_engine.run_cc_turn` + Opus via `cc_router._resolve_cc_model_id()`).

```bash
docker cp .../run_1c_claude_md_live_probe.py nextseek:/app/nextseek_api/cc_assistant/evidence/
docker exec nextseek sh -lc 'cd /app && PYTHONPATH=/app uv run python nextseek_api/cc_assistant/evidence/run_1c_claude_md_live_probe.py'
```

| Field | Value |
|-------|-------|
| Model | `us.anthropic.claude-opus-4-8` |
| Memory bind | `/srv/dmac/cc-state/1c-probe/memory/CLAUDE.md` → `/home/user/.claude/CLAUDE.md:ro` |
| cc_state_key | `1c-claude-md-probe` |
| Cost | **$0.14** |
| `saw_write_safety` | **true** |
| `saw_user_memory_marker` | **true** |

Agent reply (parsed JSON):

```json
{"saw_write_safety": true, "saw_user_memory_marker": true, "notes": "Both the project-tier 'Write-safety on NExtSEEK' guidance and the user-tier USER_MEMORY_MARKER=1C_PROBE_ALPHA are present in my loaded instructions, indicating the two CLAUDE.md tiers merged rather than one replacing the other."}
```

**VERDICT live-merge: CONFIRMED**

## Recommendation for Step 1c implementation

**Keep the planned injection point:**

- Render per-user memory to a host file
- RO bind at `/home/user/.claude/CLAUDE.md` (nested over 1b's session `.claude` RW mount)
- Leave `/home/user/CLAUDE.md` → `/app/CLAUDE.md` untouched

**Do not switch to fallbacks** (`@import`, `--append-system-prompt-file`) unless a live verification turn contradicts this probe.

## Alternate strategies (only if live test fails)

| Fallback | When |
|----------|------|
| `@import` from project `CLAUDE.md` into user memory file | User-tier content ignored at runtime |
| Additive section beside project file (different mount path) | Path collision or mount conflict |
| `--append-system-prompt-file` | Last resort; changes delivery mechanism |
