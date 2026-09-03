# PLAN-3 Phase 2 Review (iter 5, fresh, cold context)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-3-ui-based-io.md`  
**Locked design:** `nextseek_api/cc_assistant/archive/SPEC-3-ui-based-io.md`  
**Reviewer:** Independent adversarial pre-execution (lenses 2A–2D)  
**Date:** 2026-06-30  

---

## 2A — Vet (permissions, execution snags)

### HIGH — AppLayout 3e fix reads the wrong session-id source at `query_complete` time

**Location:** Task 12 Step 1 — *"AppLayout: use `sessionId` from `useChatApi()`"*

**Quote:** `const authSid = sessionId ?? d.session_id;`

**Why defect:** Verified `useChatApi.ts`: `NextseekApiService._sessionId` is set synchronously on HTTP 202 (`chatApi.ts:96`), but React `sessionId` state is updated only in `.finally()` after the WS/poll completes (`useChatApi.ts:44-46`). `query_complete` fires mid-stream, before `.finally()`, so hook `sessionId` is still `null` or stale. The fallback `?? d.session_id` reproduces today's WS promotion — 3e intent is not met for `AppLayout.tsx`.

**Fix:** For AppLayout, mirror EmbeddedApp: hold a `serviceRef` to the **same** `NextseekApiService` instance that `submitQuery` uses (or expose synchronous `getSessionId()` from `useChatApi`), and promote with `serviceRef.current.sessionId` at `query_complete`. Add a Vitest or integration note that promotion must occur before WS teardown.

---

### HIGH — Task 12 wires CC artifact download via `serviceRef`, but AppLayout has no submitting-service ref

**Location:** Task 12 Step 5 — *"Wire `onCcArtifactDownload` from `EmbeddedApp`/`AppLayout` to `serviceRef.current.downloadCcArtifact(...)`"*

**Why defect:** Verified `AppLayout.tsx`: queries go through `useChatApi()`'s internal ref; there is no `serviceRef` to that instance. A separate `service` state (`AppLayout.tsx:62`) is passed only to `useSessions`. Implementer following the plan cannot wire CC downloads in AppLayout without inventing architecture.

**Fix:** Unify on one `NextseekApiService` ref per layout (shared by `useChatApi`, sessions, and CC download/upload), or extend `useChatApi` to return `{ serviceRef, downloadCcArtifact, uploadFiles, ... }`. Document the pattern explicitly for both entry points.

---

### MEDIUM — Upload destination field diverges from locked SPEC §4 without reconciliation note

**Location:** Task 9 Step 5 vs SPEC-3 §4

**Quote (SPEC):** *"`build_user_dirs(...).input_src` = … Files are saved there."*  
**Quote (PLAN):** *"`run_cc_upload_task.delay(input_mnt=dirs.input_mnt, ...)`"*

**Why defect:** Authority hierarchy is locked design > plan. Inside the nextseek container, Django must write via mount paths (`*_mnt` pattern from Step 2). Plan is technically correct; SPEC names `input_src` (host bind). A cold implementer cross-checking SPEC may write uploads to a host path invisible to the container.

**Fix:** Add an explicit Task 9 note: *"SPEC §4 `input_src` intent; implementation uses `input_mnt` because Django runs in-container (mirror `output_mnt` publish pattern)."* Optionally amend SPEC in a separate `/ultraplan amend` — do not silently leave the contradiction.

---

### MEDIUM — Task 9b (upload list) is not an executable TDD contract

**Location:** Task 9b — Steps 1–5 are outline bullets only (no failing test code, no action signature, no verify command).

**Why defect:** Every other backend task includes RED→GREEN steps. Task 9b is required for SPEC §4 *"upload + list"* and Task 13 Step 4 live gate, but an implementer can skip or stub it and still close adjacent tasks.

**Fix:** Expand Task 9b to full TDD form: paste `list_input_files` helper test, DRF action skeleton, verify command, commit message — matching Task 9 density.

---

### LOW — Permissions table omits `Path`/`tempfile` imports for Task 10 snippet

Task 10 paste uses `Path`, `Response`, `StreamingHttpResponse` without listing required imports at module top. Minor stall risk.

---

## 2B — Stress test

### HIGH — Task 6 Step 5b `--cov-fail-under=95` on `cc_engine` is unsatisfiable as written

**Location:** Task 6 Step 5b verify command

**Quote:** `--cov=nextseek_api.cc_assistant.cc_engine --cov-fail-under=95`

**Why defect:** Global constraints (line 29) limit ≥95% to *pure modules* (Tasks 1, 3–7, 9 validator) — `cc_engine` is not listed. Measured today: `test_cc_engine_publish.py` alone yields **39%** line coverage on `cc_engine.py` (155 stmts, 94 miss). Hitting 95% would require docker/run_cc_turn integration tests outside the hermetic harness — contradicting the plan's DB/Docker exclusions.

**Likely failure:** Implementer passes publish tests, fails verify, blocks Task 6 indefinitely.

**Catastrophic failure mode:** Task 11 persist + live reload (Risk Register #1) — if `on_turn_complete` lands in the wrong module or `chat_log` uses `reply` instead of `assistant_reply`, panel data vanishes on reload while live WS looks fine.

**Fix:** Scope coverage to `_publish_artifacts` + helpers (extract under test, or `--cov=...cc_engine` with documented exception + Task 13 live gate), OR add hermetic unit tests with heavy mocking for `run_cc_turn` post-publish block only. Align Task 6 verify with line 29 pure-module policy.

---

### MEDIUM — AppLayout live path drops `artifacts` and `cc_traces` on `query_complete`

**Location:** Task 12 Step 2 (EmbeddedApp-only live attach)

**Why defect:** Verified `EmbeddedApp.tsx:115-123` calls `updateLastAssistantMessage({ … artifacts })`; `AppLayout.tsx:112-114` only `addAssistantMessage(d.reply)`. Both layouts are shipped. CC deliverables and activity panel will not appear live in AppLayout until reload (if persist works).

**Fix:** Task 12 Step 2 must require identical `query_complete` patching in **both** `EmbeddedApp.tsx` and `AppLayout.tsx` (artifacts + ccTraces + updateLastAssistantMessage pattern).

---

### MEDIUM — `hasSearchDetails` gate will hide CC activity panel unless extended

**Location:** Task 12 Step 3 vs `MessageBubble.tsx:77-79`

**Why defect:** Collapsible chrome renders only when `hasSearchDetails = hasDebug || hasExtracted`. CC turns have neither; plan says render when `ccTraces?.length` but does not instruct updating `hasSearchDetails`. Panel toggle never appears.

**Fix:** Add explicit step: `const hasCcTrace = !message.isUser && (message.ccTraces?.length ?? 0) > 0;` and `hasSearchDetails = hasDebug || hasExtracted || hasCcTrace`.

---

### MEDIUM — Artifact download endpoint session param does not isolate per-chat-session artifacts

**Location:** Task 10 `download_artifact` — reads `dirs.output_mnt/artifacts` for any owned session

**Why defect:** Storage is per-user project output tree, cumulative across turns/sessions. Any valid `session` UUID owned by the user grants access to the entire artifacts directory, not artifacts from that turn only. Cross-turn leakage within the same user is possible if URLs are guessed.

**Fix:** Document as accepted (user-scoped not session-scoped), OR namespace artifacts under `output/artifacts/<chat_session_id>/` in Task 6 publish, OR require turn-scoped keys stored in `chat_log`.

---

### LOW — PLAN-7 cross-note (not re-vetting PLAN-7)

Task 8 E8 sets neutral default `_DEFAULT_HOST_USER_ROOT = "/srv/dmac/users"`. PLAN-7 Task 6 (G7-10) retires host bind `/srv/dmac/users` in favor of named volume `dmac-cc-users`. No immediate Step 3 blocker on dev (env override), but prod cutover will supersede E8 host default semantics — track in integration tracker, not Step 3 scope.

---

## 2C — Validate external dependencies

### OK — `zstandard>=0.25`

PyPI/doc confirm `ZstdDecompressor.stream_reader` + chunked read supports bounded output; plan's loop cap is compatible. Task 1 adds dep early (good).

### OK — pydantic v2 ordered `Union` with `_Other` last

Matches unpinned pydantic guidance in plan; `_Other.type: str | None` in plan intentionally looser than SPEC §6.3 `type: str` to tolerate `parse_transcript` unparsed lines — acceptable plan hardening if implementer follows plan over SPEC prose.

### OK — Celery registration pattern

Verified `batch_upload/celery_app.py` already routes `cc_assistant.*` to `batch_upload` queue; explicit `import nextseek_api.cc_assistant.cc_upload_tasks` (Task 9 Step 3b) mirrors existing `cc_sweep` import — necessary and correct.

### OK — Migration dependency `0006_merge_extra_state_guards`

Exists on disk; Task 2 dependency claim verified.

### LOW — `dmac_assistant.run_tracker.diff_files` import in `_publish_artifacts`

Hermetic `test_cc_engine_publish.py` currently fails with `ModuleNotFoundError: dmac_assistant` when run in isolation. Plan does not note vendoring/mocking for hermetic runs (pre-existing; may stall Task 6 until addressed).

---

## 2D — Gameproof

### MEDIUM — Task 11 / 11a acceptance is deferrable to prose

**Success condition (quoted):** Task 11 Step 4 *"Hermetic regression suite only"*; Task 11a Step 1 *"grep/source guard … mock ORM save"*

**Cheapest fake:** Implement empty `on_turn_complete` or write `chat_log` with key `reply` instead of `assistant_reply`; grep guard passes if function exists; Task 13 live gate is the only real oracle.

**Remedy:** Already partially hardened (re-raise on missing jsonl, Task 13 reload assertion). Add mandatory hermetic test constructing `TurnCompletePayload` and asserting serialized `chat_log[-1]` keys via a small pure serializer helper, not grep-only.

---

### MEDIUM — Task 2 model-shape guard is gameable

**Success condition:** `test_ccsessiontranscript_model_shape` with standalone `django.setup()` or source-text fallback.

**Cheapest fake:** Source-text grep guard after brittle setup failure — never exercises model fields at runtime.

**Remedy:** Acceptable if Task 13 migrate + ORM upsert is non-negotiable (plan says so). Make Task 13 Step 3 explicitly assert row count + unique constraint — already implied; strengthen Task 2 fallback wording to discourage grep-only without escalation note.

---

### LOW — Task 8 grep-guard gameable by moving strings to comments/docstrings outside scanned files

Plan scans only `cc_engine.py` and `services/cc_assistant.py`. Scan `cc_assistant/` package or add CI grep step.

---

## Verified claims (no finding)

- `_publish_artifacts` returns `list[str]` today; Dropbox augmentation at `cc_engine.py:580-587` — plan baseline correct.
- `_handle_result` on `CCStreamTranslator` at `translate.py:130-156`; lacks `num_turns`/`duration_ms` — Task 5 accurate.
- `UserDirs` lacks `input_mnt`; has `output_mnt` — Task 3 additive field correct.
- `Turn` model `extra="forbid"` without `cc_traces` — Task 7 correct.
- `get_session` builds turns from `chat_log` with `assistant_reply` — Task 11a rationale verified (`assistant.py:515-518`).
- `run_cc_turn` owns post-publish flow; `_run` does not persist transcripts — Task 11 placement verified.
- Celery app import path `nextseek_api.batch_upload.celery_app` — verified.
- Frontend TODOs at `AppLayout.tsx:117-124`, `EmbeddedApp.tsx:126-133` — verified.
- `DROPBOX_DIRECTORY` at `seek/views.py:94` — dead line, grep-removal justified.
- Migration leaf `0006_merge_extra_state_guards` — exists.

---

## Non-blocking cosmetic notes

- File Structure lists `lib/api/chatApi.ts`; actual path is `lib/services/chatApi.ts` (Task 12 body uses correct path).
- Task 6 duplicates "Step 5" heading (lines ~834 and ~898).
- Phase 2 Vetting Log still says "iter-5 reviewer dispatched" — meta, not execution-blocking.
- SPEC status header still says "awaiting writing-plans" while PLAN exists — stale metadata in SPEC only.

---

## Summary counts

| Severity | Count |
|----------|------:|
| CRITICAL | 0 |
| HIGH | 3 |
| MEDIUM | 8 |
| LOW | 5 |

**Top findings (one line each):**
1. AppLayout 3e promotion via `useChatApi().sessionId` cannot see HTTP-202 id at `query_complete` time.
2. Task 6 `cc_engine` 95% coverage gate is unsatisfiable (39% today) and contradicts pure-module coverage policy.
3. AppLayout lacks `serviceRef` pattern for CC artifact download / unified API service.
4. AppLayout `query_complete` omits live `artifacts`/`cc_traces` attach that EmbeddedApp performs.
5. Task 12 must extend `hasSearchDetails` or CC activity panel toggle never renders.
