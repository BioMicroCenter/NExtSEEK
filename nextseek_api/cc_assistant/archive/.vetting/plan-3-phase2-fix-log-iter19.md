# PLAN-3 Phase 2 Hardener Fix-Log — iter-19

Target: `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md` (edited file only).
Source review: `.vetting/plan-3-phase2-review-19-fresh.md` (CONDITIONAL_ACCEPTANCE).
Locked design: `SPEC-3-ui-based-io.md` (NOT edited; no escalation needed).

All five defect classes resolved surgically. The Task 5 translate coverage exception was
adjudicated LEGITIMATE by the reviewer and left untouched. The "Phase 2 Vetting Log" table and
"Phase 2 status" line were NOT touched (orchestrator-owned).

---

## Verification table (before → after, per defect)

### Finding 1 — HIGH: Task 9 cross-device move (`os.replace` → `shutil.move`)

**Facts relied on (verified live this session):**
- `dmac/settings.py:90` → `MEDIA_ROOT = "/media"` (staging root: `MEDIA_ROOT/cc_upload_staging`).
- `docker-compose.yml:28` → `- /srv/dmac/users:/dmac/users` (the destination `input_mnt` is under this host bind).
- `/media` has **no** compose volume → container overlayfs. Staging (`/media/...`) and destination
  (`/dmac/users/...`) are therefore on **different devices** → `os.replace` = `rename(2)` raises
  `OSError(EXDEV, "Invalid cross-device link")` on every upload.
- Probe (`python3 -c` over `inspect.getsource(shutil.move)`): `shutil.move` falls back to
  `copy2` + unlink when `os.rename` fails cross-device — confirmed it handles EXDEV.

| | Before | After |
|---|---|---|
| Celery body (Task 9 Step 3) | `os.replace(f["tmp_path"], dst)` | `shutil.move(f["tmp_path"], dst)` + 6-line comment explaining the EXDEV boundary |
| Import block | `import os` / `from pathlib import Path` | added `import shutil` |
| Staging-hygiene note | unlink described as always cleaning up | clarified the `finally: os.unlink` is a **no-op** for a moved file (source already gone); matters only when a mid-batch validate-raise leaves later `tmp_path`s un-moved |
| New "Cross-device move" note | — | records this is the **only** cross-device move in the plan; mandates `shutil.move`; the only other rename-family call (`os.unlink`) stays within `/media` |
| Task 13 Step 4 | "confirm it lands in input/" (soft) | **assert** via host/`docker exec` `ls -l` that the file is present + non-empty in `demo/input/`; capture upload job final `state` must be `SUCCESS` not `FAILURE`. Explicitly named the live gate for the EXDEV regression |

No other `os.replace`/`os.rename` remains as executable code (grep confirms remaining hits are
prose/comments naming what NOT to do).

### Finding 2 — MEDIUM: FIFO cap (50) unguarded → extract to neutral module + hermetic test

The cap lived inline in the Django-importing `services/cc_assistant._append_cc_turn_complete`
(not hermetically importable; the live gate never hits the 50-turn boundary; a `chat_log[:50]`
mutation passed everything). Re-read the canonical owner module `cc_turn_complete.py` (defined in
Task 11; it carries `from __future__ import annotations`, so its `ChatSession` annotation is a
string and the module is Django-free / hermetically importable). Added the pure helper there.

| | Before | After |
|---|---|---|
| `cc_turn_complete.py` (Task 11) | `TurnCompletePayload` + `serialize_cc_chat_log_entry` | + `append_capped(chat_log, entry, *, cap=50) -> list` (pure, returns new list, newest-kept) |
| `_append_cc_turn_complete` (Task 11a) | inline `if len(chat_log) > MAX_CC_CHAT_LOG_TURNS: chat_log = chat_log[-MAX_CC_CHAT_LOG_TURNS:]` | `chat_log = append_capped(chat_log, serialize_cc_chat_log_entry(payload), cap=MAX_CC_CHAT_LOG_TURNS)` |
| Import (Task 11a) | `import TurnCompletePayload, serialize_cc_chat_log_entry` | + `append_capped` |
| File Structure line | `…serialize_cc_chat_log_entry (neutral module…)` | + `append_capped (the pure FIFO-cap helper…)` |
| Task 11a Step 1 | one test (serialize keys) | added a **mutation-sensitive** test `test_append_capped_keeps_newest_in_order` (feeds 60 → asserts `range(10,60)` in order) + an under-cap test, in `test_cc_chat_log_writer.py` |
| Task 11a Interfaces | "FIFO cap 50 turns" inline | now states the cap is applied via `cc_turn_complete.append_capped` and is hermetically unit-tested; a `chat_log[:50]` mutation would otherwise pass |

**New hermetic test (added in the plan, Task 11a Step 1):**
```python
def test_append_capped_keeps_newest_in_order():
    log: list = []
    for i in range(60):
        log = append_capped(log, {"i": i}, cap=50)
    assert len(log) == 50
    assert [e["i"] for e in log] == list(range(10, 60))
```
**Mutation proof (probe, outside the repo tree):**
- correct impl → `[10..59]` (len 50): assertion PASS.
- `[:50]` (keep-oldest) mutation → `[0..49]` (first=0, last=49): assertion `got != range(10,60)` → **FAILS**.
So the new test is mutation-sensitive exactly as required.

### Finding 3 — MEDIUM: Task 6 Step 6 reorg under-specified → literal paste NameErrors

Re-read real `cc_engine.run_cc_turn` (`cc_engine.py:560-588`). Live order:
```
568  if terminal is None:
569      for event, data in translator.finalize():
570          terminal = (event, data)
572  # Post-turn publish ...
573  published = _publish_artifacts(...)          # produces only `published`
576  if terminal is None:
577      terminal = ("query_complete", {...})
579  event, data = terminal                       # <-- UNPACK; event/data don't exist before here
580  if event == "query_complete" and published:  # old Dropbox block 580-587
588  send_event(event, data)
```
The previous Step 6 paste placed `if event == "query_complete":` **immediately under** the
`_publish_artifacts` call (before line 579) → `NameError: event` on a literal paste.

| | Before | After |
|---|---|---|
| Task 6 Step 6 | publish call + `if event == "query_complete":` consume block shown back-to-back at the `:573` anchor (NameErrors) | rewritten to show the **final assembled `:573-588` region verbatim**: publish call (produces `result`) → `if terminal is None` default → `event, data = terminal` (unpack stays at 579) → `if event == "query_complete":` consume block (replaces old 580-587) → a `>>> Task 11 persist block inserted HERE <<<` marker → `send_event(event, data)`. Added an explicit **ORDERING INVARIANT** paragraph stating the publish call must stay before the unpack and the consume block must stay after it |

Side-by-side (real line ↔ final assembled):
| real cc_engine.py | final assembled (plan Step 6) |
|---|---|
| `published = _publish_artifacts(...)` (573) | `result = _publish_artifacts(...)` (same position, before unpack) |
| `event, data = terminal` (579) | `event, data = terminal` (unchanged position) |
| `if event == "query_complete" and published:` (580) | `if event == "query_complete":` consume block (replaces 580-587) |
| `send_event(event, data)` (588) | `send_event(event, data)` (last; Task 11 block inserted just above it) |

Variable-name consistency confirmed: Step 6 names the publish result `result`, and the Task 11
persist block reads `result["files_created"]`/`result["files_modified"]` — consistent.

### Finding 4 — MEDIUM: re-raise on the SUCCESS path discards a paid reply

**SPEC-3 decision checked (verbatim re-read):**
- §6.5 "Persist + render" — describes only the *write path* ("append the CCTrace…", "persist so it
  survives reload"); **no** failure-discard rule.
- §7 "Full transcript recoverability" — "Write path: after a turn, read the session jsonl,
  compress, and upsert the CCSessionTranscript row"; **no** failure-discard rule.
- E5 (display-trace storage), E6 (dedicated table), E7 (zstd) — storage choices only.
- **Conclusion: SPEC-3 does NOT lock a re-raise-and-discard behavior.** The re-raise was a
  plan-level "Phase 2 hardened" policy, not a locked design. → **Fixed in-plan (no escalation).**

| | Before | After |
|---|---|---|
| Persist block (Task 11) | `else: raise RuntimeError("cc persist: missing transcript jsonl…")`; `on_turn_complete(...)` uncaught | best-effort: `on_turn_complete(...)` wrapped in `try/except` that **logs at error level** and re-raises **only** under `getattr(settings, "CC_PERSIST_STRICT", False)`; the missing-jsonl `else` branch logs at error level and re-raises only under the same strict gate. `data["cc_traces"]`/`data["mode"]` are set before the try so the live trace rides out; `send_event(event, data)` (from Step 6 order) always delivers the reply |
| Task 11 policy item #6 | "**Always re-raise** on persist failure … no silent degrade" | "best-effort on the success path … never converted to query_error … hard re-raise only behind dev/test `CC_PERSIST_STRICT`" + note that SPEC §6.5/§7 lock only the write path |
| "Empty/missing jsonl policy" | "re-raise RuntimeError so Task 13 reload gate fails loudly" | "do not discard the paid reply"; reload assertion (Step 6) is the gate; `CC_PERSIST_STRICT=True` opt-in for hard failure |
| Risk Register row 9 | "Persist re-raise after successful CC turn → user sees query_error … retry doubles spend" | reframed: best-effort silently delivers without a trace if a path mismatch persists post-gate; mitigated by error log + Task 13 reload assertion + `CC_PERSIST_STRICT`; guarantees a paid reply is never turned into query_error |
| Task 13 Step 6 | reload assertion only | added: this non-empty-`cc_traces`-after-reload assertion is **the** acceptance gate for persistence now (best-effort no longer surfaces as query_error); run with `CC_PERSIST_STRICT=True` for a hard signal |

**Status: verified (fixed in-plan; no SPEC override).**

### Finding 5 — LOW / cosmetic notes

| Note | Disposition |
|---|---|
| LOW `_Other.type: str \| None` deviates from §6.3 illustrative `str` | No change required (reviewer: "No change needed beyond the existing note"; §6.3 is illustrative, §6.2 is the LOCKED schema). Left as-is. |
| LOW Task 13 does not enumerate PLAN-7's content-marker allowlist | **Fixed** — Task 13 Step 8 now names the PLAN-7 §8 markers (`migrate nextseek_api 0007`, `cc_traces`, `inspect registered`, per-command exit codes) the validator re-checks, making the cross-target handshake explicit. |
| LOW Task 10 "all" download re-zips `artifacts.zip` | **Fixed** — the `rglob("*")` now filters `and p.name != "artifacts.zip"` with an explanatory comment. |
| LOW Phase 2 Vetting Log numbering gap (row 23 → 25) | **NOT fixed — out of my authority.** The Vetting Log table is orchestrator-owned (task instructions forbid touching it). Recorded here for the orchestrator. |

---

## Defects I could NOT fully resolve
- **LOW (Vetting Log row numbering gap):** intentionally left — the "Phase 2 Vetting Log" table is
  orchestrator-owned and explicitly off-limits to the hardener. Not a code/contract defect.

## Follow-up suggestions (NOT applied — out of surgical scope)
- The §3/E3 raw-transcript convenience copy (`shutil.copy2(jsonl_path, raw_copy)`) and its
  fixed-name basename guard `raise ValueError("bad transcript basename")` are still hard on the
  success path. The basename is the constant `transcript-{run_id}.jsonl` (provably safe), so the
  ValueError is effectively unreachable; and a `copy2` IOError would propagate to query_error. This
  is outside finding 4's exact scope (which the reviewer scoped to *jsonl discovery*), so it was
  left unchanged. If desired, the raw-copy could also be wrapped best-effort in a later pass.
- `data["mode"] = "cc"` is now set in both the Task 6 Step 6 consume block and the Task 11 persist
  block (pre-existing redundancy, harmless). Not consolidated to keep the diff minimal.

---

Self-verification: re-read [7 sections] (Task 6 Step 6, Task 9 Step 3 + staging/cross-device
notes, Task 11 persist block + policies, Task 11a writer + tests, Task 13 Steps 4/6/8, Risk
Register row 9, File Structure), confirmed [9/9 defects landed as claimed] (HIGH×1, MEDIUM×3,
LOW×2 fixed + LOW×1 no-op-by-design + LOW×1 deferred-to-orchestrator, plus the LOW `_Other` note
adjudicated no-change), introduced [0 collateral changes], cross-checked [4 cross-target contracts]
verbatim against canonical owners (MEDIA_ROOT=/media, /srv/dmac/users bind, real run_cc_turn 560-588
order, SPEC §6.5/§7 persistence wording). Locked-design alignment: verified (SPEC-3 unedited; finding
4 confirmed not locked). Defects unresolved: [Vetting-Log numbering gap — orchestrator-owned table,
off-limits to hardener].
